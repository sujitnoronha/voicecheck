"""Generate a self-contained HTML dashboard from stored results.

Produces a single HTML file with:
- Scenario overview cards (pass rate, avg latency, run count)
- Latency trend chart per scenario (Chart.js)
- Pass/fail history timeline
- Expandable run details with conversation transcripts
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Any

from voicecheck.storage.store import ResultStore

logger = logging.getLogger("voicecheck.storage.dashboard")


def generate_dashboard(
    store: ResultStore,
    output_path: str | Path,
    scenario_filter: str | None = None,
    limit: int = 100,
) -> Path:
    """Generate an HTML dashboard from stored results.

    Args:
        store: ResultStore instance.
        output_path: Where to write the HTML file.
        scenario_filter: Only show this scenario (None = all).
        limit: Max runs per scenario to include.

    Returns:
        Path to the generated HTML file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scenarios = store.get_scenarios()
    if scenario_filter:
        scenarios = [s for s in scenarios if s["scenario_name"] == scenario_filter]

    # Gather data for each scenario
    scenario_data: list[dict[str, Any]] = []
    for s in scenarios:
        history = store.get_scenario_history(s["scenario_name"], limit=limit)
        # Get the latest run with full details
        latest_run = None
        if history:
            latest_run = store.get_run(history[0]["id"])

        scenario_data.append({
            "name": s["scenario_name"],
            "run_count": s["run_count"],
            "pass_count": s["pass_count"],
            "pass_rate": (s["pass_count"] / s["run_count"] * 100) if s["run_count"] else 0,
            "avg_latency": s["avg_latency"] or 0,
            "last_run": s["last_run"],
            "history": history,
            "latest_run": latest_run,
        })

    html_content = _render_html(scenario_data)
    output_path.write_text(html_content)
    logger.info("Dashboard written to %s", output_path)
    return output_path


