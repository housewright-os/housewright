"""Energy watch: read the Shelly plugs, log usage, alert when it matters.

Read only. This never switches a plug on or off; v1 has no switching code at
all. Plugs and roles live in config/household.json.

What it does each run (the launchd job fires every 10 minutes):
  - polls each plug's local RPC (no cloud, no account, LAN only)
  - appends a line per plug to state/energy.jsonl
  - role "freezer": alerts on power loss or unreachability, repeats daily
    while bad, announces recovery. Deliberately ignores quiet hours: a warm
    freezer at 2am is worth waking for.
  - role "shiftable": one nudge per day if it draws over the configured
    threshold during the configured time-of-use peak window.

Usage:
    python3 scripts/energy.py --watch      # poll, log, alert (the cron entry)
    python3 scripts/energy.py --status     # human-readable right-now table
    python3 scripts/energy.py --report     # kWh and rough cost so far today
    python3 scripts/energy.py --watch --dry-run   # poll and print, send nothing
"""

import json
import os
import sys
import urllib.request
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOUSEHOLD = os.path.join(ROOT, "config", "household.json")
BUDGET = os.path.join(ROOT, "config", "budget.json")
LOG = os.path.join(ROOT, "state", "energy.jsonl")
SEEN = os.path.join(ROOT, "state", "energy_seen.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send as tg_send  # noqa: E402


def load_json(p, default):
    if not os.path.exists(p):
        return default
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def load_seen():
    """Alert state, always with every expected key. A file that fails to
    parse is preserved for inspection, never silently overwritten."""
    defaults = {"bad": {}, "bad_alert_date": {}, "peak_nudge": {}}
    if not os.path.exists(SEEN):
        return defaults
    try:
        with open(SEEN) as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("not a dict")
    except Exception:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(SEEN, f"{SEEN}.corrupt-{stamp}")
            print(f"energy_seen.json unreadable, preserved as .corrupt-{stamp}",
                  file=sys.stderr)
        except Exception:
            pass
        return defaults
    for k, v in defaults.items():
        if not isinstance(loaded.get(k), dict):
            loaded[k] = v
    return loaded


def save_seen(seen):
    os.makedirs(os.path.dirname(SEEN), exist_ok=True)
    tmp = SEEN + ".tmp"
    with open(tmp, "w") as f:
        json.dump(seen, f)
    os.replace(tmp, SEEN)


def energy_cfg():
    return load_json(HOUSEHOLD, {}).get("energy", {})


def poll_plug(ip):
    """One plug's Switch status, or None if unreachable."""
    try:
        with urllib.request.urlopen(f"http://{ip}/rpc/Switch.GetStatus?id=0", timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def record(name, ip, st):
    row = {"ts": datetime.now().isoformat(timespec="seconds"), "name": name, "ip": ip,
           "ok": st is not None}
    if st is not None:
        row.update({
            "on": bool(st.get("output")),
            "w": round(float(st.get("apower") or 0.0), 1),
            "wh_total": float((st.get("aenergy") or {}).get("total") or 0.0),
            "temp_c": (st.get("temperature") or {}).get("tC"),
        })
    return row


def broadcast(text, dry):
    if dry:
        print(f"[would send] {text}")
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [c for c in (load_json(BUDGET, {}).get("notify", {})
                            .get("telegram_chat_ids") or []) if c]
    if token and chat_ids:
        for c in chat_ids:
            tg_send(token, c, text)
    else:
        print(f"telegram not configured; would have sent: {text}")


def watch(dry=False):
    cfg = energy_cfg()
    plugs = cfg.get("plugs", [])
    if not plugs:
        print("no plugs configured")
        return 0

    seen = load_seen()
    now = datetime.now()
    today = date.today().isoformat()
    peak = cfg.get("peak", {})
    in_peak = int(peak.get("start_hour", 16)) <= now.hour < int(peak.get("end_hour", 21))
    rows = []

    for p in plugs:
        name, ip = p.get("name"), p.get("ip")
        if not name or not ip:
            print(f"skipping malformed plug entry: {p}", file=sys.stderr)
            continue
        role = p.get("role", "unassigned")
        st = poll_plug(ip)
        row = record(name, ip, st)
        rows.append(row)

        bad = st is None or not st.get("output")
        was_bad = bool(seen["bad"].get(name))

        if role == "freezer":
            if bad and not was_bad:
                why = "is unreachable" if st is None else "has its output OFF"
                broadcast(f"🧊🛑 *Freezer plug {name} {why}.* The freezer has no "
                          f"power. Worth checking now.", dry)
                seen["bad_alert_date"][name] = today
            elif bad and was_bad and seen["bad_alert_date"].get(name) != today:
                since = seen["bad"][name][5:16].replace("T", " ")
                broadcast(f"🧊🛑 Freezer plug {name} is still without power "
                          f"(since {since}).", dry)
                seen["bad_alert_date"][name] = today
            elif not bad and was_bad:
                broadcast(f"🧊✅ Freezer plug {name} is back on power.", dry)

        if role == "shiftable" and in_peak and st is not None:
            watts = float(st.get("apower") or 0)
            if watts >= float(peak.get("nudge_watts", 500)) \
                    and seen["peak_nudge"].get(name) != today:
                broadcast(f"⚡ {name} is pulling {watts:.0f}W during the "
                          f"{peak.get('start_hour', 16)}:00-{peak.get('end_hour', 21)}:00 "
                          f"peak, when your utility charges the most. If it can wait "
                          f"until after {peak.get('end_hour', 21)}:00, that is real money.", dry)
                seen["peak_nudge"][name] = today

        seen["bad"][name] = (seen["bad"].get(name) or row["ts"]) if bad else None

    if not dry:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        save_seen(seen)
    print(f"polled {len(rows)} plug(s): "
          + ", ".join(f"{r['name']}={'?' if not r['ok'] else (str(r['w']) + 'W' if r.get('on') else 'off')}"
                      for r in rows))
    return 0


def today_summary():
    """(total_kwh, est_cost, {name: kwh}) for today, or None if no data yet."""
    cfg = energy_cfg()
    if not os.path.exists(LOG):
        return None
    today = date.today().isoformat()
    wh = {}
    with open(LOG) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("ok") and r["ts"][:10] == today and "wh_total" in r:
                wh.setdefault(r["name"], []).append(r["wh_total"])
    per = {n: max(0.0, (max(v) - min(v)) / 1000.0) for n, v in wh.items() if len(v) >= 2}
    if not per:
        return None
    total = sum(per.values())
    return total, total * float(cfg.get("rate_per_kwh", 0.30)), per


def main():
    dry = "--dry-run" in sys.argv

    if "--watch" in sys.argv:
        return watch(dry)

    if "--status" in sys.argv:
        for p in energy_cfg().get("plugs", []):
            st = poll_plug(p.get("ip")) if p.get("ip") else None
            name, ip = p.get("name", "?"), p.get("ip", "?")
            if st is None:
                print(f"{name:12} {ip:15} UNREACHABLE  role={p.get('role')}")
            else:
                state = f"{st.get('apower', 0):.0f}W" if st.get("output") else "off"
                print(f"{name:12} {ip:15} {state:>8}  "
                      f"lifetime {(st.get('aenergy') or {}).get('total', 0)/1000:.1f}kWh  "
                      f"role={p.get('role')}")
        return 0

    if "--report" in sys.argv:
        s = today_summary()
        if not s:
            print("no usage data for today yet")
            return 0
        total, cost, per = s
        for n, k in sorted(per.items()):
            print(f"{n}: {k:.2f} kWh")
        print(f"total today: {total:.2f} kWh (~${cost:.2f} est)")
        return 0

    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
