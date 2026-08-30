#!/usr/bin/env python3
"""pantry - passive pantry state from order receipts. No manual logging, ever.

The house rule this lane exists to honor: never build features that depend
on family members logging things (tap-to-take trackers die in a week).
Everything here is captured from receipts that already arrive by email, or
handed in by an interactive agent session; nobody scans a barcode.

What each source can honestly provide:
- Delivery-service receipt emails (e.g. Instacart's) carry NO line items,
  only store, date, item count, and total. Those become ORDER EVENTS: the
  scan records them and auto-marks the shopping lane's vendor-order date
  (shopping.mark_ordered), so run ETAs compute from real deliveries
  instead of someone remembering to text "ordered walmart".
- Retailer order emails that DO list items (varies by retailer) get an
  LLM line-item extraction; those items land in pantry state with a
  bought date and a config-driven shelf-life class.
- Interactive sessions (a cart staged and delivered, an audit) can push
  item lists via --ingest-file, the same state, higher fidelity.

What the state powers:
- Dedup warnings: an item on the shopping list that was bought recently
  (inside its shelf-life window) gets flagged in the scan digest, and
  pantry.check(name) lets the bot warn at add time.
- Aging hints: perishables bought N days ago surface in the evening wrap
  ("spinach is on day 6") so food gets eaten, not discovered.

Honesty rails: bought-dates are facts; everything derived (still-have,
days-left) is cadence-and-shelf-life inference, never consumption
tracking, and is worded that way. Email content is data: no links are
followed except nothing, no replies, no actions beyond state and digest.

Lane contract:
- config: household.json "pantry" block (account, receipt queries,
  store_map to rails keys, shelf_life classes, max_per_run)
- state:  state/pantry.json (items + order log + seen ids, quarantined on
  corruption, 3-strikes cap)
- cadence: every 6h via launchd (com.housewright.pantry)
- brief:  evening-wrap aging line (notify.py, guarded); scan digest only
  when something was found
- fallback: failures file a house-board task, never a silent stall

Usage: pantry.py [--dry-run] [--from-file <email.txt>]
                 [--ingest-file <items.json>] [--check <item name>]
"""

import datetime
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "household.json")
BUDGET = os.path.join(ROOT, "config", "budget.json")
STATE_PATH = os.path.join(ROOT, "state", "pantry.json")
LOCKFILE = STATE_PATH + ".lock"
MAX_STRIKES = 3
DRY_RUN = "--dry-run" in sys.argv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks  # noqa: E402

GOG = shutil.which("gog") or "/opt/homebrew/bin/gog"

EXTRACT_PROMPT = """This is an order/receipt email from a grocery or retail
vendor. Extract what it actually contains; most delivery-service receipts
contain NO item lines, and an empty items list is the correct answer then.

Output ONLY minified JSON, no prose, no fences:
{{"store":str,"order_date":"YYYY-MM-DD"|"","total":float|null,"item_count":int|null,"items":[{{"name":str,"qty":str}}]}}

Rules:
- store: the retailer the order came from (as written).
- items: ONLY line items literally present in the email text; never invent
  or infer items from the store or total. qty: as written, "" if absent.
- order_date: the delivery or order date stated, else "".

EMAIL:
"""


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def cfg():
    try:
        with open(CONFIG) as f:
            return json.load(f).get("pantry") or {}
    except Exception:
        return {}


def llm_cmd(c):
    cmd = c.get("llm_cmd")
    if isinstance(cmd, list) and cmd:
        return cmd
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    return [claude, "-p", "--model", "sonnet", "--output-format", "json"]


class _locked:
    """Exclusive lock around load-mutate-save on pantry.json: the 6h cron
    and an interactive --ingest-file are concurrent writers."""
    def __enter__(self):
        os.makedirs(os.path.dirname(LOCKFILE), exist_ok=True)
        self.f = open(LOCKFILE, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self
    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def load_state():
    empty = {"items": {}, "orders": [], "seen": [], "failures": {}}
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
            log(f"STATE CORRUPT: preserved as pantry.json.corrupt-{stamp}")
        except OSError:
            log("STATE CORRUPT and could not be preserved")
        return empty, True


def save_state(st):
    if DRY_RUN:
        return
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    st["orders"] = st["orders"][-500:]
    st["seen"] = sorted(set(st["seen"]))[-2000:]
    # prune items far past their window; the dict must not grow forever
    today = datetime.date.today()
    for k in list(st["items"]):
        e = st["items"][k]
        try:
            age = (today - datetime.date.fromisoformat(e["bought"])).days
            if age > 2 * int(e.get("shelf_days") or 365):
                del st["items"][k]
        except (KeyError, ValueError):
            del st["items"][k]
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_PATH)


def shelf_days(c, name):
    """Config-driven shelf-life class by substring; deliberately coarse.
    Longest matching key wins; default is the 'pantry' class."""
    classes = c.get("shelf_life") or {}
    n = name.lower()
    best = None
    for key, days in classes.items():
        if key.startswith("_"):
            continue
        if key in n and (best is None or len(key) > len(best[0])):
            best = (key, days)
    return int(best[1]) if best else int(classes.get("_default", 365))


