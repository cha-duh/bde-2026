#!/usr/bin/env python3
"""
Disc Golf League Score Processor (Rating-Based)
=================================================
Accepts a CSV of weekly round ratings (0–300 scale, higher = better),
computes performance vs. personal baseline, awards points, and outputs
a JSON payload ready for Claude to push to Notion via MCP.

Baselines are sourced directly from Notion (Current Baseline Rating +
Rounds Played per player) and passed in as inline JSON via --baselines-json.
No local baselines file is required — Notion is the source of truth.

INPUT CSV FORMAT:
  player,rating
  Alice,224
  Bob,198
  Charlie,251

  - Header row required.
  - `rating` is the 0–300 round rating from the scorekeeping app.
    The app already normalises for course difficulty in most cases.
  - Side game winners are provided via CLI args, not the CSV.

BASELINES JSON FORMAT (built by Claude from Notion data, passed via --baselines-json):
  {
    "Alice": {"baseline": 221.4, "rounds_played": 7},
    "Bob":   {"baseline": 198.0, "rounds_played": 3}
  }
  Players not present in this dict are treated as new (round 1).

OPTIONAL GROUP ADJUSTMENT:
  Use --group-adjust (auto) or --group-shift N (manual) when the app's
  rating normalisation is suspected to be off — e.g. a new course with
  poor calibration data. This shifts all ratings by a fixed amount before
  comparing to individual baselines, and is recorded on the Round record.
  Raw app ratings are always preserved in the output.

USAGE:
  # Standard round — pass baselines as inline JSON (built from Notion data)
  python disc_golf_scorer.py scores.csv \
    --round-name "Week 7 - Maple Hill" \
    --round-date 2026-03-16 \
    --course "Maple Hill" \
    --ctp "Alice, Bob" \
    --long-putt "Charlie, Diana" \
    --baselines-json '{"Alice":{"baseline":221.4,"rounds_played":7},"Bob":{"baseline":198.0,"rounds_played":7}}'

  # With auto group adjustment
  python disc_golf_scorer.py scores.csv ... \
    --group-adjust \
    --adjustment-reason "New course, app calibration data sparse"

  # With manual shift (e.g. bump everyone up 12 points)
  python disc_golf_scorer.py scores.csv ... \
    --group-shift 12 \
    --adjustment-reason "Temporary layout, SSA underestimated"

OUTPUT JSON:
  {
    "round": {
      "name": ..., "date": ..., "course": ...,
      "players_present": N,
      "ctp_winners": "...", "long_putt_winners": "...",
      "rating_adjustment": 0.0,        # 0 if no adjustment applied
      "adjustment_reason": ""          # empty if no adjustment
    },
    "scores": [
      {
        "player": "Alice",
        "raw_rating": 224.0,           # always the app's original value
        "adjusted_rating": 236.0,      # = raw_rating + shift (same as raw if no adjustment)
        "player_baseline": 221.4,
        "rating_vs_baseline": 14.6,    # based on adjusted_rating
        "performance_points": 4,
        "attendance_points": 1,
        "ctp_points": 1,
        "long_putt_points": 0,
        "total_points": 7,
        "new_baseline": 224.5          # baseline always updated from RAW rating
      },
      ...
    ]
  }
"""

import csv
import json
import sys
import argparse
from datetime import date


# ── Points Table ─────────────────────────────────────────────────────────────
ATTENDANCE_POINTS = 1

def performance_points(diff_vs_baseline: float) -> int:
    """
    Convert (adjusted_rating − personal_baseline) to performance points (0–6).

    Symmetric/mirrored around 0 — positive and negative bands are reciprocal.
    Calibrated for a 0–300 scale where ±10–20 pts is a typical weekly swing.

      +15 or better  → 6 pts
      +5  to +14     → 4 pts
       0  to  +4     → 3 pts
      -4  to  -0.1  → 2 pts
      -14 to  -5    → 1 pt
      -15 or worse   → 0 pts
    """
    if diff_vs_baseline >= 15:
        return 6
    elif diff_vs_baseline >= 5:
        return 4
    elif diff_vs_baseline >= 0:
        return 3
    elif diff_vs_baseline >= -4:
        return 2
    elif diff_vs_baseline >= -14:
        return 1
    else:
        return 0


