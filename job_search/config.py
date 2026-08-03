#!/usr/bin/env python3
"""Non-identifying configuration: keyword allowlist, source registry, tunables.

Anything that identifies the candidate (name, salary target, resume bullets,
fit criteria) lives in the CANDIDATE_PROFILE secret instead — see profile.py.
This file is safe to have in a public repo.
"""

from job_search import sources
from job_search.keywords import load_keywords
from job_search.exclusions import load_exclusions

GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

BATCH_SIZE = 5

# Hard cap on how many brand-new vacancies get scored in a single run.
# Confirmed live (2026-07-25): after expanding to 123 sources, a single run
# surfaced 1,069 newly-matched jobs; at BATCH_SIZE=5 with a 10s pause between
# batches that's 200+ Groq calls, which blows through the free-tier rate
# limit and stacks up 429 retry-after waits until the run hits GitHub
# Actions' 6h job cap without ever reaching the commit/publish step. Capping
# here means any run finishes in bounded time; jobs beyond the cap simply
# aren't in jobs_db yet, so they're picked up as "new" again on the next
# scheduled run instead of being lost.
#
# Raised from 150 (2026-08-02) when the schedule moved from every 2h to a
# handful of overnight runs -- fewer total runs per day means each one needs
# more headroom to actually clear the backlog before morning. The 8B model
# (see GROQ_MODEL) scored 149 jobs in ~5min in practice, so 300 has real
# margin under the 35min job timeout even with some rate-limit backoff.
MAX_NEW_JOBS_PER_RUN = 300

# These sources are Ukrainian job boards by construction -- every listing on
# them targets Ukraine, so region is known from the source itself rather than
# left to the LLM to infer per-listing from free-text.
UKRAINE_ONLY_SOURCES = {"DOU", "Djinni", "Work.ua", "Jooble"}

# How many days of scored history to show on the page, and how long to keep
# in data/jobs.json before pruning. Display window is shorter than retention
# so the page doesn't grow unbounded while still surviving a few missed checks.
DISPLAY_DAYS = 21
RETENTION_DAYS = 90

# Final relevance gate applied to every job's title, from every source,
# regardless of whether that source claims server-side search/filtering.
# Confirmed live (2026-07-25) that server-side "search" params on several of
# these APIs return clearly irrelevant titles, so this allowlist -- not the
# upstream search -- is what actually keeps signal-to-noise reasonable before
# a job is worth spending a Groq call on.
#
# Lives in data/keywords.json, not here, so the site's "Keywords" panel can
# edit it via the GitHub API without anyone touching this file. Loaded fresh
# on every run.py invocation (a new process each cron tick), so an edit made
# on the site takes effect on the very next scheduled run.
TITLE_KEYWORDS = load_keywords()

# Title phrases that veto a match regardless of TITLE_KEYWORDS -- see
# exclusions.py. Lives in data/exclude_keywords.json, edited from the site
# via the "Блокувати схожі" button on a job card.
EXCLUDE_KEYWORDS = load_exclusions()

# Sources whose upstream ToS/quota explicitly caps request frequency
# (Remotive: "we advise max. 4 times a day" in its own API response;
# Adzuna: ~1,000 calls/month free tier). Everything else has no published
# constraint and can run every scheduled tick.
RATE_LIMITED_HOURS_UTC = {0, 6, 12, 18}

# Sources with no structured remote flag and no reliable server-side remote
# filter -- run.py double-checks these against the actual fetched job text
# after filling in the description, since a keyword-title match alone isn't
# enough (confirmed live: an unfiltered Work.ua query surfaced a plain
# office job in Kyiv that only matched on title). Every other source is
# either an inherently remote-only board, or already enforces remote via a
# structured field/URL param inside its own fetch_X() function.
REMOTE_VERIFY_SOURCES = {"Work.ua"}

# Support-channel phrases that make a role irrelevant regardless of title
# match. These don't reliably show up in the title, only in the body, so
# run.py checks them against the fetched description (after the HTML-scrape
# step, before spending a Groq call) rather than in title_matches().
DESCRIPTION_EXCLUDE_PHRASES = [
    "live chat support", "live chat agent", "live chat specialist",
    "chat support agent", "chat support representative",
    "phone support", "call center", "call centre",
    "inbound calls", "outbound calls", "answering calls", "answering phones",
    "phone calls with customers", "voice support",
]


