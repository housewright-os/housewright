"""Household Telegram bot: the conversational surface for the household.

Answers in plain language, on their phones, for free:
  - "can we afford $45 for shoes?"     -> deterministic verdict from status.json
  - "what's our number" / [Number]     -> current Safe-to-Spend summary
  - "what bills are coming" / [Bills]  -> due-before-payday list
  - "how did the week go" / [Week]     -> Sunday review content
  - anything else                      -> local Ollama (free), with the real
                                          numbers supplied as context and a
                                          deterministic footer appended

Design rules (mirrors CLAUDE.md):
  - All dollar figures in answers come from the engine, never from the LLM.
    The LLM only classifies intent and adds conversational glue.
  - Read-only. No credentials. Allowlisted chat ids only.
  - No shaming; a "no" always includes when it becomes a "yes".

Runs as a long-polling daemon (no webhook, no public port, no cost).

Usage:
    TELEGRAM_BOT_TOKEN=... python3 scripts/telegram_bot.py
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import tasks
except Exception as _e:  # the bot must come up even if the task board breaks
    tasks = None
    print(f"task board unavailable: {type(_e).__name__}: {_e}", file=sys.stderr)
try:
    import shopping
except Exception as _e:  # the bot must come up even if the shopping list breaks
    shopping = None
    print(f"shopping list unavailable: {type(_e).__name__}: {_e}", file=sys.stderr)
try:
    import family_events
except Exception as _e:  # the bot must come up even if the calendar lane breaks
    family_events = None
    print(f"family calendar unavailable: {type(_e).__name__}: {_e}", file=sys.stderr)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
STATUS = os.path.join(STATE_DIR, "status.json")
WEEKLY = os.path.join(STATE_DIR, "weekly.md")
OFFSET = os.path.join(STATE_DIR, "bot_offset.json")
CONFIG = os.path.join(ROOT, "config", "budget.json")
MESSAGE_LOG = os.path.join(STATE_DIR, "message_log.jsonl")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
OLLAMA = "http://localhost:11434"
OLLAMA_MODEL = os.environ.get("HOUSEHOLD_OLLAMA_MODEL", "llama3.2:3b")

KEYBOARD = {
    "keyboard": [
        [{"text": "Number"}, {"text": "Bills"}],
        [{"text": "Tasks"}, {"text": "Week"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

HELP = (
    "I run the house's numbers, its to-do list, the shopping list, and the "
    "family calendar. Ask me things like:\n\n"
    "  can we afford $45 for cleats?\n"
    "  what's our number?\n"
    "  what bills are coming?\n"
    "  how did this week go?\n\n"
    "  add task fix the gate latch\n"
    "  tasks\n"
    "  done 3\n"
    "  defer 3 2   (push task 3 out two days)\n\n"
    "  add milk\n"
    "  we're out of eggs\n"
    "  add pork shoulder, need it tomorrow   (marks it urgent)\n"
    "  shopping   (see the list, grouped by vendor)\n"
    "  bought 5\n"
    "  ordered walmart   (tells me a run just went out, sharpens future ETAs)\n\n"
    "  add event soccer practice, tuesday 4pm\n\n"
    "The buttons below do the same. The number updates from Monarch a few "
    "times a day, and I never touch the accounts, I only read. I also never "
    "place an order myself, I only track and suggest."
)


def money(x):
    return f"${abs(x):,.2f}" if abs(x) < 100 else f"${abs(x):,.0f}"


def signed(x):
    return ("-" if x < 0 else "") + money(x)


def tg(method, **params):
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
    ).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.loads(r.read().decode())


def send(chat_id, text):
    try:
        tg("sendMessage", chat_id=chat_id, text=text, parse_mode="Markdown",
           reply_markup=KEYBOARD)
    except Exception:
        # Markdown parse errors fall back to plain text.
        try:
            tg("sendMessage", chat_id=chat_id, text=text, reply_markup=KEYBOARD)
        except Exception as e:
            print(f"send failed: {e}", file=sys.stderr)


def log_message(chat_id, name, text):
    """Append one family message to state/message_log.jsonl: full text, not
    just metadata. Only ever called for already-authorized (family) chat
    ids -- never for the stranger/unauthorized path. Consent basis: both
    adults in the household know every message here is recorded; that
    consent is what makes it OK for anything else (this session, a future
    digest) to read family conversation content off disk.
    A write failure never blocks the reply -- logging is best-effort."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        try:  # retention bound: keep ~90 days of family chat, not forever
            with open(MESSAGE_LOG) as f:
                lines = f.readlines()
            if len(lines) > 20000:
                with open(MESSAGE_LOG + ".tmp", "w") as f:
                    f.writelines(lines[-15000:])
                os.replace(MESSAGE_LOG + ".tmp", MESSAGE_LOG)
        except OSError:
            pass
        with open(MESSAGE_LOG, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "chat_id": chat_id,
                "name": name,
                "text": text,
            }) + "\n")
            f.flush()
            os.fsync(f.fileno())  # force to disk now; a message can exceed
            # PIPE_BUF once JSON-escaped, so append alone is not guaranteed
            # atomic. This narrows the crash window to near-zero; a reader
            # should still tolerate one truncated trailing line, the worst
            # case for an append-only log like this one.
    except Exception as e:
        print(f"message log write failed: {type(e).__name__}: {e}", file=sys.stderr)


