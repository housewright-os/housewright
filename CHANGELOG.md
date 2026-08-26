# Changelog

## 0.5.0

The interview release. INTERVIEW.md: an agent-conducted onboarding
interview that runs BEFORE any configuration: goals (cheaper vs easier vs
better vs load-shifting, with baselines before targets), household shape,
kitchen equipment, dietary lines with allergies as verbatim
safety-critical constraints, money comfort (process questions only, never
amounts or credentials), shopping rails, rhythm, and consent items. The
README now opens with a two-phase paste flow: a chat-only interview
phase any AI can run, then a repo-capable setup phase with an explicit
stop-if-you-cannot-read-the-repo instruction. The example config gains
the `profile` block the interview writes and agents read as standing
context.

## 0.4.0

The lessons release, plus two bot lanes. PLAYBOOKS.md: five
agent-executable procedures distilled from a week of running the
reference household (channel audit, rails comparison, membership fee
anatomy, joint money review, meal system bootstrap), with de-identified
receipts kept as calibration. New operating rules in CLAUDE.md (data
honesty: staleness warnings, exact-vs-directional labeling, no
manual-logging features, item-level-or-it-did-not-happen). New:
shopping.py, a shared bot-driven shopping list with vendor routing and
cadence-based run suggestions (report-and-suggest only, never orders);
calendar quick-add by texting the bot, riding the family-events
confirm-and-digest discipline; optional family-message logging gated on
recorded all-adults consent, local-only and retention-bounded. Docs
caught up across ARCHITECTURE, README, and AGENTS.

## 0.2.0

The money lane no longer needs a budget-app subscription: new source
adapters selected by `money.source`. `simplefin` pulls bank data through
SimpleFIN Bridge (~$15/yr, tiny read-only protocol, access URL in the OS
keychain or env, one-time `pull_simplefin.py --claim`); `csv` imports a
bank-export file for free. Categories come from deterministic substring
rules in `money.category_rules` (a server-provided category wins when
present). The `monarch` reference client keeps working unchanged.

## 0.1.0

Initial public export: money lane (bring-your-own finance client), shared
task board and Telegram bot, morning and evening briefs, meal-plan dinner
pick, read-only energy watch with freezer alerting, and the family-calendar
email scanner. Docs written agent-first (AGENTS.md). Clean-room export with
fresh history; the private deployment repo is never published.
