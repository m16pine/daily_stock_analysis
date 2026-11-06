# -*- coding: utf-8 -*-
"""Point-in-time rule engine for the user's A-share discipline strategies."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from enum import Enum
from math import isfinite
from typing import Optional, Sequence


class EventLevel(str, Enum):
    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True)
class DailyBar:
    code: str
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    pct_change: float  # percentage points, e.g. -2.3 means -2.3%


@dataclass(frozen=True)
class PitValuation:
    code: str
    observed_on: date
    available_on: date
    pe_ttm: Optional[float]


@dataclass(frozen=True)
class PitFinancial:
    code: str
    announcement_date: date
    available_on: date
    attributable_net_profit_yoy_pct: Optional[float]


@dataclass(frozen=True)
class BollingerBand:
    middle: float
    upper: float
    lower: float


@dataclass(frozen=True)
class DisciplineSignal:
    code: str
    signal_date: date
    discipline: str
    triggered: bool
    reasons: tuple[str, ...]
    data_status: str = "complete"


@dataclass(frozen=True)
class Discipline3Event:
    code: str
    event_date: date
    announcement_date: date
    category: str
    source_type: str
    source_url: str
    verified_official_source: bool
    description: str = ""


DISCIPLINE3_LEVELS = {
    "ST_OR_STAR_ST": EventLevel.A,
    "DELISTING_DECISION": EventLevel.A,
    "DELISTING_CONSOLIDATION": EventLevel.A,
    "CSRC_CONFIRMED_MAJOR_FRAUD": EventLevel.A,
    "AUDIT_ADVERSE_OPINION": EventLevel.A,
    "AUDIT_DISCLAIMER_OF_OPINION": EventLevel.A,
    "COURT_ACCEPTED_BANKRUPTCY_LIQUIDATION": EventLevel.A,
    "DELISTING_RISK_WARNING": EventLevel.A,
    "MATERIAL_DEBT_DEFAULT": EventLevel.B,
    "CORE_OPERATION_SUSPENSION": EventLevel.B,
    "MATERIAL_ASSET_FREEZE": EventLevel.B,
    "CONTROLLING_SHAREHOLDER_FUND_OCCUPANCY": EventLevel.B,
    "MATERIAL_ILLEGAL_GUARANTEE": EventLevel.B,
    "CONSECUTIVE_LOSSES": EventLevel.C,
    "PERSISTENT_NEGATIVE_OPERATING_CASH_FLOW": EventLevel.C,
    "ABNORMAL_RECEIVABLES_GROWTH": EventLevel.C,
    "ABNORMAL_INVENTORY_GROWTH": EventLevel.C,
    "PERSISTENT_NON_RECURRING_PROFIT_DECLINE": EventLevel.C,
}

OFFICIAL_SOURCE_TYPES = {
    "exchange_announcement", "csrc_decision", "court_document",
    "company_announcement", "audit_report", "bond_default_announcement",
}


def weekly_bollinger(completed_weekly_closes: Sequence[float], period: int = 20,
                     num_std: float = 2.0) -> Optional[BollingerBand]:
    """Bands use completed weekly closes only; current partial week is excluded."""
    if period <= 1 or num_std <= 0:
        raise ValueError("period must be > 1 and num_std must be positive")
    if len(completed_weekly_closes) < period:
        return None
    values = [float(v) for v in completed_weekly_closes[-period:]]
    if any(not isfinite(v) or v <= 0 for v in values):
        return None
    middle = sum(values) / period
    variance = sum((v - middle) ** 2 for v in values) / period
    std = variance ** 0.5
    return BollingerBand(middle, middle + num_std * std, middle - num_std * std)


def discipline2_signal(*, bar: DailyBar, prior_completed_weekly_closes: Sequence[float],
                       valuation: Optional[PitValuation], financial: Optional[PitFinancial],
                       period: int = 20, num_std: float = 2.0) -> DisciplineSignal:
    """All four buy-candidate conditions, evaluated using only data available that day."""
    reasons: list[str] = []
    band = weekly_bollinger(prior_completed_weekly_closes, period, num_std)
    if band is None:
        return DisciplineSignal(bar.code, bar.trading_date, "D2", False,
                                ("不足20个已完成周线收盘数据",), "insufficient_weekly_history")
    lower_touch = bar.low <= band.lower
    rebound = bar.pct_change >= 2.0
    if lower_touch:
        reasons.append("当日最低价触及或跌破前20个已完成周线计算的布林下轨")
    if rebound:
        reasons.append("当日涨幅达到2%")
    if valuation is None or valuation.code != bar.code or valuation.available_on > bar.trading_date:
        return DisciplineSignal(bar.code, bar.trading_date, "D2", False,
                                tuple(reasons + ["缺少信号日可用的历史PE(TTM)快照"]), "missing_pit_valuation")
    pe_ok = valuation.pe_ttm is not None and valuation.pe_ttm <= 15.0
    reasons.append("PE(TTM)不高于15" if pe_ok else "PE(TTM)不满足不高于15")
    if financial is None or financial.code != bar.code or financial.available_on > bar.trading_date:
        return DisciplineSignal(bar.code, bar.trading_date, "D2", False,
                                tuple(reasons + ["缺少信号日之前已公告的归母净利润同比数据"]), "missing_pit_financial")
    growth_ok = (financial.attributable_net_profit_yoy_pct is not None
                 and financial.attributable_net_profit_yoy_pct > 10.0)
    reasons.append("最新已公告归母净利润同比增长超过10%" if growth_ok
                   else "归母净利润同比增长未超过10%")
    triggered = lower_touch and rebound and pe_ok and growth_ok
    return DisciplineSignal(bar.code, bar.trading_date, "D2", triggered, tuple(reasons))


def discipline1_signal(*, bar: DailyBar, prior_completed_weekly_closes: Sequence[float],
                       upper_band_touched_earlier_this_week: bool = False,
                       period: int = 20, num_std: float = 2.0) -> DisciplineSignal:
    """Sell if the upper band was touched at any point this week and today's
    percentage change is <= -2%. The caller supplies the week-to-date touch
    state from daily bars; today's high is also checked here."""
    band = weekly_bollinger(prior_completed_weekly_closes, period, num_std)
    if band is None:
        return DisciplineSignal(bar.code, bar.trading_date, "D1", False,
                                ("不足20个已完成周线收盘数据",), "insufficient_weekly_history")
    touched_today = bar.high >= band.upper
    touched = upper_band_touched_earlier_this_week or touched_today
    dropped = bar.pct_change <= -2.0
    reasons = (
        "本周此前或当日最高价触及/突破周线布林上轨" if touched else "本周尚未触及周线布林上轨",
        "当日跌幅达到或超过2%" if dropped else "当日跌幅未达到2%",
    )
    return DisciplineSignal(bar.code, bar.trading_date, "D1", touched and dropped, reasons)


def classify_discipline3_event(event: Discipline3Event) -> tuple[Optional[EventLevel], str]:
    """Only verified primary-source events can be classified."""
    if event.category not in DISCIPLINE3_LEVELS:
        return None, "unknown_event_category"
    if not event.verified_official_source or event.source_type not in OFFICIAL_SOURCE_TYPES:
        return None, "unverified_or_non_official_source"
    if not event.source_url.strip():
        return None, "missing_primary_source_url"
    if event.announcement_date > event.event_date:
        return DISCIPLINE3_LEVELS[event.category], "announced_after_event_pit_effective_on_announcement_date"
    return DISCIPLINE3_LEVELS[event.category], "verified_official_event"


def discipline3_action(level: Optional[EventLevel]) -> str:
    if level == EventLevel.A:
        return "SELL_TRIGGER"
    if level == EventLevel.B:
        return "RISK_WATCH_ONLY"
    if level == EventLevel.C:
        return "FINANCIAL_MONITOR_ONLY"
    return "NO_ACTION"
