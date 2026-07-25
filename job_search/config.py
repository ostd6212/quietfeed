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
]


def title_matches(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in TITLE_KEYWORDS)
