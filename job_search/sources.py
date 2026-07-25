#!/usr/bin/env python3
"""Fetchers for every job source.

Each fetch_X() returns list[dict] shaped:
    {"title": str, "url": str, "source": str, "description": str | None}

`description` is None for Djinni/DOU/Work.ua -- their search-results pages
don't include job body text, so run.py does a second per-job page fetch for
those three specifically. Every other source includes the description
inline in its API/RSS response, so no second fetch is needed (or wanted --
it would just be extra load for no reason).
"""

import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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


def extract_text(html: str, limit: int = 4000) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:
        pass
    return parser.get_text()[:limit]


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


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


_WORKUA_KEYWORDS = [
    "technical+account+manager", "solution+consultant", "implementation+consultant",
    "customer+success+manager", "technical+support+engineer", "technical+consultant",
    "integration+specialist", "onboarding+manager",
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
        jobs.append({
            "title": _strip_tags(j.get("title", "")),
            "url": j.get("redirect_url", ""),
            "source": "Adzuna",
            "description": extract_text(j.get("description", ""), MAX_DESC),
        })
    return jobs
