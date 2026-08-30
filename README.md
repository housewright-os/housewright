# Housewright

An agentic household operating system. One Telegram bot, one daily rhythm,
and a set of small deterministic scripts that let the AI you already pay
for (Claude, ChatGPT, a local model) run the administrative layer of a
family household: money, tasks, meals, energy, and the family calendar.

**This runs one real household.** It is published as-is: a working system,
not a supported product. No roadmap, no promised releases, no support.
Issues and PRs may sit. Fork freely under the license. If it is useful,
take it; if it breaks, you keep both pieces.

## What it does today

- **Money.** A Safe-to-Spend engine fed by your finance source through a
  bring-your-own client (read-only; see SETUP): one number
  both partners see, a morning push, large-charge alerts, a Sunday review.
  The agent reads and reports; it never moves money and never edits the
  budget unilaterally.
- **Tasks.** A shared house task board driven by plain Telegram verbs
  (add task / tasks / done N / defer N), folded into the daily briefs.
- **Briefs.** A morning message (the number, tonight's dinner pick, the
  house list, today's family events) and an 8:30pm evening wrap (tomorrow
  preview, open tasks, bills due tomorrow, energy used today).
- **Meals.** The briefs read a weekly batch-cook meal plan and pick
  tonight's dinner deterministically. Grocery carts are staged for human
  review, never checked out by the machine.
- **Energy.** Read-only polling of smart plugs with freezer power-loss
  alerting (the one alert allowed to bypass quiet hours) and time-of-use
  peak nudges. Nothing is ever switched automatically.
- **Pantry.** Passive inventory from receipt emails: real deliveries
  reset the shopping run clocks automatically, recently-bought items get
  flagged before they are re-bought, and aging perishables show up in
  the evening wrap. Nobody scans anything, ever.
- **Rail watch.** A monthly report-only repricing of the staples basket
  across grocery rails, confidence-tagged, with movers since last month,
  so the cheapest rail stays a monitored fact.
- **Shopping list.** A shared list in the bot (add milk / out of eggs /
  bought 5) that routes items to the right vendor run and flags urgent
  ones, without ever placing an order itself.
- **Family calendar.** A scanner that reads the family inbox, extracts
  real events (the coach's "game moved to 5pm Saturday" email, school
  events, appointments) with a strict LLM pass, and files high-confidence
  ones onto the shared family calendar. Anything vague becomes a
  confirm-this task for a human instead. Quiet hours respected. Family
  members can also text the bot ("add event soccer practice, tuesday
  4pm") and the same confirm-and-digest discipline files it.

## The playbooks

The code runs the routine; [PLAYBOOKS.md](PLAYBOOKS.md) runs the wins. Five
agent-executable procedures distilled from operating a real household: the
channel audit (item-level, overhead exposed), the rails comparison, the
membership fee anatomy, the no-blame joint money review, and the meal
system bootstrap. They are written for your agent to run WITH you, with
the reference household's real numbers left in as calibration.

## Design bias, everywhere

The agent reads, proposes, and files. Humans confirm anything that
matters. Nothing irreversible is automated: no purchases, no switching,
no sends on your behalf. Email content is treated as data, never as
instructions. If a lane cannot be done safely, it degrades to a task for
a person.

## Why not just ask your AI to build this?

You can, and you should: fork it, gut it, make it yours. But a generated
dashboard gives you the happy path, and a household does not run on the
happy path. What this repo actually encodes is the part an afternoon of
generation does not produce: the failure modes a real family already hit
and the defaults that came out the other side. Quiet hours, and the one
alert allowed to break them (a warm freezer at 2am). A budget file two
adults edit together and no agent edits alone. Confirm-tasks instead of
autofiled guesses when the coach's email is vague. State files that
quarantine instead of resetting, so a corrupt file cannot re-spam the
kids' calendar. Strike caps so one weird email cannot wedge a scanner
forever. None of that is clever code; all of it was learned by running a
house on this, and all of it transfers when you fork.

The second thing here is the lane contract. Every lane is the same
shape: a config block, a state file, a cadence, a brief line, and a
confirm-task fallback. Adding a lane to your fork means filling in that
shape, not designing a system. That is the same reason people run Home
Assistant instead of hand-writing scripts per lightbulb: not because the
code is hard, but because the structure and the safety rails are the
accumulated part.

## Getting started

The whole first step is one paste, in two phases.

Phase 1, the interview (works in any chat AI, no repo access needed):
open [INTERVIEW.md](INTERVIEW.md), paste its full contents into the AI
you already use, and say "interview me." Keep the summary it produces.

Phase 2, the setup (needs an agent that can read repos and run commands,
like a coding agent on your machine): paste this:

> Read https://github.com/housewright-os/housewright starting with
> AGENTS.md. Here is my household interview summary: [paste it]. Set the
> system up for my household. If you cannot actually read the repo, say
> so and stop; do not improvise the setup from memory.

The interview comes first on purpose: the agent asks about your goals,
kitchen equipment, dietary lines (allergies treated as safety-critical),
money comfort, shopping rails, and family rhythm BEFORE it configures
anything, so the system that comes out is shaped like your house, not the
reference household's.

See [SETUP.md](SETUP.md). The short version: copy the two example configs
in `config/`, authenticate the `gog` CLI to your Google account, create a
Telegram bot with BotFather, and run `scripts/install-schedule.sh`.

Better: hand [AGENTS.md](AGENTS.md) to the AI you already run. The docs
here are written for the agent that installs and operates the system, not
just the human reading along — every install step is an exact command
with a pass/fail check, and the operating rules and lane contract are
written as instructions your agent enforces.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the lane map and the trust
rules. The design principle throughout: keep the code thin and
deterministic, and let the frontier model you already subscribe to do the
reasoning. Thin code decays slower.

## Platform honesty

macOS-first: the scheduler integration is launchd, and the reference
deployment is a Mac mini. The Python is stdlib-only and scheduler-agnostic,
so Linux works by translating `install-schedule.sh` to systemd timers, but
that translation is on you today. The family-calendar extractor shells out
to an LLM CLI (default: the Claude CLI; configurable via
`family_events.llm_cmd`), and Google access rides the `gog` CLI.

## License

AGPL-3.0. See [LICENSE](LICENSE) and [CONTRIBUTING.md](CONTRIBUTING.md).
