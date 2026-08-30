"""Household shopping list: the shared "add milk" list, vendor-aware.

Backed by data/shopping.json (gitignored, like all household data). Used from
the Telegram bot ("add milk", "shopping list", "ordered walmart") and from
notify.py for the briefs. Mirrors tasks.py's storage pattern exactly: stable
ids, exclusive-lock read-modify-write, atomic temp-file replace, a corrupt
file is preserved rather than overwritten.

Vendor assignment: each item is matched (longest keyword first) against
config/household.json's "item_vendor_map", falling back to the most recent
rail_check.py winner for that item if one exists, falling back to null (the
bot says so rather than guessing). ETA is cadence-based: config's
"shopping_cadence" gives each vendor a typical order interval and a reason;
state/vendor_orders.json tracks when each vendor was last actually ordered
from (see mark_ordered), and the ETA is last_order + cadence_days. With no
last-order date yet the bot gives the cadence itself, not a fake date.

This is deliberately NOT consumption-based run-out prediction: no lane in
this repo tracks how fast the house actually goes through milk or rice, so
building a "runs out in 3 days" claim here would be a guess dressed as data.
The honest v1 is "here's who's cheapest and how often we typically order,"
with the data model left open (see ITEM fields) for real consumption data to
plug in later if that lane ever gets built.

Usage:
    python3 scripts/shopping.py add "milk" [--by Alex] [--urgent]
    python3 scripts/shopping.py list
    python3 scripts/shopping.py ordered walmart
"""

import fcntl
import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOPPING = os.path.join(ROOT, "data", "shopping.json")
LOCKFILE = SHOPPING + ".lock"
VENDOR_ORDERS = os.path.join(ROOT, "state", "vendor_orders.json")
CONFIG = os.path.join(ROOT, "config", "household.json")
RAIL_PRICES = os.path.join(ROOT, "state", "rail_prices.jsonl")

EMPTY = {"seq": 0, "items": []}

VENDOR_LABEL = {
    "walmart": "Walmart",
    "costco-business-center-via-instacart": "Costco Business Center",
    "sams-club-via-instacart": "Sam's Club",
}

# Shared with telegram_bot.py so "ordered sams club" resolves the same way
# whether it's typed in Telegram or run from this CLI.
VENDOR_ALIASES = {
    "walmart": "walmart",
    "cbc": "costco-business-center-via-instacart",
    "costco business center": "costco-business-center-via-instacart",
    "costco": "costco-business-center-via-instacart",
    "sams": "sams-club-via-instacart",
    "sam's": "sams-club-via-instacart",
    "sams club": "sams-club-via-instacart",
    "sam's club": "sams-club-via-instacart",
}


def resolve_vendor(text):
    """A rails vendor key from free-typed text ('sams club', 'CBC', a bare
    key already), or None if it doesn't match anything known."""
    low = text.strip().lower()
    if low in VENDOR_LABEL:
        return low
    return VENDOR_ALIASES.get(low)


@contextmanager
def _locked():
    os.makedirs(os.path.dirname(SHOPPING), exist_ok=True)
    with open(LOCKFILE, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _load():
    if not os.path.exists(SHOPPING):
        return json.loads(json.dumps(EMPTY))
    try:
        with open(SHOPPING) as f:
            return json.load(f)
    except Exception:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(SHOPPING, f"{SHOPPING}.corrupt-{stamp}")
            print(f"shopping.json unreadable, preserved as shopping.json.corrupt-{stamp}",
                  file=sys.stderr)
        except Exception:
            pass
        return json.loads(json.dumps(EMPTY))


def _save(db):
    os.makedirs(os.path.dirname(SHOPPING), exist_ok=True)
    tmp = SHOPPING + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=1)
    os.replace(tmp, SHOPPING)


def _household_cfg():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except Exception:
        return {}


