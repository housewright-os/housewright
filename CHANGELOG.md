# Changelog

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
