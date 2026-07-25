#!/usr/bin/env python3
"""Loads the candidate profile (name, target roles, resume bullets, fit
criteria) from the CANDIDATE_PROFILE env var (a GitHub Actions secret in
production) so none of that text lives in the public repo.

For local dry runs without touching the real secret, falls back to
data/candidate_profile.local.json -- gitignored, never committed.
"""

import json
import os

_LOCAL_FALLBACK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "candidate_profile.local.json",
)


def load_profile() -> dict:
    raw = os.environ.get("CANDIDATE_PROFILE")
    if raw:
        return json.loads(raw)
    if os.path.exists(_LOCAL_FALLBACK):
        with open(_LOCAL_FALLBACK, "r", encoding="utf-8") as f:
            return json.load(f)
    raise RuntimeError(
        "No CANDIDATE_PROFILE env var set and no local fallback found at "
        f"{_LOCAL_FALLBACK}. Set the env var (production) or create the "
        "local file (dev) -- see README for the expected shape."
    )


def build_profile_text(profile: dict) -> str:
    target_str = "\n".join(f"- {t}" for t in profile.get("target", []))
    not_str = "\n".join(f"- {t}" for t in profile.get("not_interested", []))
    return f"""# Job Search Profile

## Target roles
{target_str}

## Not interested in
{not_str}

## Key experience
{profile.get('key_experience', '')}

## Fit criteria
{profile.get('fit_criteria', '')}

## Scoring guide
- 8-10: Perfect match — right role, product company, right domain, remote, salary OK
- 6-7: Good potential — mostly matches with minor gaps
- 4-5: Worth a look — some red flags but interesting aspects
- 1-3: Skip — agency, wrong level, low salary, office-only
"""
