# Discipline strategy backtest (v1)

This feature is separate from the existing AI-analysis backtest. It evaluates only the user's approved rule set and must not create discretionary trades.

## Discipline 1 — sell signal
A daily bar triggers only when both are true:
- Today's high touches/exceeds the upper Bollinger band calculated from the previous 20 completed weekly closes (2 standard deviations).
- Today's close-to-close percentage change is <= -2%.

## Discipline 2 — buy candidate
All four conditions must be true on the signal date:
- Today's low touches/breaks the lower weekly Bollinger band calculated from the previous 20 completed weekly closes (2 standard deviations).
- Today's percentage change is >= +2%.
- Point-in-time PE(TTM) <= 15.
- Latest attributable net profit YoY growth already publicly available by the signal date is > 10%.

The current partial week is not used to calculate the band. This avoids using a future Friday close for a Monday–Thursday signal. The current week's daily high/low is used only to test the touch.

## Point-in-time (PIT) requirements
Every valuation/fundamental observation must have an available_on date. The engine rejects an observation whose availability date is after the signal date. For financial statements, available_on must be the actual public announcement date, not the reporting-period end date. A modern PE snapshot or latest financial value must never be backfilled into older dates.

## Discipline 3 — official event handling
Only verified primary-source events are classifiable:
- A: formal ST/*ST, delisting decision/consolidation period, CSRC-confirmed major fraud or disclosure violation, adverse/disclaimer audit opinion, court acceptance of bankruptcy liquidation, delisting-risk warning. Action: sell trigger.
- B: material debt default, core-operation suspension, material asset freeze, confirmed controlling-shareholder fund occupation, material illegal guarantee. Action: risk watch only; no automatic sale.
- C: consecutive losses, persistent negative operating cash flow, abnormal receivables/inventory growth, persistent non-recurring profit decline. Action: financial monitoring only; no automatic sale.

Accepted source types: exchange/company announcements, CSRC decisions, court documents, audit reports and bond-default announcements. News reports, forums and rumors alone do not qualify. Store event date, announcement/availability date, category, source type, primary URL and verification status. A later announcement becomes knowable only on its announcement date.

## Backtest protocol
- Universe: historical A-share listings, including delisted securities and historical ST states (survivorship-bias control).
- Recommended period: 2021 onward, subject to actual source coverage.
- Evaluate Discipline 2 forward returns at 5/10/20 trading days; report win rate, mean/median return, max favorable/adverse excursion, annual/industry breakdown, and unfilled/limit-up/limit-down cases.
- Evaluate Discipline 3 as event studies at 5/20/60 trading days. Keep A/B/C separate. For A events, simulate the first executable session/price after the public trigger; suspension/limit-down constraints must be reported rather than assumed executable.
- Include fees, slippage and price-limit execution assumptions as explicit run parameters.
- No performance result is valid until PIT coverage, delisted-stock coverage and data-quality checks are reported.

## Current implementation boundary
src/core/discipline_backtest.py contains pure signal and event classification logic. It does not fetch data, infer missing PIT dates, or claim historical performance. Database persistence, source adapters, API and UI integration are subsequent steps.