def _render_html(scenario_data: list[dict[str, Any]]) -> str:
    """Render the full HTML dashboard."""
    cards_html = "\n".join(_render_scenario_card(s) for s in scenario_data)
    charts_js = _render_charts_js(scenario_data)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VoiceCheck Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e4e4e7; --text-muted: #8b8d98; --accent: #6366f1;
    --green: #22c55e; --red: #ef4444; --yellow: #eab308;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); padding: 2rem; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
  .subtitle {{ color: var(--text-muted); margin-bottom: 2rem; font-size: 0.9rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1.5rem; margin-bottom: 2rem; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; }}
  .card h2 {{ font-size: 1.1rem; margin-bottom: 1rem; }}
  .stats {{ display: flex; gap: 1.5rem; margin-bottom: 1rem; }}
  .stat {{ text-align: center; }}
  .stat-value {{ font-size: 1.8rem; font-weight: 700; }}
  .stat-label {{ font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }}
  .pass {{ color: var(--green); }} .fail {{ color: var(--red); }} .warn {{ color: var(--yellow); }}
  .chart-container {{ position: relative; height: 200px; margin: 1rem 0; }}
  .timeline {{ display: flex; gap: 3px; flex-wrap: wrap; margin: 0.5rem 0; }}
  .timeline-dot {{ width: 18px; height: 18px; border-radius: 4px; cursor: pointer; transition: transform 0.1s; }}
  .timeline-dot:hover {{ transform: scale(1.3); }}
  .dot-pass {{ background: var(--green); }} .dot-fail {{ background: var(--red); }}
  .transcript {{ margin-top: 1rem; }}
  .turn {{ padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.85rem; }}
  .turn:last-child {{ border-bottom: none; }}
  .turn-user {{ color: var(--accent); }} .turn-agent {{ color: var(--text); }}
  .turn-meta {{ color: var(--text-muted); font-size: 0.75rem; margin-top: 0.25rem; }}
  .eval-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem;
                 font-weight: 600; margin-right: 4px; }}
  .eval-pass {{ background: rgba(34,197,94,0.15); color: var(--green); }}
  .eval-fail {{ background: rgba(239,68,68,0.15); color: var(--red); }}
  details > summary {{ cursor: pointer; color: var(--accent); font-size: 0.85rem; margin-top: 0.5rem; }}
  .empty {{ text-align: center; color: var(--text-muted); padding: 3rem; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; }}
  .header-right {{ color: var(--text-muted); font-size: 0.8rem; }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>VoiceCheck Dashboard</h1>
    <p class="subtitle">Voice agent E2E test results</p>
  </div>
  <div class="header-right">
    Generated {_now_str()}
  </div>
</div>

{"<p class='empty'>No test results yet. Run <code>voicecheck run</code> to generate data.</p>" if not scenario_data else ""}

<div class="grid">
{cards_html}
</div>

<script>
{charts_js}
</script>
</body>
</html>"""


def _render_scenario_card(s: dict[str, Any]) -> str:
    """Render a single scenario card with stats, timeline, chart, and transcript."""
    name = html.escape(s["name"])
    pass_rate = s["pass_rate"]
    rate_class = "pass" if pass_rate >= 80 else "warn" if pass_rate >= 50 else "fail"
    avg_lat = s["avg_latency"]
    lat_class = "pass" if avg_lat < 2000 else "warn" if avg_lat < 4000 else "fail"

    # Timeline dots (most recent first, reversed for left-to-right = old-to-new)
    dots = ""
    for run in reversed(s["history"][:50]):
        cls = "dot-pass" if run["passed"] else "dot-fail"
        date = run["created_at"][:10]
        dots += f'<div class="timeline-dot {cls}" title="{date}"></div>'

    # Latest run transcript
    transcript = ""
    if s["latest_run"] and s["latest_run"].get("turns"):
        turns_html = ""
        for t in s["latest_run"]["turns"]:
            evals_html = ""
            for ev in t.get("evaluations", []):
                badge_cls = "eval-pass" if ev["passed"] else "eval-fail"
                evals_html += f'<span class="eval-badge {badge_cls}">{html.escape(ev["type"])}: {ev["score"]:.1f}</span>'

            turns_html += f"""<div class="turn">
  <div class="turn-user">User: {html.escape(t["user_text"])}</div>
  <div class="turn-agent">Agent: {html.escape(t.get("agent_text", "") or "(no response)")}</div>
  <div class="turn-meta">{evals_html} | {t.get("first_byte_ms", 0):.0f}ms first byte</div>
</div>"""
        transcript = f"""<details>
  <summary>Latest conversation ({len(s["latest_run"]["turns"])} turns)</summary>
  <div class="transcript">{turns_html}</div>
</details>"""

    chart_id = _safe_chart_id(s["name"])

    return f"""<div class="card">
  <h2>{name}</h2>
  <div class="stats">
    <div class="stat">
      <div class="stat-value {rate_class}">{pass_rate:.0f}%</div>
      <div class="stat-label">Pass Rate</div>
    </div>
    <div class="stat">
      <div class="stat-value {lat_class}">{avg_lat:.0f}<span style="font-size:0.7em">ms</span></div>
      <div class="stat-label">Avg Latency</div>
    </div>
    <div class="stat">
      <div class="stat-value">{s["run_count"]}</div>
      <div class="stat-label">Runs</div>
    </div>
  </div>
  <div class="timeline">{dots}</div>
  <div class="chart-container"><canvas id="chart_{chart_id}"></canvas></div>
  {transcript}
</div>"""


def _render_charts_js(scenario_data: list[dict[str, Any]]) -> str:
    """Generate Chart.js initialization code for all scenario latency charts."""
    snippets: list[str] = []
    for s in scenario_data:
        chart_id = _safe_chart_id(s["name"])
        # Chronological order (oldest first)
        history = list(reversed(s["history"][:50]))
        labels = [h["created_at"][:16].replace("T", " ") for h in history]
        latencies = [h.get("avg_first_byte_ms", 0) or 0 for h in history]
        pass_rates = [
            (h["passed_turns"] / h["total_turns"] * 100) if h["total_turns"] else 0
            for h in history
        ]

        snippets.append(f"""
new Chart(document.getElementById('chart_{chart_id}'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(labels)},
    datasets: [
      {{
        label: 'First Byte (ms)',
        data: {json.dumps(latencies)},
        borderColor: '#6366f1',
        backgroundColor: 'rgba(99,102,241,0.1)',
        fill: true,
        tension: 0.3,
        yAxisID: 'y',
      }},
      {{
        label: 'Pass Rate (%)',
        data: {json.dumps(pass_rates)},
        borderColor: '#22c55e',
        borderDash: [5, 5],
        tension: 0.3,
        yAxisID: 'y1',
      }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: '#8b8d98', font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ display: false }},
      y: {{
        position: 'left',
        title: {{ display: true, text: 'Latency (ms)', color: '#8b8d98' }},
        grid: {{ color: 'rgba(255,255,255,0.05)' }},
        ticks: {{ color: '#8b8d98' }}
      }},
      y1: {{
        position: 'right',
        min: 0, max: 100,
        title: {{ display: true, text: 'Pass %', color: '#8b8d98' }},
        grid: {{ drawOnChartArea: false }},
        ticks: {{ color: '#8b8d98' }}
      }}
    }}
  }}
}});""")

    return "\n".join(snippets)


def _safe_chart_id(name: str) -> str:
    """Sanitize a scenario name into a safe JS identifier for Chart.js canvas IDs."""
    return re.sub(r"[^a-zA-Z0-9_]", "", name.replace(" ", "_").replace("-", "_"))


def _now_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
