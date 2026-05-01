import csv
import shutil
import unittest
from decimal import Decimal
from pathlib import Path

from coinbase_tax_calculator.futures_tax import FuturesTaxReport, FuturesTaxYearSummary
from coinbase_tax_calculator.spot_btc_tax import SpotBtcTaxReport, SpotBtcYearSummary
from coinbase_tax_calculator.unified_tax import (
    build_unified_tax_report,
    write_unified_tax_report,
)


def workspace_output_dir(name: str) -> Path:
    path = Path("tests") / ".tmp" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def futures_report() -> FuturesTaxReport:
    return FuturesTaxReport(
        filled_orders_path=Path("filled_orders.md"),
        csv_paths=[],
        product_years={},
        year_summaries={
            2024: FuturesTaxYearSummary(
                report_year=2024,
                product_count=2,
                realized_pnl_usd=Decimal("20"),
                funding_income_received_usd=Decimal("3"),
                funding_costs_paid_usd=Decimal("5"),
                trading_fees_paid_usd=Decimal("1"),
            ),
            2025: FuturesTaxYearSummary(
                report_year=2025,
                product_count=1,
                realized_pnl_usd=Decimal("-10"),
                funding_income_received_usd=Decimal("0"),
                funding_costs_paid_usd=Decimal("2"),
                trading_fees_paid_usd=Decimal("0.5"),
            ),
        },
        all_years_summary=FuturesTaxYearSummary(
            report_year="ALL",
            product_count=3,
            realized_pnl_usd=Decimal("10"),
            funding_income_received_usd=Decimal("3"),
            funding_costs_paid_usd=Decimal("7"),
            trading_fees_paid_usd=Decimal("1.5"),
        ),
    )


def spot_report() -> SpotBtcTaxReport:
    return SpotBtcTaxReport(
        cost_basis_path=Path("btc_cost_basis.csv"),
        csv_paths=[],
        buys=[],
        sells=[],
        tax_by_sell=[],
        tax_lots=[],
        year_summaries={
            2024: SpotBtcYearSummary(
                report_year=2024,
                sell_count=1,
                complete_sell_count=1,
                incomplete_sell_count=0,
                size_sold_btc=Decimal("0.1"),
                net_proceeds_usd=Decimal("100"),
                fees_usd=Decimal("1"),
                cost_basis_usd=Decimal("70"),
                taxable_gain_loss_known_usd=Decimal("30"),
                missing_basis_size_btc=Decimal("0"),
            ),
            2026: SpotBtcYearSummary(
                report_year=2026,
                sell_count=1,
                complete_sell_count=0,
                incomplete_sell_count=1,
                size_sold_btc=Decimal("0.2"),
                net_proceeds_usd=Decimal("200"),
                fees_usd=Decimal("2"),
                cost_basis_usd=Decimal("50"),
                taxable_gain_loss_known_usd=Decimal("0"),
                missing_basis_size_btc=Decimal("0.05"),
            ),
        },
        all_years_summary=SpotBtcYearSummary(
            report_year="ALL",
            sell_count=2,
            complete_sell_count=1,
            incomplete_sell_count=1,
            size_sold_btc=Decimal("0.3"),
            net_proceeds_usd=Decimal("300"),
            fees_usd=Decimal("3"),
            cost_basis_usd=Decimal("120"),
            taxable_gain_loss_known_usd=Decimal("30"),
            missing_basis_size_btc=Decimal("0.05"),
        ),
    )


class UnifiedTaxReportTests(unittest.TestCase):
    def test_build_unified_tax_report_combines_perp_and_intx_by_year_and_all(
        self,
    ) -> None:
        report = build_unified_tax_report(
            futures_tax_report=futures_report(),
            spot_btc_tax_report=spot_report(),
        )

        by_year = {summary.report_year: summary for summary in report.year_summaries}
        self.assertEqual(
            by_year[2024].perpetual_taxable_or_deductible_amount_usd,
            Decimal("17.00000000"),
        )
        self.assertEqual(
            by_year[2024].intx_btc_taxable_gain_loss_known_usd,
            Decimal("30.00000000"),
        )
        self.assertEqual(
            by_year[2024].unified_taxable_or_deductible_known_usd,
            Decimal("47.00000000"),
        )
        self.assertEqual(
            by_year[2025].unified_taxable_or_deductible_known_usd,
            Decimal("-12.50000000"),
        )
        self.assertEqual(by_year[2026].intx_btc_incomplete_sell_count, 1)
        self.assertEqual(
            report.all_years_summary.unified_taxable_or_deductible_known_usd,
            Decimal("34.50000000"),
        )

    def test_write_unified_tax_report_outputs_years_and_all_total(self) -> None:
        report = build_unified_tax_report(
            futures_tax_report=futures_report(),
            spot_btc_tax_report=spot_report(),
        )
        output_dir = workspace_output_dir("unified-tax-report")

        paths = write_unified_tax_report(report, output_dir)

        self.assertEqual(
            {path.name for path in paths},
            {"unified_tax_summary_by_year.csv"},
        )
        with (output_dir / "unified_tax_summary_by_year.csv").open(
            newline="", encoding="utf-8"
        ) as csv_file:
            rows = list(csv.DictReader(csv_file))

        self.assertEqual([row["report_year"] for row in rows], ["2024", "2025", "2026", "ALL"])
        self.assertEqual(
            rows[-1]["unified_taxable_or_deductible_known_usd"],
            "34.50000000",
        )
        self.assertEqual(rows[-1]["result_type"], "taxable")


if __name__ == "__main__":
    unittest.main()