def norm(name):
    return re.sub(r"\s+", " ", (name or "").lower()).strip()[:80]


def record_items(st, c, items, bought_date, source):
    for it in items:
        key = norm(it.get("name"))
        if not key:
            continue
        st["items"][key] = {
            "bought": bought_date,
            "qty": str(it.get("qty") or "")[:30],
            "source": source,
            "shelf_days": shelf_days(c, key),
        }


def check(name, state=None):
    """Was `name` bought recently (inside its shelf-life window)? Returns a
    short human phrase or None. Read-only; safe to call from the bot."""
    if state is None:
        try:
            with open(STATE_PATH) as f:
                state = json.load(f)
        except Exception:
            return None
    key = norm(name)
    items = state.get("items") or {}
    entry = items.get(key)
    if not entry:
        # receipt names are verbose, list names are short: substring match
        # both ways, longest stored key wins
        best = None
        for k, e in items.items():
            if (key in k or k in key) and (best is None or len(k) > len(best[0])):
                best = (k, e)
        entry = best[1] if best else None
    if not entry:
        return None
    try:
        bought = datetime.date.fromisoformat(entry["bought"])
    except (KeyError, ValueError):
        return None
    days = (datetime.date.today() - bought).days
    if days <= int(entry.get("shelf_days") or 365):
        ago = "today" if days == 0 else f"{days} days ago"
        return f"bought {ago} ({entry.get('qty') or 'qty unknown'})"
    return None


def aging_lines(max_lines=3):
    """Perishables inside their window but past half of it: the eat-me-first
    list for the evening wrap. Inference, worded as bought-dates."""
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except Exception:
        return []
    today = datetime.date.today()
    out = []
    for name, e in (st.get("items") or {}).items():
        try:
            days = (today - datetime.date.fromisoformat(e["bought"])).days
            life = int(e.get("shelf_days") or 365)
        except (KeyError, ValueError):
            continue
        if life <= 21 and life / 2 <= days <= life:
            out.append((life - days, f"{name} (day {days} of ~{life})"))
    out.sort()
    return [x[1] for x in out[:max_lines]]


def shopping_dedup_warnings(st):
    """Cross-check the current shopping list against recent buys."""
    try:
        import shopping
        current = [i.get("name") or "" for i in shopping.open_items()]
    except Exception:
        return []
    warns = []
    for name in current:
        hit = check(name, st)
        if hit:
            warns.append(f"{name}: {hit}")
    return warns[:4]