def allowed_ids():
    try:
        with open(CONFIG) as f:
            return {str(c) for c in json.load(f)["notify"]["telegram_chat_ids"] if c}
    except Exception:
        return set()


def add_chat_id(new_id):
    """Add a chat id to the allowlist (family-approved via the allow verb).
    Atomic write; returns False if already present or on any failure."""
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
        ids = cfg["notify"]["telegram_chat_ids"]
        if str(new_id) in {str(c) for c in ids}:
            return False
        ids.append(str(new_id))
        tmp = CONFIG + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG)
        return True
    except Exception as e:
        print(f"add_chat_id failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def status(recompute=True):
    """Current status; recompute from cached data first (fast, no network)."""
    if recompute:
        try:
            subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "engine.py")],
                capture_output=True, timeout=120,
            )
        except Exception:
            pass
    if not os.path.exists(STATUS):
        return None
    with open(STATUS) as f:
        return json.load(f)


def fmt_number(s):
    icon = {"green": "✅", "amber": "⚠️", "red": "🛑"}[s["status"]]
    L = [f"{icon} *Safe to spend: {signed(s['safe_to_spend'])}*"]
    if s["safe_to_spend"] >= 0 and s["days_until_payday"] > 0:
        L.append(f"{money(s['safe_per_day'])}/day for {s['days_until_payday']} days")
    else:
        L.append(f"Short by {money(-s['safe_to_spend'])} before payday")
    L.append(f"Payday {s['next_payday'][5:]} (+{money(s['expected_paycheck'])})")
    d = s.get("discretionary") or {}
    rem = d.get("period_remaining")
    if rem is not None:
        L.append(f"Choices this period: {money(abs(rem))} {'left' if rem >= 0 else 'over'}")
    return "\n".join(L)


def fmt_bills(s):
    bills = s.get("bills_due_before_payday") or []
    if not bills:
        return f"Nothing due before payday on {s['next_payday'][5:]}. 🎉"
    L = ["*Due before payday:*"]
    for b in bills:
        L.append(f"  {b['due'][5:]}  {money(b['amount'])}  {b['name']}")
    L.append(f"\nPayday {s['next_payday'][5:]}: +{money(s['expected_paycheck'])}")
    return "\n".join(L)


def fmt_week():
    if not os.path.exists(WEEKLY):
        return "No weekly review yet. It generates every Sunday at 5pm."
    with open(WEEKLY) as f:
        text = f.read()
    # Telegram messages cap at 4096 chars; trim the tables if needed.
    if len(text) > 3800:
        text = text[:3800] + "\n…(full review in state/weekly.md)"
    return text


