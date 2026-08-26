# Playbooks

Procedures the agent runs WITH the household's humans. AGENTS.md covers
installing and operating the system; this file covers the recurring plays
that actually move money and reduce load. Each one was run for real in the
reference household before it was written down; the numbers quoted are that
household's receipts, kept as calibration so you know what "worked" means.

Ground rules for every playbook: the agent gathers, computes, and proposes;
humans decide and act. No purchases, no cancellations, no messages sent by
the agent. Numbers are labeled exact (read from a primary source or the
household's own accounts) or directional (search-derived); the code's
confidence tags use the same split.
No-blame framing is mandatory on anything involving shared spending: a
charge the agent cannot attribute is a visibility gap, never an accusation.

## 1. The Channel Audit

When: any recurring spending channel nobody has looked at item-by-item in
6+ months (grocery delivery, a marketplace account, subscriptions).

1. Pull the channel's item-level history for 30-90 days (order history,
   receipts in email, card transactions). Card statements alone are not an
   audit: the unit of insight is the ITEM, not the charge.
2. Categorize every item honestly, including a category for "not what this
   channel is for" (dog products riding grocery orders, business gear on
   the household card).
3. Separate ITEM COST from OVERHEAD (fees, markup, tips, memberships).
   Overhead hides inside delivered totals; compute it explicitly.
4. Present: totals by category, the overhead line, the three largest
   surprises, and at most five proposed changes ranked by dollars.
5. Route follow-ups: wrong-channel items get their own lane, business
   charges move to business payment methods, unknowns go to a joint
   review (playbook 4), never to speculation.

Reference receipts: a 30-day grocery-channel audit found only 36% of item
spend was actual meal food, a 20.5% delivery-overhead stack ($486/mo), and
duplicate orders of the same dog chews. A 90-day marketplace audit found the majority
of card charges had no order trail in the audited login (playbook 4
material) and business subscriptions riding household money.

## 2. The Rails Comparison

When: before assuming your current retailer or delivery layer is cheapest,
and any time the monthly rail-check digest (scripts/rail_check.py) shows
the champion flipping.

1. Fix a basket: 10-15 staples the household actually buys, with sizes.
2. Price the basket per rail from PRIMARY sources (the retailer's own site
   for its own delivery; the delivery layer's app for marked-up rails).
   Label every number exact or directional (search-derived). Never
   average away the difference.
3. Decompose each rail: shelf price, item markup, service fees, delivery
   fees, tips, membership. Memberships usually waive the visible fee, not
   the expensive part (see playbook 3).
4. Decide as a SPLIT, not a winner-take-all: the cheap-staples rail for
   commodity items, the bulk rail only for what it uniquely carries.
5. Feed the result back into config: the rail-check basket, and the
   grocery category rules if sources changed.

Reference receipts: the marked-up warehouse rail lost on 4 of 5 staples to
a first-party discounter (chicken 26% cheaper, no case commitment), one
national same-day service priced 32% above the discounter and was dropped,
and the legacy supermarket lost by 33-61% on everything and was retired.
The split-cart shape saved roughly $180-280/mo before rewards.

## 3. The Membership Anatomy

When: any paid membership that claims to make a channel cheaper (delivery
subscriptions, warehouse tiers, retailer plus programs).

1. Write the fee table: every fee type the channel charges, member vs
   non-member, from the provider's own pages. Include item markup, which
   marketing never lists as a fee.
2. Identify what the membership actually waives. In the reference case, a
   delivery membership waived delivery fees only; item markup (~10% even
   for members), service fees, and tips continued on every order, and the
   flagship bulk store was excluded from the waiver entirely.
3. Check whose login holds the membership (family accounts hide this) and
   when it renews.
4. Compute: membership cost + retained overhead vs the first-party
   alternative, at the household's real order volume.
5. Stack rewards honestly: cash-back only wins when balances clear monthly;
   a rewards card on carried debt is a loss, and the play gets flagged as
   a decide-together item, never auto-recommended.

## 4. The Joint Money Review

When: shared-card spend is partially invisible from the auditing login, or
any audit surfaces a category needing a decision both partners own.

1. Build a one-page review both partners see: wins already banked first,
   findings with numbers second, decisions third, split into "tonight"
   (max 3) and "this month."
2. Frame every unknown as a visibility gap. The card statement cannot say
   who bought what; the page must not pretend otherwise.
3. Each decision is one sentence plus its dollar effect. No decision on
   the page that either partner cannot veto.
4. The agent prepares the page and stops. The review is a conversation;
   the agent is not in the room.

Reference receipts: the one-page format turned a large
visibility gap into a calm 30-minute joint session instead of an argument,
alongside ~$300-450/mo of quantified, no-conflict switches.

## 5. The Meal System Bootstrap

When: food spend is dominated by delivery overhead, snacks, and per-serving
costs nobody has computed.

1. Run playbook 1 on the grocery channel first: the baseline per-meal cost
   is the number that motivates everything else.
2. Design a batch-cook week: one prep day, 4-6 dishes sharing prep and
   equipment the household actually owns, per-serving cost computed at
   shelf prices.
3. Verify the cart per rail (playbook 2) and STAGE it: the humans check
   out, always.
4. Write the prep-day runbook so every step carries its own numbers
   (quantities, temperatures, which dish each chopped onion belongs to).
   A step that requires opening another document mid-cook is a defect.
5. Never build features that depend on family members logging things
   manually (tap-to-take trackers die in a week). Capture passively from
   receipts and orders, or not at all.

Reference receipts: baseline $7.95 per effective meal-slot fell to $1.35
per serving in week one (56 dinner-plus-soup servings from ~$76 of
ingredients), with the batch plan built around a smoker and a slow cooker
the household already owned.
