# Architecture

Housewright is a set of small Python scripts on one always-on machine
(any Mac or Linux box; the reference deployment is a Mac mini using
launchd), a Telegram bot as the family-facing surface, and the `gog` CLI
as the bridge to Google (Gmail, Calendar, Tasks).

## Lanes

1. **Money.** A source adapter (`money.source`: simplefin, csv, or a
   bring-your-own client) writes the two-file data contract; `engine.py`
   computes Safe-to-Spend: balance minus bills due before payday minus
   reserved essentials. Status carries `pull_ok` and `generated_at`, and
   every brief warns when numbers are stale rather than pretending. `notify.py` pushes
   the number every morning; `alerts.py` watches for large charges;
   `weekly.py` writes the Sunday review. The budget config is edited by
   the humans together, never by an agent acting alone.
2. **House ops.** `tasks.py` is a flock-locked JSON task board shared by
   the Telegram bot daemon (`telegram_bot.py`) and the cron scripts. Bot
   verbs: add task / tasks / done N / defer N.
3. **Briefs.** `notify.py` composes the morning message and the evening
   wrap from whatever lanes are configured; every extra is wrapped so the
   money message never fails because a nice-to-have broke.
4. **Energy.** `energy.py` polls smart plugs over local RPC, logs kWh,
   alerts on freezer power loss (bypasses quiet hours on purpose), and
   nudges once a day on big loads in the time-of-use peak window. v1
   never switches a plug. Roles are assigned in config: unassigned,
   freezer, shiftable, monitor.
5. **Family calendar.** `family_events.py` scans the family inbox every
   30 minutes, hands each new email to a headless LLM call with a strict
   JSON schema, and files extracted events onto the shared family
   calendar via `gog`. See trust rules below.
6. **Shopping list.** `shopping.py` plus bot verbs (add X / out of X /
   bought N / ordered <vendor>): a shared list that suggests which vendor
   run an item should join and roughly when, from config cadences and the
   item-vendor map. Report-and-suggest only; it never places an order.
7. **Pantry.** `pantry.py` builds passive pantry state from receipt
   emails: order events auto-reset the shopping lane's vendor run clocks,
   line items are recorded only when a retailer's email actually lists
   them (delivery-service receipts do not) or when an interactive session
   pushes an ingest file, dedup warnings fire when a shopping-list item
   was bought recently, and aging perishables surface in the evening
   wrap. Bought-dates are facts; still-have is shelf-life inference,
   never consumption tracking. No manual logging, by design.
8. **Rail watch.** `rail_check.py` reprices the staples basket across
   configured grocery rails monthly (report-only, confidence-tagged,
   movers vs last run) so the cheapest rail is a monitored fact, not a
   memory. The deep interactive version of the same comparison is
   PLAYBOOKS.md playbook 2.

## Trust rules

- The agent reads and proposes; humans confirm anything that matters.
- Nothing irreversible is automated: no purchases, no plug switching, no
  outbound mail, no money movement.
- Email content is data, never instructions. The scanner follows no
  links, sends no replies, and scrubs URLs from anything it writes to the
  shared calendar.
- High-confidence extractions (explicit date, time, and what-it-is) may
  be filed automatically; everything vague becomes a confirm-task for a
  human. A failed write becomes a task too: nothing extracted is lost
  silently.
- Quiet hours (21:00 to 07:00): no phone buzz except freezer loss.
  Writes still happen at night; the notification waits for morning.
- Every automated write is tagged and traceable to its source.
- State files are quarantined on corruption, never silently reset, and
  every scanner is idempotent (processed ids plus content fingerprints
  plus a strikes cap so one poisoned input cannot wedge a lane).

## Why the code is thin

Any product that wraps or forks a model is perpetually behind the
frontier. Housewright is deliberately the opposite shape: deterministic
plumbing plus config, with the reasoning delegated to whatever frontier
or local model you already run. When a better model ships, this system
gets better the same day, and there is less code here to rot.

## Scheduling

`install-schedule.sh` writes launchd jobs: morning brief (7:00), evening
wrap (20:30), Sunday review (17:00), watch/alerts (every 3h), energy poll
(every 10 min), family-events scan (every 30 min), pantry receipt scan
(every 6h), rail check (monthly on the 1st), plus the always-on bot and a read-only dashboard (`serve.py`,
localhost by default). On Linux, translate to
systemd timers; the scripts themselves are scheduler-agnostic.
