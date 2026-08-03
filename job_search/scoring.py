#!/usr/bin/env python3
"""Groq batch scoring of vacancies against the candidate profile."""

import json
import os
import re
import time

import requests

from job_search.config import GROQ_MODEL, GROQ_URL


class QuotaExhausted(Exception):
    """All retries for a batch were rejected with 429 -- the API quota for
    this run is spent, not just a transient per-minute limit. The caller
    should stop scoring entirely rather than burn the same wait on every
    remaining batch."""


def analyze_batch_with_groq(jobs_batch: list[dict], profile_text: str) -> list[dict | None]:
    """Score a batch (typically 5) of vacancies in one Groq request."""
    api_key = os.environ["GROQ_API_KEY"]

    vacancies_text = ""
    for i, job in enumerate(jobs_batch, 1):
        vacancies_text += (
            f"\n--- ВАКАНСІЯ {i} ---\nНазва: {job['title']}\nURL: {job['url']}\n"
            f"Текст: {(job.get('description') or '')[:800]}\n"
        )

    prompt = f"""Проаналізуй {len(jobs_batch)} вакансій на основі профілю кандидата.

ПРОФІЛЬ КАНДИДАТА:
{profile_text}

ВАКАНСІЇ:
{vacancies_text}

Відповідай ТІЛЬКИ валідним JSON масивом з {len(jobs_batch)} об'єктів, без жодного тексту до або після:
[
  {{
    "score": <число 1-10>,
    "summary": "<СУВОРО у форматі 'Назва компанії. Сфера/індустрія. Дуже короткий опис чим займається компанія (до ~12 слів).' українською -- лише про компанію, БЕЗ оцінки відповідності кандидату чи деталей вакансії>",
    "role_type": "<тип ролі>",
    "company_type": "<тип компанії>",
    "salary": "<зарплата або Не вказана>",
    "remote": "<ОБОВ'ЯЗКОВО одне з: Фул-Remote / Гібрид / Офіс / Не вказано>",
    "region": "<ОБОВ'ЯЗКОВО одне з: Україна / Закордон / Не вказано -- визнач за локацією компанії, згадками міста/країни чи валюти зарплати в тексті; якщо вакансія прямо вимагає перебування в Україні або компанія українська -- Україна; якщо явно про іншу країну/віддалено на іноземну компанію -- Закордон; якщо незрозуміло -- Не вказано>",
    "pros": ["<плюс 1>", "<плюс 2>"],
    "cons": ["<мінус 1>", "<мінус 2>"]
  }}
]"""

    rate_limited_attempts = 0
    for attempt in range(3):
        try:
            resp = requests.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2000,
                    "temperature": 0.2,
                },
                timeout=60,
            )
            if resp.status_code == 429:
                # Capped at 60s: Groq's retry-after reflects whatever window
                # was exceeded, and for a daily/token quota that can be
                # hours -- sleeping the raw value is what stacked up into a
                # 6h+ hang once run volume grew (see MAX_NEW_JOBS_PER_RUN).
                # A bounded wait plus the 3-attempt cap below means a batch
                # gives up and moves on instead of blocking the whole run.
                rate_limited_attempts += 1
                if rate_limited_attempts >= 3:
                    raise QuotaExhausted(
                        "Groq rejected 3/3 attempts with 429 -- quota exhausted"
                    )
                retry_after = min(int(resp.headers.get("retry-after", 30)), 60)
                print(f"    ⏳ Rate limit hit, waiting {retry_after}s...")
                time.sleep(retry_after + 2)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```[a-z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
            results = json.loads(content)
            if isinstance(results, list):
                return results
            return [None] * len(jobs_batch)
        except QuotaExhausted:
            raise
        except Exception as e:
            if attempt < 2:
                print(f"    ⚠ Retry {attempt + 1}/3: {e}")
                time.sleep(10)
            else:
                print(f"    ⚠ Groq batch error: {e}")
                return [None] * len(jobs_batch)
    return [None] * len(jobs_batch)
