# -*- coding: utf-8 -*-
"""Persistence helpers for the experimental discipline backtest subsystem."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import and_, select

from src.storage import (
    DatabaseManager,
    Discipline3OfficialEvent,
    DisciplineBacktestRun,
    DisciplineFinancialPIT,
    DisciplineForwardReturn,
    DisciplineSignalRecord,
    DisciplineStockMaster,
    DisciplineValuationPIT,
)


class DisciplineBacktestRepository:
    """Thin repository over the existing SQLAlchemy/SQLite database."""

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager.get_instance()

    def upsert_stock(self, *, code: str, name: Optional[str] = None,
                     exchange: Optional[str] = None, listed_on: Optional[date] = None,
                     delisted_on: Optional[date] = None,
                     security_type: str = "A_SHARE") -> None:
        with self.db.session_scope() as session:
            row = session.get(DisciplineStockMaster, code)
            if row is None:
                row = DisciplineStockMaster(code=code)
                session.add(row)
            row.name = name
            row.exchange = exchange
            row.listed_on = listed_on
            row.delisted_on = delisted_on
            row.security_type = security_type

    def add_valuation_snapshot(self, *, code: str, observed_on: date,
                               available_on: date, source: str,
                               pe_ttm: Optional[float] = None,
                               pb: Optional[float] = None,
                               source_record_id: Optional[str] = None,
                               raw_payload: Optional[dict[str, Any]] = None) -> int:
        if available_on < observed_on:
            raise ValueError("available_on cannot precede observed_on")
        with self.db.session_scope() as session:
            row = DisciplineValuationPIT(
                code=code, observed_on=observed_on, available_on=available_on,
                pe_ttm=pe_ttm, pb=pb, source=source,
                source_record_id=source_record_id,
                raw_payload=json.dumps(raw_payload, ensure_ascii=False) if raw_payload else None,
            )
            session.add(row)
            session.flush()
            return row.id

    def latest_valuation_asof(self, *, code: str, asof: date) -> Optional[DisciplineValuationPIT]:
        with self.db.get_session() as session:
            return session.execute(
                select(DisciplineValuationPIT).where(
                    and_(DisciplineValuationPIT.code == code,
                         DisciplineValuationPIT.available_on <= asof,
                         DisciplineValuationPIT.observed_on <= asof)
                ).order_by(DisciplineValuationPIT.available_on.desc(),
                           DisciplineValuationPIT.observed_on.desc(),
                           DisciplineValuationPIT.id.desc()).limit(1)
            ).scalar_one_or_none()

    def add_financial_snapshot(self, *, code: str, report_period: date,
                               announcement_date: date, available_on: date,
                               source: str, attributable_net_profit_yoy_pct: Optional[float] = None,
                               attributable_net_profit: Optional[float] = None,
                               non_recurring_net_profit: Optional[float] = None,
                               operating_cash_flow: Optional[float] = None,
                               accounts_receivable: Optional[float] = None,
                               inventory: Optional[float] = None,
                               source_record_id: Optional[str] = None,
                               raw_payload: Optional[dict[str, Any]] = None) -> int:
        if available_on < announcement_date:
            raise ValueError("available_on cannot precede announcement_date")
        with self.db.session_scope() as session:
            row = DisciplineFinancialPIT(
                code=code, report_period=report_period,
                announcement_date=announcement_date, available_on=available_on,
                attributable_net_profit=attributable_net_profit,
                attributable_net_profit_yoy_pct=attributable_net_profit_yoy_pct,
                non_recurring_net_profit=non_recurring_net_profit,
                operating_cash_flow=operating_cash_flow,
                accounts_receivable=accounts_receivable, inventory=inventory,
                source=source, source_record_id=source_record_id,
                raw_payload=json.dumps(raw_payload, ensure_ascii=False) if raw_payload else None,
            )
            session.add(row)
            session.flush()
            return row.id

    def latest_financial_asof(self, *, code: str, asof: date) -> Optional[DisciplineFinancialPIT]:
        with self.db.get_session() as session:
            return session.execute(
                select(DisciplineFinancialPIT).where(
                    and_(DisciplineFinancialPIT.code == code,
                         DisciplineFinancialPIT.available_on <= asof,
                         DisciplineFinancialPIT.announcement_date <= asof)
                ).order_by(DisciplineFinancialPIT.available_on.desc(),
                           DisciplineFinancialPIT.announcement_date.desc(),
                           DisciplineFinancialPIT.report_period.desc(),
                           DisciplineFinancialPIT.id.desc()).limit(1)
            ).scalar_one_or_none()

    def add_official_event(self, *, code: str, event_date: date,
                           announcement_date: date, category: str, level: str,
                           action: str, source_type: str, source_url: str,
                           verified_official_source: bool, description: Optional[str] = None,
                           raw_payload: Optional[dict[str, Any]] = None) -> int:
        with self.db.session_scope() as session:
            row = Discipline3OfficialEvent(
                code=code, event_date=event_date, announcement_date=announcement_date,
                available_on=announcement_date, category=category, level=level,
                action=action, source_type=source_type, source_url=source_url,
                verified_official_source=verified_official_source,
                description=description,
                raw_payload=json.dumps(raw_payload, ensure_ascii=False) if raw_payload else None,
            )
            session.add(row)
            session.flush()
            return row.id

    def create_run(self, *, run_key: str, strategy_version: str,
                   start_date: date, end_date: date, universe_definition: str,
                   parameters: dict[str, Any]) -> int:
        if end_date < start_date:
            raise ValueError("end_date cannot precede start_date")
        with self.db.session_scope() as session:
            row = DisciplineBacktestRun(
                run_key=run_key, strategy_version=strategy_version,
                start_date=start_date, end_date=end_date,
                universe_definition=universe_definition,
                parameters_json=json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                status="created",
            )
            session.add(row)
            session.flush()
            return row.id

    def update_run(self, *, run_id: int, status: str,
                   data_coverage: Optional[dict[str, Any]] = None,
                   result_summary: Optional[dict[str, Any]] = None,
                   error_message: Optional[str] = None) -> None:
        allowed = {"created", "running", "completed", "partial", "failed"}
        if status not in allowed:
            raise ValueError(f"unsupported run status: {status}")
        with self.db.session_scope() as session:
            row = session.get(DisciplineBacktestRun, run_id)
            if row is None:
                raise KeyError(f"backtest run not found: {run_id}")
            row.status = status
            row.data_coverage_json = json.dumps(data_coverage, ensure_ascii=False) if data_coverage is not None else row.data_coverage_json
            row.result_summary_json = json.dumps(result_summary, ensure_ascii=False) if result_summary is not None else row.result_summary_json
            row.error_message = error_message
            if status in {"completed", "partial", "failed"}:
                row.completed_at = datetime.now()

    def save_signal(self, *, run_id: int, code: str, signal_date: date,
                    discipline: str, triggered: bool, data_status: str = "complete",
                    reasons: Optional[list[str]] = None, band_upper: Optional[float] = None,
                    band_lower: Optional[float] = None, pe_ttm: Optional[float] = None,
                    attributable_net_profit_yoy_pct: Optional[float] = None,
                    source_snapshot: Optional[dict[str, Any]] = None) -> int:
        if discipline not in {"D1", "D2", "D3"}:
            raise ValueError("discipline must be D1, D2 or D3")
        with self.db.session_scope() as session:
            row = DisciplineSignalRecord(
                run_id=run_id, code=code, signal_date=signal_date,
                discipline=discipline, triggered=triggered, data_status=data_status,
                reasons_json=json.dumps(reasons or [], ensure_ascii=False),
                band_upper=band_upper, band_lower=band_lower, pe_ttm=pe_ttm,
                attributable_net_profit_yoy_pct=attributable_net_profit_yoy_pct,
                source_snapshot_json=json.dumps(source_snapshot, ensure_ascii=False) if source_snapshot else None,
            )
            session.add(row)
            session.flush()
            return row.id

    def save_forward_return(self, *, signal_id: int, horizon_sessions: int,
                            entry_date: Optional[date], entry_price: Optional[float],
                            exit_date: Optional[date], exit_price: Optional[float],
                            return_pct: Optional[float], executable: Optional[bool],
                            max_favorable_excursion_pct: Optional[float] = None,
                            max_adverse_excursion_pct: Optional[float] = None,
                            execution_note: Optional[str] = None) -> int:
        if horizon_sessions <= 0:
            raise ValueError("horizon_sessions must be positive trading sessions")
        with self.db.session_scope() as session:
            row = DisciplineForwardReturn(
                signal_id=signal_id, horizon_sessions=horizon_sessions,
                entry_date=entry_date, entry_price=entry_price,
                exit_date=exit_date, exit_price=exit_price, return_pct=return_pct,
                executable=executable,
                max_favorable_excursion_pct=max_favorable_excursion_pct,
                max_adverse_excursion_pct=max_adverse_excursion_pct,
                execution_note=execution_note,
            )
            session.add(row)
            session.flush()
            return row.id
