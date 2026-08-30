"""Shared mapping layer for money-source adapters (simplefin, csv).

Adapters normalize whatever their source returns into the two files every
consumer reads (see docs/2026-08-14-money-adapter-design.md):

  data/raw_accounts.json      {"accounts": [...]}
  data/raw_transactions.json  {"allTransactions": {"results": [...]}}

Categories are the one thing external sources lack, so they come from
deterministic substring rules in config (money.category_rules): first hit
against payee + description wins, else "Uncategorized". Two rules are
load-bearing for the engine: the employer name mapping to "Paychecks"
(payday detection) and grocery stores mapping to "Groceries" (essentials
reservation).
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def money_cfg():
    try:
        with open(os.path.join(ROOT, "config", "household.json")) as f:
            return json.load(f).get("money") or {}
    except Exception:
        return {}


def categorize(rules, *texts):
    hay = " ".join(t for t in texts if t).lower()
    for r in rules or []:
        m = (r.get("match") or "").lower()
        if m and m in hay:
            return r.get("category") or "Uncategorized"
    return "Uncategorized"


def account_row(name, balance, subtype="checking"):
    return {
        "displayName": name,
        "currentBalance": float(balance),
        "subtype": {"name": subtype},
        "deactivatedAt": None,
    }


def txn_row(date, amount, description, category, payee=None, pending=False):
    """One transaction in the contract shape. amount: spending negative,
    income positive (the engine's sign convention throughout)."""
    return {
        "date": date,
        "amount": float(amount),
        "plaidName": description or "",
        "merchant": {"name": payee or description or ""},
        "category": {"name": category},
        "pending": bool(pending),
        "hideFromReports": False,
    }


def write_contract(accounts, txns):
    """Write both contract files, each via tmp+replace. Transactions land
    first so a crash between the two replaces leaves new transactions with
    old balances (harmless) rather than new balances with old transactions
    (misleading). Callers guard against empty data; this function writes
    what it is given."""
    os.makedirs(DATA, exist_ok=True)
    txns = sorted(txns, key=lambda t: t["date"], reverse=True)
    for fname, payload in (
        ("raw_transactions.json", {"allTransactions": {"results": txns}}),
        ("raw_accounts.json", {"accounts": accounts}),
    ):
        path = os.path.join(DATA, fname)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=1)
        os.replace(tmp, path)
    return len(accounts), len(txns)
