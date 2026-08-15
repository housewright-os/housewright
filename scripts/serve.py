"""Serve Safe-to-Spend on the tailnet, read-only.

Routes:
    /            the dashboard (mobile-first HTML)
    /status.json the full status object
    /plain       plain-text summary for curl

Binds 127.0.0.1:8770 by default; set serve.bind in household.json to
"0.0.0.0" to opt into LAN/VPN exposure (unauthenticated: never internet).

Usage:
    python3 scripts/serve.py
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "status.json")
WEEKLY = os.path.join(ROOT, "state", "weekly.md")
HISTORY = os.path.join(ROOT, "state", "history.jsonl")
BALHIST = os.path.join(ROOT, "data", "raw_balance_history.json")
ACCOUNTS = os.path.join(ROOT, "data", "raw_accounts.json")
PORT = 8770


def build_history():
    """Daily card-debt and liquid series: Monarch backfill + local history."""
    from datetime import date, timedelta

    series = {}  # date -> {"card_debt": x, "liquid": y}

    try:
        with open(ACCOUNTS) as f:
            accts = json.load(f)["accounts"]
        try:
            with open(os.path.join(ROOT, "config", "budget.json")) as f:
                excluded = [e.upper() for e in
                            json.load(f).get("accounts", {}).get("exclude_from_household", [])]
        except Exception:
            excluded = []
        accts = [a for a in accts
                 if not any(e in (a.get("displayName") or "").upper() for e in excluded)]
        kinds = {}
        for a in accts:
            t = ((a.get("type") or {}).get("name") or "").lower()
            st = ((a.get("subtype") or {}).get("name") or "").lower()
            retirement = st in ("ira", "roth", "st_401k", "education_savings_account",
                                "health_savings_account", "pension", "401k", "403b")
            kinds[str(a["id"])] = (t, retirement)

        with open(BALHIST) as f:
            bh = json.load(f)
        start = date.fromisoformat(bh["start_date"])
        payload = bh["payload"]
        rows = payload.get("accounts") if isinstance(payload, dict) else payload
        for a in rows or []:
            t, retirement = kinds.get(str(a.get("id")), ("", False))
            for i, bal in enumerate(a.get("recentBalances") or []):
                if bal is None:
                    continue
                d = (start + timedelta(days=i)).isoformat()
                e = series.setdefault(d, {"card_debt": 0.0, "liquid": 0.0})
                if t == "credit" and bal < 0:
                    e["card_debt"] += -bal
                elif bal > 0 and (t == "depository" or (t in ("brokerage", "investment") and not retirement)):
                    e["liquid"] += bal
    except Exception:
        pass

    # Local daily snapshots override / extend the backfill.
    if os.path.exists(HISTORY):
        with open(HISTORY) as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                e = series.setdefault(r["date"], {"card_debt": 0.0, "liquid": 0.0})
                if r.get("card_debt") is not None:
                    e["card_debt"] = r["card_debt"]
                if r.get("liquid") is not None:
                    e["liquid"] = r["liquid"]

    out = [{"date": d, "card_debt": round(v["card_debt"], 2), "liquid": round(v["liquid"], 2)}
           for d, v in sorted(series.items())]
    return out


def money(x):
    return f"${x:,.2f}"


def summary(s):
    L = []
    tag = {"green": "OK", "amber": "TIGHT", "red": "SHORT"}[s["status"]]
    L.append(f"SAFE TO SPEND: {money(s['safe_to_spend'])}   [{tag}]")
    if s["safe_to_spend"] >= 0 and s["days_until_payday"] > 0:
        L.append(f"  {money(s['safe_per_day'])}/day for {s['days_until_payday']} days")
    else:
        L.append(f"  Short by {money(-s['safe_to_spend'])} before payday")
    L.append("")
    L.append(f"Checking ({s['account']}): {money(s['balance'])}")
    L.append(f"Next payday: {s['next_payday']} ({s['days_until_payday']} days), "
             f"about {money(s['expected_paycheck'])}")
    bills = s.get("bills_due_before_payday") or []
    if bills:
        L.append("")
        L.append("Due before payday:")
        for b in bills:
            L.append(f"  {b['due']}  {money(b['amount']):>12}  {b['name']}")
    L.append("")
    L.append(f"as of {s['generated_at']}")
    return "\n".join(L)


DASHBOARD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Can we afford it?</title>
<style>
  :root {
    --bg: #101317;
    --card: #181C22;
    --ink: #EDEBE6;
    --dim: #9BA3AD;
    --faint: #6A727C;
    --line: #262B33;
    --green: #5CB878;
    --amber: #D9A94A;
    --red: #E06C5E;
  }
  * { box-sizing: border-box; margin: 0; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    background: var(--bg);
    color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    min-height: 100vh;
    padding: max(20px, env(safe-area-inset-top)) 18px 48px;
  }
  .wrap { max-width: 480px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }
  .num { font-variant-numeric: tabular-nums; }

  .hero {
    text-align: center;
    padding: 28px 0 10px;
  }
  .hero .label {
    font-size: 12px; letter-spacing: .14em; text-transform: uppercase;
    color: var(--faint); font-weight: 600;
  }
  .hero .amount {
    font-size: clamp(56px, 18vw, 84px);
    font-weight: 700; letter-spacing: -0.03em; line-height: 1.05;
    margin: 6px 0 2px;
  }
  .hero .sub { font-size: 15px; color: var(--dim); }
  .s-green .amount { color: var(--green); }
  .s-amber .amount { color: var(--amber); }
  .s-red   .amount { color: var(--red); }

  .calc {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 14px; display: flex; gap: 10px; align-items: center;
  }
  .calc input {
    flex: 1; min-width: 0;
    background: var(--bg); color: var(--ink);
    border: 1px solid var(--line); border-radius: 10px;
    font-size: 22px; padding: 10px 12px; text-align: center;
    font-variant-numeric: tabular-nums;
  }
  .calc input:focus { outline: 2px solid var(--dim); }
  .calc button {
    background: var(--ink); color: var(--bg); border: 0; border-radius: 10px;
    font-size: 16px; font-weight: 650; padding: 12px 18px; cursor: pointer;
  }
  .calc button:active { transform: scale(.97); }
  #verdict {
    display: none; border-radius: 14px; padding: 16px; font-size: 17px;
    line-height: 1.45; border: 1px solid var(--line); background: var(--card);
  }
  #verdict.yes { border-color: var(--green); }
  #verdict.no  { border-color: var(--red); }
  #verdict b { font-size: 19px; }

  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 16px;
  }
  .card h2 {
    font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
    color: var(--faint); font-weight: 650; margin-bottom: 10px;
  }
  .row {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 7px 0; border-bottom: 1px solid var(--line);
    font-size: 15px; gap: 12px;
  }
  .row:last-child { border-bottom: 0; }
  .row .amt { font-weight: 650; white-space: nowrap; }
  .row .when { color: var(--faint); font-size: 13px; }

  .meter { margin: 10px 0 2px; }
  .meter .top {
    display: flex; justify-content: space-between; font-size: 14px; margin-bottom: 5px;
  }
  .meter .bar {
    height: 8px; border-radius: 4px; background: var(--line); overflow: hidden;
  }
  .meter .fill { height: 100%; border-radius: 4px; background: var(--green); }
  .meter.over .fill { background: var(--red); }
  .meter .top .r { color: var(--dim); }

  .runway { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
  .runway .months { font-size: 34px; font-weight: 700; letter-spacing: -0.02em; }
  .runway .rw-sub { font-size: 13px; color: var(--dim); }
  .rw-bar { height: 10px; border-radius: 5px; background: var(--line); overflow: hidden; }
  .rw-fill { height: 100%; border-radius: 5px; }
  .rw-detail { font-size: 13px; color: var(--dim); margin-top: 8px; line-height: 1.5; }
  .debt-head { display: flex; justify-content: space-between; align-items: baseline; }
  .debt-head span:first-child { font-size: 26px; font-weight: 700; letter-spacing: -0.02em; }
  .debt-head span:last-child { font-size: 14px; font-weight: 650; }
  .debt-axis { display: flex; justify-content: space-between; font-size: 11px;
               color: var(--faint); margin-top: 4px; }

  .pay {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 15px; color: var(--dim);
  }
  .pay b { color: var(--ink); font-size: 17px; }
  .stamp { text-align: center; color: var(--faint); font-size: 12px; margin-top: 6px; }
  .stamp.stale { color: var(--amber); }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero" id="hero">
    <div class="label">Safe to spend</div>
    <div class="amount num" id="big">…</div>
    <div class="sub num" id="sub"></div>
  </div>

  <div class="calc">
    <input id="amt" type="number" inputmode="decimal" placeholder="$ how much?" step="0.01" min="0">
    <button id="ask">Can we?</button>
  </div>
  <div id="verdict" class="num"></div>

  <div class="card">
    <div class="pay num" id="pay"></div>
  </div>

  <div class="card" id="billsCard" style="display:none">
    <h2>Due before payday</h2>
    <div id="bills"></div>
  </div>

  <div class="card" id="essCard" style="display:none">
    <h2>Has to last until payday</h2>
    <div id="ess"></div>
  </div>

  <div class="card" id="discCard" style="display:none">
    <h2>Choices this period</h2>
    <div id="disc"></div>
  </div>

  <div class="card" id="runwayCard" style="display:none">
    <h2>Runway</h2>
    <div class="runway num">
      <div class="months" id="rwMonths">…</div>
      <div class="rw-sub" id="rwSub"></div>
    </div>
    <div class="rw-bar"><div class="rw-fill" id="rwFill"></div></div>
    <div class="rw-detail num" id="rwDetail"></div>
  </div>

  <div class="card" id="debtCard" style="display:none">
    <h2>Card debt</h2>
    <div class="debt-head num">
      <span id="debtNow"></span>
      <span id="debtDelta"></span>
    </div>
    <svg id="debtChart" viewBox="0 0 320 84" preserveAspectRatio="none"
         style="width:100%;height:84px;display:block;margin-top:8px"></svg>
    <div class="debt-axis num"><span id="debtFrom"></span><span id="debtTo"></span></div>
  </div>

  <div class="stamp" id="stamp"></div>
</div>

<script>
"use strict";
let S = null;

const $ = id => document.getElementById(id);
const money = x => (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("en-US",
  {minimumFractionDigits: Math.abs(x) < 100 ? 2 : 0, maximumFractionDigits: Math.abs(x) < 100 ? 2 : 0});

function render(s) {
  S = s;
  const hero = $("hero");
  hero.className = "hero s-" + s.status;
  $("big").textContent = money(s.safe_to_spend);
  $("sub").textContent = s.safe_to_spend >= 0 && s.days_until_payday > 0
    ? money(s.safe_per_day) + "/day for " + s.days_until_payday + " days"
    : "short by " + money(-s.safe_to_spend) + " before payday";

  $("pay").innerHTML = "<span>Payday " + s.next_payday.slice(5).replace("-", "/") +
    " · " + s.days_until_payday + " day" + (s.days_until_payday === 1 ? "" : "s") +
    "</span><b>+" + money(s.expected_paycheck) + "</b>";

  const bills = s.bills_due_before_payday || [];
  $("billsCard").style.display = bills.length ? "" : "none";
  $("bills").innerHTML = bills.map(b =>
    '<div class="row num"><span>' + b.name +
    ' <span class="when">' + b.due.slice(5).replace("-", "/") + '</span></span>' +
    '<span class="amt">' + money(b.amount) + "</span></div>").join("");

  const ess = s.essentials_detail || [];
  $("essCard").style.display = ess.length ? "" : "none";
  $("ess").innerHTML = ess.map(e => {
    const pct = Math.min(100, e.period_allowance > 0 ? e.spent / e.period_allowance * 100 : 0);
    const over = e.spent > e.period_allowance;
    return '<div class="meter' + (over ? ' over' : '') + '"><div class="top num"><span>' +
      e.name + '</span><span class="r">' + money(e.spent) + ' of ' + money(e.period_allowance) +
      '</span></div><div class="bar"><div class="fill" style="width:' + pct + '%"></div></div></div>';
  }).join("");

  const d = s.discretionary || {};
  const buckets = d.by_bucket || {};
  const keys = Object.keys(buckets);
  $("discCard").style.display = keys.length ? "" : "none";
  const rem = d.period_remaining || 0;
  $("disc").innerHTML =
    '<div class="row num"><span>Budget this period</span><span class="amt">' +
    money(d.period_budget || 0) + '</span></div>' +
    keys.map(k => '<div class="row num"><span>' + k + '</span><span class="amt">' +
      money(buckets[k]) + '</span></div>').join("") +
    '<div class="row num"><span><b>' + (rem >= 0 ? "Left" : "Over") + '</b></span>' +
    '<span class="amt" style="color:var(--' + (rem >= 0 ? "green" : "red") + ')">' +
    money(Math.abs(rem)) + "</span></div>";

  const rw = s.runway;
  $("runwayCard").style.display = rw ? "" : "none";
  if (rw) {
    const m = rw.months_at_current_pace;
    const mb = rw.months_at_budget_pace;
    const col = m == null ? "var(--green)" : m < 3 ? "var(--red)" : m < 8 ? "var(--amber)" : "var(--green)";
    $("rwMonths").textContent = m == null ? "steady" : "≈ " + m + " mo";
    $("rwMonths").style.color = col;
    $("rwSub").textContent = m == null
      ? "not losing money at the current pace"
      : "at the last-30-days pace";
    const fill = $("rwFill");
    fill.style.background = col;
    fill.style.width = (m == null ? 100 : Math.max(4, Math.min(100, m / 15 * 100))) + "%";
    $("rwDetail").innerHTML =
      "Reachable money: <b>" + money(rw.liquid_reachable) + "</b> · burning " +
      money(rw.burn_30d) + "/mo right now" +
      (mb != null ? "<br>If the budget holds: ≈ <b>" + mb + " months</b>" +
       " (burn drops to " + money(rw.budget_burn) + "/mo)"
       : "<br>If the budget holds: not losing money");
  }

  const gen = new Date(s.generated_at);
  const hrs = (Date.now() - gen.getTime()) / 36e5;
  const st = $("stamp");
  st.textContent = "updated " + gen.toLocaleString([], {weekday:"short", hour:"numeric", minute:"2-digit"});
  st.className = "stamp" + (hrs > 12 ? " stale" : "");
  if (hrs > 12) st.textContent += " · stale";
}

async function drawDebt() {
  let H = [];
  try {
    const r = await fetch("/history.json", {cache: "no-store"});
    if (r.ok) H = await r.json();
  } catch (e) { return; }
  H = H.filter(p => p.card_debt > 0);
  if (H.length < 2) return;
  $("debtCard").style.display = "";

  const now = H[H.length - 1], first = H[0];
  const back30 = H[Math.max(0, H.length - 31)];
  const delta = now.card_debt - back30.card_debt;
  $("debtNow").textContent = money(now.card_debt);
  const dd = $("debtDelta");
  dd.textContent = (delta <= 0 ? "▼ " : "▲ ") + money(Math.abs(delta)) + " / 30d";
  dd.style.color = delta <= 0 ? "var(--green)" : "var(--red)";

  const vals = H.map(p => p.card_debt);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.1, 1);
  const y = v => 78 - ((v - (lo - pad)) / ((hi + pad) - (lo - pad))) * 72;
  const x = i => i / (H.length - 1) * 320;
  const pts = H.map((p, i) => x(i).toFixed(1) + "," + y(p.card_debt).toFixed(1)).join(" ");
  const rising = delta > 0;
  $("debtChart").innerHTML =
    '<polyline points="' + pts + '" fill="none" stroke="' +
    (rising ? "#E06C5E" : "#5CB878") + '" stroke-width="2" stroke-linejoin="round"/>' +
    '<circle cx="' + x(H.length - 1).toFixed(1) + '" cy="' + y(now.card_debt).toFixed(1) +
    '" r="3.5" fill="' + (rising ? "#E06C5E" : "#5CB878") + '"/>';
  $("debtFrom").textContent = first.date.slice(5).replace("-", "/");
  $("debtTo").textContent = "today · low " + money(lo) + " · high " + money(hi);
}
drawDebt();

function ask() {
  const v = parseFloat($("amt").value);
  const out = $("verdict");
  if (!S || isNaN(v) || v <= 0) { out.style.display = "none"; return; }
  const after = S.safe_to_spend - v;
  const days = S.days_until_payday;
  out.style.display = "block";
  if (S.safe_to_spend < 0) {
    out.className = "num no";
    out.innerHTML = "<b>Not now.</b> There is already a shortfall of " + money(-S.safe_to_spend) +
      " before payday. After payday on " + S.next_payday.slice(5).replace("-", "/") + ", ask again.";
  } else if (after >= 0) {
    out.className = "num yes";
    out.innerHTML = "<b>Yes.</b> " + money(v) + " takes the number to " + money(after) +
      (days > 0 ? " (" + money(after / days) + "/day until payday)." : ".");
  } else {
    out.className = "num no";
    out.innerHTML = "<b>Not yet.</b> " + money(v) + " is " + money(-after) +
      " more than what's free. It fits after payday on " + S.next_payday.slice(5).replace("-", "/") + ".";
  }
}

$("ask").addEventListener("click", ask);
$("amt").addEventListener("keydown", e => { if (e.key === "Enter") ask(); });

async function tick() {
  try {
    const r = await fetch("/status.json", {cache: "no-store"});
    if (r.ok) render(await r.json());
  } catch (e) { /* leave last render in place */ }
}
tick();
setInterval(tick, 60000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(DASHBOARD, "text/html; charset=utf-8")
            return
        if not os.path.exists(STATE):
            self.send_error(503, "No status yet. Run scripts/engine.py")
            return
        with open(STATE) as f:
            s = json.load(f)
        if path == "/status.json":
            self._send(json.dumps(s, indent=1), "application/json; charset=utf-8")
        elif path == "/history.json":
            self._send(json.dumps(build_history()), "application/json; charset=utf-8")
        elif path == "/plain":
            self._send(summary(s), "text/plain; charset=utf-8")
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass


def _bind():
    try:
        with open(os.path.join(ROOT, "config", "household.json")) as f:
            return (json.load(f).get("serve") or {}).get("bind") or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    bind = _bind()
    print(f"Dashboard on http://{bind}:{PORT}")
    HTTPServer((bind, PORT), Handler).serve_forever()
