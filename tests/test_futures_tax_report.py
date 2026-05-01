import csv
import shutil
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from coinbase_tax_calculator.futures_tax import (
    build_futures_tax_report,
    write_futures_tax_reports,
)
from coinbase_tax_calculator.models import CoinbaseRow


def workspace_output_dir(name: str) -> Path:
    path = Path("tests") / ".tmp" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_filled_orders_fixture(text: str) -> Path:
    path = workspace_output_dir("futures-tax-fixtures") / "filled-orders.md"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def write_adjustments_fixture(text: str) -> Path:
    path = workspace_output_dir("futures-tax-adjustments") / "manual-adjustments.yaml"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def coinbase_row(
    row_number: int,
    timestamp: datetime,
    transaction_type: str,
    asset: str,
    total: Decimal,
    fees: Decimal = Decimal("0"),
) -> CoinbaseRow:
    return CoinbaseRow(
        source_file=Path(f"coinbase_pro_transactions_{timestamp.year}.csv"),
        source_row_number=row_number,
        transaction_id=f"row-{row_number}",
        timestamp=timestamp,
        transaction_type=transaction_type,
        asset=asset,
        quantity_transacted=None,
        price_currency="USD",
        price_at_transaction=Decimal("1"),
        subtotal_amount=total,
        total_amount=total,
        fees_amount=fees,
        notes="fixture",
    )


