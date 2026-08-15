"""Spending alerts: push the things worth knowing the moment they happen.

Run after each engine refresh (the watch job does this every 3 hours). Sends:

  - any new charge at or above the alert threshold ("$120 at a gas station")
  - Safe-to-Spend changing color (green -> amber -> red, or recovering)
  - a bill due within 2 days, once per bill per month
  - the paycheck actually landing (and a warning if payday passes without it)

Quiet hours are respected: nothing sends between 9pm and 7am; those alerts
just wait for the morning message.

State lives in state/alerts_seen.json so nothing alerts twice.

Usage:
    python3 scripts/alerts.py            # check and send
    python3 scripts/alerts.py --dry-run  # print what would send
"""

import json
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE_DIR = os.path.join(ROOT, "state")
STATUS = os.path.join(STATE_DIR, "status.json")
SEEN = os.path.join(STATE_DIR, "alerts_seen.json")
CONFIG = os.path.join(ROOT, "config", "budget.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send as tg_send  # noqa: E402


def money(x):
    return f"${abs(x):,.2f}"


def load_json(p, default):
    if not os.path.exists(p):
        return default
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def main():
    dry = "--dry-run" in sys.argv

    cfg = load_json(CONFIG, {})
    alerts_cfg = cfg.get("alerts", {})
    threshold = float(alerts_cfg.get("large_charge_threshold", 100))
    quiet_start = int(alerts_cfg.get("quiet_start_hour", 21))
    quiet_end = int(alerts_cfg.get("quiet_end_hour", 7))

    hour = datetime.now().hour
    in_quiet = hour >= quiet_start or hour < quiet_end
    if in_quiet and not dry:
        # Don't buzz phones at night. The morning message covers it.
        return 0

    status = load_json(STATUS, None)
    txdata = load_json(os.path.join(DATA, "raw_transactions.json"), None)
    if not status or not txdata:
        print("no data yet")
        return 0

    seen = load_json(SEEN, {"tx": [], "color": None, "bills": [], "paychecks": [],
                            "missed_payday": []})
    seen_tx = set(seen.get("tx", []))
    out = []

    today = date.today()
    recent_floor = (today - timedelta(days=4)).isoformat()
    txns = txdata["allTransactions"]["results"]

    # --- new large charges ---
    ignore = set(cfg.get("ignore_categories", []))
    for t in txns:
        tid = t.get("id")
        amt = t.get("amount") or 0
        if not tid or tid in seen_tx:
            continue
        if t["date"] < recent_floor:
            continue
        cat = ((t.get("category") or {}).get("name")) or "?"
        if amt <= -threshold and cat not in ignore:
            mer = ((t.get("merchant") or {}).get("name")) or "somewhere"
            out.append(f"🔔 New charge: {money(amt)} at {mer} ({cat}), {t['date'][5:]}")
            seen_tx.add(tid)
        elif amt <= -threshold:
            seen_tx.add(tid)  # transfer/CC payment: note it silently

    # --- paycheck landed / missed ---
    for t in txns:
        tid = t.get("id")
        cat = ((t.get("category") or {}).get("name")) or ""
        if cat == "Paychecks" and (t.get("amount") or 0) > 0 and t["date"] >= recent_floor:
            if tid not in seen.get("paychecks", []):
                out.append(f"💰 Paycheck landed: {money(t['amount'])} ({t['date'][5:]})")
                seen.setdefault("paychecks", []).append(tid)

    # Missed payday: expected payday passed yesterday with no paycheck since.
    try:
        payday = datetime.strptime(status["next_payday"], "%Y-%m-%d").date()
        prev_payday = payday - timedelta(days=int(cfg["income"]["period_days"]))
        if today == prev_payday + timedelta(days=1):
            landed = any(
                ((t.get("category") or {}).get("name")) == "Paychecks"
                and (t.get("amount") or 0) > 0
                and t["date"] >= prev_payday.isoformat()
                for t in txns
            )
            key = prev_payday.isoformat()
            if not landed and key not in seen.get("missed_payday", []):
                out.append(
                    f"⚠️ Expected paycheck on {key[5:]} hasn't shown up in Monarch yet. "
                    f"Worth a look."
                )
                seen.setdefault("missed_payday", []).append(key)
    except Exception:
        pass

    # --- color change ---
    color = status.get("status")
    prev = seen.get("color")
    if prev and color and color != prev:
        direction = {"green": "✅ Back in the green", "amber": "⚠️ Getting tight",
                     "red": "🛑 In the red"}[color]
        out.append(f"{direction}: Safe-to-Spend is now "
                   f"{'-' if status['safe_to_spend'] < 0 else ''}{money(status['safe_to_spend'])}.")
    seen["color"] = color

    # --- bills due soon ---
    for b in status.get("bills_due_before_payday") or []:
        due = datetime.strptime(b["due"], "%Y-%m-%d").date()
        days = (due - today).days
        key = f"{b['name']}:{b['due']}"
        if 0 <= days <= 2 and key not in seen.get("bills", []):
            when = {0: "today", 1: "tomorrow", 2: "in 2 days"}[days]
            out.append(f"📅 {b['name']} ({money(b['amount'])}) is due {when}, {b['due'][5:]}.")
            seen.setdefault("bills", []).append(key)

    # Trim state so it doesn't grow forever.
    seen["tx"] = list(seen_tx)[-500:]
    seen["bills"] = seen.get("bills", [])[-50:]
    seen["paychecks"] = seen.get("paychecks", [])[-20:]
    seen["missed_payday"] = seen.get("missed_payday", [])[-10:]

    if not out:
        print("nothing to alert")
    else:
        text = "\n".join(out)
        if dry:
            print(text)
        else:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            chat_ids = [c for c in (cfg.get("notify", {}).get("telegram_chat_ids") or []) if c]
            if token and chat_ids:
                for c in chat_ids:
                    tg_send(token, c, text)
                print(f"alerted {len(chat_ids)} chats:\n{text}")
            else:
                print(f"telegram not configured; would have sent:\n{text}")

    if not dry:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(SEEN, "w") as f:
            json.dump(seen, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
