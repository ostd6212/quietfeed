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
        <a href="{job['url']}" target="_blank" rel="noopener" class="apply-btn">Переглянути вакансію →</a>
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


def generate_html(all_jobs: list[dict], source_stats: list[dict], generated_at: str) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DISPLAY_DAYS)
    visible = []
    for job in all_jobs:
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

  .progress-panel {{ max-width: 900px; margin: 20px auto 0; padding: 12px 24px; display: none; align-items: center; gap: 12px; }}
  .progress-text {{ font-size: 13px; color: #94a3b8; white-space: nowrap; }}
  .progress-track {{ flex: 1; height: 6px; background: #1e293b; border: 1px solid #334155; border-radius: 4px; overflow: hidden; }}
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
  .description {{ margin-top: 14px; border-top: 1px solid #334155; padding-top: 14px; }}
  .description summary {{ font-size: 13px; color: #64748b; cursor: pointer; user-select: none; }}
  .description summary:hover {{ color: #94a3b8; }}
  .description p {{ margin-top: 10px; font-size: 13px; color: #64748b; line-height: 1.6; white-space: pre-wrap; }}

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

<div class="progress-panel" id="progress-panel">
  <span class="progress-text" id="progress-text"></span>
  <div class="progress-track"><div class="progress-bar-fill" id="progress-bar-fill"></div></div>
</div>

<div class="stats">
  <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">Вакансій (за {DISPLAY_DAYS} дн.)</div></div>
  <div class="stat"><div class="stat-num" style="color:#22c55e">{good}</div><div class="stat-label">Скор 7+</div></div>
  <div class="stat"><div class="stat-num">{len(source_stats)}</div><div class="stat-label">Джерел перевірено</div></div>
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

{_status_panel(source_stats)}

<script type="application/json" id="jobs-data">{jobs_json}</script>
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

(function () {{
  var STALE_MS = 20 * 60 * 1000;
  var panel = document.getElementById('progress-panel');
  var textEl = document.getElementById('progress-text');
  var barEl = document.getElementById('progress-bar-fill');

  function render(status) {{
    if (!status) {{ panel.style.display = 'none'; return; }}
    var age = Date.now() - new Date(status.updated_at).getTime();
    if (status.stage === 'idle' || status.stage === 'error' || age > STALE_MS) {{
      panel.style.display = 'none';
      return;
    }}

    var found = status.found || 0;
    var scored = status.scored || 0;
    var text, pct;
    if (status.stage === 'fetching' || found === 0) {{
      text = 'Пошук нових вакансій…';
      pct = null;
    }} else {{
      pct = Math.round(scored / found * 100);
      text = 'Знайдено ' + found + ', проскоровано ' + scored + ' (' + pct + '%)';
    }}

    panel.style.display = 'flex';
    textEl.textContent = text;
    barEl.style.width = (pct === null ? 100 : pct) + '%';
    barEl.classList.toggle('indeterminate', pct === null);
  }}

  function poll() {{
    fetch('{STATUS_URL}?t=' + Date.now(), {{cache: 'no-store'}})
      .then(function (r) {{ return r.ok ? r.json() : null; }})
      .then(render)
      .catch(function () {{ panel.style.display = 'none'; }});
  }}

  poll();
  setInterval(poll, 15000);
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
