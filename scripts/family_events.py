#!/usr/bin/env python3
"""family_events - capture household/family events from Gmail onto the shared
Family calendar, so a coach's "game moved to 5pm Saturday" email becomes a
calendar entry everyone in the Google family group sees without anyone
retyping it.

Pipeline (a pattern proven by an earlier email-to-calendar lane in this household):

  1. gog gmail search on the family inbox (deny-listed senders excluded in
     the query itself), newest first, capped per run.
  2. For each NEW message, a headless Sonnet call extracts zero or more
     real-world family events (kids' sports, school, medical, activities,
     service appointments). Most emails contain none; the prompt is strict.
  3. High-confidence future events are created on the shared Family calendar
     via gog, tagged [auto] with the source gmail id for provenance.
     Low-confidence extractions, and any event whose calendar write fails,
     become a task on the house board instead ("Confirm event: ..."), so
     nothing extracted is ever silently lost.
  4. A Telegram digest lists everything filed or tasked. Digests respect the
     house quiet hours (21:00-07:00, same rule as alerts.py): calendar and
     task writes still happen at night, but the phone buzz waits for morning.

Email content is treated as data only: nothing here follows links, replies,
or forwards, and URLs are scrubbed from anything written to the shared
calendar. The only outputs are calendar entries, house tasks, and the digest.

Idempotent three ways: processed gmail ids, a title+start fingerprint per
created event (reminder emails about the same game never double-file), and a
3-strikes failure cap so one broken message can never wedge the scanner.
Times are normalized to the household timezone's wall time in code, not trusted
from the model's offset guess (which gets DST wrong for far-future dates).

Runs via launchd (com.housewright.familyevents, every 30 min).

Usage: family_events.py [--dry-run]
"""

import datetime
import fcntl
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "household.json")
BUDGET = os.path.join(ROOT, "config", "budget.json")
STATE_PATH = os.path.join(ROOT, "state", "family_events_seen.json")
LOCKFILE = STATE_PATH + ".lock"

GOG = shutil.which("gog") or "/opt/homebrew/bin/gog"


def _tz():
    """Household timezone from config; falls back to the system zone. A
    hardcoded zone would silently misfile every event for any other house."""
    try:
        with open(CONFIG) as f:
            name = (json.load(f).get("timezone") or "").strip()
        if name:
            return ZoneInfo(name)
    except Exception:
        pass
    return datetime.datetime.now().astimezone().tzinfo


TZ = _tz()
QUIET_START, QUIET_END = 21, 7  # same window alerts.py enforces
MAX_STRIKES = 3
DRY_RUN = "--dry-run" in sys.argv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks  # noqa: E402

EXTRACT_PROMPT = """You scan ONE email for real-world scheduled events relevant to running a family household with kids: kids' sports (games, practices, tournaments), school events (conferences, back-to-school night, minimum days, picture day, performances), medical or dental appointments, kids' activities (scouts, classes, recitals, camps), and household service appointments that need someone home (repair visit, installation, delivery window). NOT relevant: marketing, sales, webinars, job or career mail, politics, general newsletters with no dated commitment, streaming or content schedules, receipts, shipping notices without an appointment.

Today is {today} in the household timezone {tz}.

Output ONLY minified JSON, no prose, no markdown fences:
{{"events":[{{"title":str,"who":str,"start_iso":str,"end_iso":str,"location":str,"notes":str,"confidence":"high"|"low"}}]}}

Rules:
- Empty events list if nothing qualifies. Most emails have none; be strict.
- title: short and specific ("Baseball game vs Storm", "Dentist cleaning"). who: which family member it is for if the email says ("Riley"), else "".
- start_iso and end_iso: ISO 8601 local time as stated in the email (offsets are normalized downstream, do not convert timezones yourself; if the email states a timezone different from the household's, convert the wall time to the household timezone). No end time given: use start plus 1 hour. Date but no time: use 09:00 and set confidence "low".
- Only future events (today or later). Omit past events entirely.
- confidence "high" only when the date, the time, and what-it-is are all explicit in the email. Anything inferred or ambiguous: "low".
- location: venue or address if stated, else "".
- notes: one short line of source context (team name, teacher, provider), no URLs.

EMAIL:
"""