def _last_rail_winner(name):
    """Most recent rail_check.py verdict for an item whose name contains (or
    is contained by) the requested name. Returns (vendor_key, reason) or
    (None, None). rail_check winners use the same vendor keys as rails.rails."""
    try:
        with open(RAIL_PRICES) as f:
            lines = [ln for ln in f if ln.strip()]
        if not lines:
            return None, None
        record = json.loads(lines[-1])
    except Exception:
        return None, None
    n = name.lower().strip()
    for it in record.get("items") or []:
        basket_name = str(it.get("item") or "").lower()
        if n in basket_name or basket_name in n:
            w = it.get("winner")
            if w and w != "tie":
                return w, f"cheapest rail as of the {record.get('date', 'last')} price check"
    return None, None


def vendor_for(name):
    """(vendor_key, reason) for an item name, or (None, None) if unknown.
    Checks the curated item_vendor_map first (exact/substring, longest match
    wins), then falls back to the live rail_check verdict."""
    cfg = _household_cfg()
    vmap = (cfg.get("shopping") or {}).get("item_vendor_map") or {}
    n = name.lower().strip()
    best = None
    for key, vendor in vmap.items():
        if key.startswith("_"):
            continue  # doc keys in the map are not vendors
        if key in n or n in key:
            if best is None or len(key) > len(best[0]):
                best = (key, vendor)
    if best:
        cadence = (cfg.get("shopping") or {}).get("vendor_cadence") or {}
        why = (cadence.get(best[1]) or {}).get("why", "")
        return best[1], why
    return _last_rail_winner(name)


def _vendor_orders():
    try:
        with open(VENDOR_ORDERS) as f:
            return json.load(f)
    except Exception:
        return {}


def mark_ordered(vendor, when=None):
    """Record that an order just went out to `vendor` (a rails key), so
    future ETAs compute from a real date instead of guessing one. Locked
    (the pantry cron and the bot are concurrent writers) and monotonic: a
    late-processed receipt never moves a clock backward past a newer
    stamp."""
    w = when or date.today()
    stamp = w.isoformat() if hasattr(w, "isoformat") else str(w)
    os.makedirs(os.path.dirname(VENDOR_ORDERS), exist_ok=True)
    with _locked():
        orders = _vendor_orders()
        if orders.get(vendor) and str(orders[vendor]) >= stamp:
            return  # newer stamp already on the clock
        orders[vendor] = stamp
        tmp = VENDOR_ORDERS + f".tmp{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(orders, f, indent=1)
        os.replace(tmp, VENDOR_ORDERS)


def eta_for(vendor):
    """Best-effort next-order estimate for a vendor: (date_iso_or_None, note).
    Honest about what it doesn't know: no cadence configured, or no order
    history yet, both say so rather than fabricating a date."""
    cfg = _household_cfg()
    cadence = ((cfg.get("shopping") or {}).get("vendor_cadence") or {}).get(vendor)
    if not cadence:
        return None, "no cadence configured for this vendor yet"
    days = cadence.get("cadence_days")
    if not isinstance(days, (int, float)) or days <= 0:
        return None, "no valid cadence configured for this vendor yet"
    last = _vendor_orders().get(vendor)
    if not last:
        return None, f"no order history yet; typically every ~{days} days once one lands"
    try:
        last_d = datetime.strptime(last, "%Y-%m-%d").date()
    except Exception:
        return None, f"typically every ~{days} days"
    nxt = last_d + timedelta(days=days)
    if nxt < date.today():
        return date.today().isoformat(), f"overdue (last ordered {last}, usual cadence {days}d)"
    return nxt.isoformat(), f"usual {days}-day cadence from the {last} order"


def add(name, by=None, urgent=False):
    """Add an item, return its id. Vendor/eta are resolved and stored at add
    time so a later price-rail change doesn't silently rewrite history."""
    vendor, reason = vendor_for(name)
    eta, eta_note = eta_for(vendor) if vendor else (None, None)
    with _locked():
        db = _load()
        db["seq"] += 1
        db["items"].append({
            "id": db["seq"],
            "name": name.strip(),
            "added": date.today().isoformat(),
            "by": by or None,
            "urgent": bool(urgent),
            "vendor": vendor,
            "vendor_reason": reason,
            "eta": eta,
            "eta_note": eta_note,
            "fulfilled_at": None,
        })
        _save(db)
        return db["seq"]


