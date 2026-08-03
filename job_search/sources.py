#!/usr/bin/env python3
"""Fetchers for every job source.

Each fetch_X() returns list[dict] shaped:
    {"title": str, "url": str, "source": str, "description": str | None,
     "posted_at": str | None}

`description` is None for Djinni/DOU/Work.ua/Greenhouse/SmartRecruiters/
Workable -- their search-results responses don't include full job body
text, so run.py does a second per-job page fetch for those specifically.
Every other source includes the description inline in its API/RSS
response, so no second fetch is needed (or wanted -- it would just be
extra load for no reason).

`posted_at` is the source's own posting-date field (see _to_iso), normalized
to a UTC ISO string -- the real "vacancy went up on this date", as opposed
to first_seen (added in run.py), which is just when *we* first scraped it
and can lag the real posting by up to a scheduled-run interval. Absent
(key missing entirely) for Djinni/DOU/Work.ua: HTML-scraped, and none of
the three expose a posting date on the listing page without a much deeper
per-job scrape. render.py falls back to first_seen for those.
"""

import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

MAX_DESC = 1500


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header"):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            text = data.strip()
            if text:
                self.text.append(text)

    def get_text(self):
        return " ".join(self.text)


_SALARY_RE = re.compile(
    r"[$€£]\s?\d[\d,]*(?:\.\d+)?\s?[kK]?\s*(?:-|–|—|to)\s*[$€£]?\s?\d[\d,]*(?:\.\d+)?\s?[kK]?"
    r"(?:\s*(?:USD|EUR|GBP|per\s*year|/\s*year|/\s*yr|annually))?",
    re.IGNORECASE,
)