QUICKADD_PROMPT = """A family member is directly telling you, right now, about ONE thing to put on the calendar (typed by hand, not an email) -- trust it more than the email pipeline: a short deliberate request like this is usually exactly what they mean.

Today is {today} in the household timezone {tz}.

Output ONLY minified JSON, no prose, no markdown fences:
{{"events":[{{"title":str,"who":str,"start_iso":str,"end_iso":str,"location":str,"notes":str,"confidence":"high"|"low"}}]}}

Rules:
- Normally exactly one event; more than one only if the message clearly lists several.
- title: short and specific. who: which family member if named, else "".
- start_iso: best-effort ISO 8601 local time from what was said ("tuesday 4pm" means the next Tuesday at 16:00, "tomorrow morning" means tomorrow at 09:00). A bare weekday with no other date context always means the NEXT occurrence of that weekday, never one that has already passed. No end time stated: use start plus 1 hour. A day with no time at all: use 09:00.
- confidence "high" whenever a day and a time are both stated, even loosely ("tuesday 4pm" counts as both). "low" only if the day itself is genuinely unclear.
- location: venue if stated, else "". notes: "".
- If the message truly has no event in it, return an empty events list.

MESSAGE:
"""


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def cfg():
    with open(CONFIG) as f:
        return json.load(f).get("family_events") or {}


def quiet_now():
    h = datetime.datetime.now(TZ).hour
    return h >= QUIET_START or h < QUIET_END


def scrub(s):
    """Strip URLs from email-derived text before it reaches the shared
    calendar or the digest. Email content is data, never a link to follow."""
    return re.sub(r"(?:https?://|www\.)\S+", "", s or "").strip()


@contextmanager
def _locked(timeout=None):
    """Exclusive lock around a read-modify-write cycle on state/family_events_seen.json.
    Needed once the bot's add_manual_event() became a second writer alongside
    the cron scan; the same file-lock pattern tasks.py already uses.

    timeout=None: block until acquired (the cron run; nothing else is timing
    on it). A number: poll non-blocking up to that many seconds, then raise
    TimeoutError -- used by the interactive bot path so a family member never
    silently hangs waiting for a 30-minute cron run to release the lock."""
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(LOCKFILE, "w") as lk:
        if timeout is None:
            fcntl.flock(lk, fcntl.LOCK_EX)
        else:
            deadline = datetime.datetime.now() + datetime.timedelta(seconds=timeout)
            while True:
                try:
                    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if datetime.datetime.now() >= deadline:
                        raise TimeoutError("family calendar is busy (a scan is running)")
                    import time as _time
                    _time.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def load_state():
    """Returns (state_dict, was_reset). A corrupt state file is quarantined,
    never silently discarded: both dedup layers live in it, and losing them
    would re-file 3 days of events onto the calendar the kids see."""
    empty = {"messages": [], "filed": [], "tasked": [],
             "failures": {}, "pending_digest": [],
             "calendar_seen": [], "calendar_seen_initialized": False}
    if not os.path.exists(STATE_PATH):
        return empty, False
    try:
        with open(STATE_PATH) as f:
            d = json.load(f)
        for k, v in empty.items():
            d.setdefault(k, v)
        return d, False
    except (OSError, json.JSONDecodeError, ValueError):
        stamp = f"{datetime.datetime.now():%Y%m%d-%H%M%S}"
        try:
            os.replace(STATE_PATH, STATE_PATH + f".corrupt-{stamp}")
            log(f"STATE CORRUPT: preserved as family_events_seen.json.corrupt-{stamp}")
        except OSError:
            log("STATE CORRUPT and could not be preserved")
        return empty, True


def save_state(st):
    if DRY_RUN:
        return
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"messages": sorted(set(st["messages"])),
                   "filed": sorted(set(st["filed"])),
                   "tasked": sorted(set(st["tasked"])),
                   "failures": st["failures"],
                   "pending_digest": st["pending_digest"],
                   "calendar_seen": sorted(set(st.get("calendar_seen") or [])),
                   "calendar_seen_initialized": bool(st.get("calendar_seen_initialized"))}, f)
    os.replace(tmp, STATE_PATH)


def build_query(c):
    base = c.get("query") or "in:inbox category:primary newer_than:3d"
    denies = []
    for d in c.get("deny_from") or []:
        # A malformed entry (spaces, leading -) would corrupt the whole
        # query, silently zeroing results. Skip and say so instead.
        if re.fullmatch(r"[A-Za-z0-9@._+-]+", d) and not d.startswith(("-", "+")):
            denies.append(f"-from:{d}")
        else:
            log(f"deny_from entry skipped as unsafe: {d!r}")
    return " ".join([base] + denies)