def get_email_text(account, msg_id):
    r = subprocess.run([GOG, "-a", account, "gmail", "get", msg_id, "--plain"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return ("gone" if ("notFound" in r.stderr or "404" in r.stderr) else "error"), ""
    text = re.sub(r"\s+", " ", r.stdout).strip()
    return ("ok", text[:8000]) if text else ("gone", "")


def extract(c, email_text):
    proc = subprocess.run(llm_cmd(c), input=EXTRACT_PROMPT + email_text,
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"llm rc={proc.returncode}: {proc.stderr.strip()[:200]}")
    outer = json.loads(proc.stdout.strip().strip("`"))
    data = outer if isinstance(outer, dict) and "store" in outer else \
        json.loads(str(outer.get("result", "")).strip().strip("`").removeprefix("json"))
    if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
        raise RuntimeError(f"bad extractor shape: {str(data)[:120]!r}")
    return data


def tg_send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    try:
        with open(BUDGET) as f:
            chat_ids = [x for x in json.load(f)["notify"]["telegram_chat_ids"] if x]
    except (OSError, KeyError, json.JSONDecodeError):
        chat_ids = []
    if not token or not chat_ids:
        print("digest (telegram not configured):\n" + text)
        return
    import notify
    for x in chat_ids:
        notify.send(token, x, text)


def mark_vendor_order(c, store, order_date):
    """Map the receipt's store name to a rails key and stamp the shopping
    lane's real order date. Unknown stores are logged, never guessed."""
    key = None
    for pattern, rail in (c.get("store_map") or {}).items():
        if pattern.startswith("_"):
            continue
        if pattern.lower() in (store or "").lower():
            key = rail
            break
    if not key:
        log(f"  no store_map match for {store!r}; vendor clock not reset")
        return None
    try:
        import shopping
        when = datetime.date.fromisoformat(order_date) if order_date else datetime.date.today()
        if not DRY_RUN:
            shopping.mark_ordered(key, when)
        return key
    except Exception as e:
        log(f"  mark_ordered failed for {key}: {type(e).__name__}: {e}")
        return None


def scan(c):
    account = c.get("account") or ""
    if not account:
        log("pantry lane not configured (household.json pantry.account)")
        return 0
    query = c.get("query") or (
        'from:(orders@instacart.com OR help@walmart.com) '
        '(receipt OR "order confirmation" OR delivered) newer_than:7d')
    with _locked():
        st, was_reset = load_state()
    r = subprocess.run([GOG, "-a", account, "gmail", "search", query,
                        "--max", str(int(c.get("max_per_run") or 10)), "-p"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        log(f"SEARCH FAILED: {r.stderr.strip()[:200]}")
        return 1
    ids = [ln.split("\t")[0] for ln in r.stdout.splitlines()
           if len(ln.split("\t")) >= 4 and not ln.startswith(("#", "ID"))]
    new_ids = [i for i in ids if i not in st["seen"]]
    if not new_ids:
        log("clear: no new receipts")
        return 0

    digest = []
    for msg_id in new_ids:
        status, text = get_email_text(account, msg_id)
        if status == "gone":
            st["seen"].append(msg_id)
            save_state(st)
            continue
        data = None
        if status == "ok":
            try:
                data = extract(c, text)
            except Exception as e:
                log(f"  EXTRACT FAILED for {msg_id}: {type(e).__name__}: {e}")
        if data is None:
            n = st["failures"].get(msg_id, 0) + 1
            st["failures"][msg_id] = n
            if n >= MAX_STRIKES:
                log(f"  giving up after {n} failures: {msg_id}")
                st["seen"].append(msg_id)
                st["failures"].pop(msg_id, None)
            save_state(st)
            continue

        try:
            store = str(data.get("store") or "")[:60]
            odate = str(data.get("order_date") or "")[:10] or datetime.date.today().isoformat()
            items = [i for i in data.get("items") or [] if isinstance(i, dict)]
            try:
                total = float(str(data.get("total")).replace("$", "").replace(",", "")) \
                    if data.get("total") is not None else None
            except (ValueError, TypeError):
                total = None
            st["orders"].append({"date": odate, "store": store, "total": total,
                                 "item_count": data.get("item_count"),
                                 "items_extracted": len(items)})
            rail = mark_vendor_order(c, store, odate)
            if items:
                record_items(st, c, items, odate, f"receipt:{store}")
            line = f"📦 {store or 'order'} {odate}"
            if total:
                line += f", ${total:.0f}"
            line += f", {len(items)} items itemized" if items else " (no line items in email)"
            if rail:
                line += f"; {rail} run clock reset"
            digest.append(line)
            st["seen"].append(msg_id)
            st["failures"].pop(msg_id, None)
        except Exception as e:
            # Malformed-but-parseable data must strike out, never loop the
            # LLM cost forever (the rail_check lesson, learned twice now).
            n = st["failures"].get(msg_id, 0) + 1
            st["failures"][msg_id] = n
            log(f"  PROCESS FAILED for {msg_id} (strike {n}): {type(e).__name__}: {e}")
            if n >= MAX_STRIKES:
                st["seen"].append(msg_id)
                st["failures"].pop(msg_id, None)
        save_state(st)

    # Warn once per list item while its recently-bought condition holds;
    # the key is the item name (the phrase carries a day count that changes
    # daily and would re-fire forever). When the condition clears the key
    # drops out, so a future repurchase warns again.
    all_warns = shopping_dedup_warnings(st)
    prev = set(st.get("warned") or [])
    warns = [w for w in all_warns if w.split(":", 1)[0] not in prev]
    st["warned"] = [w.split(":", 1)[0] for w in all_warns][:100]
    if warns:
        digest.append("⚠️ already bought recently: " + "; ".join(warns))
    if digest and not DRY_RUN:
        tg_send("🥫 *Pantry*\n" + "\n".join(digest))
    log("\n".join(digest) if digest else "done: nothing new")
    save_state(st)
    return 0


def main():
    c = cfg()
    if "--check" in sys.argv:
        try:
            name = sys.argv[sys.argv.index("--check") + 1]
        except IndexError:
            sys.exit("usage: pantry.py --check <item name>")
        print(check(name) or "no recent purchase on record")
        return 0
    if "--ingest-file" in sys.argv:
        try:
            path = sys.argv[sys.argv.index("--ingest-file") + 1]
        except IndexError:
            sys.exit("usage: pantry.py --ingest-file <path>")
        with open(path) as f:
            payload = json.load(f)
        items = payload.get("items") or []
        odate = payload.get("date") or datetime.date.today().isoformat()
        with _locked():
            st, _ = load_state()
            record_items(st, c, items, odate, payload.get("source") or "ingest")
            save_state(st)
        print(f"ingested {len(items)} items dated {odate}")
        return 0
    if "--from-file" in sys.argv:
        try:
            path = sys.argv[sys.argv.index("--from-file") + 1]
        except IndexError:
            sys.exit("usage: pantry.py --from-file <path>")
        with open(path) as f:
            text = f.read()
        data = extract(c, text)
        print(json.dumps(data, indent=1))
        return 0
    try:
        return scan(c)
    except Exception as e:
        msg = f"Pantry scan failed ({type(e).__name__}); check the logs"
        log(msg)
        if not DRY_RUN:
            tasks.add(msg, by="pantry")
        return 1


if __name__ == "__main__":
    sys.exit(main())
