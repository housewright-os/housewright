#!/usr/bin/env python3
"""rail_check - monthly grocery-rail price watch, report-only.

Prices the configured staples basket across the configured rails (e.g.
walmart.com first-party vs a warehouse club via its delivery layer) using a
headless LLM CLI with web tools, then reports movement so the household
notices when the cheapest rail changes. Born from a one-off comparison that
found a 20.5% delivery-overhead stack and a cheaper rail for 4 of 5 staples;
this lane keeps that finding fresh instead of letting it rot.

Honesty rail: retail sites CAPTCHA-block automated zip-exact lookups, so
cron prices are search-derived DIRECTIONALS with a confidence tag per item.
The digest says so. Before acting on a big swap, run a deep interactive
check. This lane never purchases, never stages carts, never logs in
anywhere: it reads public prices and writes a report.

Lane contract:
- config: household.json "rails" block (basket, rails, llm_cmd override)
- state:  state/rail_prices.jsonl (one line per run, full item detail)
- cadence: monthly via launchd (com.housewright.railcheck)
- brief:  Telegram digest per run (falls back to stdout untokened)
- fallback: any failure files a house-board task instead of guessing

Usage: rail_check.py [--dry-run] [--from-file <canned.json>]
"""

import datetime
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "household.json")
BUDGET = os.path.join(ROOT, "config", "budget.json")
STATE_PATH = os.path.join(ROOT, "state", "rail_prices.jsonl")
MAX_ITEMS = 15  # bounds one LLM call; a bigger basket is a config smell
DRY_RUN = "--dry-run" in sys.argv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks  # noqa: E402

PROMPT = """You are pricing a household staples basket for a monthly report.
Today is {today}. Use web search to find CURRENT prices. For each item and
each rail, find the most representative current price and normalize a unit
price. Retail sites often block zip-exact automated lookups; when you only
have search-index or general listings, say so via confidence "search" (use
"exact" only for a price read from the retailer's own live page).

RAILS: {rails}
BASKET: {basket}

Output ONLY minified JSON, no prose, no markdown fences:
{{"items":[{{"item":str,"prices":{{{rail_keys}}},"unit":str,"winner":str,"confidence":"exact"|"search"}}],"notes":str}}

Rules:
- prices: one number (USD, plain float, no $) per rail key, null if not
  found or not stocked. unit: the normalized basis you compared on
  (e.g. "$/lb", "$/gal", "$/ct").
- winner: the rail key with the lowest comparable unit price, or "tie".
- Never invent a price: null beats a guess. notes: one line on anything
  that would mislead (pack-size mismatches, out-of-stocks, promos).
"""


def cfg():
    try:
        with open(CONFIG) as f:
            return json.load(f).get("rails") or {}
    except Exception:
        return {}


def llm_cmd(c):
    cmd = c.get("llm_cmd")
    if isinstance(cmd, list) and cmd:
        return cmd
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    return [claude, "-p", "--model", "sonnet",
            "--allowedTools", "WebSearch,WebFetch", "--output-format", "json"]


def strip_fences(s):
    s = s.strip()
    if s.startswith("```"):
        import re
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def run_llm(c, prompt):
    proc = subprocess.run(llm_cmd(c), input=prompt, capture_output=True,
                          text=True, timeout=1200)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"llm rc={proc.returncode}: {proc.stderr.strip()[:200]}")
    outer = json.loads(strip_fences(proc.stdout))
    if isinstance(outer, dict) and "items" in outer:
        return outer
    return json.loads(strip_fences(outer["result"]))


