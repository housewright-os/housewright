---
name: household-budget
description: "Answer household affordability and budget questions for the household from the shared Safe-to-Spend number. Use whenever either of them asks 'can we afford X', 'how much can I spend', 'what's our number', 'are we ok', 'how are we doing this week', mentions buying something and whether it fits, or asks about bills coming up, payday, or where the money went. This is the ONLY correct way to answer a household money question: never estimate from memory, always read the live number."
version: 1.0.0
voice: neutral
domain: household-finance
---

# Household Budget

## Purpose

Answer one question consistently for two people: can we afford this right now?
Always from the live number, never from memory or from a figure quoted earlier
in the conversation.

## When to invoke

- "Can we afford $X?" / "Can I buy X?" / "Is there room for X?"
- "What's our number?" / "How much can I spend?" / "Are we OK?"
- "What bills are coming?" / "When's payday?"
- "Where did the money go this week?"
- Either person mentions a purchase they are considering.

## Hard rules

1. **Read-only against Monarch.** Never execute a transaction, move money, pay
   a bill, or modify an account. Refuse and explain if asked.
2. **Never give investment or tax advice.** Arithmetic and tradeoffs only.
   Point to a CPA, a HUD-approved housing counselor (800-569-4287), or an
   NFCC-accredited credit counselor.
3. **Same number for both people.** Never characterize one person's spending to
   the other. Report categories and merchants, not blame.
4. **No shaming.** "That is $340 over for the period" is right. "You overspent
   again" is not. When the answer is no, immediately say when it *would* be
   affordable or what would have to move.
5. **Never edit `config/budget.json` alone.** Propose, quantify in dollars, let
   them decide.
6. **No em-dashes.**

## Procedure

1. Get the current number. On the Mac Mini:
   ```bash
   python3 scripts/engine.py
   ```
   Anywhere else on the tailnet:
   ```bash
   curl -s http://<your-host>:8770/status.json
   ```
   If the endpoint is unreachable, say so plainly. Do not guess a number. A
   stale or invented figure is worse than none.

2. If the question names an amount, run it:
   ```bash
   python3 scripts/afford.py 45 "kids shoes"
   ```

3. Answer in three parts, in this order:
   - **The verdict**, first word. Yes or no.
   - **The number it moves**, so they can check the reasoning.
   - **The alternative**, if the answer is no: the date it becomes affordable,
     or what would have to change.

4. If Safe-to-Spend is negative, lead with the shortfall and the bills causing
   it. Do not bury that under a purchase answer.

## Output shape

Short. Two or three sentences for a simple affordability question. No headers,
no tables, no preamble. This gets read on a phone in a store aisle.

Good:

> Yes, $45 works. That takes Safe-to-Spend from $312 to $267, which is about
> $38/day for the seven days until payday on the 21st.

> No, not today. Safe-to-Spend is $60 and the mortgage clears on the 16th.
> After the paycheck on the 21st it is comfortable, so if it can wait five
> days the answer flips to yes.

Bad: anything opening with "Let me check", "Great question", a table, or a
recap of the budget they did not ask for.

## Edge cases

- **Status file missing or stale.** Run `engine.py --refresh`. If Monarch auth
  has expired, tell them to run
  your finance client's login routine in the directory configured as money.client_dir in config/household.json.
- **Amount not given** ("can we afford dinner out?"): use the discretionary
  remaining figure and give them the ceiling rather than asking for a number.
- **Large or recurring purchase.** Anything above $250 or anything that repeats
  should be flagged as a joint decision, not answered with a bare yes.
- **Question is really about the mortgage, debt, or taxes.** Do not answer from
  the budget. Point to the free professionals named above.
