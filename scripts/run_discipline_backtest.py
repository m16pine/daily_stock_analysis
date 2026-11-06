# -*- coding: utf-8 -*-
"""Run reproducible D1/D2/D3 backtests against already-ingested PIT data.

This command deliberately does not download or synthesize missing history. It
reports coverage gaps and marks a run partial when PIT valuation/financial data
or the historical security master is incomplete.
"""
from __future__ import annotations

import argparse
import json
import uuid
from collections import defaultdict
from datetime import date
from statistics import mean, median
from typing import Any

from sqlalchemy import select

from src.core.discipline_backtest import (
    DailyBar, PitValuation, PitFinancial, Discipline3Event,
    weekly_bollinger, discipline1_signal, discipline2_signal,
    classify_discipline3_event, discipline3_action,
)
from src.repositories.discipline_backtest_repo import DisciplineBacktestRepository
from src.storage import (
    DatabaseManager, StockDaily, DisciplineStockMaster,
    DisciplineValuationPIT, DisciplineFinancialPIT, Discipline3OfficialEvent,
)


def _weekly_closes(rows: list[StockDaily]) -> dict[date, list[float]]:
    by_week: dict[tuple[int, int], list[StockDaily]] = defaultdict(list)
    for row in rows:
        iso = row.date.isocalendar()
        by_week[(iso.year, iso.week)].append(row)
    result = {}
    for key, bars in sorted(by_week.items()):
        bars.sort(key=lambda x: x.date)
        result[bars[-1].date] = [float(bars[-1].close)]
    return result


def _summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "win_rate_pct": None, "mean_return_pct": None,
                "median_return_pct": None, "profit_loss_ratio": None}
    wins = [x for x in values if x > 0]
    gains = [x for x in values if x > 0]
    losses = [-x for x in values if x < 0]
    return {
        "count": len(values),
        "win_rate_pct": round(100 * len(wins) / len(values), 2),
        "mean_return_pct": round(mean(values), 3),
        "median_return_pct": round(median(values), 3),
        "profit_loss_ratio": round(mean(gains) / mean(losses), 3) if gains and losses else None,
    }


