#!/usr/bin/env python3
"""Persistence for the title-exclusion blocklist: data/exclude_keywords.json.

Same shape and purpose as keywords.py's allowlist, but inverted: a title
matching one of these phrases is rejected regardless of how well it matches
TITLE_KEYWORDS. Exists so a single broad allow-keyword (e.g. "operation")
doesn't have to be narrowed or dropped just because it also matches one
irrelevant title pattern (e.g. "Chief Operating Officer") -- the site's
"Блокувати схожі" button on a job card writes here via the GitHub Contents
API, same as the Keywords panel does for data/keywords.json.
"""

import json
import os

DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "exclude_keywords.json"
)


def load_exclusions() -> list[str]:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and all(isinstance(k, str) for k in data):
                return data
        except Exception:
            pass
    return []


def save_exclusions(exclusions: list[str]) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(exclusions, f, ensure_ascii=False, indent=2)
