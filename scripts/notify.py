"""Morning and evening Telegram briefs: the household chief-of-staff push.

Morning is the number plus the day (dinner pick, top of the house list).
Evening is tomorrow's preview. Sends nothing if TELEGRAM_BOT_TOKEN is unset
or no chat ids are configured, so it is safe to schedule before setup is
finished.

Usage:
    python3 scripts/notify.py            # send the morning message
    python3 scripts/notify.py --dry-run  # print it, send nothing
    python3 scripts/notify.py --weekly   # send the Sunday review instead
    python3 scripts/notify.py --evening  # send the 8:30pm wrap instead
"""

import glob
import json
import os
import re
import sys
import shutil
import urllib.parse
import urllib.request
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "status.json")
CONFIG = os.path.join(ROOT, "config", "budget.json")
WEEKLY = os.path.join(ROOT, "state", "weekly.md")
HOUSEHOLD = os.path.join(ROOT, "config", "household.json")


def _gog():
    return shutil.which("gog") or "/opt/homebrew/bin/gog"


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks  # noqa: E402


def money(x):
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"


def household_cfg():
    try:
        with open(HOUSEHOLD) as f:
            return json.load(f)
    except Exception:
        return {}


def dinner_pick(day):
    """Deterministic nightly pick from the current week's dinner pool.

    The meal plan lives wherever meal_plan_dir points. On a machine without
    that path, or with no plan yet, this quietly returns None and the brief
    just omits the dinner line.
    """
    d = household_cfg().get("meal_plan_dir") or ""
    plans = sorted(glob.glob(os.path.join(d, "meal_plan_*.md"))) if d else []
    if not plans:
        return None
    try:
        with open(plans[-1]) as f:
            text = f.read()
        names = [n.split(" (")[0].strip() for n in
                 re.findall(r"^###\s*\d+\.\s*(.+)$", text, re.M)]
        names = [n for n in names if n and not n.lower().startswith("bonus")]
        return names[day.toordinal() % len(names)] if names else None
    except Exception:
        return None


def family_events_lines(day_flag):
    """Today's (--today) or tomorrow's (--tomorrow) entries on the shared
    Family calendar, via gog. Quietly returns [] when the calendar is not
    configured, gog is missing, or the network is slow: the money message
    must never wait on Google."""
    fe = household_cfg().get("family_events") or {}
    cal, acct = fe.get("calendar_id"), fe.get("account")
    if not (cal and acct):
        return []
    import subprocess
    try:
        out = subprocess.run(
            [_gog(), "-a", acct, "calendar", "events", cal,
             day_flag, "-j", "--results-only"],
            capture_output=True, text=True, timeout=15).stdout
        items = json.loads(out)
        if isinstance(items, dict):
            items = items.get("items") or []
    except Exception:
        return []
    lines = []
    for ev in items:
        summary = (ev.get("summary") or "").strip()
        if not summary:
            continue
        start = ev.get("start") or {}
        dt = start.get("dateTime") or ""
        if dt:
            try:
                from datetime import datetime as _dt
                when = f"{_dt.fromisoformat(dt):%-I:%M%p}".lower()
            except ValueError:
                when = ""
        else:
            when = "all day"
        lines.append(f"{when} {summary}".strip())
    return lines


def morning_extras():
    """Chief-of-staff lines appended after the money block."""
    lines = []
    pick = dinner_pick(date.today())
    if pick:
        lines += ["", f"🍽 Tonight's pick: {pick}"]
    count = int((household_cfg().get("brief") or {}).get("morning_task_count") or 3)
    t = tasks.brief_lines(count)
    if t:
        lines += ["", "*House list:*"] + [f"  {x}" for x in t]
    fam = family_events_lines("--today")
    if fam:
        lines += ["", "*Family today:*"] + [f"  {x}" for x in fam]
    return lines


def compose_evening(s):
    """The 8:30pm wrap: where the day ended and what tomorrow needs."""
    tomorrow = date.today() + timedelta(days=1)
    lines = ["🌙 *Evening wrap*"]

    if s:
        fresh = freshness_note(s)
        if fresh:
            lines.append(fresh)
        if s["safe_to_spend"] >= 0:
            lines.append(f"Safe to spend: {money(s['safe_to_spend'])}")
        else:
            lines.append(f"Short by {money(-s['safe_to_spend'])} before payday")
        for b in (s.get("bills_due_before_payday") or []):
            if b["due"] == tomorrow.isoformat():
                lines.append(f"📅 {b['name']} ({money(b['amount'])}) comes out tomorrow.")

    pick = dinner_pick(tomorrow)
    if pick:
        lines.append(f"🍽 Tomorrow's pick: {pick}. Anything frozen, move it down tonight.")

    open_t = tasks.open_tasks()
    if open_t:
        top = "; ".join(t["text"] for t in open_t[:2])
        lines.append(f"📋 Still open: {len(open_t)}. Top: {top}")

    fam = family_events_lines("--tomorrow")
    if fam:
        lines.append("👨‍👩‍👧‍👦 Family tomorrow: " + "; ".join(fam[:3]))

    try:
        import pantry  # lazy; the wrap never breaks on a missing lane
        aging = pantry.aging_lines()
        if aging:
            lines.append("🥬 Eat first: " + "; ".join(aging))
    except Exception:
        pass

    try:
        import energy  # local module; lazy so notify never breaks without it
        es = energy.today_summary()
        if es:
            total, cost, _ = es
            lines.append(f"⚡ Plugs today: {total:.1f} kWh (~${cost:.2f} est)")
    except Exception:
        pass

    return "\n".join(lines)


