"""Safe-to-Spend engine.

Computes ONE number: how much is genuinely free to spend between now and the
next paycheck, after the bills that are still coming and the grocery and fuel
money that has to last.

Read-only against Monarch. Writes state/status.json. Never moves money.

Usage:
    python3 scripts/engine.py            # use cached pull if fresh, else re-pull
    python3 scripts/engine.py --refresh  # always re-pull from Monarch
"""

import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(ROOT, "state")
CONFIG = os.path.join(ROOT, "config", "budget.json")
MAX_STALE_HOURS = 8


def load_config():
    with open(CONFIG) as f:
        return json.load(f)


def pull_is_stale():
    p = os.path.join(DATA, "raw_accounts.json")
    if not os.path.exists(p):
        return True
    age_h = (datetime.now().timestamp() - os.path.getmtime(p)) / 3600
    return age_h > MAX_STALE_HOURS


def refresh():
    """Re-pull from the finance source via the read-only pull script.

    The client directory comes from household.json money.client_dir (or the
    HOUSEWRIGHT_MONEY_CLIENT env var). Unset: the lane skips cleanly and the
    briefs simply omit the money block.
    """
    try:
        with open(os.path.join(ROOT, "config", "household.json")) as f:
            money = json.load(f).get("money") or {}
    except Exception:
        money = {}
    source = (money.get("source") or "").strip().lower()
    if source and source not in ("monarch", "simplefin", "csv"):
        print(f"WARN: unknown money.source {source!r} "
              "(expected monarch, simplefin, or csv); skipping the money lane",
              file=sys.stderr)
        return False
    if source in ("simplefin", "csv"):
        script = os.path.join(ROOT, "scripts", f"pull_{source}.py")
        try:
            r = subprocess.run([sys.executable, script], capture_output=True,
                               text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"WARN: {source} pull failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return False
        if r.returncode != 0:
            print(f"WARN: {source} pull failed rc={r.returncode}", file=sys.stderr)
            print((r.stderr or r.stdout)[-800:], file=sys.stderr)
            return False
        return True

    client = os.path.expanduser(
        os.environ.get("HOUSEWRIGHT_MONEY_CLIENT") or money.get("client_dir") or "")
    if not client or not os.path.isdir(client):
        print("money lane skipped: no finance client configured "
              "(household.json money.client_dir)", file=sys.stderr)
        return False
    script = os.path.join(ROOT, "scripts", "pull.py")
    uv = shutil.which("uv") or "uv"
    try:
        r = subprocess.run(
            [uv, "run", "--directory", client, "python", script],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"WARN: pull failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(f"WARN: pull failed rc={r.returncode}", file=sys.stderr)
        print(r.stderr[-800:], file=sys.stderr)
        return False
    return True


def load(name):
    with open(os.path.join(DATA, f"raw_{name}.json")) as f:
        return json.load(f)


def next_payday(cfg, today):
    """Project forward from the last known payday on the configured cadence."""
    inc = cfg["income"]
    last = datetime.strptime(inc["last_known_payday"], "%Y-%m-%d").date()
    step = timedelta(days=int(inc["period_days"]))
    d = last
    while d <= today:
        d += step
    return d


def spending_balance(cfg, accounts):
    hint = cfg["accounts"]["spending_account_hint"].lower()
    exact = [a for a in accounts if hint in (a.get("displayName") or "").lower()]
    if exact:
        return float(exact[0].get("currentBalance") or 0), exact[0]["displayName"]
    # Fall back to the largest positive checking balance.
    checking = [
        a
        for a in accounts
        if ((a.get("subtype") or {}).get("name") or "") == "checking"
        and not a.get("deactivatedAt")
        and (a.get("currentBalance") or 0) > 0
    ]
    if not checking:
        return 0.0, None
    best = max(checking, key=lambda a: a["currentBalance"])
    return float(best["currentBalance"]), best["displayName"]


def bills_due_before(cfg, today, until):
    """Fixed bills whose day-of-month falls in (today, until]."""
    out = []
    for name, spec in cfg["fixed_monthly"].items():
        if name.startswith("_"):
            continue
        dom = int(spec["day_of_month"])
        d = today
        while d <= until:
            if d.day == dom and d > today:
                out.append({"name": name, "amount": float(spec["amount"]), "due": d.isoformat()})
                break
            d += timedelta(days=1)
    return sorted(out, key=lambda b: b["due"])


def override_bucket(cfg, t):
    """Named transfers that are really spending (house cleaner paid by Zelle)."""
    hay = (((t.get("merchant") or {}).get("name") or "") + " " +
           (t.get("plaidName") or "")).upper()
    for pat, bucket in cfg.get("transfer_overrides", {}).items():
        if pat.startswith("_"):
            continue
        if pat.upper() in hay:
            return bucket
    return None


def period_spend(cfg, txns, since):
    """Spending since `since`, bucketed by budget bucket."""
    ignore = set(cfg["ignore_categories"])
    cmap = cfg["category_map"]
    buckets = defaultdict(float)
    for t in txns:
        amt = t.get("amount") or 0
        if amt >= 0:
            continue
        d = datetime.strptime(t["date"], "%Y-%m-%d").date()
        if d < since:
            continue
        ob = override_bucket(cfg, t)
        if ob:
            buckets[ob] += -amt
            continue
        cat = ((t.get("category") or {}).get("name")) or "Uncategorized"
        if cat in ignore:
            continue
        bucket = cmap.get(cat, "discretionary:Everything else")
        if bucket.startswith("_"):
            continue
        buckets[bucket] += -amt
    return dict(buckets)


RETIREMENT_SUBTYPES = {
    "ira", "roth", "st_401k", "education_savings_account",
    "health_savings_account", "pension", "401k", "403b",
}


def runway(cfg, accounts, txns, today):
    """Months of reachable money left at the actual trailing-30-day pace."""
    liquid = 0.0
    liquid_detail = []
    card_debt = 0.0
    excluded = [e.upper() for e in cfg.get("accounts", {}).get("exclude_from_household", [])]
    for a in accounts:
        if a.get("deactivatedAt") or a.get("isHidden"):
            continue
        name = (a.get("displayName") or "")
        if any(e in name.upper() for e in excluded):
            continue  # kids' accounts are not household money
        t = ((a.get("type") or {}).get("name") or "").lower()
        st = ((a.get("subtype") or {}).get("name") or "").lower()
        bal = float(a.get("currentBalance") or 0)
        if t == "credit" and bal < 0:
            card_debt += -bal
        if bal <= 0:
            continue
        if t == "depository" or (t in ("brokerage", "investment") and st not in RETIREMENT_SUBTYPES):
            liquid += bal
            liquid_detail.append({"name": a["displayName"], "balance": round(bal, 2)})

    # Trailing-30d burn: real spending (overrides included) minus paychecks.
    ignore = set(cfg["ignore_categories"])
    d30 = today - timedelta(days=30)
    spend = income = 0.0
    for t in txns:
        d = datetime.strptime(t["date"], "%Y-%m-%d").date()
        if d < d30:
            continue
        amt = t.get("amount") or 0
        cat = ((t.get("category") or {}).get("name")) or "Uncategorized"
        if amt > 0:
            if cat == "Paychecks":
                income += amt
            continue
        if override_bucket(cfg, t):
            spend += -amt
        elif cat not in ignore:
            spend += -amt

    burn = spend - income  # positive = losing money
    months = round(liquid / burn, 1) if burn > 60 else None

    # Budget pace: what the burn becomes if the targets hold.
    fixed = sum(float(v["amount"]) for k, v in cfg["fixed_monthly"].items() if not k.startswith("_"))
    ess = sum(float(v) for k, v in cfg["essentials_monthly"].items() if not k.startswith("_"))
    disc = sum(float(v) for k, v in cfg["discretionary_monthly"].items() if not k.startswith("_"))
    paychecks_mo = float(cfg["income"]["typical_paycheck"]) * 26 / 12
    budget_burn = (fixed + ess + disc) - paychecks_mo
    months_budget = round(liquid / budget_burn, 1) if budget_burn > 60 else None

    return {
        "liquid_reachable": round(liquid, 2),
        "liquid_detail": liquid_detail,
        "card_debt": round(card_debt, 2),
        "burn_30d": round(burn, 2),
        "income_30d": round(income, 2),
        "spend_30d": round(spend, 2),
        "months_at_current_pace": months,
        "budget_burn": round(budget_burn, 2),
        "months_at_budget_pace": months_budget,
    }


def append_history(out):
    """One line per day in state/history.jsonl; same-day rerun replaces."""
    os.makedirs(STATE, exist_ok=True)
    p = os.path.join(STATE, "history.jsonl")
    rows = []
    if os.path.exists(p):
        with open(p) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    rows = [r for r in rows if r.get("date") != out["as_of_date"]]
    rw = out.get("runway") or {}
    rows.append({
        "date": out["as_of_date"],
        "safe_to_spend": out["safe_to_spend"],
        "balance": out["balance"],
        "liquid": rw.get("liquid_reachable"),
        "card_debt": rw.get("card_debt"),
    })
    rows.sort(key=lambda r: r["date"])
    with open(p, "w") as f:
        for r in rows[-730:]:
            f.write(json.dumps(r) + "\n")


def main():
    cfg = load_config()
    force = "--refresh" in sys.argv

    if force or pull_is_stale():
        refresh()

    accounts = load("accounts")["accounts"]
    txns = load("transactions")["allTransactions"]["results"]

    today = date.today()
    payday = next_payday(cfg, today)
    days_left = (payday - today).days

    balance, acct_name = spending_balance(cfg, accounts)
    bills = bills_due_before(cfg, today, payday)
    bills_total = sum(b["amount"] for b in bills)

    # Essentials: reserve the pro-rata share for the days remaining in the period,
    # less whatever has already been spent on them since the last payday.
    last_payday = payday - timedelta(days=int(cfg["income"]["period_days"]))
    spent = period_spend(cfg, txns, last_payday)

    ess_reserve = 0.0
    ess_detail = []
    for name, monthly in cfg["essentials_monthly"].items():
        if name.startswith("_"):
            continue
        per_day = float(monthly) * 12 / 365
        allowance = per_day * int(cfg["income"]["period_days"])
        used = spent.get(f"essentials:{name}", 0.0)
        remaining = max(0.0, allowance - used)
        ess_reserve += remaining
        ess_detail.append(
            {
                "name": name,
                "period_allowance": round(allowance, 2),
                "spent": round(used, 2),
                "reserved": round(remaining, 2),
            }
        )

    safe = balance - bills_total - ess_reserve

    disc_budget = sum(
        float(v) for k, v in cfg["discretionary_monthly"].items() if not k.startswith("_")
    )
    disc_period = disc_budget * 12 / 365 * int(cfg["income"]["period_days"])
    disc_spent = sum(v for k, v in spent.items() if k.startswith("discretionary:"))

    th = cfg["thresholds"]
    status = (
        "green"
        if safe >= th["green_above"]
        else "amber"
        if safe >= th["amber_above"]
        else "red"
    )

    rw = runway(cfg, accounts, txns, today)

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of_date": today.isoformat(),
        "safe_to_spend": round(safe, 2),
        "safe_per_day": round(safe / days_left, 2) if days_left > 0 else round(safe, 2),
        "status": status,
        "next_payday": payday.isoformat(),
        "days_until_payday": days_left,
        "expected_paycheck": cfg["income"]["typical_paycheck"],
        "account": acct_name,
        "balance": round(balance, 2),
        "bills_due_before_payday": bills,
        "bills_total": round(bills_total, 2),
        "essentials_reserved": round(ess_reserve, 2),
        "essentials_detail": ess_detail,
        "discretionary": {
            "period_budget": round(disc_period, 2),
            "period_spent": round(disc_spent, 2),
            "period_remaining": round(disc_period - disc_spent, 2),
            "by_bucket": {
                k.split(":", 1)[1]: round(v, 2)
                for k, v in sorted(spent.items(), key=lambda kv: -kv[1])
                if k.startswith("discretionary:")
            },
        },
        "runway": rw,
    }

    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "status.json"), "w") as f:
        json.dump(out, f, indent=1)
    append_history(out)

    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
