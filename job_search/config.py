#!/usr/bin/env python3
"""Non-identifying configuration: keyword allowlist, source registry, tunables.

Anything that identifies the candidate (name, salary target, resume bullets,
fit criteria) lives in the CANDIDATE_PROFILE secret instead — see profile.py.
This file is safe to have in a public repo.
"""

from job_search import sources

GROQ_MODEL = "llama-3.3-70b-versatile"
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
MAX_NEW_JOBS_PER_RUN = 150

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
TITLE_KEYWORDS = [
    # original set
    "technical account", "solution", "consultant",
    "customer success", "implementation", "pre-sales", "crm", "billing",
    "support engineer", "integrat",
    # explicitly missing synonyms
    "onboarding", "client partner", "renewal", "solutions engineer",
    # adjacent CS/TAM-family titles
    "account manager", "customer experience", "partner success",
    # adjacent support-family titles
    "technical support", "product support", "support specialist",
    # adjacent solutions/professional-services family
    "solutions consultant", "solutions architect", "professional services",
    "deployment specialist", "deployment engineer",
    # matches KEY_EXPERIENCE background (incident/escalation management)
    "escalation", "incident manager",
]

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
    {"name": "WeWorkRemotely", "fetch": sources.fetch_weworkremotely, "rate_limited": False},
    {"name": "Jobicy", "fetch": sources.fetch_jobicy, "rate_limited": False},
    {"name": "Adzuna", "fetch": sources.fetch_adzuna, "rate_limited": True},
    {"name": "Greenhouse", "fetch": sources.fetch_greenhouse, "rate_limited": False},
    {"name": "Working Nomads", "fetch": sources.fetch_workingnomads, "rate_limited": False},
    {"name": "Himalayas", "fetch": sources.fetch_himalayas, "rate_limited": False},
    {"name": "Lever", "fetch": sources.fetch_lever, "rate_limited": False},
    {"name": "Ashby", "fetch": sources.fetch_ashby, "rate_limited": False},
    {"name": "SmartRecruiters", "fetch": sources.fetch_smartrecruiters, "rate_limited": False},
    {"name": "Workable", "fetch": sources.fetch_workable, "rate_limited": False},
]


def title_matches(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in TITLE_KEYWORDS)
