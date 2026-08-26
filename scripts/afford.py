"""Answer 'can we afford this?' against the current Safe-to-Spend number.

Usage:
    python3 scripts/afford.py 45
    python3 scripts/afford.py 45 "kids shoes"
    python3 scripts/afford.py 129.99 "headphones" --json
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "status.json")


def load():
    if not os.path.exists(STATE):
        print("No status yet. Run: python3 scripts/engine.py")
        sys.exit(2)
    with open(STATE) as f:
        return json.load(f)


def money(x):
    return f"${x:,.2f}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__)
        return 2

    try:
        amount = float(args[0].lstrip("$").replace(",", ""))
    except ValueError:
        print(f"Could not read an amount from {args[0]!r}")
        return 2
    label = args[1] if len(args) > 1 else "this"

    s = load()
    safe = s["safe_to_spend"]
    after = safe - amount
    days = s["days_until_payday"]

    verdict = "yes" if after >= 0 else "no"

    if as_json:
        print(json.dumps({"amount": amount, "label": label, "safe_before": safe,
                          "safe_after": round(after, 2), "verdict": verdict}, indent=1))
        return 0

    print()
    if safe < 0:
        print(f"  NO. There is already a shortfall of {money(-safe)} before payday.")
        print(f"  Buying {label} for {money(amount)} would deepen it to {money(-after)}.")
    elif after >= 0:
        print(f"  YES. {label.capitalize()} for {money(amount)} is affordable.")
        print(f"  Safe-to-Spend goes {money(safe)} -> {money(after)}")
        if days > 0:
            print(f"  That leaves {money(after / days)}/day for the {days} days to payday.")
    else:
        print(f"  NO. {label.capitalize()} costs {money(amount)} but only {money(safe)} is free.")
        print(f"  Short by {money(-after)}.")

    print()
    print(f"  Next payday: {s['next_payday']} ({days} days), about {money(s['expected_paycheck'])}")

    bills = s.get("bills_due_before_payday") or []
    if bills:
        print("  Still due before then:")
        for b in bills:
            print(f"     {b['due']}  {money(b['amount']):>12}  {b['name']}")

    if after < 0 and safe > 0:
        print()
        print(f"  If it can wait until {s['next_payday']}, it is affordable then.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
