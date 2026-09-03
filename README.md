# Bank Reconciliation & Exception-Flagging Agent

Built for Razorpay AI Buildathon 2026 — Track 4: AI Finance Controller.

## The problem

Every finance team runs the same loop: does what the bank/payment gateway says
happened match what our internal books say happened? In practice it never
matches perfectly — settlement lag, rounding, partial refunds, double
entries, and in-flight transactions all create drift. Someone has to work
through the mismatches by hand every cycle.

This agent automates that loop: it ingests a bank statement and an internal
ledger, matches what it can with confidence, and produces a clean, documented
list of exactly what it couldn't resolve and why — so a human only has to
look at the genuinely hard cases.

## How it works

1. **Deterministic pass** — exact match by transaction ID, amount, and date.
   No AI involved; a plain join handles this reliably and for free.
2. **Tolerance pass** — still deterministic. Small amount drift (≤₹5,
   rounding) or settlement lag (≤2 days) is auto-accepted against fixed
   thresholds.
3. **AI judgment pass** — only for rows that survive both passes: same
   transaction ID but drift too large to auto-accept. The agent sends the
   two records to an LLM and asks a same/different question with a one-line
   reason attached. This is the only place AI is used — everywhere else a
   rule does the job better and cheaper.
4. **Exception classification** — anything unresolved is bucketed by type
   (`missing_in_ledger`, `missing_in_bank`, `duplicate_in_ledger`,
   `unhandled_pattern`) rather than dumped as one generic "mismatch" pile,
   so a reviewer knows what kind of fix each one needs.

## Why AI only shows up once

Reconciliation is mostly a lookup problem, not a reasoning problem. Using an
LLM for every row would be slower, more expensive, and less auditable than a
join. The agent is built to prove the opposite instinct: use AI exactly
where a threshold can't make the call, and nowhere else.

## Results on the sample batch (55 synthetic transactions)

| Metric | Value |
|---|---|
| Total transactions | 55 |
| Matched | 46 |
| Exceptions (flagged for review) | 9 |
| Match rate | 83.6% |
| AI calls made | 1 |
| Runtime | <0.01s (excluding AI call latency) |

Exception breakdown:
- **missing_in_ledger** (3) — bank shows a transaction the books never
  recorded — the highest-priority category, since it's real money movement
  with no internal trace.
- **missing_in_bank** (3) — booked internally but not yet settled — normal
  in-flight state, lowest priority.
- **duplicate_in_ledger** (2) — same transaction ID entered twice
  internally — a double-entry bug, needs manual dedupe.
- **unresolved drift** (1) — sent to the AI judgment layer, resolved as a
  match with a documented reason.

## What broke while building this (Failure Recovery)

- **First version used amount as the join key instead of transaction ID.**
  This silently merged unrelated transactions that happened to have close
  amounts. Fixed by switching to ID-first matching with amount/date used
  only for confidence scoring, not identity.
- **The AI layer originally ran on every unmatched row**, including the
  "obviously missing" ones (present on only one side). That's wasted
  cost — there's no ambiguity to resolve when a record simply doesn't
  exist on the other side. Restricted AI calls to only the case where both
  sides have the same ID but disagree on details.
- **No graceful fallback if `ANTHROPIC_API_KEY` isn't set** in the first
  draft — the whole pipeline crashed. Added a transparent rule-based
  fallback so the reconciliation still runs and is auditable end-to-end
  without credentials, with the fallback clearly labeled in the output
  (never silently pretending to be an AI judgment).

## Running it

```bash
pip install -r requirements.txt   # only needs Python stdlib + optional requests
python3 generate_data.py          # creates bank_statement.csv, internal_ledger.csv
python3 reconcile.py              # runs the agent, prints summary, writes report.json
```

Set `ANTHROPIC_API_KEY` as an environment variable to enable real AI
judgment calls; without it, the script runs on a transparent fallback
heuristic (clearly labeled in the output) so the pipeline is always
runnable.

## Files

- `generate_data.py` — builds the synthetic bank/ledger datasets with
  realistic drift, missing rows, and duplicates
- `reconcile.py` — the agent itself
- `report.json` — full output: match rate, matched detail, exception detail
- `bank_statement.csv`, `internal_ledger.csv` — the test batch
