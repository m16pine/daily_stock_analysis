# -*- coding: utf-8 -*-
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.storage import DatabaseManager
from src.repositories.discipline_backtest_repo import DisciplineBacktestRepository


class TestDisciplineBacktestRepository(unittest.TestCase):
    def setUp(self):
        DatabaseManager.reset_instance()
        self.db = DatabaseManager("sqlite:///:memory:")
        self.repo = DisciplineBacktestRepository(self.db)

    def tearDown(self):
        DatabaseManager.reset_instance()

    def test_pit_valuation_is_not_visible_before_available_date(self):
        self.repo.add_valuation_snapshot(
            code="600000", observed_on=date(2024, 4, 30),
            available_on=date(2024, 5, 6), source="fixture", pe_ttm=12.5,
        )
        self.assertIsNone(self.repo.latest_valuation_asof(
            code="600000", asof=date(2024, 5, 5)))
        row = self.repo.latest_valuation_asof(
            code="600000", asof=date(2024, 5, 6))
        self.assertIsNotNone(row)
        self.assertEqual(row.pe_ttm, 12.5)

    def test_pit_financial_is_not_visible_before_announcement(self):
        self.repo.add_financial_snapshot(
            code="600000", report_period=date(2024, 3, 31),
            announcement_date=date(2024, 4, 26), available_on=date(2024, 4, 26),
            source="fixture", attributable_net_profit_yoy_pct=12.0,
        )
        self.assertIsNone(self.repo.latest_financial_asof(
            code="600000", asof=date(2024, 4, 25)))
        row = self.repo.latest_financial_asof(
            code="600000", asof=date(2024, 4, 26))
        self.assertIsNotNone(row)
        self.assertEqual(row.attributable_net_profit_yoy_pct, 12.0)

    def test_reject_invalid_availability_chronology(self):
        with self.assertRaises(ValueError):
            self.repo.add_valuation_snapshot(
                code="600000", observed_on=date(2024, 5, 2),
                available_on=date(2024, 5, 1), source="fixture", pe_ttm=10,
            )
        with self.assertRaises(ValueError):
            self.repo.add_financial_snapshot(
                code="600000", report_period=date(2024, 3, 31),
                announcement_date=date(2024, 4, 26), available_on=date(2024, 4, 25),
                source="fixture",
            )

    def test_run_and_signal_are_persisted(self):
        run_id = self.repo.create_run(
            run_key="test-run-001", strategy_version="v1",
            start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
            universe_definition="unit-test fixture",
            parameters={"horizons": [5, 10, 20]},
        )
        signal_id = self.repo.save_signal(
            run_id=run_id, code="600000", signal_date=date(2024, 5, 6),
            discipline="D2", triggered=True, reasons=["fixture"],
        )
        self.assertGreater(run_id, 0)
        self.assertGreater(signal_id, 0)
        self.repo.update_run(run_id=run_id, status="partial",
                             data_coverage={"pit_valuation": "fixture-only"})
        with self.db.get_session() as session:
            from src.storage import DisciplineBacktestRun, DisciplineSignalRecord
            self.assertEqual(session.get(DisciplineBacktestRun, run_id).status, "partial")
            self.assertEqual(session.get(DisciplineSignalRecord, signal_id).discipline, "D2")


if __name__ == "__main__":
    unittest.main()
