#!/usr/bin/env python3
"""
Disc Golf League Leaderboard Generator
=======================================
Takes JSON exported from Notion (via Claude) and produces a
shareable standalone HTML leaderboard with:
  - "This Week" summary card (most recent round, sorted by weekly pts)
  - Season standings table with click-to-expand per-player round history

USAGE:
  python generate_leaderboard.py leaderboard_data.json \
    --output leaderboard.html \
    --season-label "Spring 2026"

leaderboard_data.json schema:
{
  "season_label": "Spring 2026",
  "generated": "2026-03-18",
  "players": [
    {
      "name": "Alice",
      "season_points": 47,
      "rounds_played": 7,
      "current_baseline": 221.4,
      "rounds": [
        {
          "label": "Week 1 - Maple Hill",
          "date": "2026-01-12",
          "raw_rating": 224,
          "adjusted_rating": 224,
          "player_baseline": 224.0,
          "rating_vs_baseline": 0.0,
          "performance_points": 3,
          "attendance_points": 2,
          "ctp_points": 0,
          "long_putt_points": 0,
          "total_points": 5
        },
        ...
      ]
    }
  ]
}

Backward compat: if a player has no "rounds" array but has "recent_rounds"
(list of ints), the standings table still renders with sparklines only —
the This Week card and expandable rows are skipped for that player.
"""

import html as html_lib
import json
import sys
import argparse
from datetime import date
from pathlib import Path


MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list, max_val: int = 10) -> str:
    if not values:
        return "—"
    chars = []
    for v in values:
        idx = int((v / max_val) * (len(SPARK_CHARS) - 1))
        idx = max(0, min(idx, len(SPARK_CHARS) - 1))
        chars.append(SPARK_CHARS[idx])
    return ''.join(chars)


def trend_arrow(recent: list) -> str:
    if len(recent) < 4:
        return ""
    last3 = sum(recent[-3:]) / 3
    prev3 = sum(recent[-6:-3]) / 3 if len(recent) >= 6 else sum(recent[:-3]) / max(len(recent) - 3, 1)
    if last3 > prev3 + 0.5:
        return '<span class="trend up">▲</span>'
    elif last3 < prev3 - 0.5:
        return '<span class="trend down">▼</span>'
    return '<span class="trend flat">→</span>'


def baseline_badge(baseline: float | None) -> str:
    if baseline is None:
        return '<span class="badge gray">—</span>'
    cls = "green" if baseline >= 200 else ("gray" if baseline >= 150 else "red")
    return f'<span class="badge {cls}">{baseline:.1f}</span>'


def vs_baseline_cell(diff: float) -> str:
    if diff > 0:
        return f'<span class="pos">+{diff:.1f}</span>'
    elif diff < 0:
        return f'<span class="neg">{diff:.1f}</span>'
    return f'<span class="neu">{diff:.1f}</span>'