def fmt_afford(s, amount, item="that"):
    safe = s["safe_to_spend"]
    after = safe - amount
    days = s["days_until_payday"]
    payday = s["next_payday"][5:]
    if safe < 0:
        return (
            f"🛑 *Not right now.* We're already short {money(-safe)} before payday. "
            f"{item.capitalize()} for {money(amount)} would make it {money(-after)}. "
            f"After payday on {payday} it's worth asking again."
        )
    if after >= 0:
        per_day = f" That leaves {money(after / days)}/day until payday." if days > 0 else ""
        return (
            f"✅ *Yes.* {item.capitalize()} for {money(amount)} works. "
            f"The number goes {money(safe)} → {money(after)}.{per_day}"
        )
    return (
        f"🛑 *Not yet.* {item.capitalize()} is {money(amount)} but only {money(safe)} "
        f"is free, so it's {money(-after)} over. After payday on {payday} "
        f"(+{money(s['expected_paycheck'])}) it likely fits."
    )


AMOUNT_RE = re.compile(r"\$?\s*(\d{1,6}(?:[.,]\d{1,2})?)\s*(?:dollars|bucks|usd)?", re.I)
AFFORD_HINTS = ("afford", "buy", "get ", "purchase", "spend", "cost", "pay for", "order")

# --- shopping list: deterministic-first parsing (mirrors parse_afford) ---
SHOPPING_ADD_RE = re.compile(r"^(?:add|need|buy|get)\s+(?!event\b|calendar\b)(.+)$", re.I)
SHOPPING_OUT_OF_RE = re.compile(
    r"^(?:we'?re\s+|we\s+are\s+)?(?:completely\s+|totally\s+|all\s+)?out\s+of\s+(.+)$", re.I)
# Only an actual price token ($, "dollars", "bucks", "usd") should defer to
# the afford-check flow. A bare digit ("2 gallons", "2 dozen eggs") must not:
# that is a normal quantity, not a dollar amount.
PRICE_LIKE_RE = re.compile(r"\$\s*\d|\b\d+(?:[.,]\d{1,2})?\s*(?:dollars|bucks|usd)\b", re.I)
TRAILING_URGENCY_RE = re.compile(
    r"(?:,?\s*(?:(?:we'?re\s+|we\s+)?need\s+(?:it|this)\b|please\b|"
    r"as\s+soon\s+as\s+possible\b|asap\b|urgently\b|"
    r"(?:by|for|before)\s+(?:tomorrow|tonight|today|this\s+week|the\s+weekend)\b|"
    r"this\s+week\b|today\b|tomorrow\b|tonight\b|right\s+now\b|immediately\b))+"
    r"\s*[.!]?\s*$", re.I)
URGENT_HINTS = ("urgent", "emergency", "asap", "as soon as possible", "right now",
                "immediately", "today", "tomorrow", "tonight", "this week",
                "before the weekend", "completely out", "totally out", "all out")


def _shopping_urgent(text):
    low = text.lower()
    return any(h in low for h in URGENT_HINTS)


def _clean_item_name(raw):
    cleaned = TRAILING_URGENCY_RE.sub("", raw).strip().rstrip(",").strip()
    return cleaned or raw.strip()


def parse_afford(text):
    """Deterministic first pass: an amount plus a buying-ish verb."""
    t = text.lower()
    m = AMOUNT_RE.search(t)
    if not m:
        return None
    if not any(h in t for h in AFFORD_HINTS) and not t.strip().startswith("$"):
        return None
    amount = float(m.group(1).replace(",", ""))
    # Item = words after 'for' ("afford $45 for cleats"), with the amount text
    # removed so "buy shoes for 120" doesn't capture "120" as the item.
    stripped = (t[: m.start()] + " " + t[m.end():]).strip()
    item = "that"
    fm = re.search(r"\bfor\s+([a-z][a-z0-9' ]{1,40})", stripped)
    if fm:
        item = fm.group(1).strip().rstrip("?.!")
    else:
        vm = re.search(
            r"\b(?:buy|get|afford|purchase|order)\s+([a-z][a-z0-9' ]{1,40}?)(?:\s+for\b|[?.!]|$)",
            stripped,
        )
        if vm:
            item = vm.group(1).strip()
    return amount, item