# Phrases indicating a listing is scoped to a specific place outside
# Europe/Ukraine (Latin America, APAC, a named non-European country, a
# region abbreviation, or a "citizens/residents of X only" clause) -- the
# relevant set here is Europe, Ukraine, or genuinely worldwide/unrestricted.
# Checked against both title and description since boards phrase this
# inconsistently. Heuristic, not exhaustive: a worldwide listing that merely
# name-drops one of these in passing (e.g. "we also have an office in
# Brazil") could get caught too -- use "Блокувати схожі" on the card for
# anything specific this list misses, or trim an entry here if it's
# over-excluding.
NON_EUROPE_REGION_PHRASES = [
    "latin america", "latam",
    "brazil", "mexico", "colombia", "argentina", "chile", "peru", "ecuador",
    "venezuela", "costa rica", "guatemala", "honduras", "el salvador",
    "panama", "bolivia", "paraguay", "uruguay",
    "apac", "asia-pacific", "philippines", "india", "pakistan", "bangladesh",
    "vietnam", "indonesia", "malaysia", "thailand", "singapore", "china",
    "japan", "south korea", "australia", "new zealand",
    "nigeria", "south africa", "kenya", "egypt", "morocco",
    "united arab emirates", "uae", "saudi arabia", "israel",
    "us citizens only", "us-based only", "united states only", "usa only",
    "authorized to work in the united states", "canada only",
]


def description_excluded(text: str) -> bool:
    t = (text or "").lower()
    if any(p in t for p in DESCRIPTION_EXCLUDE_PHRASES):
        return True
    return any(p in t for p in NON_EUROPE_REGION_PHRASES)


def has_remote_signal(text: str) -> bool:
    t = (text or "").lower()
    return "remote" in t or "віддал" in t or "дистанц" in t

SOURCES = [
    {"name": "Djinni", "fetch": sources.fetch_djinni, "rate_limited": False},
    {"name": "DOU", "fetch": sources.fetch_dou, "rate_limited": False},
    {"name": "Work.ua", "fetch": sources.fetch_workua, "rate_limited": False},
    {"name": "Remotive", "fetch": sources.fetch_remotive, "rate_limited": True},
    {"name": "RemoteOK", "fetch": sources.fetch_remoteok, "rate_limited": False},
    {"name": "Arbeitnow", "fetch": sources.fetch_arbeitnow, "rate_limited": False},
    {"name": "Jobicy", "fetch": sources.fetch_jobicy, "rate_limited": False},
    {"name": "Adzuna", "fetch": sources.fetch_adzuna, "rate_limited": True},
    {"name": "Greenhouse", "fetch": sources.fetch_greenhouse, "rate_limited": False},
    {"name": "Working Nomads", "fetch": sources.fetch_workingnomads, "rate_limited": False},
    {"name": "Himalayas", "fetch": sources.fetch_himalayas, "rate_limited": False},
    {"name": "Lever", "fetch": sources.fetch_lever, "rate_limited": False},
    {"name": "Ashby", "fetch": sources.fetch_ashby, "rate_limited": False},
    {"name": "SmartRecruiters", "fetch": sources.fetch_smartrecruiters, "rate_limited": False},
    {"name": "Workable", "fetch": sources.fetch_workable, "rate_limited": False},
    # Not rate_limited: with the schedule down to ~4-5 runs/day total
    # (see scrape-and-publish.yml), Jooble would hit its 4-hour allowlist
    # window on only some of those runs anyway, which just meant confusing
    # "0 found" cycles for no real quota benefit -- the 500-request free
    # tier isn't remotely at risk from 4-5 calls/day.
    {"name": "Jooble", "fetch": sources.fetch_jooble, "rate_limited": False},
]


def title_matches(title: str) -> bool:
    t = title.lower()
    if any(kw in t for kw in EXCLUDE_KEYWORDS):
        return False
    return any(kw in t for kw in TITLE_KEYWORDS)