class FuturesTaxReportTests(unittest.TestCase):
    def test_build_futures_tax_report_combines_realized_pnl_funding_and_trading_fees_by_year(
        self,
    ) -> None:
        filled_orders_path = write_filled_orders_fixture(
            """
            1/03/25 10:00:00
            Perpetuals
            BTC PERP
            Limit
            Sell
            120 USDC
            1
            100%
            \\-- / --
            120 USDC
            Filled
            1/02/25 10:00:00
            Perpetuals
            BTC PERP
            Limit
            Buy
            100 USDC
            1
            100%
            \\-- / --
            100 USDC
            Filled
            12/31/24 10:00:00
            Perpetuals
            BTC PERP
            Limit
            Sell
            110 USDC
            1
            100%
            \\-- / --
            110 USDC
            Filled
            12/30/24 10:00:00
            Perpetuals
            BTC PERP
            Limit
            Buy
            100 USDC
            1
            100%
            \\-- / --
            100 USDC
            Filled
            """
        )
        rows = [
            coinbase_row(
                1,
                datetime(2024, 12, 31, 23, tzinfo=timezone.utc),
                "Funding Fees (24 Hours)",
                "BTC-PERP",
                Decimal("-2"),
            ),
            coinbase_row(
                2,
                datetime(2024, 12, 31, 10, tzinfo=timezone.utc),
                "Perpetual Futures Sell",
                "BTC-PERP",
                Decimal("0"),
                fees=Decimal("0.50"),
            ),
            coinbase_row(
                3,
                datetime(2025, 1, 3, 12, tzinfo=timezone.utc),
                "Funding Fee",
                "BTC-PERP",
                Decimal("3"),
            ),
            coinbase_row(
                4,
                datetime(2025, 1, 4, 12, tzinfo=timezone.utc),
                "Funding Fee",
                "BTC-PERP",
                Decimal("-1"),
            ),
            coinbase_row(
                5,
                datetime(2025, 1, 3, 10, tzinfo=timezone.utc),
                "Perpetual Futures Sell",
                "BTC-PERP",
                Decimal("0"),
                fees=Decimal("0.25"),
            ),
        ]

        report = build_futures_tax_report(
            filled_orders_path=filled_orders_path,
            coinbase_rows=rows,
            timezone_name="UTC",
        )

        btc_2024 = report.product_years[(2024, "BTC-PERP")]
        self.assertEqual(btc_2024.realized_pnl_usd, Decimal("10.00000000"))
        self.assertEqual(btc_2024.funding_costs_paid_usd, Decimal("2.00000000"))
        self.assertEqual(btc_2024.trading_fees_paid_usd, Decimal("0.50000000"))
        self.assertEqual(
            btc_2024.taxable_or_deductible_amount_usd, Decimal("7.50000000")
        )

        btc_2025 = report.product_years[(2025, "BTC-PERP")]
        self.assertEqual(btc_2025.realized_pnl_usd, Decimal("20.00000000"))
        self.assertEqual(btc_2025.funding_income_received_usd, Decimal("3.00000000"))
        self.assertEqual(btc_2025.funding_costs_paid_usd, Decimal("1.00000000"))
        self.assertEqual(btc_2025.trading_fees_paid_usd, Decimal("0.25000000"))
        self.assertEqual(
            btc_2025.taxable_or_deductible_amount_usd, Decimal("21.75000000")
        )

        self.assertEqual(report.year_summaries[2024].result_type, "taxable")
        self.assertEqual(report.year_summaries[2025].result_type, "taxable")
        self.assertEqual(
            report.all_years_summary.taxable_or_deductible_amount_usd,
            Decimal("29.25000000"),
        )

    def test_build_futures_tax_report_attributes_cross_year_close_to_close_year(
        self,
    ) -> None:
        filled_orders_path = write_filled_orders_fixture(
            """
            1/02/25 10:00:00
            Perpetuals
            ETH PERP
            Limit
            Sell
            130 USDC
            1
            100%
            \\-- / --
            130 USDC
            Filled
            12/31/24 10:00:00
            Perpetuals
            ETH PERP
            Limit
            Buy
            100 USDC
            1
            100%
            \\-- / --
            100 USDC
            Filled
            """
        )

        report = build_futures_tax_report(
            filled_orders_path=filled_orders_path,
            coinbase_rows=[],
            timezone_name="UTC",
        )

        self.assertEqual(
            report.product_years[(2024, "ETH-PERP")].realized_pnl_usd,
            Decimal("0E-8"),
        )
        self.assertEqual(
            report.product_years[(2025, "ETH-PERP")].realized_pnl_usd,
            Decimal("30.00000000"),
        )

    def test_build_futures_tax_report_includes_forced_close_adjustments(
        self,
    ) -> None:
        filled_orders_path = write_filled_orders_fixture(
            """
            2/19/26 10:00:00
            Perpetuals
            PROMPT PERP
            Limit
            Buy
            0.10 USDC
            100
            100%
            \\-- / --
            10 USDC
            Filled
            """
        )
        adjustments_path = write_adjustments_fixture(
            """
            version: 1
            order_classifications:
              - fill_id: suspended_PROMPT-PERP_2026-02-20-13:00
                order_function: CLOSE
                open_pos_type: LONG
                classified_at: 2026-04-22T06:23:48Z
            """
        )

        report = build_futures_tax_report(
            filled_orders_path=filled_orders_path,
            coinbase_rows=[],
            manual_adjustments_path=adjustments_path,
            timezone_name="UTC",
        )

        prompt_2026 = report.product_years[(2026, "PROMPT-PERP")]
        self.assertEqual(prompt_2026.realized_pnl_usd, Decimal("-10.00000000"))
        self.assertEqual(prompt_2026.ending_position_qty, Decimal("0"))
        self.assertEqual(
            prompt_2026.taxable_or_deductible_amount_usd,
            Decimal("-10.00000000"),
        )
        self.assertEqual(report.year_summaries[2026].result_type, "deductible_loss")

    def test_write_futures_tax_reports_creates_year_summary_and_product_year_csvs(
        self,
    ) -> None:
        filled_orders_path = write_filled_orders_fixture(
            """
            1/03/25 10:00:00
            Perpetuals
            BTC PERP
            Limit
            Sell
            120 USDC
            1
            100%
            \\-- / --
            120 USDC
            Filled
            1/02/25 10:00:00
            Perpetuals
            BTC PERP
            Limit
            Buy
            100 USDC
            1
            100%
            \\-- / --
            100 USDC
            Filled
            """
        )
        report = build_futures_tax_report(
            filled_orders_path=filled_orders_path,
            coinbase_rows=[
                coinbase_row(
                    1,
                    datetime(2025, 1, 3, tzinfo=timezone.utc),
                    "Funding Fee",
                    "BTC-PERP",
                    Decimal("-2"),
                )
            ],
            timezone_name="UTC",
        )
        output_dir = workspace_output_dir("futures-tax-report")

        paths = write_futures_tax_reports(report, output_dir)

        self.assertEqual(
            {path.name for path in paths},
            {
                "futures_tax_summary_by_year.csv",
                "futures_tax_by_product_year.csv",
            },
        )
        with (output_dir / "futures_tax_summary_by_year.csv").open(
            newline="", encoding="utf-8"
        ) as csv_file:
            summary_rows = list(csv.DictReader(csv_file))
        self.assertEqual(summary_rows[-1]["report_year"], "ALL")
        self.assertEqual(
            summary_rows[-1]["taxable_or_deductible_amount_usd"], "18.00000000"
        )


if __name__ == "__main__":
    unittest.main()
