#!/usr/bin/env python3
"""CSV money adapter: turn a bank-export CSV into the engine's data contract.

The zero-dollar, zero-aggregator option: download a CSV from your bank on
whatever cadence you like and point money.csv_path at it. Columns are
config-mapped so any bank's export works:

  "money": {
    "source": "csv",
    "csv_path": "~/Downloads/transactions.csv",
    "csv_columns": {"date": "Date", "amount": "Amount",
                    "description": "Description", "category": ""},
    "csv_date_format": "%m/%d/%Y",
    "csv_spend_negative": true,
    "csv_balance": 1234.56,
    "csv_account_name": "Checking",
    "category_rules": [{"match": "employer name", "category": "Paychecks"}]
  }

csv_spend_negative: true when the export already shows spending as negative
numbers (the engine's convention); false flips the sign. csv_balance is the
current balance of the account (a CSV of transactions cannot know it); update
it when it drifts, or leave 0 and read Safe-to-Spend as relative only.
A category column, when your bank provides one, wins over the rules.

Usage: pull_csv.py [--dry-run]
"""

import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import money_map  # noqa: E402


def main():
    dry = "--dry-run" in sys.argv
    cfg = money_map.money_cfg()
    path = os.path.expanduser(cfg.get("csv_path") or "")
    if not path or not os.path.exists(path):
        sys.exit("csv source not configured or file missing (money.csv_path)")

    cols = cfg.get("csv_columns") or {}
    c_date = cols.get("date") or "Date"
    c_amount = cols.get("amount") or "Amount"
    c_desc = cols.get("description") or "Description"
    c_cat = cols.get("category") or ""
    fmt = cfg.get("csv_date_format") or "%Y-%m-%d"
    sign = 1 if cfg.get("csv_spend_negative", True) else -1
    rules = cfg.get("category_rules") or []

    txns, skipped = [], 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            try:
                d = datetime.strptime((row.get(c_date) or "").strip(), fmt)
                raw = (row.get(c_amount) or "").replace("$", "").replace(",", "").strip()
                neg = raw.startswith("(") and raw.endswith(")")
                amount = sign * float(raw.strip("()"))
                if neg:
                    amount = -abs(amount)
            except ValueError:
                skipped += 1
                continue
            desc = (row.get(c_desc) or "").strip()
            cat = (row.get(c_cat) or "").strip() if c_cat else ""
            txns.append(money_map.txn_row(
                date=d.strftime("%Y-%m-%d"),
                amount=amount,
                description=desc,
                category=cat or money_map.categorize(rules, desc),
            ))

    if not txns:
        sys.exit(f"no parseable transactions ({skipped} rows skipped): check "
                 "csv_columns and csv_date_format; not overwriting existing data")
    if skipped > 5 and skipped >= len(txns):
        print(f"WARNING: {skipped} rows skipped vs {len(txns)} parsed; "
              "the column mapping may be wrong", file=sys.stderr)

    accounts = [money_map.account_row(
        cfg.get("csv_account_name") or "Checking",
        float(cfg.get("csv_balance") or 0))]

    if dry:
        print(f"DRY-RUN: {len(accounts)} account, {len(txns)} transactions "
              f"({skipped} rows skipped), nothing written")
        return 0
    na, nt = money_map.write_contract(accounts, txns)
    print(f"csv pull: {na} account, {nt} transactions ({skipped} rows skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