def compute_baseline_update(current_baseline: float | None,
                             rounds_played: int,
                             new_rating: float) -> float:
    """
    Rolling average of raw round ratings (higher = better).
    Baselines are always updated from the RAW app rating, not the adjusted
    one — the adjustment is a one-off correction, not a shift in true ability.

    - Round 1:    baseline IS the rating
    - Rounds 2–5: simple running average
    - Round 6+:   EMA (alpha=0.3) for recency weighting
    """
    if current_baseline is None or rounds_played == 0:
        return round(new_rating, 2)
    if rounds_played < 5:
        old_sum = current_baseline * rounds_played
        return round((old_sum + new_rating) / (rounds_played + 1), 2)
    else:
        alpha = 0.3
        return round(alpha * new_rating + (1 - alpha) * current_baseline, 2)


def compute_group_shift(players: list[dict], baselines: dict,
                         baselines_lower: dict) -> float:
    """
    Auto-compute the group shift from established baselines.

    shift = mean(baselines of players with ≥1 prior round)
            − mean(raw ratings of those same players this week)

    A positive shift means the group underperformed vs. their baselines
    (ratings are bumped up). New players (no prior baseline) don't
    influence the calculation but still receive the shift.

    Returns 0.0 if no players have an established baseline.
    """
    paired = []
    for p in players:
        canonical = baselines_lower.get(p['name'].lower(), p['name'])
        data = baselines.get(canonical, {})
        prior_baseline = data.get('baseline')
        if prior_baseline is not None and data.get('rounds_played', 0) > 0:
            paired.append((p['rating'], prior_baseline))

    if not paired:
        return 0.0

    mean_rating   = sum(r for r, _ in paired) / len(paired)
    mean_baseline = sum(b for _, b in paired) / len(paired)
    return round(mean_baseline - mean_rating, 2)


def parse_scores_csv(filepath: str) -> list[dict]:
    """
    Parse a simple player,rating CSV.
    Returns: [{"name": str, "rating": float}, ...]
    """
    players = []
    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('player', '').strip()
            if not name or name.lower().startswith('side_games'):
                continue
            rating_raw = row.get('rating', '').strip()
            try:
                rating = float(rating_raw)
            except ValueError:
                print(f'WARNING: Could not parse rating for {name} '
                      f'("{rating_raw}") — skipping row.', file=sys.stderr)
                continue
            players.append({'name': name, 'rating': rating})
    return players


def process_round(players: list[dict],
                  baselines: dict,
                  round_name: str,
                  round_date: str,
                  course: str,
                  ctp_winner: str | None,
                  long_putt_winner: str | None,
                  group_shift: float = 0.0,
                  adjustment_reason: str = '') -> dict:
    """
    Core computation for one round.

    group_shift > 0  → ratings bumped up   (group underperformed vs. baselines)
    group_shift < 0  → ratings bumped down (group overperformed vs. baselines)
    group_shift = 0  → standard round, no adjustment

    Points are calculated on adjusted_rating.
    Baselines are always updated from raw_rating.
    """
    baselines_lower = {k.lower(): k for k in baselines}

    results = []

    for p in players:
        name = p['name']
        raw_rating = p['rating']
        adjusted_rating = round(raw_rating + group_shift, 2)

        canonical = baselines_lower.get(name.lower(), name)
        player_data = baselines.get(canonical, {'baseline': None, 'rounds_played': 0})
        old_baseline = player_data.get('baseline')
        rounds_played = player_data.get('rounds_played', 0)

        # First-round players: when a group shift is applied, seed their effective
        # baseline from adjusted_rating so the shift doesn't inflate vs-baseline.
        # In a no-shift round adjusted_rating == raw_rating, so behaviour is identical.
        is_new_player = old_baseline is None
        eff_baseline = old_baseline if not is_new_player else adjusted_rating
        diff_vs_baseline = round(adjusted_rating - eff_baseline, 2)

        perf_pts = performance_points(diff_vs_baseline)
        att_pts  = ATTENDANCE_POINTS

        ctp_names = [w.strip().lower() for w in ctp_winner.split(',')] if ctp_winner else []
        lp_names  = [w.strip().lower() for w in long_putt_winner.split(',')] if long_putt_winner else []
        ctp_pts = ctp_names.count(name.strip().lower())
        lp_pts  = lp_names.count(name.strip().lower())

        total_pts = att_pts + perf_pts + ctp_pts + lp_pts

        # Existing players: baseline updated from RAW rating (shift is a one-off correction).
        # New players in an adjusted round: seed from ADJUSTED rating so their baseline
        # reflects conditions-corrected ability, not the raw difficult-day score.
        seed_rating = adjusted_rating if is_new_player else raw_rating
        new_baseline = compute_baseline_update(old_baseline, rounds_played, seed_rating)

        results.append({
            'player': name,
            'raw_rating': raw_rating,
            'adjusted_rating': adjusted_rating,
            'player_baseline': round(eff_baseline, 2),
            'rating_vs_baseline': diff_vs_baseline,
            'performance_points': perf_pts,
            'attendance_points': att_pts,
            'ctp_points': ctp_pts,
            'long_putt_points': lp_pts,
            'total_points': total_pts,
            'old_baseline': old_baseline,
            'new_baseline': new_baseline,
            'rounds_played_before': rounds_played
        })

    results.sort(key=lambda r: r['total_points'], reverse=True)

    return {
        'round': {
            'name': round_name,
            'date': round_date,
            'course': course,
            'players_present': len(players),
            'ctp_winners': ctp_winner or '',
            'long_putt_winners': long_putt_winner or '',
            'rating_adjustment': group_shift,
            'adjustment_reason': adjustment_reason
        },
        'scores': results
    }


