# -*- coding: utf-8 -*-
"""Tests for discipline strategy rule and PIT semantics."""
import unittest
from datetime import date
from src.core.discipline_backtest import (
    DailyBar, Discipline3Event, EventLevel, PitFinancial, PitValuation,
    classify_discipline3_event, discipline1_signal, discipline2_signal,
    discipline3_action, weekly_bollinger,
)


class DisciplineBacktestTests(unittest.TestCase):
    def setUp(self):
        self.closes = [100.0 + (i % 3) for i in range(20)]

    def test_weekly_bollinger_requires_twenty_completed_weeks(self):
        self.assertIsNone(weekly_bollinger(self.closes[:19]))
        self.assertIsNotNone(weekly_bollinger(self.closes))

    def test_discipline1_requires_both_conditions(self):
        band = weekly_bollinger(self.closes)
        hit = DailyBar("600000", date(2025, 1, 6), 100, band.upper + 1, 98, 98, -2.1)
        miss = DailyBar("600000", date(2025, 1, 7), 100, band.upper - 1, 98, 98, -2.1)
        self.assertTrue(discipline1_signal(bar=hit, prior_completed_weekly_closes=self.closes).triggered)
        self.assertFalse(discipline1_signal(bar=miss, prior_completed_weekly_closes=self.closes).triggered)

    def test_discipline2_all_four_conditions_required(self):
        band = weekly_bollinger(self.closes)
        bar = DailyBar("600000", date(2025, 1, 6), 100, 103, band.lower - 1, 102, 2.0)
        val = PitValuation("600000", date(2025, 1, 3), date(2025, 1, 3), 15.0)
        fin = PitFinancial("600000", date(2025, 1, 3), date(2025, 1, 3), 10.01)
        signal = discipline2_signal(bar=bar, prior_completed_weekly_closes=self.closes,
                                    valuation=val, financial=fin)
        self.assertTrue(signal.triggered)
        late_val = PitValuation("600000", date(2025, 1, 3), date(2025, 1, 7), 10)
        self.assertFalse(discipline2_signal(bar=bar, prior_completed_weekly_closes=self.closes,
                         valuation=late_val, financial=fin).triggered)

    def test_discipline3_levels_do_not_upgrade_b_or_c_to_sell(self):
        base = dict(code="600000", event_date=date(2025, 1, 2),
                    announcement_date=date(2025, 1, 2), source_type="company_announcement",
                    source_url="https://example.com/announcement", verified_official_source=True)
        a = Discipline3Event(category="ST_OR_STAR_ST", **base)
        b = Discipline3Event(category="MATERIAL_DEBT_DEFAULT", **base)
        c = Discipline3Event(category="CONSECUTIVE_LOSSES", **base)
        self.assertEqual(classify_discipline3_event(a)[0], EventLevel.A)
        self.assertEqual(discipline3_action(classify_discipline3_event(a)[0]), "SELL_TRIGGER")
        self.assertEqual(discipline3_action(classify_discipline3_event(b)[0]), "RISK_WATCH_ONLY")
        self.assertEqual(discipline3_action(classify_discipline3_event(c)[0]), "FINANCIAL_MONITOR_ONLY")

    def test_unverified_event_is_rejected(self):
        event = Discipline3Event("600000", date(2025, 1, 2), date(2025, 1, 2),
                                 "ST_OR_STAR_ST", "news", "https://example.com", False)
        self.assertIsNone(classify_discipline3_event(event)[0])


if __name__ == "__main__":
    unittest.main()
