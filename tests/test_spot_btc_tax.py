import csv
import shutil
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from coinbase_tax_calculator.models import CoinbaseRow
from coinbase_tax_calculator.spot_btc_tax import (
    build_spot_btc_tax_report,
    write_spot_btc_tax_reports,
)


def workspace_output_dir(name: str) -> Path:
    path = Path("tests") / ".tmp" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_cost_basis_fixture(text: str) -> Path:
    path = workspace_output_dir("spot-btc-fixtures") / "btc_cost_basis.tsv"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def coinbase_row(
    row_number: int,
    timestamp: datetime,
    transaction_type: str,
    asset: str,
    quantity: Decimal,
    subtotal: Decimal,
    total: Decimal,
    fees: Decimal,
    notes: str = "fixture",
) -> CoinbaseRow:
    return CoinbaseRow(
        source_file=Path(f"coinbase_pro_transactions_{timestamp.year}.csv"),
        source_row_number=row_number,
        transaction_id=f"row-{row_number}",
        timestamp=timestamp,
        transaction_type=transaction_type,
        asset=asset,
        quantity_transacted=quantity,
        price_currency="USD",
        price_at_transaction=Decimal("1"),
        subtotal_amount=subtotal,
        total_amount=total,
        fees_amount=fees,
        notes=notes,
    )


class SpotBtcTaxTests(unittest.TestCase):
    def test_build_spot_btc_tax_report_uses_hifo_without_future_buys(self) -> None:
        cost_basis_path = write_cost_basis_fixture(
            """
            Product	Size	Cost	Date	Notes
            BTC	0.5	10000	2024-01-01 00:00	older external lot
            BTC	0.5	30000	2024-01-03 00:00	future external lot
            """
        )
        rows = [
            coinbase_row(
                1,
                datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
                "Advanced Trade Buy",
                "BTC",
                Decimal("0.4"),
                Decimal("8000"),
                Decimal("8010"),
                Decimal("10"),
            ),
            coinbase_row(
                2,
                datetime(2024, 1, 2, 12, tzinfo=timezone.utc),
                "Intx Spot Fill",
                "BTC",
                Decimal("-0.6"),
                Decimal("-18000"),
                Decimal("-17995"),
                Decimal("5"),
            ),
            coinbase_row(
                3,
                datetime(2024, 1, 4, 12, tzinfo=timezone.utc),
                "Intx Spot Fill",
                "BTC",
                Decimal("-0.4"),
                Decimal("-8000"),
                Decimal("-8000"),
                Decimal("0"),
            ),
        ]

        report = build_spot_btc_tax_report(
            coinbase_rows=rows, cost_basis_path=cost_basis_path
        )

        self.assertEqual([buy.lot_id for buy in report.buys], ["BTC-BUY-0001", "BTC-BUY-0002", "BTC-BUY-0003"])
        self.assertEqual(report.sells[0].sell_id, "BTC-SELL-0001")
        self.assertEqual(report.sells[0].fees_usd, Decimal("5.00000000"))

        first_sell = report.tax_by_sell[0]
        self.assertEqual(first_sell.basis_status, "complete")
        self.assertEqual(first_sell.cost_basis_usd, Decimal("10010.00000000"))
        self.assertEqual(first_sell.taxable_gain_loss_usd, Decimal("7985.00000000"))
        self.assertEqual(
            [(item.buy_lot_id, item.size_btc) for item in report.tax_lots[:2]],
            [("BTC-BUY-0002", Decimal("0.4")), ("BTC-BUY-0001", Decimal("0.2"))],
        )

        second_sell = report.tax_by_sell[1]
        self.assertEqual(second_sell.cost_basis_usd, Decimal("12000.00000000"))
        self.assertEqual(second_sell.taxable_gain_loss_usd, Decimal("-4000.00000000"))
        remaining_by_lot = {buy.lot_id: buy.remaining_size_btc for buy in report.buys}
        self.assertEqual(remaining_by_lot["BTC-BUY-0001"], Decimal("0.3"))
        self.assertEqual(remaining_by_lot["BTC-BUY-0002"], Decimal("0.0"))
        self.assertEqual(remaining_by_lot["BTC-BUY-0003"], Decimal("0.1"))

    def test_build_spot_btc_tax_report_marks_incomplete_basis_and_excludes_from_known_summary(
        self,
    ) -> None:
        cost_basis_path = write_cost_basis_fixture(
            """
            Product	Size	Cost	Date	Notes
            BTC	0.1	10000	2024-01-01 00:00	small lot
            """
        )
        rows = [
            coinbase_row(
                1,
                datetime(2024, 1, 2, 12, tzinfo=timezone.utc),
                "Intx Spot Fill",
                "BTC",
                Decimal("-0.2"),
                Decimal("-4000"),
                Decimal("-4000"),
                Decimal("0"),
            )
        ]

        report = build_spot_btc_tax_report(
            coinbase_rows=rows, cost_basis_path=cost_basis_path
        )

        sell_tax = report.tax_by_sell[0]
        self.assertEqual(sell_tax.basis_status, "incomplete")
        self.assertEqual(sell_tax.missing_basis_size_btc, Decimal("0.1"))
        self.assertIsNone(sell_tax.taxable_gain_loss_usd)
        self.assertEqual(
            report.year_summaries[2024].taxable_gain_loss_known_usd,
            Decimal("0E-8"),
        )
        self.assertEqual(report.year_summaries[2024].incomplete_sell_count, 1)

    def test_write_spot_btc_tax_reports_outputs_auditable_csvs(self) -> None:
        cost_basis_path = write_cost_basis_fixture(
            """
            Product	Size	Cost	Date	Notes
            BTC	1	10000	2024-01-01 00:00	external lot
            """
        )
        rows = [
            coinbase_row(
                1,
                datetime(2024, 1, 2, 12, tzinfo=timezone.utc),
                "Intx Spot Fill",
                "BTC",
                Decimal("-0.25"),
                Decimal("-5000"),
                Decimal("-4995"),
                Decimal("5"),
            )
        ]
        report = build_spot_btc_tax_report(
            coinbase_rows=rows, cost_basis_path=cost_basis_path
        )
        output_dir = workspace_output_dir("spot-btc-report")

        paths = write_spot_btc_tax_reports(report, output_dir)

        self.assertEqual(
            {path.name for path in paths},
            {
                "Spot_BTC_buys.csv",
                "Spot_BTC_sells.csv",
                "Spot_BTC_tax_by_sell.csv",
                "Spot_BTC_tax_lots.csv",
                "Spot_BTC_tax_summary_by_year.csv",
            },
        )
        with (output_dir / "Spot_BTC_tax_summary_by_year.csv").open(
            newline="", encoding="utf-8"
        ) as csv_file:
            summary_rows = list(csv.DictReader(csv_file))
        self.assertEqual(summary_rows[0]["report_year"], "2024")
        self.assertEqual(summary_rows[0]["net_proceeds_usd"], "4995.00000000")
        self.assertEqual(
            summary_rows[0]["taxable_gain_loss_known_usd"], "2495.00000000"
        )
        self.assertEqual(summary_rows[-1]["report_year"], "ALL")
        self.assertEqual(
            summary_rows[-1]["taxable_gain_loss_known_usd"], "2495.00000000"
        )


if __name__ == "__main__":
    unittest.main()
