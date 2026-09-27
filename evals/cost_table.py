#!/usr/bin/env python3
"""Cost table: read review-log JSONL and print per-review cost + latency.

Run:  python evals/cost_table.py reviews.jsonl
      python evals/cost_table.py evals/sample_reviews.jsonl   # the 10-row demo log
"""

import json
import sys


def load(path):
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        print(f"no log at {path} — run cli.py --log {path} first", file=sys.stderr)
        raise SystemExit(1)
    rows = []
    with fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        print(f"{path} is empty", file=sys.stderr)
        raise SystemExit(1)
    return rows


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "reviews.jsonl"
    rows = load(path)

    print(f"{'time':<16}{'backend':<9}{'files':>5}{'+/-':>9}"
          f"{'findings':>9}{'lat_ms':>9}{'tok':>8}{'cost_$':>10}")
    for r in rows:
        ts = r.get("timestamp", "")[:16].replace("T", " ")
        sev = r.get("findings_by_severity", {})
        fl = f"{sev.get('critical', 0)}/{sev.get('warning', 0)}/{sev.get('info', 0)}"
        lat = r.get("latency_ms", {})
        print(f"{ts:<16}{r.get('backend', '?'):<9}"
              f"{r.get('files_changed', 0):>5}"
              f"{'+' + str(r.get('added_lines', 0)) + '/-' + str(r.get('removed_lines', 0)):>9}"
              f"{fl:>9}"
              f"{lat.get('total_ms', 0):>9.1f}"
              f"{(r.get('tokens_estimated', {}) or {}).get('input', 0):>8}"
              f"{r.get('cost_usd', 0):>10.6f}")

    n = len(rows)
    total_cost = sum(r.get("cost_usd", 0) for r in rows)
    avg_lat = sum((r.get("latency_ms", {}) or {}).get("total_ms", 0) for r in rows) / n
    # per-backend rollup: where the money/time actually goes
    by_backend: dict[str, dict] = {}
    for r in rows:
        b = r.get("backend", "?")
        d = by_backend.setdefault(b, {"n": 0, "cost": 0.0, "lat": 0.0})
        d["n"] += 1
        d["cost"] += r.get("cost_usd", 0)
        d["lat"] += (r.get("latency_ms", {}) or {}).get("total_ms", 0)

    print(f"\n{n} reviews | total ${total_cost:.6f} | "
          f"avg ${total_cost / n:.6f}/review | avg {avg_lat:.1f} ms/review")
    for b, d in sorted(by_backend.items()):
        print(f"  {b}: {d['n']} reviews, ${d['cost']:.6f} total, "
              f"${d['cost'] / d['n']:.6f}/review, {d['lat'] / d['n']:.1f} ms avg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