def ollama_up():
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=3) as r:
            tags = json.loads(r.read().decode())
        return any(OLLAMA_MODEL.split(":")[0] in (m.get("name") or "") for m in tags.get("models", []))
    except Exception:
        return False


def ollama_chat(system, user):
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 220},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["message"]["content"].strip()


CLASSIFY_SYS = (
    "You classify one short family message. Reply with ONLY a JSON "
    "object, no other text. Pick exactly ONE intent word. "
    '"afford" = asking whether a specific purchase fits. '
    '"number" = asking how much is safe to spend or how we are doing. '
    '"bills" = upcoming bills or payday. "week" = how the week went. '
    '"tasks" = the to-do list, chores, or things to fix around the house. '
    '"shopping" = asking to add a grocery or household item to the shopping '
    'list, or mentioning something has run out or is running low. '
    '"chat" = anything else.\n'
    "Fields: amount (number or null), item (string or null, the thing being "
    "discussed), urgent (true only if it sounds needed very soon or has run "
    "out, else false).\n"
    'Example input: could we swing sixty bucks for uniforms?\n'
    'Example output: {"intent": "afford", "amount": 60, "item": "uniforms", "urgent": false}\n'
    'Example input: hey how are we doing\n'
    'Example output: {"intent": "number", "amount": null, "item": null, "urgent": false}\n'
    'Example input: someone grab milk on the way home, we are almost out\n'
    'Example output: {"intent": "shopping", "amount": null, "item": "milk", "urgent": true}'
)

CHAT_SYS = (
    "You are the family budget assistant for a household, speaking on "
    "Telegram. Warm, brief, zero judgment, no drama. HARD RULES: never write "
    "any dollar amount, number, or date in your reply, none at all, because "
    "the exact budget summary is automatically appended below whatever you "
    "say. Refer to it as 'the summary below' when figures matter. Never give "
    "investment or tax advice. Never agree to move money; you are read-only. "
    "Two to four sentences maximum. No em-dashes."
)


def fmt_tasks():
    ts = tasks.open_tasks()
    if not ts:
        return "Nothing on the house list. 🎉 Add one with: add task <thing>"
    L = ["*House list:*"]
    for t in ts:
        L.append(f"  {tasks.fmt(t)}")
    L.append("\nSay: done <id>, or defer <id> <days>.")
    return "\n".join(L)


def fmt_shopping():
    items = shopping.open_items()
    if not items:
        return "Shopping list is empty. Add one: add milk"
    grouped, unassigned = {}, []
    for it in items:
        (grouped.setdefault(it["vendor"], []) if it.get("vendor") else unassigned).append(it)
    order = sorted(grouped, key=lambda v: min(i["id"] for i in grouped[v]))
    L = ["*Shopping list:*"]
    for v in order:
        its = grouped[v]
        urgent_ct = sum(1 for i in its if i["urgent"])
        L.append(f"\n*{shopping.vendor_label(v)}*" + (f"  🚨{urgent_ct} urgent" if urgent_ct else ""))
        for it in its:
            L.append(f"  #{it['id']} {it['name']}" + (" 🚨" if it["urgent"] else ""))
    if unassigned:
        L.append("\n*Unassigned (no vendor match yet):*")
        for it in unassigned:
            L.append(f"  #{it['id']} {it['name']}")
    L.append("\nSay: bought <id>, or ordered <vendor> once a run goes out.")
    return "\n".join(L)


