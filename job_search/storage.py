#!/usr/bin/env python3
"""Persistence for scored jobs: data/jobs.json, a dict keyed by URL.

One file serves three purposes: dedup membership test (`url in jobs`), the
render source (render.py reads straight from this), and duplicate-proofing
(a dict can't hold the same key twice, by construction) -- replacing the
old flat seen_jobs.json URL array, which only did the first of these.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from job_search.config import RETENTION_DAYS

DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jobs.json"
)


def load_jobs() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_jobs(jobs: dict) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    pruned = {}
    for url, job in jobs.items():
        try:
            seen_at = datetime.fromisoformat(job.get("first_seen", ""))
        except ValueError:
            seen_at = datetime.now(timezone.utc)  # malformed timestamp: keep it, don't lose data
        if seen_at >= cutoff:
            pruned[url] = job
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=2)