def extract_text(html: str, limit: int = 4000) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:
        pass
    full = parser.get_text()
    # Postings often put compensation near the end (e.g. after a long
    # requirements list) -- confirmed live on an Ashby listing where "$92,000
    # - $138,000 USD" sat at character 5300 of a 6000-char description, well
    # past both this function's limit and scoring.py's further [:800] slice
    # for the Groq prompt, so the model always saw "not specified" regardless
    # of the real salary being right there in the text. Hoisting a found
    # match to the front means it survives whatever gets sliced off after.
    m = _SALARY_RE.search(full)
    prefix = f"Compensation: {m.group(0)}. " if m else ""
    return (prefix + full)[:limit]


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _to_iso(value) -> str | None:
    """Normalizes a source's native posting-date field to a UTC ISO string.

    Handles every shape seen across sources: epoch seconds (Arbeitnow,
    Himalayas), epoch milliseconds (Lever), ISO 8601 strings with or
    without a timezone/fractional seconds/trailing "Z" (most APIs), and
    RFC 2822 (WeWorkRemotely's RSS <pubDate>). Returns None on anything
    unexpected -- render.py falls back to first_seen in that case, so a
    source drifting its date format quietly degrades instead of crashing
    the run.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            # 13-digit ms vs 10-digit sec, same heuristic every ATS's JS
            # client uses to tell the two apart.
            seconds = value / 1000 if value > 10**12 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        text = str(value)
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = parsedate_to_datetime(text)  # RFC 2822, e.g. RSS <pubDate>
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def fetch_url(url: str, timeout: int = 15) -> str | None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk,en;q=0.9",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"    ⚠ fetch error {url[:70]}: {e}")
        return None


def _dedupe(jobs: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for j in jobs:
        if j["url"] in seen:
            continue
        seen.add(j["url"])
        out.append(j)
    return out


# ============================================================
# Djinni, DOU, Work.ua -- HTML search pages, keyword URL per query
# ============================================================

_DJINNI_KEYWORDS = [
    "technical-account-manager", "solution-consultant", "implementation-consultant",
    "customer-success-manager", "product-support-engineer", "technical-support-engineer",
    "pre-sales-engineer", "technical-consultant", "crm-consultant", "support-engineer",
    "integrator", "integration-specialist", "onboarding-manager", "solutions-engineer",
]


def fetch_djinni() -> list[dict] | None:
    jobs = []
    any_success = False
    for kw in _DJINNI_KEYWORDS:
        html = fetch_url(f"https://djinni.co/jobs/keyword-{kw}/?employment=remote")
        time.sleep(1)
        if not html:
            continue
        any_success = True
        jobs.extend(_extract_djinni_jobs(html))
    return _dedupe(jobs) if any_success else None


def _extract_djinni_jobs(html: str) -> list[dict]:
    jobs = []
    links = re.findall(r'href="(/jobs/\d+-[^"]+)"', html)
    seen = set()
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        # Djinni's title-tag markup churns often; the URL slug is a stable
        # stand-in and is always present for every listing.
        slug = link.split("/", 2)[-1].rstrip("/")
        parts = slug.split("-")
        if parts and parts[0].isdigit():
            parts = parts[1:]
        title = " ".join(parts).title()
        jobs.append({
            "title": title,
            "url": f"https://djinni.co{link}",
            "source": "Djinni",
            "description": None,
        })
    return jobs


_DOU_KEYWORDS = [
    "Technical+Account+Manager", "Solution+Consultant", "Implementation+Consultant",
    "Customer+Success+Manager", "Technical+Support+Engineer", "Technical+Consultant",
    "Integration+Specialist", "Onboarding+Manager",
]


def fetch_dou() -> list[dict] | None:
    jobs = []
    any_success = False
    for kw in _DOU_KEYWORDS:
        html = fetch_url(f"https://jobs.dou.ua/vacancies/?search={kw}&remote=1")
        time.sleep(1)
        if not html:
            continue
        any_success = True
        jobs.extend(_extract_dou_jobs(html))
    return _dedupe(jobs) if any_success else None


def _extract_dou_jobs(html: str) -> list[dict]:
    # Title and link live on the *same* <a class="vt"> tag -- parsed
    # structurally so mis-pairing (the original bug) is impossible.
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for li in soup.select("li.l-vacancy"):
        a = li.select_one("a.vt")
        if a and a.get("href"):
            jobs.append({
                "title": a.get_text(strip=True),
                "url": a["href"],
                "source": "DOU",
                "description": None,
            })
    return jobs


# "remote+" prefix confirmed live (2026-07-25): work.ua treats "remote" as
# just another free-text token in the keyword slug, and it reliably biases
# results toward listings actually tagged for remote work (verified: job
# pages returned this way say "work remote" in their own <title>, versus an
# unprefixed search that surfaced a plain office job in Kyiv). Work.ua has
# no structured remote flag/URL param, so this text bias plus the
# description-based verification in run.py (REMOTE_VERIFY_SOURCES) is the
# two-layer filter for this source specifically.
_WORKUA_KEYWORDS = [
    "remote+technical+account+manager", "remote+solution+consultant",
    "remote+implementation+consultant", "remote+customer+success+manager",
    "remote+technical+support+engineer", "remote+technical+consultant",
    "remote+integration+specialist", "remote+onboarding+manager",
]


def fetch_workua() -> list[dict] | None:
    jobs = []
    any_success = False
    for kw in _WORKUA_KEYWORDS:
        html = fetch_url(f"https://www.work.ua/en/jobs-{kw}/")
        time.sleep(1)
        if not html:
            continue
        any_success = True
        jobs.extend(_extract_workua_jobs(html))
    return _dedupe(jobs) if any_success else None


def _extract_workua_jobs(html: str) -> list[dict]:
    # Same fix as DOU: title+link from one <a> inside <h2 class="my-0">,
    # never two independently-indexed regex sweeps.
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for h2 in soup.select("h2.my-0"):
        a = h2.find("a", href=True)
        if a and a["href"].startswith("/en/jobs/"):
            jobs.append({
                "title": a.get_text(strip=True),
                "url": f"https://www.work.ua{a['href']}",
                "source": "Work.ua",
                "description": None,
            })
    return jobs


# ============================================================
# International remote boards -- structured JSON/RSS, no regex needed
# ============================================================

def fetch_remotive() -> list[dict] | None:
    # No search/category param: confirmed live that Remotive's own
    # `search=` returns the same unfiltered ~38-job feed regardless of
    # query, so there's nothing gained from passing one. The shared
    # TITLE_KEYWORDS gate (applied later in run.py) does the real filtering.
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs", timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    ⚠ Remotive fetch error: {e}")
        return None
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "title": j.get("title", ""),
            "url": j.get("url", ""),
            "source": "Remotive",
            "description": extract_text(j.get("description", ""), MAX_DESC),
            "location": j.get("candidate_required_location", ""),
            "posted_at": _to_iso(j.get("publication_date")),
        })
    return jobs


def fetch_remoteok() -> list[dict] | None:
    try:
        resp = requests.get(
            "https://remoteok.com/api",
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    ⚠ RemoteOK fetch error: {e}")
        return None
    jobs = []
    for j in data:
        if "position" not in j:
            continue  # first element is a legal-notice blob, not a job
        jobs.append({
            "title": j.get("position", ""),
            "url": j.get("url") or j.get("apply_url", ""),
            "source": "RemoteOK",
            "description": extract_text(j.get("description", ""), MAX_DESC),
            "location": j.get("location", ""),
            "posted_at": _to_iso(j.get("date")),
        })
    return jobs


def fetch_arbeitnow() -> list[dict] | None:
    jobs = []
    any_success = False
    for page in (1, 2):
        try:
            resp = requests.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params={"page": page},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠ Arbeitnow fetch error (page {page}): {e}")
            continue
        any_success = True
        for j in data.get("data", []):
            if not j.get("remote"):
                continue
            jobs.append({
                "title": j.get("title", ""),
                "url": j.get("url", ""),
                "source": "Arbeitnow",
                "description": extract_text(j.get("description", ""), MAX_DESC),
                "location": j.get("location", ""),
                "posted_at": _to_iso(j.get("created_at")),
            })
        time.sleep(0.5)
    return jobs if any_success else None


_WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
    "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
]


def fetch_weworkremotely() -> list[dict] | None:
    jobs = []
    any_success = False
    for feed_url in _WWR_FEEDS:
        xml_text = fetch_url(feed_url)
        if not xml_text:
            continue
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            print(f"    ⚠ WeWorkRemotely RSS parse error: {e}")
            continue
        any_success = True
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = item.findtext("description") or ""
            if title and link:
                jobs.append({
                    "title": title,
                    "url": link,
                    "source": "WeWorkRemotely",
                    "description": extract_text(desc, MAX_DESC),
                    "posted_at": _to_iso(item.findtext("pubDate")),
                })
        time.sleep(0.5)
    return _dedupe(jobs) if any_success else None


def fetch_jobicy() -> list[dict] | None:
    try:
        resp = requests.get(
            "https://jobicy.com/api/v2/remote-jobs",
            params={"count": 50},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    ⚠ Jobicy fetch error: {e}")
        return None
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "title": j.get("jobTitle", ""),
            "url": j.get("url", ""),
            "source": "Jobicy",
            "description": extract_text(j.get("jobExcerpt") or j.get("jobDescription") or "", MAX_DESC),
            "location": j.get("jobGeo", ""),
            "posted_at": _to_iso(j.get("pubDate")),
        })
    return jobs


# Rotates one keyword/day (by day-of-year) rather than burning the free
# quota (~1,000 calls/month) on every keyword every run.
_ADZUNA_KEYWORDS = [
    "technical account manager",
    "solution consultant",
    "customer success manager",
    "implementation consultant",
]


def fetch_adzuna() -> list[dict] | None:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("    ⚠ Adzuna skipped: ADZUNA_APP_ID/ADZUNA_APP_KEY not set")
        return None
    keyword = _ADZUNA_KEYWORDS[datetime.now(timezone.utc).timetuple().tm_yday % len(_ADZUNA_KEYWORDS)]
    try:
        resp = requests.get(
            "https://api.adzuna.com/v1/api/jobs/gb/search/1",
            params={
                "app_id": app_id,
                "app_key": app_key,
                "what": keyword,
                "results_per_page": 50,
                "content-type": "application/json",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    ⚠ Adzuna fetch error (keyword={keyword!r}): {e}")
        return None
    jobs = []
    for j in data.get("results", []):
        # Adzuna has no structured remote flag -- confirmed live. Require
        # "remote" in the title or description as a text-based gate before
        # this job is even considered, since the user wants remote-only,
        # no exceptions, and Adzuna listings are mostly on-site by default.
        title = j.get("title", "")
        desc_raw = j.get("description", "")
        if "remote" not in (title + " " + desc_raw).lower():
            continue
        jobs.append({
            "title": _strip_tags(title),
            "url": j.get("redirect_url", ""),
            "source": "Adzuna",
            "description": extract_text(desc_raw, MAX_DESC),
            "posted_at": _to_iso(j.get("created")),
        })
    return jobs


_JOOBLE_KEYWORDS = [
    "customer success", "technical support", "account manager",
    "solutions engineer", "implementation", "customer support",
]


def fetch_jooble() -> list[dict] | None:
    # Official partner API (POST, JSON body), not scraped HTML -- unlike
    # Work.ua this isn't a consumer-facing page a WAF can bot-block. Queried
    # against location="Україна" specifically since this is meant to widen
    # Ukraine coverage; region is forced by UKRAINE_ONLY_SOURCES in config.py
    # same as DOU/Djinni/Work.ua rather than left to the LLM to infer.
    api_key = os.environ.get("JOOBLE_API_KEY")
    if not api_key:
        print("    ⚠ Jooble skipped: JOOBLE_API_KEY not set")
        return None
    keyword = _JOOBLE_KEYWORDS[datetime.now(timezone.utc).timetuple().tm_yday % len(_JOOBLE_KEYWORDS)]
    try:
        resp = requests.post(
            f"https://jooble.org/api/{api_key}",
            json={"keywords": keyword, "location": "Україна"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    ⚠ Jooble fetch error (keyword={keyword!r}): {e}")
        return None
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "title": _strip_tags(j.get("title", "")),
            "url": j.get("link", ""),
            "source": "Jooble",
            "description": extract_text(j.get("snippet", ""), MAX_DESC),
            "posted_at": _to_iso(j.get("updated")),
        })
    return jobs


# ============================================================
# Greenhouse -- curated per-company public boards, zero auth, zero ToS risk
# ============================================================
# Verified live (2026-07-25): boards-api.greenhouse.io/v1/boards/{slug}/jobs
# works with no auth for any company using Greenhouse's public job board,
# returning every open role with an absolute_url to the public listing page.
# This is a fundamentally different source shape from the aggregators above:
# instead of a keyword search across a board, it's a curated list of
# specific companies worth targeting directly (all B2B SaaS/fintech/dev-tools
# companies with a track record of TAM/CS/Solutions/Support hiring). No
# published rate limit, but a short delay between companies is kept anyway
# to be a polite, low-load client.
_GREENHOUSE_COMPANIES = [
    "gitlab", "stripe", "datadog", "twilio", "figma", "intercom", "pagerduty",
    "mongodb", "elastic", "gusto", "airtable", "okta", "calendly", "lattice",
    "smartsheet", "cloudflare", "fastly", "newrelic", "sumologic", "asana",
    "amplitude", "dropbox",
    # Verified live (2026-07-25), same criteria as above.
    "affirm", "airbnb", "algolia", "anthropic", "attentive", "bark", "block",
    "boxinc", "braze", "carvana", "checkr", "chime", "coinbase", "coursera",
    "cultureamp", "databricks", "discord", "doximity", "duolingo", "everlaw",
    "instacart", "iterable", "khanacademy", "klaviyo", "lyft", "mixpanel",
    "netskope", "outschool", "peloton", "pinterest", "reddit", "remotecom",
    "squarespace", "tanium", "temporaltechnologies", "twitch", "typeform",
    "udemy", "vercel", "webflow", "zscaler",
]


def fetch_greenhouse() -> list[dict] | None:
    jobs = []
    any_success = False
    for company in _GREENHOUSE_COMPANIES:
        try:
            resp = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs",
                params={"content": "false"},
                timeout=15,
            )
            if resp.status_code == 404:
                # Company no longer on Greenhouse / wrong slug -- not a
                # transient failure, just skip it without counting against
                # any_success for the *other* companies in the list.
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠ Greenhouse fetch error ({company}): {e}")
            continue
        any_success = True
        for j in data.get("jobs", []):
            # Confirmed live (2026-07-25): location.name is a real, reliable
            # signal here ("Remote, Italy" vs "San Francisco, CA") -- most
            # of these companies' listings are on-site, so this check
            # matters a lot, not just a formality.
            location_name = (j.get("location") or {}).get("name", "")
            if "remote" not in location_name.lower():
                continue
            if j.get("absolute_url"):
                jobs.append({
                    "title": j.get("title", ""),
                    "url": j["absolute_url"],
                    "source": "Greenhouse",
                    "description": None,
                    "location": location_name,
                    # Greenhouse's job-board API has no created_at, only
                    # updated_at -- the closest available proxy for posting
                    # date (usually the same for a listing that's never
                    # been edited since it went up).
                    "posted_at": _to_iso(j.get("updated_at")),
                })
        time.sleep(0.3)
    return jobs if any_success else None


def fetch_himalayas() -> list[dict] | None:
    # No working search/category filter -- confirmed live that `search=`
    # and `categories=` both return the same unfiltered feed regardless of
    # value, same situation as Remotive. TITLE_KEYWORDS does the filtering.
    try:
        resp = requests.get("https://himalayas.app/jobs/api", params={"limit": 100}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    ⚠ Himalayas fetch error: {e}")
        return None
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "title": j.get("title", ""),
            "url": j.get("applicationLink", ""),
            "source": "Himalayas",
            "description": extract_text(j.get("description", ""), MAX_DESC),
            "location": " / ".join(j.get("locationRestrictions") or []),
            "posted_at": _to_iso(j.get("pubDate")),
        })
    return jobs


# ============================================================
# Lever, Ashby, SmartRecruiters -- curated per-company public ATS boards,
# same rationale as Greenhouse above: zero auth, zero ToS risk, and each
# slug below was verified live (2026-07-25) to return real postings.
# ============================================================
_LEVER_COMPANIES = [
    "netomi", "RouteThis", "jobgether", "conversica", "Sprinto",
    "fetchpackage", "tonkean", "palantir",
    # Verified live (2026-07-25) -- most large orgs migrated off Lever, so
    # the hit rate here is much lower than Greenhouse/Ashby.
    "spotify", "wealthfront", "outreach",
]


def fetch_lever() -> list[dict] | None:
    jobs = []
    any_success = False
    for company in _LEVER_COMPANIES:
        try:
            resp = requests.get(
                f"https://api.lever.co/v0/postings/{company}",
                params={"mode": "json"},
                timeout=15,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠ Lever fetch error ({company}): {e}")
            continue
        if not isinstance(data, list):
            continue  # {"ok": false, ...} for boards that no longer exist
        any_success = True
        for j in data:
            # Confirmed live (2026-07-25): workplaceType is a real, clean
            # enum here ("remote" / "hybrid" / "onsite") -- e.g. Palantir's
            # board was 101 onsite + 185 hybrid + only 1 remote, so this
            # check matters a lot, not just a formality.
            if j.get("workplaceType") != "remote":
                continue
            categories = j.get("categories") or {}
            location = " / ".join(filter(None, [categories.get("location")] + (categories.get("allLocations") or [])))
            jobs.append({
                "title": j.get("text", ""),
                "url": j.get("hostedUrl", ""),
                "source": "Lever",
                "description": extract_text(j.get("descriptionPlain", ""), MAX_DESC),
                "location": location,
                "posted_at": _to_iso(j.get("createdAt")),
            })
        time.sleep(0.3)
    return jobs if any_success else None


_SMARTRECRUITERS_COMPANIES = ["Flywire1", "MicroStrategy1", "Freshworks", "Visa"]


def fetch_smartrecruiters() -> list[dict] | None:
    jobs = []
    any_success = False
    for company in _SMARTRECRUITERS_COMPANIES:
        try:
            resp = requests.get(
                f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                params={"limit": 100},
                timeout=15,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠ SmartRecruiters fetch error ({company}): {e}")
            continue
        any_success = True
        for j in data.get("content", []):
            # Confirmed live (2026-07-25): location.remote is a real
            # boolean here, cleaner than guessing from free text.
            if not (j.get("location") or {}).get("remote"):
                continue
            jobs.append({
                "title": j.get("name", ""),
                "url": f"https://jobs.smartrecruiters.com/{company}/{j.get('id', '')}",
                "source": "SmartRecruiters",
                "description": None,
                "location": (j.get("location") or {}).get("fullLocation", ""),
                "posted_at": _to_iso(j.get("releasedDate")),
            })
        time.sleep(0.3)
    return jobs if any_success else None


# ============================================================
# Workable -- curated per-company public boards, same shape as Greenhouse.
# Verified live (2026-07-25): apply.workable.com/api/v1/widget/accounts/{slug}
# needs no auth. Hit rate on guessed slugs is much lower than Greenhouse/
# Ashby (most companies that size have migrated to a bigger ATS), so this
# list stays short until more real slugs are found.
# ============================================================
_WORKABLE_COMPANIES = ["huggingface", "flosum", "recurly"]


def fetch_workable() -> list[dict] | None:
    jobs = []
    any_success = False
    for company in _WORKABLE_COMPANIES:
        try:
            resp = requests.get(
                f"https://apply.workable.com/api/v1/widget/accounts/{company}",
                timeout=15,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠ Workable fetch error ({company}): {e}")
            continue
        any_success = True
        for j in data.get("jobs", []):
            if not j.get("shortlink") or not j.get("telecommuting"):
                continue
            location = ", ".join(filter(None, [j.get("city", ""), j.get("state", ""), j.get("country", "")]))
            jobs.append({
                "title": j.get("title", ""),
                "url": j["shortlink"],
                "source": "Workable",
                # The widget response has no job body text; run.py's
                # HTML-scrape fallback (triggered by description=None)
                # fetches it from the shortlink page instead.
                "description": None,
                "location": location,
                "posted_at": _to_iso(j.get("published_on")),
            })
        time.sleep(0.3)
    return jobs if any_success else None


def fetch_workingnomads() -> list[dict] | None:
    try:
        resp = requests.get("https://www.workingnomads.com/api/exposed_jobs/", timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    ⚠ Working Nomads fetch error: {e}")
        return None
    jobs = []
    for j in data:
        jobs.append({
            "title": j.get("title", ""),
            "url": j.get("url", ""),
            "source": "Working Nomads",
            "description": extract_text(j.get("description", ""), MAX_DESC),
            "location": j.get("location", ""),
            "posted_at": _to_iso(j.get("pub_date")),
        })
    return jobs


# ============================================================
# Ashby -- curated per-company public boards, same shape as Greenhouse
# ============================================================
# Verified live (2026-07-25): api.ashbyhq.com/posting-api/job-board/{slug}
# works with no auth, 404s cleanly for a wrong/unused slug (a reliable
# validity signal, unlike SmartRecruiters/Workable which return 200 for
# any slug -- tried both, couldn't distinguish real accounts from guesses,
# so left out for now). Each job has a proper `isRemote` boolean, the
# cleanest remote signal of any source here -- no text heuristics needed.
_ASHBY_COMPANIES = [
    "ramp", "linear", "notion", "vanta", "substack", "watershed", "runway",
    "modal", "posthog", "perplexity", "openai",
    # Verified live (2026-07-25), same criteria -- Ashby is the fastest-
    # growing ATS among AI-native/dev-tool startups so the hit rate here
    # is high.
    "elevenlabs", "cohere", "langchain", "supabase", "workos", "gamma",
    "harvey", "sierra", "cursor", "replit", "fireworks", "baseten",
    "pinecone", "deepgram", "sardine",
]


def fetch_ashby() -> list[dict] | None:
    jobs = []
    any_success = False
    for company in _ASHBY_COMPANIES:
        try:
            resp = requests.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{company}",
                timeout=15,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠ Ashby fetch error ({company}): {e}")
            continue
        any_success = True
        for j in data.get("jobs", []):
            if not j.get("isRemote"):
                continue
            secondary = [loc.get("location", "") for loc in (j.get("secondaryLocations") or [])]
            location = " / ".join(filter(None, [j.get("location", "")] + secondary))
            jobs.append({
                "title": j.get("title", ""),
                "url": j.get("jobUrl", ""),
                "source": "Ashby",
                "description": extract_text(j.get("descriptionPlain") or "", MAX_DESC),
                "location": location,
                "posted_at": _to_iso(j.get("publishedAt")),
            })
        time.sleep(0.3)
    return jobs if any_success else None
