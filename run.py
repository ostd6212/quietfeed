#!/usr/bin/env python3
"""Entry point: fetch every source, filter, score new jobs, persist, render.

Usage: python3.11 run.py
Requires env vars: GROQ_API_KEY, CANDIDATE_PROFILE (or a local fallback --
see job_search/profile.py), and optionally ADZUNA_APP_ID/ADZUNA_APP_KEY and
JOOBLE_API_KEY.

Output: site/index.html + site/robots.txt (published to GitHub Pages by the
workflow), data/jobs.json (committed back to main by the workflow).
"""

import time
from datetime import datetime, timedelta, timezone

import requests

from job_search import config, render, scoring, sources, status, storage
from job_search.scoring import QuotaExhausted
from job_search.profile import build_profile_text, load_profile

# How many already-stored, non-hidden listings to re-check per run, and how
# long a listing goes between checks. Confirmed live (2026-08-04): Lever/
# Ashby postings routinely close and start 404ing well within the 21-day
# display window, and nothing else in the pipeline ever re-visits a URL
# once it's scored -- a closed listing just sits there until it ages out.
# Batched + interval-gated instead of checking every stored job every run
# so this stays light (~100-150 stored jobs -> full coverage in a few
# cycles, not one slow one).
LINK_RECHECK_BATCH_SIZE = 40
LINK_RECHECK_INTERVAL_DAYS = 3


def _current_hour_utc() -> int:
    return datetime.now(timezone.utc).hour


