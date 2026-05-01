import unittest
import shutil
from decimal import Decimal
from pathlib import Path

from coinbase_tax_calculator.coinbase_csv import load_coinbase_rows


def write_coinbase_fixture() -> Path:
    path = Path("tests") / ".tmp" / "coinbase-csv-fixture" / "coinbase.csv"
    if path.parent.exists():
        shutil.rmtree(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "Coinbase export preamble",
                "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes",
                "row-1,2024-12-30 10:00:00 UTC,Perpetual Futures Buy,BTC-PERP,1.2500,USD,1,0.00,0.00,0.00,fixture",
                "row-2,2024-12-31 10:00:00 UTC,Funding Fee,BTC-PERP,,USD,1,-2.00,-2.00,0.00,fixture",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class CoinbaseCsvTests(unittest.TestCase):
    def test_load_coinbase_rows_skips_preamble_and_preserves_row_order(self) -> None:
        rows = load_coinbase_rows(write_coinbase_fixture())

        self.assertEqual(rows[0].transaction_type, "Perpetual Futures Buy")
        self.assertEqual(rows[0].source_row_number, 1)

    def test_load_coinbase_rows_uses_decimal_for_money_and_quantity(self) -> None:
        rows = load_coinbase_rows(write_coinbase_fixture())

        self.assertEqual(rows[0].quantity_transacted, Decimal("1.2500"))
        self.assertEqual(rows[0].total_amount, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
