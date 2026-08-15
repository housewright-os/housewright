"""Sunday review: what happened, what leaked, the one decision for the week.

Writes state/weekly.md. Read it together, ten minutes, then get on with the week.

Usage:
    python3 scripts/weekly.py
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(ROOT, "state")
CONFIG = os.path.join(ROOT, "config", "budget.json")


def money(x):
    return f"${x:,.2f}"


def main():
    with open(CONFIG) as f:
        cfg = json.load(f)
    with open(os.path.join(DATA, "raw_transactions.json")) as f:
        txns = json.load(f)["allTransactions"]["results"]
    status = {}
    sp = os.path.join(STATE, "status.json")
    if os.path.exists(sp):
        with open(sp) as f:
            status = json.load(f)

    today = date.today()
    w1 = today - timedelta(days=7)
    w2 = today - timedelta(days=14)
    ignore = set(cfg["ignore_categories"])

    def window(a, b):
        out = []
        for t in txns:
            amt = t.get("amount") or 0
            if amt >= 0:
                continue
            d = datetime.strptime(t["date"], "%Y-%m-%d").date()
            if a <= d < b:
                cat = ((t.get("category") or {}).get("name")) or "Uncategorized"
                if cat in ignore:
                    continue
                mer = ((t.get("merchant") or {}).get("name")) or "?"
                out.append((d, -amt, cat, mer))
        return out

    this_w = window(w1, today + timedelta(days=1))
    last_w = window(w2, w1)

    tw = sum(r[1] for r in this_w)
    lw = sum(r[1] for r in last_w)

    by_cat = defaultdict(float)
    for _, a, c, _m in this_w:
        by_cat[c] += a
    prev_cat = defaultdict(float)
    for _, a, c, _m in last_w:
        prev_cat[c] += a

    by_mer = defaultdict(float)
    cnt = defaultdict(int)
    for _, a, _c, m in this_w:
        by_mer[m] += a
        cnt[m] += 1

    L = []
    L.append(f"# Week of {w1.isoformat()} to {today.isoformat()}")
    L.append("")

    if status:
        icon = {"green": "OK", "amber": "TIGHT", "red": "SHORT"}[status["status"]]
        L.append(f"**Safe to spend right now: {money(status['safe_to_spend'])}**  ({icon})")
        L.append("")
        L.append(
            f"Next payday {status['next_payday']} in {status['days_until_payday']} days, "
            f"about {money(status['expected_paycheck'])}."
        )
        bills = status.get("bills_due_before_payday") or []
        if bills:
            L.append("")
            L.append("Still due before then:")
            for b in bills:
                L.append(f"- {b['due']} {money(b['amount'])} {b['name']}")
        L.append("")

    delta = tw - lw
    arrow = "up" if delta > 0 else "down"
    L.append("## What happened")
    L.append("")
    L.append(
        f"Spent **{money(tw)}** this week, {arrow} {money(abs(delta))} from "
        f"{money(lw)} the week before."
    )
    L.append("")

    L.append("| Category | This week | Last week | Change |")
    L.append("|---|---:|---:|---:|")
    for c in sorted(set(by_cat) | set(prev_cat), key=lambda k: -by_cat[k]):
        a, b = by_cat[c], prev_cat[c]
        L.append(f"| {c} | {money(a)} | {money(b)} | {'+' if a-b>=0 else ''}{money(a-b)} |")
    L.append("")

    L.append("## Where it went")
    L.append("")
    L.append("| Merchant | Total | Times |")
    L.append("|---|---:|---:|")
    for m, v in sorted(by_mer.items(), key=lambda kv: -kv[1])[:12]:
        L.append(f"| {m} | {money(v)} | {cnt[m]} |")
    L.append("")

    # The leak: biggest category increase week over week.
    leaks = sorted(
        ((c, by_cat[c] - prev_cat[c]) for c in by_cat), key=lambda kv: -kv[1]
    )
    L.append("## The one thing to fix")
    L.append("")
    if leaks and leaks[0][1] > 0:
        c, d = leaks[0]
        top = sorted(
            ((m, v) for (_d, v, cc, m) in this_w if cc == c),
            key=lambda kv: -kv[1],
        )
        who = top[0][0] if top else "unclear"
        L.append(
            f"**{c}** rose {money(d)} week over week, and the largest single charge "
            f"in it was {who}. That is this week's leak."
        )
    else:
        L.append("Nothing rose week over week. Hold the line and do it again.")
    L.append("")
    L.append("## Decide together")
    L.append("")
    L.append("- [ ] Is the leak above a one-off or a habit?")
    L.append("- [ ] Anything coming next week over $100 that we should decide on now?")
    L.append("- [ ] Any bill or renewal we can still cancel before it hits?")
    L.append("")
    L.append(f"_Generated {datetime.now().isoformat(timespec='minutes')}_")

    os.makedirs(STATE, exist_ok=True)
    out = os.path.join(STATE, "weekly.md")
    with open(out, "w") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[written to {out}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