def render_html(data: dict, season_label: str) -> str:
    players = data.get("players", [])
    generated = data.get("generated", str(date.today()))
    label = data.get("season_label", season_label)

    players = sorted(players, key=lambda p: p.get("season_points", 0), reverse=True)

    # ── This Week card ────────────────────────────────────────────────────────
    # Use the most recent round from whoever has rounds data
    this_week_label = ""
    this_week_rows = []

    players_with_rounds = [p for p in players if p.get("rounds")]
    if players_with_rounds:
        # Find the true most recent round date across all players,
        # then only include players who actually attended that round.
        latest_date = max(p["rounds"][-1].get("date", "") for p in players_with_rounds)
        attendees = [p for p in players_with_rounds if p["rounds"][-1].get("date", "") == latest_date]

        latest_round = attendees[0]["rounds"][-1]
        this_week_label = latest_round.get("label", "This Week")

        week_entries = []
        for p in attendees:
            r = p["rounds"][-1]
            week_entries.append({
                "name": p["name"],
                "raw_rating": r.get("raw_rating", "—"),
                "adjusted_rating": r.get("adjusted_rating"),
                "player_baseline": r.get("player_baseline"),
                "rating_vs_baseline": r.get("rating_vs_baseline"),
                "performance_points": r.get("performance_points", 0),
                "attendance_points": r.get("attendance_points", 2),
                "ctp_points": r.get("ctp_points", 0),
                "long_putt_points": r.get("long_putt_points", 0),
                "total_points": r.get("total_points", 0),
            })
        week_entries.sort(key=lambda x: x["total_points"], reverse=True)

        for e in week_entries:
            diff = e["rating_vs_baseline"]
            adj = e["adjusted_rating"]
            raw = e["raw_rating"]
            rating_str = str(int(raw)) if raw != adj else str(int(raw))
            if adj is not None and adj != raw:
                rating_str = f'{int(raw)} <span class="adj">(adj {adj:.0f})</span>'

            ctp_lp = ""
            if e["ctp_points"]:
                ctp_lp += f'<span class="sidegame">CTP</span> ' * e["ctp_points"]
            if e["long_putt_points"]:
                ctp_lp += f'<span class="sidegame">LP</span> ' * e["long_putt_points"]

            diff_html = vs_baseline_cell(diff) if diff is not None else "—"
            this_week_rows.append(f"""
        <tr>
          <td class="name">{e['name']}</td>
          <td class="rating">{rating_str}</td>
          <td class="vsb">{diff_html}</td>
          <td class="bkpts">{e['performance_points']}</td>
          <td class="bkpts muted">2</td>
          <td class="sidegames">{ctp_lp}</td>
          <td class="wkpts">{e['total_points']}</td>
        </tr>""")

    this_week_html = ""
    if this_week_rows:
        rows_joined = "\n".join(this_week_rows)
        this_week_html = f"""
  <section class="this-week">
    <h2 class="section-title">This Week <span class="wk-label">{this_week_label}</span></h2>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>Player</th>
            <th>Rating</th>
            <th>vs Baseline</th>
            <th>Perf</th>
            <th>Att</th>
            <th>Side Games</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>{rows_joined}
        </tbody>
      </table>
    </div>
  </section>"""

    # ── Season standings ──────────────────────────────────────────────────────
    standings_rows = ""
    for i, p in enumerate(players):
        rank = i + 1
        medal = MEDAL.get(rank, f"#{rank}")
        name = p.get("name", "Unknown")
        pts = p.get("season_points", 0)
        rounds_list = p.get("rounds", [])
        rounds_count = p.get("rounds_played", len(rounds_list))
        ppg = round(pts / rounds_count, 1) if rounds_count > 0 else 0
        baseline = p.get("current_baseline")

        # Sparkline from rounds array or fallback recent_rounds
        point_series = [r["total_points"] for r in rounds_list] if rounds_list else p.get("recent_rounds", [])
        spark = sparkline(point_series)
        trend = trend_arrow(point_series)
        b_badge = baseline_badge(baseline)

        # Build expand data
        has_history = bool(rounds_list)
        expand_attr = ""
        if has_history:
            expand_attr = f' data-history="{html_lib.escape(json.dumps(rounds_list), quote=True)}" onclick="toggleHistory(this)"'

        row_class = "top3 expandable" if rank <= 3 and has_history else ("top3" if rank <= 3 else ("expandable" if has_history else ""))

        standings_rows += f"""
        <tr class="{row_class}"{expand_attr}>
          <td class="rank">{medal}</td>
          <td class="name">{name}{trend}{'<span class="expand-hint">▾</span>' if has_history else ''}</td>
          <td class="pts">{pts}</td>
          <td class="rounds">{rounds_count}</td>
          <td class="ppg">{ppg}</td>
          <td class="baseline">{b_badge}</td>
          <td class="spark" title="Last {len(point_series)} weeks">{spark}</td>
        </tr>
        <tr class="history-row" id="hist-{i}" style="display:none">
          <td colspan="7">
            <div class="history-inner">
              <table class="history-table">
                <thead>
                  <tr>
                    <th>Week</th>
                    <th>Date</th>
                    <th>Rating</th>
                    <th>vs Baseline</th>
                    <th>Perf</th>
                    <th>Att</th>
                    <th>CTP</th>
                    <th>LP</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody id="hist-body-{i}"></tbody>
              </table>
            </div>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🥏 {label}</title>
<style>
  :root {{
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --surface3: #2a2f45;
    --accent: #5b7cf7;
    --accent2: #7ee8a2;
    --text: #e8eaf6;
    --muted: #7b8096;
    --gold: #ffd700;
    --silver: #c0c0c0;
    --bronze: #cd7f32;
    --green: #4caf50;
    --red: #f44336;
    --yellow: #ffb300;
    --radius: 12px;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 24px 16px;
  }}

  .container {{ max-width: 860px; margin: 0 auto; }}

  header {{
    text-align: center;
    margin-bottom: 32px;
  }}

  header h1 {{
    font-size: 2rem;
    font-weight: 800;
    letter-spacing: -0.5px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }}

  header .meta {{
    color: var(--muted);
    font-size: 0.85rem;
    margin-top: 6px;
  }}

  .section-title {{
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--muted);
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 10px;
  }}

  .wk-label {{
    color: var(--accent2);
    font-weight: 600;
    text-transform: none;
    letter-spacing: 0;
    font-size: 0.85rem;
  }}

  section {{ margin-bottom: 28px; }}

  .card {{
    background: var(--surface);
    border-radius: var(--radius);
    overflow: hidden;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
  }}

  table {{ width: 100%; border-collapse: collapse; }}

  thead th {{
    background: var(--surface2);
    padding: 10px 14px;
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    text-align: left;
    white-space: nowrap;
  }}

  tbody tr {{
    border-bottom: 1px solid rgba(255,255,255,0.04);
    transition: background 0.12s;
  }}

  tbody tr:last-child {{ border-bottom: none; }}
  tbody tr:hover {{ background: rgba(91,124,247,0.07); }}
  tbody tr.top3 {{ background: rgba(91,124,247,0.06); }}

  tbody td {{
    padding: 13px 14px;
    font-size: 0.88rem;
    vertical-align: middle;
  }}

  /* This Week table */
  .this-week td.name {{ font-weight: 600; min-width: 90px; }}
  .this-week td.rating {{ font-variant-numeric: tabular-nums; }}
  .this-week td.bkpts {{ color: var(--muted); text-align: center; }}
  .this-week td.wkpts {{ font-size: 1.05rem; font-weight: 700; color: var(--accent2); text-align: center; }}
  .this-week td.vsb {{ font-variant-numeric: tabular-nums; }}
  .this-week td.sidegames {{ font-size: 0.8rem; }}
  .this-week thead th:nth-child(4),
  .this-week thead th:nth-child(5) {{ text-align: center; }}
  .adj {{ color: var(--muted); font-size: 0.8rem; }}

  /* Standings table */
  td.rank {{ font-size: 1.15rem; width: 48px; }}
  td.name {{ font-weight: 600; }}
  td.pts {{ font-size: 1.05rem; font-weight: 700; color: var(--accent2); }}
  td.ppg {{ color: var(--muted); font-size: 0.83rem; }}
  td.spark {{
    font-family: monospace;
    letter-spacing: 1px;
    font-size: 1rem;
    color: var(--accent);
  }}

  .expand-hint {{
    color: var(--muted);
    font-size: 0.75rem;
    margin-left: 5px;
    transition: transform 0.2s;
    display: inline-block;
  }}

  tr.expandable {{ cursor: pointer; user-select: none; }}
  tr.expandable.open .expand-hint {{ transform: rotate(180deg); }}

  /* History rows */
  tr.history-row td {{ padding: 0; background: var(--bg); }}

  .history-inner {{
    padding: 12px 16px 16px 48px;
    background: rgba(15,17,23,0.6);
    border-top: 1px solid rgba(91,124,247,0.15);
  }}

  .history-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }}

  .history-table thead th {{
    background: transparent;
    padding: 6px 10px;
    color: var(--muted);
    font-size: 0.68rem;
    border-bottom: 1px solid rgba(255,255,255,0.06);
  }}

  .history-table tbody td {{
    padding: 7px 10px;
    border-bottom: 1px solid rgba(255,255,255,0.03);
    color: var(--text);
    font-size: 0.82rem;
  }}

  .history-table tbody tr:last-child td {{ border-bottom: none; }}
  .history-table td.h-total {{ font-weight: 700; color: var(--accent2); }}
  .history-table td.h-week {{ color: var(--muted); font-size: 0.78rem; }}

  /* Badges */
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 20px;
    font-size: 0.76rem;
    font-weight: 600;
  }}
  .badge.green {{ background: rgba(76,175,80,0.2);   color: #4caf50; }}
  .badge.red   {{ background: rgba(244,67,54,0.2);   color: #f44336; }}
  .badge.gray  {{ background: rgba(123,128,150,0.2); color: var(--muted); }}

  .sidegame {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 700;
    background: rgba(255,179,0,0.15);
    color: var(--yellow);
    margin-right: 3px;
  }}

  .pos {{ color: #4caf50; font-weight: 600; }}
  .neg {{ color: #f44336; font-weight: 600; }}
  .neu {{ color: var(--muted); }}

  .trend {{ font-size: 0.72rem; margin-left: 4px; }}
  .trend.up   {{ color: #4caf50; }}
  .trend.down {{ color: #f44336; }}
  .trend.flat {{ color: var(--muted); }}

  /* Scoring key */
  .scoring-key {{
    background: var(--surface);
    border-radius: var(--radius);
    padding: 16px 20px;
  }}

  .scoring-key h3 {{
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
    margin-bottom: 10px;
  }}

  .key-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 5px;
    font-size: 0.8rem;
    color: var(--muted);
  }}

  .key-item {{ display: flex; justify-content: space-between; gap: 8px; }}
  .key-item .kpts {{ color: var(--accent2); font-weight: 600; white-space: nowrap; }}

  .key-note {{
    color: var(--muted);
    font-size: 0.76rem;
    margin-top: 10px;
    line-height: 1.5;
  }}

  footer {{
    text-align: center;
    color: var(--muted);
    font-size: 0.73rem;
    margin-top: 20px;
  }}

  @media (max-width: 600px) {{
    .history-inner {{ padding-left: 12px; }}
    tbody td {{ padding: 10px 10px; }}
  }}
</style>
</head>
<body>
<div class="container">

  <header>
    <h1>🥏 {label}</h1>
    <div class="meta">Updated {generated} · Max 11 pts/week · Click a player to expand round history</div>
  </header>

  {this_week_html}

  <section>
    <h2 class="section-title">Season Standings</h2>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th></th>
            <th>Player</th>
            <th>Season Pts</th>
            <th>Rounds</th>
            <th>Pts/Rd</th>
            <th>Baseline</th>
            <th>Recent ▸</th>
          </tr>
        </thead>
        <tbody>
          {standings_rows}
        </tbody>
      </table>
    </div>
  </section>

  <div class="scoring-key">
    <h3>Scoring Key</h3>
    <div class="key-grid">
      <div class="key-item"><span>Attendance</span><span class="kpts">1 pt</span></div>
      <div class="key-item"><span>Rating ≥ +15 vs baseline</span><span class="kpts">6 pts</span></div>
      <div class="key-item"><span>Rating +5 to +14</span><span class="kpts">4 pts</span></div>
      <div class="key-item"><span>Rating 0 to +4</span><span class="kpts">3 pts</span></div>
      <div class="key-item"><span>Rating −0.1 to −4</span><span class="kpts">2 pts</span></div>
      <div class="key-item"><span>Rating −5 to −14</span><span class="kpts">1 pt</span></div>
      <div class="key-item"><span>Rating ≤ −15</span><span class="kpts">0 pts</span></div>
      <div class="key-item"><span>CTP win (up to 2)</span><span class="kpts">+1 pt each</span></div>
      <div class="key-item"><span>Long Putt win (up to 2)</span><span class="kpts">+1 pt each</span></div>
    </div>
    <p class="key-note">Baseline = rolling avg of your app round ratings (0–300 scale).
    Performance pts compare your rating to your own baseline — a 190-rated and 250-rated
    player have equal shots at 6 pts. Max 11 pts/week (1 att + 6 perf + 2 CTP + 2 LP).</p>
  </div>

  <footer>🥏 Disc Golf League · Generated by Claude</footer>

</div>

<script>
  function toggleHistory(row) {{
    const idx = row.getAttribute('data-history') ? [...row.parentElement.children].indexOf(row) : -1;
    if (idx < 0) return;

    // Find the history row (next sibling)
    const histRow = row.nextElementSibling;
    if (!histRow || !histRow.classList.contains('history-row')) return;

    const isOpen = histRow.style.display !== 'none';

    if (isOpen) {{
      histRow.style.display = 'none';
      row.classList.remove('open');
    }} else {{
      // Populate if empty
      const tbody = histRow.querySelector('tbody');
      if (tbody && tbody.children.length === 0) {{
        const rounds = JSON.parse(row.getAttribute('data-history'));
        rounds.forEach(r => {{
          const diff = r.rating_vs_baseline;
          const diffStr = diff > 0
            ? `<span class="pos">+${{diff.toFixed(1)}}</span>`
            : diff < 0
              ? `<span class="neg">${{diff.toFixed(1)}}</span>`
              : `<span class="neu">${{diff.toFixed(1)}}</span>`;
          const adjNote = (r.adjusted_rating && r.adjusted_rating !== r.raw_rating)
            ? ` <span class="adj">(adj ${{r.adjusted_rating.toFixed(0)}})</span>` : '';
          const ctpCell = r.ctp_points ? '<span class="sidegame">CTP</span>'.repeat(r.ctp_points) : '—';
          const lpCell  = r.long_putt_points ? '<span class="sidegame">LP</span>'.repeat(r.long_putt_points) : '—';
          tbody.innerHTML += `<tr>
            <td class="h-week">${{r.label || ''}}</td>
            <td>${{r.date || ''}}</td>
            <td>${{r.raw_rating}}${{adjNote}}</td>
            <td>${{diffStr}}</td>
            <td>${{r.performance_points}}</td>
            <td>${{r.attendance_points}}</td>
            <td>${{ctpCell}}</td>
            <td>${{lpCell}}</td>
            <td class="h-total">${{r.total_points}}</td>
          </tr>`;
        }});
      }}
      histRow.style.display = '';
      row.classList.add('open');
    }}
  }}
</script>

</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description='Generate disc golf leaderboard HTML')
    parser.add_argument('data_file', help='Path to leaderboard JSON data file')
    parser.add_argument('--output', default='leaderboard.html')
    parser.add_argument('--season-label', default='League Standings')
    args = parser.parse_args()

    with open(args.data_file) as f:
        data = json.load(f)

    html = render_html(data, args.season_label)
    out = Path(args.output)
    out.write_text(html, encoding='utf-8')
    print(f'Leaderboard written → {out.resolve()}')


if __name__ == '__main__':
    main()