def main():
    parser = argparse.ArgumentParser(
        description='Disc Golf League Score Processor (rating-based)')
    parser.add_argument('csv_file', help='Path to scores CSV (player,rating)')
    parser.add_argument('--round-name', default=f'Round {date.today()}')
    parser.add_argument('--round-date', default=str(date.today()))
    parser.add_argument('--course', default='')
    parser.add_argument('--ctp', default=None,
                        help='CTP winners, comma-separated (up to 2)')
    parser.add_argument('--long-putt', default=None,
                        help='Long Putt winners, comma-separated (up to 2)')
    parser.add_argument('--baselines-json', default=None,
                        help='Inline JSON of player baselines from Notion: '
                             '\'{"Alice":{"baseline":221.4,"rounds_played":7},...}\'')

    # Group adjustment (mutually exclusive)
    adj = parser.add_mutually_exclusive_group()
    adj.add_argument('--group-adjust', action='store_true',
                     help='Auto-compute group shift from established baselines')
    adj.add_argument('--group-shift', type=float, default=None,
                     help='Manually specify rating shift for all players (e.g. 12)')

    parser.add_argument('--adjustment-reason', default='',
                        help='Required when using --group-adjust or --group-shift; '
                             'stored on the Round record in Notion')
    args = parser.parse_args()

    # Validate: reason required when adjustment is requested
    if (args.group_adjust or args.group_shift is not None) and not args.adjustment_reason:
        parser.error('--adjustment-reason is required when using --group-adjust or --group-shift')

    players = parse_scores_csv(args.csv_file)
    if not players:
        print('ERROR: No player rows found in CSV.', file=sys.stderr)
        sys.exit(1)

    # Load baselines from inline JSON (built from Notion) or start empty
    if args.baselines_json:
        try:
            baselines = json.loads(args.baselines_json)
        except json.JSONDecodeError as e:
            print(f'ERROR: Could not parse --baselines-json: {e}', file=sys.stderr)
            sys.exit(1)
    else:
        baselines = {}

    # Resolve the shift to apply
    if args.group_adjust:
        baselines_lower = {k.lower(): k for k in baselines}
        group_shift = compute_group_shift(players, baselines, baselines_lower)
        print(f'Auto group shift: {group_shift:+.2f} pts '
              f'(based on {sum(1 for p in players if baselines_lower.get(p["name"].lower()) in baselines)} '
              f'established baselines)', file=sys.stderr)
    elif args.group_shift is not None:
        group_shift = args.group_shift
        print(f'Manual group shift: {group_shift:+.2f} pts', file=sys.stderr)
    else:
        group_shift = 0.0

    output = process_round(
        players=players,
        baselines=baselines,
        round_name=args.round_name,
        round_date=args.round_date,
        course=args.course,
        ctp_winner=args.ctp,
        long_putt_winner=args.long_putt,
        group_shift=group_shift,
        adjustment_reason=args.adjustment_reason
    )

    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