def gog_search(account, query, max_results):
    r = subprocess.run(
        [GOG, "-a", account, "gmail", "search", query, "--max", str(max_results), "-p"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        # Distinguish "outage/expired auth" from "genuinely empty inbox";
        # otherwise a dead OAuth token reads as eternal calm.
        log(f"SEARCH FAILED (rc={r.returncode}): {r.stderr.strip()[:200]}")
        return None
    ids = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4 or parts[0] in ("ID",) or line.startswith("#"):
            continue
        ids.append(parts[0])
    return ids


def get_email_text(account, msg_id):
    """Returns (status, text). status: "ok", "gone" (deleted or truly empty,
    permanent), or "error" (transient, retryable). Attachment-only emails
    (.ics invites, image flyers) fall back to the Subject line, which often
    carries the whole event ("Game Saturday 9:30 Field 3")."""
    r = subprocess.run(
        [GOG, "-a", account, "gmail", "get", msg_id, "--plain"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        gone = "notFound" in r.stderr or "404" in r.stderr
        return ("gone" if gone else "error"), ""
    text = re.sub(r"<[^<]+?>", " ", r.stdout)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if text:
        return "ok", text[:8000]  # newsletters run long; keep the Sonnet call bounded
    r2 = subprocess.run(
        [GOG, "-a", account, "gmail", "get", msg_id],
        capture_output=True, text=True, timeout=30,
    )
    m = re.search(r"^Subject:\s*(.+)$", r2.stdout, re.M) if r2.returncode == 0 else None
    if m and m.group(1).strip():
        return "ok", f"(Body was empty or attachment-only.) Subject: {m.group(1).strip()}"
    return "gone", ""


def strip_fences(s):
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def llm_cmd(c):
    """The extractor command, configurable so the lane is not welded to one
    vendor: family_events.llm_cmd in household.json (a list). Default: the
    Claude CLI emitting a JSON envelope; any CLI that prints the schema JSON
    (bare, or wrapped in a result envelope) works."""
    cmd = c.get("llm_cmd")
    if isinstance(cmd, list) and cmd:
        return cmd
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    return [claude, "-p", "--model", "sonnet", "--output-format", "json"]


def extract(text, cmd, prompt=None):
    if prompt is None:
        prompt = EXTRACT_PROMPT.format(today=datetime.date.today().isoformat(), tz=TZ)
    proc = subprocess.run(
        cmd,
        input=prompt + text,
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"claude rc={proc.returncode}: {proc.stderr.strip()[:200]}")
    outer = json.loads(strip_fences(proc.stdout))
    if isinstance(outer, dict) and "events" in outer:
        data = outer  # bare schema JSON from a non-enveloping CLI
    else:
        try:
            data = json.loads(strip_fences(outer["result"]))
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"bad extractor output: {str(proc.stdout)[:200]!r}") from e
    events = data.get("events")
    # Malformed-but-parseable JSON must strike out like any other failure,
    # not crash the loop and wedge every message queued behind this one.
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        raise RuntimeError(f"extractor returned non-event shape: {str(events)[:120]!r}")
    return events


def normalize_local(iso):
    """Parse an ISO string and pin its WALL TIME to the household timezone.
    The email states a local wall clock; the model's offset guess is
    discarded because it gets DST wrong for far-future dates, and a naive
    string would otherwise drift by the household UTC offset when gog hands
    it to Google."""
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=TZ)


def fingerprint(label, start_dt):
    # Normalized wall-minute key: reminder emails about the same game match
    # even when their ISO strings differ in offset or seconds.
    key = f"{label.strip().lower()}|{start_dt:%Y-%m-%dT%H:%M}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def create_event(account, calendar_id, label, start_dt, end_iso, location, notes, msg_id):
    end_dt = normalize_local(end_iso) if end_iso else None
    if end_dt is None or end_dt <= start_dt:
        end_dt = start_dt + datetime.timedelta(hours=1)
    desc_bits = ["[auto] filed by household OS"]
    if notes:
        desc_bits.append(notes)
    src = msg_id if str(msg_id).startswith("manual:") else f"gmail {msg_id}"
    desc_bits.append(f"source: {src}")
    args = ["calendar", "create", calendar_id,
            "--summary", label,
            "--from", start_dt.isoformat(), "--to", end_dt.isoformat(),
            "--description", "\n".join(desc_bits)]
    if location:
        args += ["--location", location]
    if DRY_RUN:
        log(f"  DRY-RUN would create: {label} @ {start_dt.isoformat()}")
        return True
    try:
        r = subprocess.run([GOG, "-a", account] + args,
                           capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as e:
        # A hung/missing gog must degrade to "a human decides" (the caller's
        # not-confident path), never propagate out of a locked state section.
        log(f"  FAILED (event {label}): {type(e).__name__}: {e}")
        return False
    if r.returncode != 0:
        log(f"  FAILED (event {label}): {r.stderr.strip()[:200]}")
        return False
    return True


def flush_digest(st):
    """Send pending + new digest lines, unless it is quiet hours: the
    calendar writes already happened, only the phone buzz waits for 7am."""
    lines = st["pending_digest"]
    if not lines:
        return
    if quiet_now():
        log(f"digest deferred (quiet hours): {len(lines)} line(s) pending")
        return
    if DRY_RUN:
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    try:
        with open(BUDGET) as f:
            chat_ids = [c for c in json.load(f)["notify"]["telegram_chat_ids"] if c]
    except (OSError, KeyError, json.JSONDecodeError):
        chat_ids = []
    if not token or not chat_ids:
        log("digest (telegram not configured):\n" + "\n".join(lines))
        st["pending_digest"] = []
        return
    import notify
    text = "📅 *Family calendar update*\n" + "\n".join(lines)
    if any(notify.send(token, c, text) for c in chat_ids):
        st["pending_digest"] = []


def add_manual_event(text, requested_by=None, dry_run=False):
    """A family member's direct request to the bot to add one thing to the
    calendar (not from the email pipeline). Shares locking, state, and the
    fingerprint dedup scheme with the email pipeline, so a later email about
    the same thing recognizes it as already filed instead of double-booking.
    The quiet-hours-aware family digest still fires normally; this also
    returns a result for an immediate reply to whoever asked.

    dry_run=True skips the real calendar/task writes and reports what would
    have happened, for safe manual testing (mirrors the CLI's --dry-run).

    The lock wait is bounded (8s): a family member's phone should never hang
    silently behind a 30-minute cron run; it gets a "busy, try again" reply
    instead. Each event is processed independently so one bad event in a
    rare multi-event message can't discard another's already-filed state."""
    c = cfg()
    account, calendar_id = c.get("account") or "", c.get("calendar_id") or ""
    if not account or not calendar_id:
        return {"status": "unconfigured", "results": []}

    cmd = llm_cmd(c)
    prompt = QUICKADD_PROMPT.format(today=datetime.date.today().isoformat(), tz=TZ)
    try:
        events = extract(text, cmd, prompt=prompt)
    except Exception as e:
        return {"status": "error", "results": [], "error": f"{type(e).__name__}: {e}"}
    if not events:
        return {"status": "empty", "results": []}

    results = []
    try:
        with _locked(timeout=8):
            st, _ = load_state()
            filed, tasked = set(st["filed"]), set(st["tasked"])
            today = datetime.datetime.now(TZ).date()
            for ev in events:
                try:
                    title = scrub(ev.get("title") or "")
                    who = scrub(ev.get("who") or "")
                    location = scrub(ev.get("location") or "")
                    notes = scrub(ev.get("notes") or "")
                    start_dt = normalize_local(ev.get("start_iso") or "")
                    if not title or start_dt is None:
                        continue
                    label = f"{who}: {title}" if who else title
                    when = f"{start_dt:%a %b %-d %-I:%M%p}"

                    if start_dt.date() < today:
                        results.append({"status": "past", "label": label, "when": when})
                        continue

                    fp = fingerprint(label, start_dt)
                    if fp in filed:
                        results.append({"status": "duplicate", "label": label, "when": when})
                        continue

                    confident = ev.get("confidence") == "high"
                    msg_id = f"manual:{requested_by or 'family'}:{fp}"
                    created = dry_run or (confident and create_event(
                        account, calendar_id, label, start_dt,
                        ev.get("end_iso") or "", location, notes, msg_id))
                    if confident and created:
                        filed.add(fp)
                        tasked.discard(fp)
                        results.append({"status": "filed", "label": label, "when": when,
                                        "location": location})
                        st["pending_digest"].append(
                            f"➕ {label}, {when}" + (f", {location}" if location else "")
                            + (f" (added by {requested_by})" if requested_by else ""))
                    else:
                        if fp not in tasked and not dry_run:
                            tasks.add(f"Confirm event: {label}, {when}?", by=requested_by)
                            tasked.add(fp)
                        results.append({"status": "tasked", "label": label, "when": when})
                        st["pending_digest"].append(f"❓ needs confirm: {label}, {when}")
                except Exception as e:
                    log(f"  manual-add event failed: {type(e).__name__}: {e}")
                    results.append({"status": "error", "label": scrub(ev.get("title") or "?"),
                                    "when": ""})

            if not dry_run:
                st["filed"], st["tasked"] = sorted(filed), sorted(tasked)
                flush_digest(st)  # mutates st in place; respects quiet hours
                save_state(st)
    except TimeoutError:
        return {"status": "busy", "results": []}

    return {"status": "ok", "results": results}


AUTO_MARKER = "[auto] filed by household OS"
CALENDAR_LOOKAHEAD_DAYS = 180


def list_calendar_events(account, calendar_id):
    """Upcoming events on the shared calendar, or None on a fetch failure.
    None must never be treated as an empty calendar: that would make the
    next successful poll look like every existing event just got added."""
    today = datetime.date.today()
    end = today + datetime.timedelta(days=CALENDAR_LOOKAHEAD_DAYS)
    r = subprocess.run(
        [GOG, "-a", account, "calendar", "events", calendar_id,
         "--from", today.isoformat(), "--to", end.isoformat(),
         "--max", "250", "--all-pages", "-j", "--results-only"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        log(f"CALENDAR FETCH FAILED (rc={r.returncode}): {r.stderr.strip()[:200]}")
        return None
    try:
        items = json.loads(r.stdout)
        return items if isinstance(items, list) else (items.get("items") or [])
    except (json.JSONDecodeError, TypeError) as e:
        log(f"CALENDAR FETCH PARSE FAILED: {type(e).__name__}: {e}")
        return None


def fmt_event_when(ev):
    start = ev.get("start") or {}
    raw = start.get("dateTime") or start.get("date")
    if not raw:
        return "?"
    try:
        if "T" in raw:
            dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(TZ)
            return f"{dt:%a %b %-d %-I:%M%p}"
        return datetime.date.fromisoformat(raw).strftime("%a %b %-d (all day)")
    except ValueError:
        return raw


def scan_calendar(account, calendar_id, st):
    """Alert the family the moment a new event appears on the shared
    calendar, however it got there. Athena's own creations (both the email
    pipeline and the bot's add_manual_event) are recognized by the
    AUTO_MARKER already stamped in their description and are recorded as
    seen without a second digest line, since their own path already
    announced them. Everything else -- someone adding, accepting, or
    importing an event straight in Google Calendar -- gets a fresh alert.

    A fetch failure leaves state untouched (see list_calendar_events); a
    corrupt/reset state file replays the whole calendar as new, which
    load_state's own quarantine-on-corruption behavior already guards
    against re-happening from bad data, not from a mid-run crash here."""
    events = list_calendar_events(account, calendar_id)
    if events is None:
        return
    seen = set(st.get("calendar_seen") or [])
    first_run = not st.get("calendar_seen_initialized")
    for ev in events:
        eid = ev.get("id")
        if not eid:
            log(f"  calendar event with no id, skipping: {scrub(ev.get('summary') or '?')}")
            continue
        if eid in seen:
            continue
        seen.add(eid)
        if first_run:
            continue  # baseline pass: record what's already there, alert on none of it
        if AUTO_MARKER in (ev.get("description") or ""):
            continue  # Athena filed this herself; already announced
        summary = scrub(ev.get("summary") or "(untitled)")
        who = (ev.get("creator") or {}).get("email") or ""
        who = who.split("@")[0] if who and who != account else ""
        when = fmt_event_when(ev)
        tag = f" (added by {who})" if who else ""
        st["pending_digest"].append(f"📅 New on the calendar: {summary}, {when}{tag}")
    st["calendar_seen"] = sorted(seen)
    st["calendar_seen_initialized"] = True


def main():
    c = cfg()
    account = c.get("account") or ""
    calendar_id = c.get("calendar_id") or ""
    if not account:
        log("family_events not configured (config/household.json family_events.account)")
        return 0

    cmd = llm_cmd(c)
    with _locked():
        st, was_reset = load_state()
        if was_reset:
            # Both dedup layers just vanished: everything in the lookback
            # window would re-file as a duplicate. One propose-only run.
            log("state was reset: calendar filing paused this run, confirm tasks only")

        if calendar_id and not DRY_RUN:
            scan_calendar(account, calendar_id, st)
            save_state(st)  # persist calendar_seen now: a crash before the
            # Gmail loop's own save_state must not re-alert the same event

        query = build_query(c)
        ids = gog_search(account, query, int(c.get("max_per_run") or 15))
        if ids is None:
            flush_digest(st)
            save_state(st)
            return 1
        new_ids = [i for i in ids if i not in st["messages"]]
        if not new_ids:
            log("clear: no new mail to scan")
            flush_digest(st)
            save_state(st)
            return 0

        log(f"{len(new_ids)} new message(s) to scan")
        filed, tasked = set(st["filed"]), set(st["tasked"])
        for msg_id in new_ids:
            status, text = get_email_text(account, msg_id)
            events = None
            if status == "gone":
                log(f"  gone/empty, skipping permanently: {msg_id}")
                st["messages"].append(msg_id)
                save_state(st)
                continue
            if status == "ok":
                try:
                    events = extract(text, cmd)
                except Exception as e:
                    log(f"  EXTRACT FAILED for {msg_id}: {type(e).__name__}: {e}")
            if events is None:
                # Transient failure: retry next run, give up after 3 strikes
                # so one broken message can never wedge the scanner forever.
                n = st["failures"].get(msg_id, 0) + 1
                st["failures"][msg_id] = n
                if n >= MAX_STRIKES:
                    log(f"  giving up after {n} failures: {msg_id}")
                    st["messages"].append(msg_id)
                    st["failures"].pop(msg_id, None)
                save_state(st)
                continue

            try:
                for ev in events:
                    title = scrub(ev.get("title") or "")
                    who = scrub(ev.get("who") or "")
                    location = scrub(ev.get("location") or "")
                    notes = scrub(ev.get("notes") or "")
                    start_dt = normalize_local(ev.get("start_iso") or "")
                    if not title or start_dt is None:
                        continue
                    if start_dt.date() < datetime.datetime.now(TZ).date():
                        continue  # past event; same-day with a passed time still files
                    label = f"{who}: {title}" if who else title
                    fp = fingerprint(label, start_dt)
                    when = f"{start_dt:%a %b %-d %-I:%M%p}"
                    confident = (ev.get("confidence") == "high"
                                 and calendar_id and not was_reset)

                    if fp in filed:
                        log(f"  duplicate skipped: {label}")
                        continue
                    if fp in tasked and not confident:
                        log(f"  already tasked, skipped: {label}")
                        continue
                    # fp in tasked but now confident: fall through and file
                    # it. The vague first email made a task; the explicit
                    # reminder upgrades it onto the calendar.

                    if confident and create_event(account, calendar_id, label,
                                                  start_dt, ev.get("end_iso") or "",
                                                  location, notes, msg_id):
                        filed.add(fp)
                        tasked.discard(fp)
                        st["pending_digest"].append(
                            f"➕ {label}, {when}" + (f", {location}" if location else ""))
                    else:
                        # Low confidence, no calendar, reset run, or the
                        # create itself failed: a human decides, nothing is
                        # lost silently.
                        if fp not in tasked:
                            if not DRY_RUN:
                                tasks.add(f"Confirm event: {label}, {when}?",
                                          by="family-events")
                            tasked.add(fp)
                            st["pending_digest"].append(f"❓ needs confirm: {label}, {when}")
            except Exception as e:
                n = st["failures"].get(msg_id, 0) + 1
                st["failures"][msg_id] = n
                log(f"  EVENT LOOP FAILED for {msg_id} (strike {n}): {type(e).__name__}: {e}")
                if n >= MAX_STRIKES:
                    st["messages"].append(msg_id)
                    st["failures"].pop(msg_id, None)
                st["filed"], st["tasked"] = sorted(filed), sorted(tasked)
                save_state(st)
                continue

            st["messages"].append(msg_id)
            st["failures"].pop(msg_id, None)
            st["filed"], st["tasked"] = sorted(filed), sorted(tasked)
            save_state(st)  # per message; a crash keeps progress

        if st["pending_digest"]:
            log("run items:\n" + "\n".join(f"  {d}" for d in st["pending_digest"]))
        else:
            log("done: no family events found")
        flush_digest(st)
        save_state(st)
        return 0


if __name__ == "__main__":
    sys.exit(main())
