#!/usr/bin/env python3
"""Entry point: fetch every source, filter, score new jobs, persist, render.

Usage: python3.11 run.py
Requires env vars: GROQ_API_KEY, CANDIDATE_PROFILE (or a local fallback --
see job_search/profile.py), and optionally ADZUNA_APP_ID/ADZUNA_APP_KEY.

Output: site/index.html + site/robots.txt (published to GitHub Pages by the
workflow), data/jobs.json (committed back to main by the workflow).
"""

import time
from datetime import datetime, timezone

from job_search import config, render, scoring, sources, storage
from job_search.profile import build_profile_text, load_profile


def _current_hour_utc() -> int:
    return datetime.now(timezone.utc).hour


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

            analyses = scoring.analyze_batch_with_groq(batch, profile_text)

            for job, analysis in zip(batch, analyses):
                analysis = analysis or {}
                jobs_db[job["url"]] = {**job, **analysis, "first_seen": now_iso}
                print(f"    → {job['title'][:45]} — {analysis.get('score', '?')}/10")

            if batch_start + config.BATCH_SIZE < len(new_jobs):
                print("  ⏳ Waiting 10s before next batch...")
                time.sleep(10)
    else:
        print("\n[ 2-3/4 ] No new vacancies to score.")

    storage.save_jobs(jobs_db)

    print("\n[ 4/4 ] Rendering site...")
    generated_at = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    html = render.generate_html(list(jobs_db.values()), stats, generated_at)
    render.write_site(html)

    good = len([
        j for j in jobs_db.values()
        if isinstance(j.get("score"), (int, float)) and j["score"] >= 7
    ])
    print(f"\n{'=' * 55}")
    print(f"  Done. {len(new_jobs)} new scored this run, "
          f"{len(jobs_db)} total tracked, {good} scored 7+")
    print(f"{'=' * 55}\n")


if __name__ == "__main__":
    main()