def fmt_shopping_added(item):
    label = shopping.vendor_label(item["vendor"]) if item.get("vendor") else None
    tag = " 🚨 marked urgent" if item["urgent"] else ""
    L = [f"Added #{item['id']}: {item['name']}{tag}"]
    if label:
        if item.get("vendor_reason"):
            L.append(f"Normally comes from {label} ({item['vendor_reason']}).")
        else:
            L.append(f"Normally comes from {label}.")
        if item.get("eta"):
            L.append(f"Next {label} order: around {item['eta'][5:]} "
                      f"({item.get('eta_note', '')}).")
        elif item.get("eta_note"):
            L.append(item["eta_note"].capitalize() + ".")
    else:
        L.append("No vendor match for that one yet, it'll ride the primary "
                  "order when the next cart gets built.")
    if item["urgent"]:
        advice = shopping.pickup_advice(item)
        if advice:
            L.append(f"\n{advice}")
        if item.get("vendor"):
            others = [o for o in shopping.by_vendor(item["vendor"]) if o["id"] != item["id"]]
            if others:
                names = ", ".join(o["name"] for o in others[:6])
                L.append(f"\nIf a rush order does go out: also on the list for {label} "
                          f"-> {names}. Worth bundling so the delivery fee only gets "
                          f"paid once.")
            else:
                L.append(f"\nNothing else queued for {label} right now. If other "
                          f"{label} staples are due soon, add them now in case a "
                          f"rush order does go out.")
    return "\n".join(L)


def fmt_event_result(result):
    status = result.get("status")
    if status == "unconfigured":
        return "The family calendar isn't set up yet."
    if status == "error":
        return "Couldn't reach the calendar extractor just now. Try again in a bit."
    if status == "busy":
        return "The calendar's mid-scan right now, give it a few seconds and try again."
    if status == "empty":
        return ("Couldn't find an event in that. Try: "
                "add event soccer practice, tuesday 4pm")
    lines = []
    for r in result.get("results") or []:
        if r["status"] == "filed":
            loc = f", {r['location']}" if r.get("location") else ""
            lines.append(f"✅ Added to the calendar: {r['label']}, {r['when']}{loc}")
        elif r["status"] == "tasked":
            lines.append(f"❓ Not sure on the time, put on the house list to confirm: "
                         f"{r['label']}, {r['when']}")
        elif r["status"] == "duplicate":
            lines.append(f"Already on the calendar: {r['label']}, {r['when']}")
        elif r["status"] == "past":
            lines.append(f"That's in the past ({r['label']}, {r['when']}), skipped.")
        elif r["status"] == "error":
            lines.append(f"Couldn't process: {r['label']}")
    return "\n".join(lines) if lines else "Couldn't parse that into an event."


