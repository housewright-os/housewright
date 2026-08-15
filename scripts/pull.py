"""Read-only pull from the household finance source. Writes raw JSON to data/.

Contract: a client directory (household.json money.client_dir, or the
HOUSEWRIGHT_MONEY_CLIENT env var) whose src/ provides a module importable
as `monarch_mcp_server.secure_session` exposing get_authenticated_client(),
returning a client with the async get_* API used below. The reference
client wraps a Monarch session held in the OS keychain; a generalized
adapter interface (Firefly/Actual/CSV) is the next planned change here. Never mutates
anything. Only get_* calls.
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    with open(os.path.join(_ROOT, "config", "household.json")) as _f:
        _client = (json.load(_f).get("money") or {}).get("client_dir") or ""
except Exception:
    _client = ""
_client = os.path.expanduser(os.environ.get("HOUSEWRIGHT_MONEY_CLIENT") or _client)
if not _client:
    sys.exit("money lane not configured: set money.client_dir in config/household.json")
sys.path.insert(0, os.path.join(_client, "src"))
try:
    from monarch_mcp_server.secure_session import secure_session  # noqa: E402
except ImportError:
    sys.exit("no finance client found in client_dir (expected a module providing secure_session)")

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(OUT, exist_ok=True)
TODAY = date.today()
START = TODAY - timedelta(days=120)


def dump(name, obj):
    p = os.path.join(OUT, f"raw_{name}.json")
    with open(p, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    return p


async def main():
    mm = secure_session.get_authenticated_client()
    if mm is None:
        print("NO_SESSION")
        return 2

    results = {}

    async def grab(name, coro_fn, *a, **kw):
        try:
            r = await coro_fn(*a, **kw)
            dump(name, r)
            n = None
            if isinstance(r, dict):
                for k, v in r.items():
                    if isinstance(v, list):
                        n = len(v)
                        break
                    if isinstance(v, dict) and isinstance(v.get("results"), list):
                        n = len(v["results"])
                        break
            elif isinstance(r, list):
                n = len(r)
            results[name] = n if n is not None else "ok"
            print(f"OK   {name}: {results[name]}")
        except Exception as e:
            results[name] = f"ERR {type(e).__name__}"
            print(f"ERR  {name}: {type(e).__name__}: {str(e)[:160]}")

    await grab("accounts", mm.get_accounts)
    await grab(
        "transactions",
        mm.get_transactions,
        limit=6000,
        start_date=START.isoformat(),
        end_date=TODAY.isoformat(),
    )
    await grab("categories", mm.get_transaction_categories)
    await grab("recurring", mm.get_recurring_transactions)
    await grab("budgets", mm.get_budgets)
    await grab(
        "cashflow_summary",
        mm.get_cashflow_summary,
        start_date=START.isoformat(),
        end_date=TODAY.isoformat(),
    )
    await grab(
        "cashflow",
        mm.get_cashflow,
        start_date=START.isoformat(),
        end_date=TODAY.isoformat(),
    )
    await grab("subscription", mm.get_subscription_details)

    # Daily balance history for every account (floats, one per day from START).
    # Wrapped with meta so consumers can reconstruct dates by index.
    try:
        hist = await mm.get_recent_account_balances(start_date=START.isoformat())
        dump("balance_history", {"start_date": START.isoformat(),
                                 "end_date": TODAY.isoformat(),
                                 "payload": hist})
        print("OK   balance_history")
    except Exception as e:
        print(f"ERR  balance_history: {type(e).__name__}: {str(e)[:160]}")

    print("\nSUMMARY " + json.dumps(results))
    return 0


sys.exit(asyncio.run(main()))
