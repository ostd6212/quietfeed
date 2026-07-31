#!/usr/bin/env python3
"""Renders site/index.html + site/robots.txt from the accumulated job data.

Two things this does that the original script didn't:
  - Shows every job scored within DISPLAY_DAYS, not just this run's diff --
    with no push notifications, a run you don't happen to open shouldn't
    cost you visibility into a good match forever.
  - Prints a per-source status line every run (see _status_panel). The
    original failure mode here was silent breakage nobody noticed for
    weeks; this makes breakage visible on the one artifact that gets
    opened, instead of buried in a log or an unread mailbox.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from job_search.config import DISPLAY_DAYS

REGIONS = ["Україна", "Закордон", "Не вказано"]

WORKFLOW_URL = "https://github.com/ostd6212/quietfeed/actions/workflows/scrape-and-publish.yml"
STATUS_URL = "https://raw.githubusercontent.com/ostd6212/quietfeed/main/data/status.json"

# Repo/path the Keywords panel's JS writes to via the GitHub Contents API --
# see the inline <script> in generate_html for why a static GitHub Pages
# site can still persist an edit (CORS-enabled REST API + a token the user
# supplies themselves, kept in their own browser's localStorage).
GH_OWNER = "ostd6212"
GH_REPO = "quietfeed"
GH_KEYWORDS_PATH = "data/keywords.json"
GH_JOBS_PATH = "data/jobs.json"
GH_BRANCH = "main"
GH_WORKFLOW_FILE = "scrape-and-publish.yml"

# A run this long is almost certainly stuck, not just slow -- worst case
# under MAX_NEW_JOBS_PER_RUN=150 is roughly 150 sequential HTML fetches
# (~37min at the 15s timeout) plus 30 Groq batches at the 60s-capped
# rate-limit backoff (~90min), so ~2h with real margin. Past that, three
# consecutive runs got stuck for hours (2026-07-25/26) before this cap
# existed, and there was no way to tell from the page -- just GitHub's
# Actions tab.
STUCK_THRESHOLD_MIN = 120

SITE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site"
)


def score_color(score) -> str:
    try:
        score = int(score)
    except (TypeError, ValueError):
        return "#9ca3af"
    if score >= 8:
        return "#22c55e"
    elif score >= 6:
        return "#f59e0b"
    elif score >= 4:
        return "#f97316"
    else:
        return "#ef4444"


def _job_card(job: dict) -> str:
    score = job.get("score", "?")
    color = score_color(score)
    summary = job.get("summary", "Аналіз недоступний")
    role_type = job.get("role_type", "—")
    company_type = job.get("company_type", "—")
    salary = job.get("salary", "—")
    remote = job.get("remote", "—")
    pros = job.get("pros") or []
    cons = job.get("cons") or []

    pros_html = "".join(f"<li>{p}</li>" for p in pros) if pros else "<li>—</li>"
    cons_html = "".join(f"<li>{c}</li>" for c in cons) if cons else "<li>—</li>"
    description = (job.get("description") or "").replace("<", "&lt;").replace(">", "&gt;")
    description_html = (
        f'<details class="description"><summary>Повний опис</summary><p>{description}</p></details>'
        if description
        else ""
    )

    return f"""
    <div class="card" data-url="{job['url']}">
        <div class="card-header">
            <div class="score-badge" style="background:{color}">{score}</div>
            <div class="card-title-block">
                <a href="{job['url']}" target="_blank" rel="noopener" class="job-title">{job['title']}</a>
                <span class="source-badge">{job['source']}</span>
            </div>
        </div>
        <p class="summary">{summary}</p>
        <div class="meta-grid">
            <div class="meta-item"><span class="meta-label">Роль</span><span>{role_type}</span></div>
            <div class="meta-item"><span class="meta-label">Компанія</span><span>{company_type}</span></div>
            <div class="meta-item"><span class="meta-label">Зарплата</span><span>{salary}</span></div>
            <div class="meta-item"><span class="meta-label">Формат</span><span>{remote}</span></div>
        </div>
        <div class="pros-cons">
            <div class="pros">
                <div class="pc-label">✓ Плюси</div>
                <ul>{pros_html}</ul>
            </div>
            <div class="cons">
                <div class="pc-label">✗ Мінуси</div>
                <ul>{cons_html}</ul>
            </div>
        </div>
        <div class="card-actions">
            <a href="{job['url']}" target="_blank" rel="noopener" class="apply-btn">Переглянути вакансію →</a>
            <button class="hide-btn" data-url="{job['url']}">Приховати</button>
        </div>
        {description_html}
    </div>"""


def _status_panel(source_stats: list[dict]) -> str:
    rows = ""
    for s in source_stats:
        if s.get("skipped"):
            icon, color, detail = "·", "#475569", "не цей цикл (ліміт частоти джерела)"
        elif s.get("ok"):
            icon, color, detail = "✓", "#22c55e", f"{s.get('count', 0)} знайдено"
        else:
            icon, color, detail = "✗", "#ef4444", s.get("error") or "помилка запиту"
        rows += (
            '<div class="status-row">'
            f'<span style="color:{color}">{icon}</span>'
            f'<span class="status-name">{s["name"]}</span>'
            f'<span class="status-detail">{detail}</span>'
            "</div>"
        )
    return f"""
    <div class="status-panel">
        <div class="status-title">Діагностика останнього запуску</div>
        {rows}
    </div>"""


def generate_html(
    all_jobs: list[dict],
    source_stats: list[dict],
    generated_at: str,
    keywords: list[str],
    deferred: int = 0,
) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DISPLAY_DAYS)
    visible = []
    for job in all_jobs:
        if job.get("hidden"):
            continue
        try:
            seen_at = datetime.fromisoformat(job.get("first_seen", ""))
        except ValueError:
            seen_at = datetime.now(timezone.utc)
        if seen_at >= cutoff:
            visible.append(job)

    def _score_key(j):
        s = j.get("score")
        return s if isinstance(s, (int, float)) else 0

    visible.sort(key=_score_key, reverse=True)

    if not visible:
        cards = """
        <div style="text-align:center; padding:80px 20px; color:#6b7280;">
            <div style="font-size:48px; margin-bottom:16px;">🔍</div>
            <h2 style="font-size:20px; font-weight:600; color:#374151;">Нових вакансій не знайдено</h2>
            <p style="margin-top:8px;">Спробуй перевірити пізніше.</p>
        </div>"""
    else:
        cards = "".join(_job_card(j) for j in visible)

    total = len(visible)
    good = len([j for j in visible if isinstance(j.get("score"), (int, float)) and j["score"] >= 7])

    jobs_meta = [
        {
            "url": j["url"],
            "score": j.get("score") if isinstance(j.get("score"), (int, float)) else 0,
            "region": j.get("region") or "Не вказано",
        }
        for j in visible
    ]
    jobs_json = json.dumps(jobs_meta, ensure_ascii=False)
    keywords_json = json.dumps(keywords, ensure_ascii=False)
    region_options = "".join(f'<option value="{r}">{r}</option>' for r in REGIONS)

    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>Job Radar</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }}

  .header {{ background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border-bottom: 1px solid #1e293b; padding: 32px 24px; }}
  .header-inner {{ max-width: 900px; margin: 0 auto; }}
  .header h1 {{ font-size: 24px; font-weight: 700; color: #f1f5f9; letter-spacing: -0.3px; }}
  .header h1 span {{ color: #38bdf8; }}
  .header-meta {{ margin-top: 8px; font-size: 13px; color: #64748b; }}
  .refresh-btn {{ display: inline-flex; align-items: center; gap: 6px; margin-top: 14px; background: #38bdf8; color: #0f172a; font-size: 13px; font-weight: 600; padding: 8px 16px; border-radius: 7px; text-decoration: none; transition: background 0.2s; }}
  .refresh-btn:hover {{ background: #7dd3fc; }}

  .run-status-panel {{ max-width: 900px; margin: 20px auto 0; padding: 12px 24px; }}
  .run-status-row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .run-status-dot {{ width: 9px; height: 9px; border-radius: 50%; background: #64748b; flex-shrink: 0; }}
  .run-status-dot.running {{ background: #38bdf8; animation: dot-pulse 1.2s ease-in-out infinite; }}
  .run-status-dot.stuck, .run-status-dot.error {{ background: #ef4444; }}
  .run-status-dot.ok {{ background: #22c55e; }}
  @keyframes dot-pulse {{ 0% {{ opacity: .4; }} 50% {{ opacity: 1; }} 100% {{ opacity: .4; }} }}
  .run-status-text {{ font-size: 13px; color: #94a3b8; flex: 1; min-width: 220px; }}
  .run-action-btn {{ background: none; border: 1px solid #334155; color: #94a3b8; font-size: 12px; padding: 6px 12px; border-radius: 7px; cursor: pointer; font-family: inherit; white-space: nowrap; }}
  .run-action-btn:hover {{ border-color: #38bdf8; color: #38bdf8; }}
  .run-action-btn.danger:hover {{ border-color: #ef4444; color: #ef4444; }}
  .run-action-btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .progress-track {{ margin-top: 10px; height: 6px; background: #1e293b; border: 1px solid #334155; border-radius: 4px; overflow: hidden; display: none; }}
  .progress-bar-fill {{ height: 100%; width: 0; background: #38bdf8; transition: width 0.4s ease; }}
  .progress-bar-fill.indeterminate {{ animation: progress-pulse 1.2s ease-in-out infinite; }}
  @keyframes progress-pulse {{ 0% {{ opacity: .35; }} 50% {{ opacity: 1; }} 100% {{ opacity: .35; }} }}

  .stats {{ max-width: 900px; margin: 24px auto; padding: 0 24px; display: flex; gap: 12px; }}
  .stat {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px 20px; flex: 1; }}
  .stat-num {{ font-size: 28px; font-weight: 700; color: #f1f5f9; }}
  .stat-label {{ font-size: 12px; color: #64748b; margin-top: 2px; }}

  .filter-bar {{ max-width: 900px; margin: 0 auto 20px; padding: 16px 24px; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }}
  .filter-bar select {{ background: #0f172a; color: #e2e8f0; border: 1px solid #334155; border-radius: 8px; padding: 8px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }}
  .filter-bar select:focus {{ outline: none; border-color: #38bdf8; }}
  .filter-label {{ font-size: 12px; color: #64748b; }}
  .filter-reset {{ background: none; border: 1px solid #334155; color: #94a3b8; border-radius: 8px; padding: 8px 12px; font-size: 13px; cursor: pointer; font-family: inherit; }}
  .filter-reset:hover {{ border-color: #38bdf8; color: #38bdf8; }}
  .filter-count {{ margin-left: auto; font-size: 13px; color: #64748b; }}

  .cards {{ max-width: 900px; margin: 0 auto; padding: 0 24px 24px; display: flex; flex-direction: column; gap: 16px; }}
  .card.hidden {{ display: none; }}
  .filter-empty {{ max-width: 900px; margin: 0 auto 24px; padding: 40px 24px; text-align: center; color: #6b7280; font-size: 14px; }}

  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; transition: border-color 0.2s; }}
  .card:hover {{ border-color: #38bdf8; }}

  .card-header {{ display: flex; align-items: flex-start; gap: 16px; margin-bottom: 14px; }}
  .score-badge {{ width: 48px; height: 48px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 800; color: #0f172a; flex-shrink: 0; }}
  .card-title-block {{ flex: 1; }}
  .job-title {{ font-size: 17px; font-weight: 600; color: #f1f5f9; text-decoration: none; line-height: 1.3; display: block; }}
  .job-title:hover {{ color: #38bdf8; }}
  .source-badge {{ display: inline-block; margin-top: 5px; font-size: 11px; font-weight: 600; color: #64748b; background: #0f172a; border: 1px solid #334155; border-radius: 4px; padding: 2px 7px; text-transform: uppercase; letter-spacing: 0.5px; }}

  .summary {{ font-size: 14px; line-height: 1.6; color: #94a3b8; margin-bottom: 16px; }}

  .meta-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 16px; }}
  .meta-item {{ background: #0f172a; border-radius: 8px; padding: 10px 12px; font-size: 13px; color: #cbd5e1; }}
  .meta-label {{ display: block; font-size: 11px; color: #475569; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px; }}

  .pros-cons {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 18px; }}
  .pros, .cons {{ background: #0f172a; border-radius: 8px; padding: 12px; }}
  .pc-label {{ font-size: 12px; font-weight: 600; margin-bottom: 6px; }}
  .pros .pc-label {{ color: #22c55e; }}
  .cons .pc-label {{ color: #ef4444; }}
  .pros-cons ul {{ list-style: none; }}
  .pros-cons li {{ font-size: 13px; color: #94a3b8; padding: 2px 0; line-height: 1.4; }}

  .apply-btn {{ display: inline-block; background: #38bdf8; color: #0f172a; font-size: 13px; font-weight: 600; padding: 8px 16px; border-radius: 7px; text-decoration: none; transition: background 0.2s; }}
  .apply-btn:hover {{ background: #7dd3fc; }}
  .card-actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .hide-btn {{ background: none; border: 1px solid #334155; color: #94a3b8; font-size: 13px; padding: 8px 16px; border-radius: 7px; cursor: pointer; font-family: inherit; }}
  .hide-btn:hover {{ border-color: #ef4444; color: #ef4444; }}
  .hide-btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .description {{ margin-top: 14px; border-top: 1px solid #334155; padding-top: 14px; }}
  .description summary {{ font-size: 13px; color: #64748b; cursor: pointer; user-select: none; }}
  .description summary:hover {{ color: #94a3b8; }}
  .description p {{ margin-top: 10px; font-size: 13px; color: #64748b; line-height: 1.6; white-space: pre-wrap; }}

  .keywords-panel {{ max-width: 900px; margin: 0 auto 24px; padding: 16px 24px; background: #1e293b; border: 1px solid #334155; border-radius: 10px; }}
  .keywords-header {{ display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }}
  .keywords-title {{ font-size: 13px; font-weight: 600; color: #f1f5f9; }}
  .keywords-hint {{ font-size: 12px; color: #64748b; }}
  .keywords-list {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
  .keyword-chip {{ display: inline-flex; align-items: center; gap: 6px; background: #0f172a; border: 1px solid #334155; border-radius: 999px; padding: 5px 6px 5px 12px; font-size: 13px; color: #cbd5e1; }}
  .keyword-chip button {{ background: none; border: none; color: #64748b; cursor: pointer; font-size: 15px; line-height: 1; padding: 2px 6px; border-radius: 999px; }}
  .keyword-chip button:hover {{ background: #ef4444; color: #fff; }}
  .keywords-add {{ display: flex; gap: 8px; margin-bottom: 12px; }}
  .keywords-add input {{ flex: 1; background: #0f172a; color: #e2e8f0; border: 1px solid #334155; border-radius: 8px; padding: 8px 12px; font-size: 13px; font-family: inherit; }}
  .keywords-add input:focus {{ outline: none; border-color: #38bdf8; }}
  .keywords-add button, .keywords-actions button {{ background: #38bdf8; color: #0f172a; border: none; font-size: 13px; font-weight: 600; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-family: inherit; white-space: nowrap; }}
  .keywords-add button:hover, .keywords-actions button:hover {{ background: #7dd3fc; }}
  .keywords-actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .keywords-actions button.secondary {{ background: none; border: 1px solid #334155; color: #94a3b8; }}
  .keywords-actions button.secondary:hover {{ background: none; border-color: #38bdf8; color: #38bdf8; }}
  .keywords-actions button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .keywords-status {{ font-size: 12px; color: #64748b; }}

  .status-panel {{ max-width: 900px; margin: 0 auto 48px; padding: 20px 24px; }}
  .status-title {{ font-size: 12px; color: #475569; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }}
  .status-row {{ display: flex; gap: 10px; align-items: center; font-size: 12px; color: #64748b; padding: 3px 0; }}
  .status-name {{ min-width: 130px; color: #94a3b8; }}
  .status-detail {{ color: #64748b; }}
</style>
</head>
<body>
<div class="header">
  <div class="header-inner">
    <h1>Job <span>Radar</span></h1>
    <div class="header-meta">Оновлено: {generated_at}</div>
    <a class="refresh-btn" href="{WORKFLOW_URL}" target="_blank" rel="noopener">↻ Оновити вакансії</a>
  </div>
</div>

<div class="run-status-panel">
  <div class="run-status-row">
    <span class="run-status-dot" id="run-status-dot"></span>
    <span class="run-status-text" id="run-status-text">Перевірка стану пайплайна…</span>
    <button class="run-action-btn" id="run-restart-btn">↻ Перезапустити</button>
    <button class="run-action-btn danger" id="run-cancel-btn" style="display:none">⛔ Скасувати</button>
  </div>
  <div class="progress-track" id="progress-track"><div class="progress-bar-fill" id="progress-bar-fill"></div></div>
</div>

<div class="stats">
  <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">Вакансій (за {DISPLAY_DAYS} дн.)</div></div>
  <div class="stat"><div class="stat-num" style="color:#22c55e">{good}</div><div class="stat-label">Скор 7+</div></div>
  <div class="stat"><div class="stat-num">{len(source_stats)}</div><div class="stat-label">Джерел перевірено</div></div>
  {f'''<div class="stat"><div class="stat-num" style="color:#f59e0b">{deferred}</div><div class="stat-label">У черзі на оцінку (наступні запуски)</div></div>''' if deferred else ""}
</div>

<div class="keywords-panel">
  <div class="keywords-header">
    <span class="keywords-title">Ключові слова пошуку</span>
    <span class="keywords-hint">Вакансія проходить у список, якщо назва містить хоча б одне слово</span>
  </div>
  <div class="keywords-list" id="keywords-list"></div>
  <div class="keywords-add">
    <input type="text" id="keyword-input" placeholder="Нове ключове слово…" maxlength="60">
    <button id="keyword-add-btn">Додати</button>
  </div>
  <div class="keywords-actions">
    <button id="keyword-save-btn" disabled>Зберегти зміни</button>
    <button class="secondary" id="keyword-token-btn">Токен GitHub</button>
    <span class="keywords-status" id="keyword-status"></span>
  </div>
</div>

<div class="filter-bar">
  <span class="filter-label">Скор від</span>
  <select id="filter-score">
    <option value="0">Будь-який</option>
    <option value="5">5+</option>
    <option value="7">7+</option>
    <option value="8">8+</option>
  </select>
  <span class="filter-label">Регіон</span>
  <select id="filter-region">
    <option value="all">Всі</option>
    {region_options}
  </select>
  <button class="filter-reset" id="filter-reset">Скинути фільтри</button>
  <span class="filter-count" id="filter-count"></span>
</div>

<div class="cards" id="cards">
{cards}
</div>
<div class="filter-empty" id="filter-empty" style="display:none">Немає вакансій за цими фільтрами.</div>

{_status_panel(source_stats)}

<script type="application/json" id="jobs-data">{jobs_json}</script>
<script type="application/json" id="keywords-data">{keywords_json}</script>
<script>
(function () {{
  var jobsByUrl = {{}};
  JSON.parse(document.getElementById('jobs-data').textContent).forEach(function (j) {{
    jobsByUrl[j.url] = j;
  }});

  var cards = Array.prototype.slice.call(document.querySelectorAll('#cards .card'));
  var scoreSelect = document.getElementById('filter-score');
  var regionSelect = document.getElementById('filter-region');
  var resetBtn = document.getElementById('filter-reset');
  var countEl = document.getElementById('filter-count');
  var emptyEl = document.getElementById('filter-empty');

  function applyFilters() {{
    var minScore = parseInt(scoreSelect.value, 10) || 0;
    var region = regionSelect.value;
    var visibleCount = 0;

    cards.forEach(function (card) {{
      var job = jobsByUrl[card.getAttribute('data-url')];
      var match = job && job.score >= minScore && (region === 'all' || job.region === region);
      card.classList.toggle('hidden', !match);
      if (match) visibleCount++;
    }});

    countEl.textContent = 'Показано ' + visibleCount + ' з ' + cards.length;
    emptyEl.style.display = (visibleCount === 0 && cards.length > 0) ? 'block' : 'none';
  }}

  scoreSelect.addEventListener('change', applyFilters);
  regionSelect.addEventListener('change', applyFilters);
  resetBtn.addEventListener('click', function () {{
    scoreSelect.value = '0';
    regionSelect.value = 'all';
    applyFilters();
  }});

  applyFilters();
}})();

// Shared by the run-status panel and the Keywords panel below -- both need
// a token to write to the repo (Actions dispatch/cancel, or Contents PUT).
// A plain top-level function (not IIFE-wrapped) so both can call it.
function jobRadarGetToken(forcePrompt) {{
  var TOKEN_KEY = 'jobRadarGhToken';
  var t = localStorage.getItem(TOKEN_KEY);
  if (t && !forcePrompt) return t;
  t = window.prompt(
    'Встав GitHub Personal Access Token (fine-grained, права Contents: Read and write ' +
      'та Actions: Read and write лише на репозиторій {GH_OWNER}/{GH_REPO}).\\n\\n' +
      'Зберігається тільки в localStorage цього браузера і використовується лише для ' +
      'запитів напряму до api.github.com.',
    ''
  );
  if (t) {{ t = t.trim(); localStorage.setItem(TOKEN_KEY, t); }}
  return t || null;
}}

(function () {{
  var STALE_MS = 20 * 60 * 1000;
  var STUCK_MS = {STUCK_THRESHOLD_MIN} * 60 * 1000;
  var OWNER = '{GH_OWNER}';
  var REPO = '{GH_REPO}';
  var WORKFLOW_FILE = '{GH_WORKFLOW_FILE}';

  var dotEl = document.getElementById('run-status-dot');
  var textEl = document.getElementById('run-status-text');
  var restartBtn = document.getElementById('run-restart-btn');
  var cancelBtn = document.getElementById('run-cancel-btn');
  var trackEl = document.getElementById('progress-track');
  var barEl = document.getElementById('progress-bar-fill');

  var latestRun = null; // {{id, status, conclusion, started_at}}

  function fmtMinutes(ms) {{
    var m = Math.round(ms / 60000);
    return m < 1 ? 'менше хвилини' : m + ' хв';
  }}

  function renderProgress(status) {{
    if (!status) {{ trackEl.style.display = 'none'; return; }}
    var age = Date.now() - new Date(status.updated_at).getTime();
    if (status.stage === 'idle' || status.stage === 'error' || age > STALE_MS) {{
      trackEl.style.display = 'none';
      return;
    }}
    var found = status.found || 0;
    var scored = status.scored || 0;
    var pct = (status.stage === 'fetching' || found === 0) ? null : Math.round(scored / found * 100);
    trackEl.style.display = 'block';
    barEl.style.width = (pct === null ? 100 : pct) + '%';
    barEl.classList.toggle('indeterminate', pct === null);
    if (found > 0) {{
      textEl.textContent = 'Знайдено ' + found + ', проскоровано ' + scored + ' (' + pct + '%)';
    }}
  }}

  function renderRunStatus(run) {{
    latestRun = run;
    cancelBtn.style.display = 'none';
    if (!run) {{
      dotEl.className = 'run-status-dot';
      textEl.textContent = 'Статус запуску невідомий (не вдалось отримати дані з GitHub).';
      return;
    }}
    var started = run.run_started_at || run.created_at;
    var elapsed = Date.now() - new Date(started).getTime();

    // GitHub's non-terminal statuses: queued, pending, requested, waiting,
    // in_progress -- anything that isn't 'completed' yet. Only 'in_progress'
    // has a meaningful start time to measure "stuck" against; the others
    // are just waiting for a runner.
    if (run.status !== 'completed') {{
      var running = run.status === 'in_progress';
      var stuck = running && elapsed > STUCK_MS;
      dotEl.className = 'run-status-dot ' + (stuck ? 'stuck' : 'running');
      textEl.textContent = (running ? 'Виконується… (' + fmtMinutes(elapsed) + ')' : 'У черзі…') +
        (stuck ? ' ⚠ Схоже, зависло — можна скасувати і перезапустити.' : '');
      cancelBtn.style.display = 'inline-block';
    }} else if (run.conclusion === 'success') {{
      dotEl.className = 'run-status-dot ok';
      textEl.textContent = 'Останній запуск успішний, ' + fmtMinutes(Date.now() - new Date(run.updated_at).getTime()) + ' тому.';
    }} else {{
      dotEl.className = 'run-status-dot error';
      textEl.textContent = 'Останній запуск: ' + (run.conclusion || 'невідомо') + ', ' +
        fmtMinutes(Date.now() - new Date(run.updated_at).getTime()) + ' тому.';
    }}
  }}

  function pollStatusJson() {{
    fetch('{STATUS_URL}?t=' + Date.now(), {{cache: 'no-store'}})
      .then(function (r) {{ return r.ok ? r.json() : null; }})
      .then(renderProgress)
      .catch(function () {{ trackEl.style.display = 'none'; }});
  }}

  function pollRunStatus() {{
    var token = localStorage.getItem('jobRadarGhToken');
    var headers = {{Accept: 'application/vnd.github+json'}};
    if (token) headers.Authorization = 'token ' + token;
    fetch('https://api.github.com/repos/' + OWNER + '/' + REPO + '/actions/workflows/' + WORKFLOW_FILE + '/runs?per_page=1', {{headers: headers}})
      .then(function (r) {{ return r.ok ? r.json() : null; }})
      .then(function (data) {{ renderRunStatus(data && data.workflow_runs && data.workflow_runs[0]); }})
      .catch(function () {{ renderRunStatus(null); }});
  }}

  restartBtn.addEventListener('click', function () {{
    var token = jobRadarGetToken(false);
    if (!token) return;
    restartBtn.disabled = true;
    fetch('https://api.github.com/repos/' + OWNER + '/' + REPO + '/actions/workflows/' + WORKFLOW_FILE + '/dispatches', {{
      method: 'POST',
      headers: {{Authorization: 'token ' + token, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json'}},
      body: JSON.stringify({{ref: '{GH_BRANCH}'}}),
    }})
      .then(function (r) {{
        if (!r.ok) return r.json().then(function (e) {{ throw new Error(e.message || ('HTTP ' + r.status)); }});
        textEl.textContent = 'Запуск поставлено в чергу…';
        setTimeout(pollRunStatus, 3000);
      }})
      .catch(function (err) {{ textEl.textContent = 'Помилка перезапуску: ' + err.message; }})
      .finally(function () {{ restartBtn.disabled = false; }});
  }});

  cancelBtn.addEventListener('click', function () {{
    if (!latestRun) return;
    var token = jobRadarGetToken(false);
    if (!token) return;
    cancelBtn.disabled = true;
    fetch('https://api.github.com/repos/' + OWNER + '/' + REPO + '/actions/runs/' + latestRun.id + '/cancel', {{
      method: 'POST',
      headers: {{Authorization: 'token ' + token, Accept: 'application/vnd.github+json'}},
    }})
      .then(function (r) {{
        if (!r.ok && r.status !== 202) return r.json().then(function (e) {{ throw new Error(e.message || ('HTTP ' + r.status)); }});
        textEl.textContent = 'Скасування запиту надіслано…';
        setTimeout(pollRunStatus, 3000);
      }})
      .catch(function (err) {{ textEl.textContent = 'Помилка скасування: ' + err.message; }})
      .finally(function () {{ cancelBtn.disabled = false; }});
  }});

  pollStatusJson();
  pollRunStatus();
  setInterval(pollStatusJson, 15000);
  // 90s, not 60s: unauthenticated GitHub API is capped at 60 req/hr per IP,
  // and this page may share an IP/NAT with other traffic. Once a token is
  // saved (pollRunStatus sends it when present), the limit jumps to 5000/hr
  // and this interval stops mattering.
  setInterval(pollRunStatus, 90000);
}})();

(function () {{
  var OWNER = '{GH_OWNER}';
  var REPO = '{GH_REPO}';
  var BRANCH = '{GH_BRANCH}';
  var PATH = '{GH_JOBS_PATH}';

  function b64EncodeUtf8(str) {{
    return btoa(unescape(encodeURIComponent(str)));
  }}
  function b64DecodeUtf8(str) {{
    return decodeURIComponent(escape(atob(str.replace(/\\n/g, ''))));
  }}

  document.querySelectorAll('.hide-btn').forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      var token = jobRadarGetToken(false);
      if (!token) {{ alert('Токен не задано.'); return; }}
      var url = btn.getAttribute('data-url');
      var card = btn.closest('.card');
      var originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Приховую…';

      var apiUrl = 'https://api.github.com/repos/' + OWNER + '/' + REPO + '/contents/' + PATH;

      fetch(apiUrl, {{headers: {{Authorization: 'token ' + token, Accept: 'application/vnd.github+json'}}}})
        .then(function (r) {{
          if (!r.ok) throw new Error('Не вдалось прочитати jobs.json (' + r.status + ')');
          return r.json();
        }})
        .then(function (fileInfo) {{
          var jobs = JSON.parse(b64DecodeUtf8(fileInfo.content));
          if (!jobs[url]) throw new Error('Вакансію не знайдено у файлі');
          jobs[url].hidden = true;
          var content = b64EncodeUtf8(JSON.stringify(jobs, null, 2));
          return fetch(apiUrl, {{
            method: 'PUT',
            headers: {{
              Authorization: 'token ' + token,
              Accept: 'application/vnd.github+json',
              'Content-Type': 'application/json',
            }},
            body: JSON.stringify({{
              message: 'chore: hide vacancy via site UI',
              content: content,
              sha: fileInfo.sha,
              branch: BRANCH,
            }}),
          }});
        }})
        .then(function (r) {{
          if (!r.ok) return r.json().then(function (e) {{ throw new Error(e.message || ('HTTP ' + r.status)); }});
          card.style.display = 'none';
        }})
        .catch(function (err) {{
          btn.disabled = false;
          btn.textContent = originalText;
          alert('Помилка приховування: ' + err.message);
        }});
    }});
  }});
}})();

(function () {{
  var OWNER = '{GH_OWNER}';
  var REPO = '{GH_REPO}';
  var PATH = '{GH_KEYWORDS_PATH}';
  var BRANCH = '{GH_BRANCH}';

  var original = JSON.parse(document.getElementById('keywords-data').textContent);
  var current = original.slice();

  var listEl = document.getElementById('keywords-list');
  var inputEl = document.getElementById('keyword-input');
  var addBtn = document.getElementById('keyword-add-btn');
  var saveBtn = document.getElementById('keyword-save-btn');
  var tokenBtn = document.getElementById('keyword-token-btn');
  var statusEl = document.getElementById('keyword-status');

  function isDirty() {{
    if (current.length !== original.length) return true;
    for (var i = 0; i < current.length; i++) {{ if (current[i] !== original[i]) return true; }}
    return false;
  }}

  function render() {{
    listEl.innerHTML = '';
    current.forEach(function (kw, idx) {{
      var chip = document.createElement('span');
      chip.className = 'keyword-chip';
      var text = document.createElement('span');
      text.textContent = kw;
      var del = document.createElement('button');
      del.type = 'button';
      del.textContent = '×';
      del.title = 'Видалити';
      del.addEventListener('click', function () {{
        current.splice(idx, 1);
        render();
      }});
      chip.appendChild(text);
      chip.appendChild(del);
      listEl.appendChild(chip);
    }});
    saveBtn.disabled = !isDirty();
    if (!isDirty()) statusEl.textContent = '';
  }}

  addBtn.addEventListener('click', function () {{
    var v = inputEl.value.trim().toLowerCase();
    if (!v) return;
    if (current.indexOf(v) === -1) current.push(v);
    inputEl.value = '';
    render();
  }});
  inputEl.addEventListener('keydown', function (e) {{
    if (e.key === 'Enter') {{ e.preventDefault(); addBtn.click(); }}
  }});

  tokenBtn.addEventListener('click', function () {{ jobRadarGetToken(true); }});

  function b64EncodeUtf8(str) {{
    return btoa(unescape(encodeURIComponent(str)));
  }}

  saveBtn.addEventListener('click', function () {{
    var token = jobRadarGetToken(false);
    if (!token) {{ statusEl.textContent = 'Токен не задано.'; return; }}

    saveBtn.disabled = true;
    statusEl.textContent = 'Зберігаю…';

    var apiUrl = 'https://api.github.com/repos/' + OWNER + '/' + REPO + '/contents/' + PATH;

    fetch(apiUrl, {{headers: {{Authorization: 'token ' + token, Accept: 'application/vnd.github+json'}}}})
      .then(function (r) {{
        if (!r.ok) throw new Error('Не вдалось прочитати поточний файл (' + r.status + ')');
        return r.json();
      }})
      .then(function (fileInfo) {{
        var content = b64EncodeUtf8(JSON.stringify(current, null, 2));
        return fetch(apiUrl, {{
          method: 'PUT',
          headers: {{
            Authorization: 'token ' + token,
            Accept: 'application/vnd.github+json',
            'Content-Type': 'application/json',
          }},
          body: JSON.stringify({{
            message: 'chore: update keywords via site UI',
            content: content,
            sha: fileInfo.sha,
            branch: BRANCH,
          }}),
        }});
      }})
      .then(function (r) {{
        if (!r.ok) return r.json().then(function (e) {{ throw new Error(e.message || ('HTTP ' + r.status)); }});
        original = current.slice();
        statusEl.textContent = 'Збережено ✓ Діятиме з наступного запуску.';
        render();
      }})
      .catch(function (err) {{
        statusEl.textContent = 'Помилка: ' + err.message;
      }})
      .finally(function () {{
        saveBtn.disabled = !isDirty();
      }});
  }});

  render();
}})();
</script>
</body>
</html>"""


def write_site(html: str) -> None:
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(SITE_DIR, "robots.txt"), "w", encoding="utf-8") as f:
        f.write("User-agent: *\nDisallow: /\n")