def sanitize_items(raw, rails):
    """LLM output is untrusted: keep only well-shaped items with numeric or
    null prices, so a malformed month degrades to the task fallback instead
    of a traceback nobody sees."""
    out = []
    for it in raw or []:
        if not isinstance(it, dict) or not isinstance(it.get("item"), str):
            continue
        prices_in = it.get("prices")
        if not isinstance(prices_in, dict):
            continue
        prices = {}
        for rail in rails:
            v = prices_in.get(rail)
            try:
                prices[rail] = None if v is None else float(str(v).replace("$", ""))
            except (ValueError, TypeError):
                prices[rail] = None
        w = it.get("winner")
        out.append({"item": it["item"][:80],
                    "prices": prices,
                    "unit": str(it.get("unit") or "")[:20],
                    "winner": w if w in rails or w == "tie" else "tie",
                    "confidence": "exact" if it.get("confidence") == "exact" else "search"})
    return out


def last_run():
    try:
        with open(STATE_PATH) as f:
            lines = [ln for ln in f if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, json.JSONDecodeError, IndexError):
        return None


def append_run(record):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def movers(prev, items, rails):
    """Biggest per-rail price moves vs the previous run, as digest lines."""
    if not prev:
        return []
    old = {i["item"]: i.get("prices") or {} for i in prev.get("items") or []}
    moves = []
    for it in items:
        for rail in rails:
            a, b = (old.get(it["item"]) or {}).get(rail), (it.get("prices") or {}).get(rail)
            if a and b and float(a) > 0:
                pct = (float(b) - float(a)) / float(a) * 100
                if abs(pct) >= 5:
                    moves.append((abs(pct), f"{it['item']}: {pct:+.0f}% at {rail}"))
    moves.sort(reverse=True)
    return [m[1] for m in moves[:4]]


def tg_send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    try:
        with open(BUDGET) as f:
            chat_ids = [c for c in json.load(f)["notify"]["telegram_chat_ids"] if c]
    except (OSError, KeyError, json.JSONDecodeError):
        chat_ids = []
    if not token or not chat_ids:
        print("digest (telegram not configured):\n" + text)
        return
    import notify
    sent = sum(1 for c in chat_ids if notify.send(token, c, text))
    if not sent:
        print("digest send failed to all chats", file=sys.stderr)


def main():
    c = cfg()
    basket = (c.get("basket") or [])[:MAX_ITEMS]
    rails = c.get("rails") or []
    if not basket or len(rails) < 2:
        print("rails lane not configured (household.json rails.basket + rails.rails)")
        return 0
    today = datetime.date.today().isoformat()

    try:
        if "--from-file" in sys.argv:
            with open(sys.argv[sys.argv.index("--from-file") + 1]) as f:
                data = json.load(f)
        else:
            prompt = PROMPT.format(
                today=today, rails=", ".join(rails),
                basket="; ".join(basket),
                rail_keys=",".join(f'"{r}":float|null' for r in rails))
            data = run_llm(c, prompt)
        items = sanitize_items(data.get("items"), rails)
        if not items:
            raise RuntimeError("empty or malformed items in response")
    except Exception as e:
        # A failed check must be visible, never a silent month of no data.
        msg = f"Rail price check failed ({type(e).__name__}); run one manually"
        print(msg, file=sys.stderr)
        if not DRY_RUN:
            tasks.add(msg, by="rail-check")
        return 1

    prev = last_run()
    wins = {}
    for it in items:
        w = it.get("winner")
        if w and w != "tie":
            wins[w] = wins.get(w, 0) + 1
    champion = max(wins, key=wins.get) if wins else "no clear winner"
    exact = sum(1 for i in items if i.get("confidence") == "exact")

    lines = [f"🛒 *Rail check {today}*",
             f"{champion} wins {wins.get(champion, 0)}/{len(items)} staples"
             if wins else "no clear winner this month"]
    lines += movers(prev, items, rails)
    lines.append(f"confidence: {exact}/{len(items)} exact, rest search-derived; "
                 "deep-check before big swaps")

    record = {"date": today, "items": items, "notes": data.get("notes") or ""}
    if DRY_RUN:
        print("DRY-RUN, nothing written:\n" + "\n".join(lines))
        return 0
    append_run(record)
    tg_send("\n".join(lines))
    print(f"rail check: {len(items)} items, champion: {champion}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
