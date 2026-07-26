#!/usr/bin/env python3
"""Persistence for the title-keyword allowlist: data/keywords.json.

Pulled out of config.py so the site's "Keywords" panel can edit it directly
via the GitHub Contents API (see render.py) without touching code -- the
file is just a JSON array of strings, same shape as DEFAULT_TITLE_KEYWORDS.
"""

import json
import os

DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "keywords.json"
)

# Seed used only if data/keywords.json doesn't exist yet (fresh checkout).
DEFAULT_TITLE_KEYWORDS = [
    "technical account", "solution", "consultant",
    "customer success", "implementation", "pre-sales", "crm", "billing",
    "support engineer", "integrat",
    "onboarding", "client partner", "renewal", "solutions engineer",
    "account manager", "customer experience", "partner success",
    "technical support", "product support", "support specialist",
    "solutions consultant", "solutions architect", "professional services",
    "deployment specialist", "deployment engineer",
    "escalation", "incident manager",
]


def load_keywords() -> list[str]:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and all(isinstance(k, str) for k in data):
                return data
        except Exception:
            pass
    return list(DEFAULT_TITLE_KEYWORDS)


def save_keywords(keywords: list[str]) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(keywords, f, ensure_ascii=False, indent=2)
