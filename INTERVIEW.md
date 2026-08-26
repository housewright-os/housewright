# The Household Interview

You are an AI agent and a household just handed you this repo. Before you
install anything or edit any config, conduct this interview. Housewright
configured for the wrong household is worse than no Housewright: the goal
here is that YOU know what is actually going on in this house before a
single lane turns on.

## How to conduct it

- One topic at a time, conversationally. Never dump this file's questions
  as a wall of text. Follow up on interesting answers; skip what they have
  clearly already told you.
- Anything can be skipped. "Skip" is a complete answer; note the gap and
  move on. An incomplete profile with true answers beats a complete one
  with guesses.
- NEVER ask for account numbers, passwords, card numbers, logins, or exact
  balances. Money amounts (bills, income) are entered by the humans into
  config themselves, or dictated to you explicitly; you never fish for
  them.
- Record the minimum about children: count and age bands (under 5 / 5-9 /
  10-13 / teens) are enough for meal planning and calendar work. First
  names only if the family volunteers them for calendar labeling.
- Allergies are safety-critical: record them verbatim, confirm them back,
  and treat them as hard constraints in every meal suggestion forever.
  Never downgrade an allergy to a preference, and treat an uncertain one
  ("maybe gluten?") as real until the humans say otherwise.
- Keep it to roughly ten minutes: at most one follow-up per topic. This
  is an intake, not an interrogation; depth comes later, in use.
- Write answers ONLY into the documented profile schema and configs
  below. Do not build free-form dossiers about the family anywhere else.
- A skipped question stays skipped: never re-ask it in later sessions
  unless the human reopens the topic themselves.
- Never open, test, or verify any account, login, or service during the
  interview. Talk only.
- At the end, play the whole profile back in plain language, get a "yes,
  that's us," and only then write it down (see "Where answers land").

## 1. The goal (ask this first, it shapes everything)

- What does winning look like in six months: the same food for less money,
  easier weeks with less decision-making, better food at the same cost, or
  mostly getting the mental load off one person's plate?
- Is there a target? A dollars-per-serving number, a monthly grocery
  ceiling, a "dinner decided by 3pm every day," an "inbox never misses a
  school event"? If they have never computed cost per serving, do not ask
  them to invent one: the Channel Audit (PLAYBOOKS.md 1) produces the real
  baseline, and targets come after baselines.
- Which pain came first? The reason they cloned this repo is the lane to
  configure first.

## 2. The household

- Adults in the household, and who does what today: who cooks, who shops,
  who tracks money, who carries the calendar in their head. (This is the
  load you are redistributing; you need to know where it sits.)
- Kids: how many, age bands. Activities that generate schedule chaos
  (sports, music, school events)? Activity SCHEDULES belong to the
  calendar lane, not the profile; here you only need to know the chaos
  exists.
- Anyone else the system should know about: a grandparent whose
  appointments matter, a pet with its own supply channel?

## 3. The kitchen (equipment shapes the meal system)

- What do they own and actually use: slow cooker, pressure cooker, smoker,
  air fryer, stand mixer, chest freezer, second fridge, canner, grill?
- Freezer space honestly: can a bulk meat case fit, or is batch-cooking
  limited to fridge-week scale?
- How many minutes does a weeknight dinner get before it becomes takeout?
  And is there one day a week where 2-3 hours of batch cooking could
  happen, and which day?

## 4. Food

- Allergies first (safety-critical, verbatim, confirmed back).
- Dietary patterns: vegetarian days, halal, kosher, low-carb, anything the
  meal system must respect.
- The honest kid-food floor: which staples do the kids reliably eat? A
  meal plan that ignores this gets abandoned in week one.
- Hard no's and beloved staples for the adults. Cuisines the household
  actually enjoys (the reference household runs on Indian and Mexican
  staples because that is what they like; yours should run on what you
  like).

## 5. Money (process questions, not amounts)

- Who should see the money number: both adults? Is there appetite for one
  shared Safe-to-Spend figure, or is that a later conversation?
- Paycheck rhythm: weekly, biweekly, monthly, irregular? (Cadence, not
  amounts.)
- What tracks money today: an app, a spreadsheet, nobody? This decides the
  money.source setting: simplefin, csv, a monarch-style bring-your-own
  client, or leaving the lane off for now.
- Comfort check: the money lane is read-only by design and the budget file
  is edited by the humans together. Confirm they want it at all; skipping
  it is a fine answer.

## 6. Shopping rails

- Which stores are actually reachable or deliverable: the discounters, a
  warehouse club (membership owned?), local supermarkets, delivery
  services (memberships owned, and whose login)?
- Current default: who shops, where, how often, delivery or pickup or
  in-store?
- Any rail they refuse (ethics, quality, a bad experience)? Respect it in
  every suggestion; do not relitigate it.

## 7. Rhythm and communications

- Where should the house talk to the system: Telegram works today; is
  everyone willing to use it?
- Confirm quiet hours (default 21:00-07:00) and the one exception (freezer
  power loss).
- Morning brief time, evening wrap time: the defaults are 7:00 and 20:30;
  adjust to the household's real rhythm.
- Message logging is OFF by default conceptually: only enable the family
  chat log if EVERY adult consents individually, to you or in the shared
  chat where you can see it; one adult cannot consent for another. Record
  the consent in the config note. Explain plainly what is logged and that it never
  leaves the machine.

## 8. The calendar

- Which email account receives the school/sports/activity mail?
- Is there a shared family calendar already (Google family group calendars
  auto-share to every member)? If not, creating one is a 2-minute human
  step during setup.
- Which senders are noise (newsletters, promotions) for the deny list?

## Where answers land

1. `config/household.json` gains a `profile` block (see the example
   config): goals, equipment, dietary (allergies separated and marked
   safety-critical), kid food floor, weeknight minutes, batch day, rails
   posture. This block is read by YOU, the agent, whenever you plan meals,
   build carts, or propose anything: it is your standing context, so keep
   it current when the household tells you something new.
2. `config/budget.json`: cadence and structure from section 5; every
   dollar amount entered by the humans.
3. `family_events` config: account, calendar id, deny list from section 8.
4. Lane on/off decisions: only configure the lanes whose pain the
   household actually named. A house that came for meal planning does not
   need the money lane turned on in week one.
5. Then proceed to AGENTS.md for the installation itself, and PLAYBOOKS.md
   playbook 1 or 5 for the first win, chosen by the goal from section 1.

## After the interview

Summarize back: the goal, the first lane, what was skipped, and the one
concrete thing that will be visibly better within a week. Underpromise:
this system earns trust by the freezer alert that works and the Tuesday
game that lands on the calendar, not by the size of the plan.