def run(start: date, end: date, horizons: list[int]) -> dict[str, Any]:
    db = DatabaseManager.get_instance()
    repo = DisciplineBacktestRepository(db)
    run_key = "discipline-" + uuid.uuid4().hex[:16]
    run_id = repo.create_run(
        run_key=run_key, strategy_version="v1.1",
        start_date=start, end_date=end,
        universe_definition="All codes present in stock_daily; NOT a survivorship-bias-free universe unless security master includes delisted securities.",
        parameters={"weekly_bollinger_period": 20, "num_std": 2,
                    "forward_horizons_sessions": horizons,
                    "entry": "next trading session open", "exit": "horizon close"},
    )
    repo.update_run(run_id=run_id, status="running")
    try:
        with db.get_session() as session:
            master = session.execute(select(DisciplineStockMaster)).scalars().all()
            rows = session.execute(
                select(StockDaily).where(StockDaily.date <= end)
                .order_by(StockDaily.code, StockDaily.date)
            ).scalars().all()
            valuations = session.execute(
                select(DisciplineValuationPIT).where(
                    DisciplineValuationPIT.available_on <= end,
                    DisciplineValuationPIT.observed_on <= end
                ).order_by(DisciplineValuationPIT.available_on, DisciplineValuationPIT.observed_on)
            ).scalars().all()
            financials = session.execute(
                select(DisciplineFinancialPIT).where(
                    DisciplineFinancialPIT.available_on <= end,
                    DisciplineFinancialPIT.announcement_date <= end
                ).order_by(DisciplineFinancialPIT.available_on, DisciplineFinancialPIT.report_period)
            ).scalars().all()
            events = session.execute(
                select(Discipline3OfficialEvent).where(
                    Discipline3OfficialEvent.announcement_date <= end
                )
            ).scalars().all()

        by_code: dict[str, list[StockDaily]] = defaultdict(list)
        for row in rows:
            by_code[row.code].append(row)
        val_by_code: dict[str, list[Any]] = defaultdict(list)
        fin_by_code: dict[str, list[Any]] = defaultdict(list)
        for x in valuations: val_by_code[x.code].append(x)
        for x in financials: fin_by_code[x.code].append(x)

        all_returns: dict[str, dict[int, list[float]]] = {
            "D1": defaultdict(list), "D2": defaultdict(list)
        }
        signals = {"D1": 0, "D2": 0, "D3_A": 0, "D3_B": 0, "D3_C": 0}
        data_gaps = {"missing_20_week_history": 0, "missing_pit_valuation": 0,
                     "missing_pit_financial": 0, "no_forward_entry": 0}
        scanned = 0

        for code, stock_rows in by_code.items():
            stock_rows.sort(key=lambda x: x.date)
            # Weekly close series are only complete through the previous week.
            week_ends = _weekly_closes(stock_rows)
            week_end_dates = sorted(week_ends)
            daily_bars = [
                DailyBar(code, x.date, float(x.open or 0), float(x.high or 0),
                         float(x.low or 0), float(x.close or 0), float(x.pct_chg or 0))
                for x in stock_rows
            ]
            val_list, fin_list = val_by_code[code], fin_by_code[code]
            v_idx = f_idx = 0
            latest_v = latest_f = None
            touched_upper_this_week = False
            active_week = None

            for i, bar in enumerate(daily_bars):
                if bar.trading_date < start or bar.trading_date > end:
                    continue
                scanned += 1
                iso = bar.trading_date.isocalendar()
                week_key = (iso.year, iso.week)
                if active_week != week_key:
                    active_week = week_key
                    touched_upper_this_week = False

                while v_idx < len(val_list) and val_list[v_idx].available_on <= bar.trading_date:
                    candidate = val_list[v_idx]
                    if candidate.observed_on <= bar.trading_date:
                        latest_v = candidate
                    v_idx += 1
                while f_idx < len(fin_list) and fin_list[f_idx].available_on <= bar.trading_date:
                    candidate = fin_list[f_idx]
                    if candidate.announcement_date <= bar.trading_date:
                        latest_f = candidate
                    f_idx += 1

                prior_weeks = [d for d in week_end_dates if d < bar.trading_date]
                closes = [week_ends[d][0] for d in prior_weeks[-20:]]
                if len(closes) < 20:
                    data_gaps["missing_20_week_history"] += 1
                    continue

                upper = weekly_bollinger(closes)
                if upper and bar.high >= upper.upper:
                    touched_upper_this_week = True

                d1 = discipline1_signal(
                    bar=bar, prior_completed_weekly_closes=closes,
                    upper_band_touched_earlier_this_week=touched_upper_this_week,
                )
                d2 = discipline2_signal(
                    bar=bar, prior_completed_weekly_closes=closes,
                    valuation=(PitValuation(code, latest_v.observed_on, latest_v.available_on, latest_v.pe_ttm) if latest_v else None),
                    financial=(PitFinancial(code, latest_f.announcement_date, latest_f.available_on, latest_f.attributable_net_profit_yoy_pct) if latest_f else None),
                )
                persisted_signal_ids = {}
                for sig in (d1, d2):
                    persisted_signal_ids[sig.discipline] = repo.save_signal(
                        run_id=run_id, code=code, signal_date=bar.trading_date,
                        discipline=sig.discipline, triggered=sig.triggered,
                        data_status=sig.data_status, reasons=list(sig.reasons),
                        band_upper=upper.upper if upper else None,
                        band_lower=upper.lower if upper else None,
                        pe_ttm=latest_v.pe_ttm if latest_v else None,
                        attributable_net_profit_yoy_pct=latest_f.attributable_net_profit_yoy_pct if latest_f else None)
                if d1.triggered: signals["D1"] += 1
                if d2.triggered: signals["D2"] += 1
                if d2.data_status == "missing_pit_valuation": data_gaps["missing_pit_valuation"] += 1
                if d2.data_status == "missing_pit_financial": data_gaps["missing_pit_financial"] += 1

                next_rows = [x for x in stock_rows if x.date > bar.trading_date]
                for discipline, triggered in (("D1", d1.triggered), ("D2", d2.triggered)):
                    if not triggered:
                        continue
                    if not next_rows or not next_rows[0].open:
                        data_gaps["no_forward_entry"] += 1
                        continue
                    entry = next_rows[0]
                    signal_id = persisted_signal_ids[discipline]
                    for h in horizons:
                        if len(next_rows) < h:
                            continue
                        exit_row = next_rows[h - 1]
                        if not entry.open or not exit_row.close:
                            continue
                        ret = (float(exit_row.close) / float(entry.open) - 1) * 100
                        future = next_rows[:h]
                        mfe = (max(float(x.high or 0) for x in future) / float(entry.open) - 1) * 100
                        mae = (min(float(x.low or entry.open) for x in future) / float(entry.open) - 1) * 100
                        all_returns[discipline][h].append(ret)
                        repo.save_forward_return(
                            signal_id=signal_id, horizon_sessions=h,
                            entry_date=entry.date, entry_price=float(entry.open),
                            exit_date=exit_row.date, exit_price=float(exit_row.close),
                            return_pct=ret, executable=True,
                            max_favorable_excursion_pct=mfe,
                            max_adverse_excursion_pct=mae,
                            execution_note="Next-session open entry; horizon-session close exit. Suspensions/limit locks require separate execution validation.")

        # Official event study: retain primary-source-only classification, do not infer trades
        for ev in events:
            if ev.announcement_date < start or ev.announcement_date > end:
                continue
            event = Discipline3Event(
                ev.code, ev.event_date, ev.announcement_date, ev.category,
                ev.source_type, ev.source_url, bool(ev.verified_official_source),
                ev.description or "",
            )
            level, reason = classify_discipline3_event(event)
            action = discipline3_action(level)
            if level:
                signals[f"D3_{level.value}"] += 1

        coverage = {
            "stock_codes_with_daily_data": len(by_code),
            "security_master_rows": len(master),
            "daily_bars_scanned_in_window": scanned,
            "pit_valuation_records": len(valuations),
            "pit_financial_records": len(financials),
            "official_event_records": len(events),
            "data_gaps": data_gaps,
            "survivorship_bias_warning": len(master) < len(by_code),
        }
        summary = {
            "signals": signals,
            "forward_returns": {
                d: {str(h): _summarize(vals) for h, vals in horizons_map.items()}
                for d, horizons_map in all_returns.items()
            },
            "note": "D1/D2 signal scan and D3 official event counts; forward returns are diagnostic, not a portfolio simulation. A full universe claim requires delisted/ST history and complete PIT coverage.",
        }
        status = "completed" if not any(data_gaps.values()) and len(master) >= len(by_code) else "partial"
        repo.update_run(run_id=run_id, status=status, data_coverage=coverage, result_summary=summary)
        return {"run_id": run_id, "run_key": run_key, "status": status,
                "coverage": coverage, "summary": summary}
    except Exception as exc:
        repo.update_run(run_id=run_id, status="failed", error_message=str(exc))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--horizons", default="5,10,20")
    args = parser.parse_args()
    result = run(date.fromisoformat(args.start), date.fromisoformat(args.end),
                 [int(x) for x in args.horizons.split(",")])
    print(json.dumps(result, ensure_ascii=False, indent=2))