def pickup_advice(item):
    """For an urgent item: wait on the vendor's next order, or go get it in
    person. Honest about the common early case where NO order has ever
    actually gone out on this vendor -- there, waiting isn't a real option
    at all, regardless of what the configured cadence says, because no
    order is actually queued."""
    if not item.get("urgent"):
        return None
    if not item.get("vendor"):
        return ("No vendor match for this one, so there's no order to wait "
                "on -- fastest path is grabbing it in person.")
    note = item.get("eta_note") or ""
    if not item.get("eta") or "no order history" in note or "no valid cadence" in note:
        return ("No order has ever gone out on this vendor yet, so there's "
                "no real ETA to wait on -- fastest path right now is "
                "picking it up yourself or placing a quick one-off order.")
    label = vendor_label(item["vendor"])
    if "overdue" in note:
        return (f"{label}'s usual order is already overdue with nothing "
                f"actually shipped -- don't wait on it, grab this in "
                f"person or place a one-off order now.")
    try:
        eta_d = datetime.strptime(item["eta"], "%Y-%m-%d").date()
    except Exception:
        return ("Couldn't work out a precise timeline for this one -- "
                "treat it as a judgment call: pick it up or place a quick "
                "one-off order.")
    days_out = (eta_d - date.today()).days
    if days_out <= 1:
        return f"That's about when the next {label} order would land anyway -- probably fine to just wait."
    return (f"That's {days_out} days out, likely too long for urgent -- "
            f"grab it in person, or place a quick one-off order instead of "
            f"waiting on {label}'s usual cadence.")


def fulfill(iid):
    """Mark an item bought/received. Returns the item or None."""
    with _locked():
        db = _load()
        for it in db["items"]:
            if it["id"] == iid and not it["fulfilled_at"]:
                it["fulfilled_at"] = datetime.now().isoformat(timespec="seconds")
                _save(db)
                return it
        return None


def open_items():
    return [it for it in _load()["items"] if not it["fulfilled_at"]]


def by_vendor(vendor):
    return [it for it in open_items() if it.get("vendor") == vendor]


def vendor_label(key):
    return VENDOR_LABEL.get(key, key or "an unassigned vendor")


def fmt(it):
    tag = " 🚨URGENT" if it.get("urgent") else ""
    who = f" (added by {it['by']})" if it.get("by") else ""
    line = f"#{it['id']} {it['name']}{tag}{who}"
    if it.get("vendor"):
        line += f"\n    -> {vendor_label(it['vendor'])}"
        if it.get("eta"):
            line += f", ETA {it['eta'][5:]} ({it.get('eta_note', '')})"
        elif it.get("eta_note"):
            line += f" ({it['eta_note']})"
    else:
        line += "\n    -> no vendor match yet, defaulting to the primary weekly order"
    return line


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip())
        return 2
    cmd = args[0]

    if cmd == "add" and len(args) >= 2:
        rest = args[1:]
        urgent = "--urgent" in rest
        if urgent:
            rest.remove("--urgent")
        by = None
        if "--by" in rest:
            i = rest.index("--by")
            by = rest[i + 1]
            del rest[i:i + 2]
        iid = add(" ".join(rest), by=by, urgent=urgent)
        print(f"added #{iid}")
        return 0

    if cmd == "list":
        items = open_items()
        if not items:
            print("shopping list is empty")
            return 0
        for it in items:
            print(fmt(it))
        return 0

    if cmd == "ordered" and len(args) >= 2:
        key = resolve_vendor(" ".join(args[1:]))
        if not key:
            print(f"unrecognized vendor: {' '.join(args[1:])!r}; "
                  f"try one of {sorted(set(VENDOR_ALIASES.values()))}")
            return 1
        mark_ordered(key)
        print(f"recorded: ordered from {vendor_label(key)} today")
        return 0

    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