def freshness_note(s):
    """One honest line when the numbers are not fresh: a failed bank pull or
    an old status file must never masquerade as this morning's truth."""
    from datetime import datetime as _dt
    notes = []
    if s.get("pull_ok") is False:
        notes.append("⚠️ bank sync failed; numbers may lag")
    gen = s.get("generated_at") or ""
    try:
        age_min = (_dt.now() - _dt.fromisoformat(gen)).total_seconds() / 60
        max_age = int((household_cfg().get("brief") or {}).get("max_age_minutes") or 120)
        if age_min > max_age:
            notes.append(f"⚠️ numbers as of {_dt.fromisoformat(gen):%a %-I:%M%p}")
    except ValueError:
        pass
    return " · ".join(notes)


def compose(s):
    icon = {"green": "✅", "amber": "⚠️", "red": "\U0001f6d1"}[s["status"]]
    days = s["days_until_payday"]

    lines = [f"{icon} *Safe to spend: {money(s['safe_to_spend'])}*"]
    fresh = freshness_note(s)
    if fresh:
        lines.append(fresh)

    if s["safe_to_spend"] >= 0 and days > 0:
        lines.append(f"_{money(s['safe_per_day'])}/day for {days} days_")
    elif s["safe_to_spend"] < 0:
        lines.append(f"_Short by {money(-s['safe_to_spend'])} before payday_")

    lines.append("")
    lines.append(f"Checking: {money(s['balance'])}")

    bills = s.get("bills_due_before_payday") or []
    if bills:
        lines.append("")
        lines.append("*Due before payday:*")
        for b in bills:
            lines.append(f"  {b['due'][5:]}  {money(b['amount'])}  {b['name']}")

    lines.append("")
    lines.append(
        f"Payday {s['next_payday'][5:]} ({days}d): +{money(s['expected_paycheck'])}"
    )

    d = s.get("discretionary") or {}
    if d:
        rem = d.get("period_remaining", 0)
        tag = "left" if rem >= 0 else "over"
        lines.append(f"Discretionary this period: {money(abs(rem))} {tag}")

    return "\n".join(lines)


def send(token, chat_id, text):
    """Send with Markdown, falling back to plain text so an urgent alert is
    never dropped over a formatting rejection."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    err = "non-200 response"
    for params in (
        {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        {"chat_id": chat_id, "text": text},
    ):
        data = urllib.parse.urlencode(params).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=20) as r:
                if r.status == 200:
                    return True
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    print(f"send failed for {chat_id}: {err}", file=sys.stderr)
    return False


def main():
    dry = "--dry-run" in sys.argv
    weekly = "--weekly" in sys.argv
    evening = "--evening" in sys.argv

    if weekly:
        if not os.path.exists(WEEKLY):
            print("No weekly review yet. Run: python3 scripts/weekly.py")
            return 2
        with open(WEEKLY) as f:
            text = f.read()
        try:
            with open(STATE) as f:
                fresh = freshness_note(json.load(f))
            if fresh:
                text = fresh + "\n" + text
        except Exception:
            pass
    elif evening:
        s = None
        try:
            if os.path.exists(STATE):
                with open(STATE) as f:
                    s = json.load(f)
        except Exception:
            s = None  # degrade: the wrap still carries dinner/tasks/energy
        text = compose_evening(s)
    else:
        if not os.path.exists(STATE):
            print("No status yet. Run: python3 scripts/engine.py")
            return 2
        with open(STATE) as f:
            text = compose(json.load(f))
        # The money message must go out even if every extra breaks.
        try:
            extras = morning_extras()
        except Exception as e:
            print(f"brief extras skipped: {type(e).__name__}: {e}", file=sys.stderr)
            extras = []
        if extras:
            text += "\n" + "\n".join(extras)

    if dry:
        print(text)
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    with open(CONFIG) as f:
        chat_ids = [c for c in json.load(f)["notify"]["telegram_chat_ids"] if c]

    if not token or not chat_ids:
        print("Telegram not configured yet (need TELEGRAM_BOT_TOKEN and chat ids).")
        print("Message that would have been sent:\n")
        print(text)
        return 0

    ok = sum(1 for c in chat_ids if send(token, c, text))
    print(f"sent to {ok}/{len(chat_ids)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
