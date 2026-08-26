# Setup

Housewright assumes one always-on machine (the reference deployment is a
Mac mini; any box that can run Python 3.9+ and a scheduler works) and a
household Google account.

**Installing with an AI agent?** Point it at [AGENTS.md](AGENTS.md): the
same procedure as this page, written as exact commands with pass/fail
checks so your agent can run the install and verify it end to end.

## 1. Prerequisites

- Python 3.9+ (`python3 --version`).
- The [gog CLI](https://gogcli.sh) (`brew install gogcli`),
  authenticated to the Google account whose inbox and calendars the
  house runs on. Housewright uses Gmail (read), Calendar (read/write),
  and Tasks.
- Dependencies are deliberately thin: the Python is stdlib-only. External
  tools: the `gog` CLI (required), an LLM CLI for the family-calendar
  extractor (default: Claude CLI, configurable via `family_events.llm_cmd`),
  and `uv` only if you run the money lane.
- The money lane needs a data source, picked by `money.source`:
  `simplefin` (recommended: SimpleFIN Bridge, ~$15/yr, connect once with
  `python3 scripts/pull_simplefin.py --claim <setup-token>`), `csv` (free:
  a bank-export file, see the `scripts/pull_csv.py` docstring), or
  `monarch` (a bring-your-own client per the `scripts/pull.py` docstring).
  Whichever source, write `money.category_rules`: external sources carry no
  categories, and two rules are load-bearing (your employer name ->
  Paychecks for payday detection; your grocery stores -> Groceries for the
  essentials reservation). Leave `source` empty and the lane skips cleanly:
  the briefs simply omit the money block.

## 2. Configuration

```
cp config/budget.example.json config/budget.json
cp config/household.example.json config/household.json
```

Edit both. `budget.json` is the money contract: income cadence, fixed
bills, essentials, discretionary buckets, thresholds. House rule worth
keeping: this file is edited by the humans together; agents propose,
people commit. `household.json` is everything else: meal plan directory,
brief settings, smart-plug inventory and roles, and the family-events
scanner (account, target calendar id, Gmail query, deny list).

Real configs are gitignored. Only the examples are tracked.

## 3. Telegram bot

1. Talk to @BotFather, create a bot, keep the token (it looks like
   `<numeric-id>:<secret>`).
2. Put the token in your shell profile as `TELEGRAM_BOT_TOKEN` (it is
   gitignored here by design; the install script embeds it into the
   launchd jobs from your environment).
3. Message your bot once, then add your chat id to
   `budget.json` under `notify.telegram_chat_ids`.

Every sender degrades to stdout when the token is unset, so it is safe
to install the schedule before the bot exists.

## 4. Family calendar lane

Point `family_events.calendar_id` at a calendar every family member can
see. If your Google account has a family group, the built-in Family
calendar is ideal: events filed there appear on everyone's devices with
zero per-person setup. Find its id with:

```
gog -a you@gmail.com calendar calendars -p
```

## 5. Install the schedule

```
TELEGRAM_BOT_TOKEN=... ./scripts/install-schedule.sh
launchctl list | grep housewright
```

Dry-run any lane first: `python3 scripts/family_events.py --dry-run`,
`python3 scripts/notify.py --dry-run`.