def handle_text(chat_id, text, first_name=""):
    t = text.strip()
    low = t.lower().lstrip("/")

    if low in ("start", "help"):
        send(chat_id, HELP)
        return

    # Family-gated onboarding: an allowlisted person approves a new chat id
    # (the bot suggests this exact command when someone new messages it).
    m = re.match(r"^allow\s+(-?\d+)$", low)
    if m:
        new_id = m.group(1)
        if add_chat_id(new_id):
            send(chat_id, f"Added {new_id} to the family. They get the briefs now.")
            try:
                with open(os.path.join(ROOT, "config", "household.json")) as f:
                    bot = (json.load(f).get("bot_name") or "").strip()
            except Exception:
                bot = ""
            intro = f"This is {bot}. " if bot else ""
            send(new_id, f"You're in. {intro}I run the house's numbers "
                         "and its to-do list. Say 'help' for what I can do.")
        else:
            send(chat_id, f"{new_id} is already on the list (or the add failed, "
                          f"check the log).")
        return

    # --- house list verbs, deterministic, before anything money-shaped ---
    # If the task module failed to import, these fall through and the rest of
    # the bot keeps working.
    if tasks is not None:
        m = re.match(r"^(?:add task|task add|todo)[:,]?\s+(.+)$", t, re.I)
        if m:
            tid = tasks.add(m.group(1).strip(), by=first_name or None)
            send(chat_id, f"Added #{tid}: {m.group(1).strip()}")
            return

        if low in ("tasks", "task list", "house list", "chores", "list"):
            send(chat_id, fmt_tasks())
            return

        m = re.match(r"^(?:task\s+)?done\s+#?(\d+)$", low)
        if m:
            done = tasks.complete(int(m.group(1)))
            send(chat_id, f"Done: {done['text']} ✅" if done
                 else "No open task with that id. Say 'tasks' for the list.")
            return

        m = re.match(r"^(?:task\s+)?defer\s+#?(\d+)(?:\s+(\d+)\s*d?(?:ays)?)?$", low)
        if m:
            deferred, until = tasks.defer(int(m.group(1)), int(m.group(2) or 1))
            send(chat_id, f"Deferred to {until[5:]}: {deferred['text']}" if deferred
                 else "No open task with that id. Say 'tasks' for the list.")
            return

    # --- shopping list, deterministic, same tier as the house list above ---
    if shopping is None and low in ("shopping", "shopping list", "groceries", "grocery list"):
        send(chat_id, "The shopping list is unavailable right now.")
        return

    if shopping is not None:
        if low in ("shopping", "shopping list", "groceries", "grocery list"):
            send(chat_id, fmt_shopping())
            return

        m = re.match(r"^(?:bought|got|have|found)\s+#?(\d+)$", low)
        if m:
            it = shopping.fulfill(int(m.group(1)))
            send(chat_id, f"Nice, crossed off: {it['name']} ✅" if it
                 else "No open item with that id. Say 'shopping' for the list.")
            return

        m = re.match(r"^ordered\s+(.+)$", low)
        if m:
            key = shopping.resolve_vendor(m.group(1).strip())
            if key:
                shopping.mark_ordered(key)
                send(chat_id, f"Got it, {shopping.vendor_label(key)} marked as ordered "
                              f"today. Future ETAs for that vendor count from here.")
            else:
                send(chat_id, "Didn't recognize that vendor. Try: ordered walmart / "
                              "ordered costco / ordered sams club")
            return

        out_of = SHOPPING_OUT_OF_RE.match(t)
        added_m = None if out_of else SHOPPING_ADD_RE.match(t)
        if out_of or added_m:
            candidate = (out_of or added_m).group(1).strip()
            if not PRICE_LIKE_RE.search(candidate):  # "buy $45 cleats" -> afford-parsing below
                name = _clean_item_name(candidate)
                urgent = bool(out_of) or _shopping_urgent(t)
                iid = shopping.add(name, by=first_name or None, urgent=urgent)
                item = next((i for i in shopping.open_items() if i["id"] == iid), None)
                send(chat_id, fmt_shopping_added(item) if item else f"Added: {name}")
                return

    # --- calendar quick-add, deterministic, same tier as tasks/shopping ---
    if family_events is not None:
        m = re.match(r"^(?:add event|event add|calendar add)[:,]?\s+(.+)$", t, re.I)
        if m:
            result = family_events.add_manual_event(m.group(1).strip(),
                                                     requested_by=first_name or None)
            send(chat_id, fmt_event_result(result))
            return

    if low in ("number", "what's our number", "whats our number", "how are we", "status"):
        s = status()
        send(chat_id, fmt_number(s) if s else "No data yet. The engine hasn't run.")
        return

    if low in ("bills", "what bills are coming"):
        s = status()
        send(chat_id, fmt_bills(s) if s else "No data yet.")
        return

    if low in ("week", "weekly", "how did this week go"):
        send(chat_id, fmt_week())
        return

    parsed = parse_afford(t)
    if parsed:
        s = status()
        if s:
            send(chat_id, fmt_afford(s, parsed[0], parsed[1]))
        else:
            send(chat_id, "No data yet, so I can't say. The engine hasn't run.")
        return

    # Natural language path: classify locally, answer deterministically,
    # fall through to a guarded chat reply.
    if ollama_up():
        s = status()
        try:
            raw = ollama_chat(CLASSIFY_SYS, t)
            m = re.search(r"\{.*\}", raw, re.S)
            c = json.loads(m.group(0)) if m else {}
        except Exception:
            c = {}
        intent = str(c.get("intent") or "")
        amt = c.get("amount")
        # A valid extracted amount is the strongest signal, regardless of how
        # the small model mangled the intent string.
        if isinstance(amt, (int, float)) and amt > 0 and s:
            send(chat_id, fmt_afford(s, float(amt), c.get("item") or "that"))
            return
        if intent == "number" and s:
            send(chat_id, fmt_number(s))
            return
        if intent == "bills" and s:
            send(chat_id, fmt_bills(s))
            return
        if intent == "week":
            send(chat_id, fmt_week())
            return
        if intent == "tasks":
            send(chat_id, fmt_tasks() if tasks is not None
                 else "The task board is unavailable right now.")
            return
        if intent == "shopping" and c.get("item"):
            if shopping is not None:
                name = _clean_item_name(str(c["item"]).strip())
                urgent = bool(c.get("urgent"))
                iid = shopping.add(name, by=first_name or None, urgent=urgent)
                item = next((i for i in shopping.open_items() if i["id"] == iid), None)
                send(chat_id, fmt_shopping_added(item) if item else f"Added: {name}")
            else:
                send(chat_id, "The shopping list is unavailable right now.")
            return
        # Guarded chat: real numbers in, canonical footer out.
        ctx = fmt_number(s).replace("*", "") if s else "no data available"
        try:
            reply = ollama_chat(CHAT_SYS, f"CONTEXT:\n{ctx}\n\nMESSAGE: {t}")
        except Exception:
            reply = None
        if reply:
            # House rule: no em- or en-dashes anywhere.
            reply = (reply.replace(" — ", ", ").replace(" – ", ", ")
                     .replace("—", "-").replace("–", "-"))
            footer = f"\n\n{fmt_number(s)}" if s else ""
            send(chat_id, f"{reply}{footer}")
            return

    # No Ollama: be useful anyway.
    s = status()
    core = fmt_number(s) if s else "No data yet."
    send(chat_id, f"{core}\n\nI understand things like: can we afford $45 for cleats? "
                  f"Or use the buttons below.")


