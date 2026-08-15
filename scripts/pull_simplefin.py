#!/usr/bin/env python3
"""SimpleFIN money adapter: bank data for ~$15/yr instead of a budget-app
subscription, through a deliberately tiny read-only protocol.

One-time connect (SimpleFIN Bridge sells the setup token):
    python3 scripts/pull_simplefin.py --claim <setup-token>
The setup token is a base64 claim URL, one-time use; claiming returns an
Access URL with credentials embedded. On macOS it is stored in the Keychain
(service "housewright-simplefin"); elsewhere export it as
SIMPLEFIN_ACCESS_URL. It never touches a file in this repo.

Normal runs (engine.py calls this when money.source is "simplefin"):
    python3 scripts/pull_simplefin.py [--dry-run]
GET {access}/accounts?version=2&start-date=<90d>&pending=1, mapped to the
engine's two contract files. SimpleFIN's sign convention (deposits positive,
withdrawals negative) already matches the engine's, so amounts pass through
untouched. Categories: SimpleFIN carries none, so money.category_rules
substring rules apply (a server-provided extra.category wins when present).

Testing without a token: --from-file <response.json> replays a canned
/accounts response through the exact same mapping.

Bridge constraints worth knowing: 90-day window per request, roughly daily
data refresh upstream, ~24 requests/day per token before rate limiting.
"""

import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import money_map  # noqa: E402

KEYCHAIN_SERVICE = "housewright-simplefin"
LOOKBACK_DAYS = 90  # the Bridge caps one request at 90 days


def keychain_get():
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def keychain_set(value):
    try:
        r = subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", KEYCHAIN_SERVICE, "-a", "money", "-w", value],
            capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def access_url():
    return os.environ.get("SIMPLEFIN_ACCESS_URL", "").strip() or keychain_get()


def claim(setup_token):
    """One-time exchange: base64 claim URL -> POST -> access URL."""
    try:
        claim_url = base64.b64decode(setup_token.strip()).decode()
    except Exception:
        sys.exit("that does not decode as a SimpleFIN setup token")
    if not claim_url.startswith("https://"):
        sys.exit("decoded claim URL is not https; refusing")
    req = urllib.request.Request(claim_url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        url = r.read().decode().strip()
    parts = urllib.parse.urlsplit(url)
    if not url.startswith("https://") or not (parts.username and parts.password):
        sys.exit("claim did not return an https access URL with embedded credentials")
    if keychain_set(url):
        print("access URL stored in the macOS Keychain "
              f"(service {KEYCHAIN_SERVICE!r}). You are connected.")
    else:
        print("no Keychain available; export this once in your shell profile "
              "(it embeds credentials, treat it like a password):")
        print(f"  export SIMPLEFIN_ACCESS_URL='{url}'")
    return 0


def fetch(url):
    """GET /accounts with the credentials embedded in the access URL."""
    parts = urllib.parse.urlsplit(url)
    user = urllib.parse.unquote(parts.username or "")
    pw = urllib.parse.unquote(parts.password or "")
    creds = base64.b64encode(f"{user}:{pw}".encode()).decode()
    bare = urllib.parse.urlunsplit(
        (parts.scheme, parts.hostname + (f":{parts.port}" if parts.port else ""),
         parts.path, "", ""))
    start = int(time.time()) - LOOKBACK_DAYS * 86400
    q = urllib.parse.urlencode({"version": "2", "start-date": start, "pending": "1"})
    req = urllib.request.Request(f"{bare}/accounts?{q}",
                                 headers={"Authorization": f"Basic {creds}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def map_response(data, rules):
    """SimpleFIN AccountSet (v2, tolerating v1's org shape) -> contract rows."""
    conn_names = {c.get("conn_id"): c.get("name") or ""
                  for c in data.get("connections") or []}
    accounts, txns = [], []
    for a in data.get("accounts") or []:
        org = conn_names.get(a.get("conn_id")) or (a.get("org") or {}).get("name") or ""
        name = " ".join(x for x in (org, a.get("name") or "Account") if x)
        accounts.append(money_map.account_row(name, float(a.get("balance") or 0)))
        for t in a.get("transactions") or []:
            posted = t.get("posted") or t.get("transacted_at") or 0
            # SimpleFIN epochs are UTC (often UTC midnight): gmtime, or a
            # negative-offset zone shifts every date back a day.
            d = time.strftime("%Y-%m-%d", time.gmtime(posted)) if posted \
                else time.strftime("%Y-%m-%d")
            desc = (t.get("description") or "").strip()
            extra_cat = ((t.get("extra") or {}).get("category") or "").strip()
            txns.append(money_map.txn_row(
                date=d,
                amount=float(t.get("amount") or 0),  # signs already match
                description=desc,
                category=extra_cat or money_map.categorize(rules, desc),
                pending=bool(t.get("pending")),
            ))
    errs = [e for e in (data.get("errlist") or data.get("errors") or []) if e]
    return accounts, txns, errs


def main():
    args = sys.argv[1:]
    if args and args[0] == "--claim":
        if len(args) < 2:
            sys.exit("usage: pull_simplefin.py --claim <setup-token>")
        return claim(args[1])

    dry = "--dry-run" in args
    rules = money_map.money_cfg().get("category_rules") or []

    if "--from-file" in args:
        path = args[args.index("--from-file") + 1]
        with open(path) as f:
            data = json.load(f)
    else:
        url = access_url()
        if not url:
            sys.exit("no SimpleFIN access URL: run --claim <setup-token> once, "
                     "or set SIMPLEFIN_ACCESS_URL")
        data = fetch(url)

    accounts, txns, errs = map_response(data, rules)
    for e in errs:
        print(f"simplefin reported: {e}", file=sys.stderr)
    if not accounts:
        sys.exit("no accounts in response; not overwriting existing data")
    if not txns:
        sys.exit("no transactions in response (upstream hiccup?); "
                 "not overwriting existing data")

    uncat = sum(1 for t in txns if t["category"]["name"] == "Uncategorized")
    if dry:
        print(f"DRY-RUN: {len(accounts)} accounts, {len(txns)} transactions "
              f"({uncat} uncategorized), nothing written")
        return 0
    na, nt = money_map.write_contract(accounts, txns)
    print(f"simplefin pull: {na} accounts, {nt} transactions ({uncat} uncategorized)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