def revalidate_stored_links(jobs_db: dict) -> int:
    """Re-checks a capped, interval-gated batch of stored listings' URLs and
    hides ones that now 404/410 (job closed/removed). Ambiguous responses
    (403/503/timeouts -- bot-blocking or a network hiccup, not proof the job
    is gone) just refresh the checked timestamp so they're retried on a
    later run instead of being hidden on a guess."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=LINK_RECHECK_INTERVAL_DAYS)

    candidates = []
    for url, job in jobs_db.items():
        if job.get("hidden"):
            continue
        checked_at = job.get("link_checked_at")
        if checked_at:
            try:
                if datetime.fromisoformat(checked_at) >= cutoff:
                    continue
            except ValueError:
                pass
        candidates.append((checked_at or "", url, job))

    candidates.sort(key=lambda c: c[0])  # never-checked ("") first, then oldest
    batch = candidates[:LINK_RECHECK_BATCH_SIZE]

    hidden = 0
    for _, url, job in batch:
        try:
            resp = requests.get(
                url, headers={"User-Agent": sources.USER_AGENT}, timeout=12, stream=True
            )
            resp.close()
            if resp.status_code in (404, 410):
                job["hidden"] = True
                hidden += 1
        except requests.RequestException:
            pass
        job["link_checked_at"] = now.isoformat()

    if batch:
        print(f"  Re-checked {len(batch)} stored listing link(s), hid {hidden} now-dead")
    return hidden


def fetch_all() -> tuple[list[dict], list[dict]]:
    """Returns (matched_jobs, source_stats) across every configured source."""
    hour = _current_hour_utc()
    matched = []
    stats = []

    for src in config.SOURCES:
        name = src["name"]
        if src["rate_limited"] and hour not in config.RATE_LIMITED_HOURS_UTC:
            print(f"  Skipping {name} (rate-limited, not this cycle)")
            stats.append({"name": name, "skipped": True})
            continue

        print(f"  Checking {name}...")
        result = src["fetch"]()
        if result is None:
            print(f"    ✗ {name}: fetch failed")
            stats.append({"name": name, "ok": False, "error": "джерело недоступне"})
            continue

        kept = [j for j in result if config.title_matches(j["title"])]
        print(f"    ✓ {name}: {len(result)} total, {len(kept)} match keywords")
        stats.append({"name": name, "ok": True, "count": len(kept)})
        matched.extend(kept)

    return matched, stats


def main():
    print(f"\n{'=' * 55}\n  Job Radar\n{'=' * 55}\n")

    profile = load_profile()
    profile_text = build_profile_text(profile)

    jobs_db = storage.load_jobs()

    print("[ 0/4 ] Re-checking stored listing links...")
    revalidate_stored_links(jobs_db)

    status.publish("fetching")
    print("[ 1/4 ] Fetching sources...")
    matched, stats = fetch_all()

    new_jobs = []
    seen_urls = set()
    for job in matched:
        if not job.get("url") or job["url"] in seen_urls or job["url"] in jobs_db:
            continue
        seen_urls.add(job["url"])
        new_jobs.append(job)

    print(f"\n  {len(new_jobs)} new vacancies out of {len(matched)} matched this run "
          f"({len(jobs_db)} already tracked)")

    deferred_count = 0
    if len(new_jobs) > config.MAX_NEW_JOBS_PER_RUN:
        deferred_count = len(new_jobs) - config.MAX_NEW_JOBS_PER_RUN
        print(f"  Capping to {config.MAX_NEW_JOBS_PER_RUN} for this run "
              f"({deferred_count} deferred to next run)")
        new_jobs = new_jobs[:config.MAX_NEW_JOBS_PER_RUN]

    status.publish("scoring", found=len(new_jobs), scored=0)

    if new_jobs:
        print("\n[ 2/4 ] Fetching full text for HTML-scraped sources...")
        for job in new_jobs:
            if job.get("description") is None:
                html = sources.fetch_url(job["url"])
                job["description"] = sources.extract_text(html) if html else ""

        # Some sources (config.REMOTE_VERIFY_SOURCES) have no reliable
        # server-side remote filter -- double-check against the actual
        # fetched page text before spending a Groq call on what might
        # turn out to be an office job that only matched on title.
        before = len(new_jobs)
        new_jobs = [
            j for j in new_jobs
            if j["source"] not in config.REMOTE_VERIFY_SOURCES
            or config.has_remote_signal(j["description"])
        ]
        if before != len(new_jobs):
            print(f"  Dropped {before - len(new_jobs)} non-remote listing(s) after verification")

        before = len(new_jobs)
        new_jobs = [
            j for j in new_jobs
            if not config.location_excluded(j.get("location", ""))
            and not config.description_excluded(f"{j.get('location', '')} {j['description']}")
        ]
        if before != len(new_jobs):
            print(f"  Dropped {before - len(new_jobs)} listing(s) (live-chat/call support or non-Europe location)")

    if new_jobs:
        print(f"\n[ 3/4 ] Scoring {len(new_jobs)} vacancies with AI "
              f"(Groq, batch of {config.BATCH_SIZE})...")
        now_iso = datetime.now(timezone.utc).isoformat()
        total_batches = (len(new_jobs) + config.BATCH_SIZE - 1) // config.BATCH_SIZE

        for batch_start in range(0, len(new_jobs), config.BATCH_SIZE):
            batch = new_jobs[batch_start:batch_start + config.BATCH_SIZE]
            batch_num = batch_start // config.BATCH_SIZE + 1
            print(f"  Batch [{batch_num}/{total_batches}]: "
                  f"{', '.join(j['title'][:25] for j in batch)}")

            try:
                analyses = scoring.analyze_batch_with_groq(batch, profile_text)
            except QuotaExhausted as e:
                deferred_count += len(new_jobs) - batch_start
                print(f"    ⚠ {e} -- stopping scoring for this run, "
                      f"{len(new_jobs) - batch_start} vacancy(ies) deferred to next run")
                break

            for job, analysis in zip(batch, analyses):
                analysis = analysis or {}
                if job["source"] in config.UKRAINE_ONLY_SOURCES:
                    analysis["region"] = "Україна"
                jobs_db[job["url"]] = {**job, **analysis, "first_seen": now_iso}
                print(f"    → {job['title'][:45]} — {analysis.get('score', '?')}/10")

            scored_so_far = min(batch_start + config.BATCH_SIZE, len(new_jobs))
            status.publish("scoring", found=len(new_jobs), scored=scored_so_far)

            if batch_start + config.BATCH_SIZE < len(new_jobs):
                print("  ⏳ Waiting 10s before next batch...")
                time.sleep(10)
    else:
        print("\n[ 2-3/4 ] No new vacancies to score.")

    storage.save_jobs(jobs_db)

    print("\n[ 4/4 ] Rendering site...")
    generated_at = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    html = render.generate_html(
        list(jobs_db.values()), stats, generated_at, config.TITLE_KEYWORDS, deferred_count
    )
    render.write_site(html)

    good = len([
        j for j in jobs_db.values()
        if isinstance(j.get("score"), (int, float)) and j["score"] >= 7
    ])
    print(f"\n{'=' * 55}")
    print(f"  Done. {len(new_jobs)} new scored this run, "
          f"{len(jobs_db)} total tracked, {good} scored 7+")
    print(f"{'=' * 55}\n")

    status.publish("idle", found=len(new_jobs), scored=len(new_jobs), deferred=deferred_count)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        status.publish("error")
        raise