def main():
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN is not set. See SETUP.md.", file=sys.stderr)
        return 2

    os.makedirs(STATE_DIR, exist_ok=True)
    offset = 0
    if os.path.exists(OFFSET):
        try:
            with open(OFFSET) as f:
                offset = json.load(f).get("offset", 0)
        except Exception:
            offset = 0

    global notified_unknown
    notified_unknown = set()
    print(f"bot up, ollama={'yes' if ollama_up() else 'no'}")
    while True:
        try:
            updates = tg("getUpdates", offset=offset + 1, timeout=50)
        except Exception as e:
            print(f"poll error: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(5)
            continue

        for u in updates.get("result", []):
            offset = max(offset, u["update_id"])
            msg = u.get("message") or u.get("edited_message")
            if not msg or "text" not in msg:
                continue
            chat_id = str(msg["chat"]["id"])
            if chat_id not in allowed_ids():
                # Private family bot. Log it, tell the family, offer the
                # one-line approval. Once per unknown id per daemon run so a
                # stranger cannot spam the family's phones.
                name = (msg.get("from") or {}).get("first_name", "?")
                print(f"unauthorized chat id: {chat_id} ({name})")
                if chat_id not in notified_unknown:
                    notified_unknown.add(chat_id)
                    for fam in allowed_ids():
                        send(fam, f"Someone new messaged me: {name} (id {chat_id}). "
                                  f"If this is family, reply: allow {chat_id}")
                try:
                    tg("sendMessage", chat_id=chat_id,
                       text="This is a private family bot.")
                except Exception:
                    pass
                continue
            name = (msg.get("from") or {}).get("first_name", "")
            log_message(chat_id, name, msg["text"])
            try:
                handle_text(chat_id, msg["text"], name)
            except Exception as e:
                print(f"handler error: {type(e).__name__}: {e}", file=sys.stderr)
                send(chat_id, "Something went wrong on my end. Try again in a minute.")

        with open(OFFSET, "w") as f:
            json.dump({"offset": offset}, f)


if __name__ == "__main__":
    sys.exit(main())
