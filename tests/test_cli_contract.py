import unittest
from pathlib import Path

from coinbase_tax_calculator.cli import build_arg_parser, _output_dir_for_mode


class CliContractTests(unittest.TestCase):
    def test_parser_uses_transaction_data_defaults(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args([])

        self.assertIsNone(args.year)
        self.assertEqual(args.input_glob, "transaction_data/coinbase_pro_transactions_*.csv")
        self.assertEqual(args.output_dir, "reports")
        self.assertEqual(args.report_mode, "unified")
        self.assertIsNone(args.cost_basis)
        self.assertEqual(
            args.filled_orders,
            "transaction_data/filled_orders/AllFilledOrders.md",
        )
        self.assertEqual(
            args.manual_adjustments,
            "transaction_data/manual_trade_list_adjustments.yaml",
        )
        self.assertEqual(args.filled_orders_timezone, "Europe/Vilnius")

    def test_parser_allows_futures_tax_report_without_single_report_year(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--report-mode",
                "futures-tax",
                "--filled-orders",
                "transaction_data/filled_orders/AllFilledOrders.md",
                "--input-glob",
                "transaction_data/coinbase_pro_transactions_*.csv",
            ]
        )

        self.assertIsNone(args.year)
        self.assertEqual(args.report_mode, "futures-tax")
        self.assertEqual(
            args.filled_orders,
            "transaction_data/filled_orders/AllFilledOrders.md",
        )
        self.assertEqual(args.input_glob, "transaction_data/coinbase_pro_transactions_*.csv")

    def test_parser_supports_spot_btc_report_mode(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--report-mode",
                "spot-btc",
                "--cost-basis",
                "transaction_data/btc_cost_basis.csv",
            ]
        )

        self.assertIsNone(args.year)
        self.assertEqual(args.report_mode, "spot-btc")
        self.assertEqual(args.cost_basis, "transaction_data/btc_cost_basis.csv")

    def test_parser_supports_unified_report_mode_without_single_report_year(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--report-mode",
                "unified",
                "--cost-basis",
                "transaction_data/btc_cost_basis.csv",
                "--filled-orders",
                "transaction_data/filled_orders/AllFilledOrders.md",
            ]
        )

        self.assertIsNone(args.year)
        self.assertEqual(args.report_mode, "unified")
        self.assertEqual(args.cost_basis, "transaction_data/btc_cost_basis.csv")
        self.assertEqual(
            args.filled_orders,
            "transaction_data/filled_orders/AllFilledOrders.md",
        )

    def test_report_modes_use_reports_subdirectories_by_default(self) -> None:
        self.assertEqual(
            _output_dir_for_mode("unified", "reports"),
            Path("reports/final"),
        )
        self.assertEqual(
            _output_dir_for_mode("futures-tax", "reports"),
            Path("reports/futures_tax"),
        )
        self.assertEqual(
            _output_dir_for_mode("spot-btc", "reports"),
            Path("reports/spot_btc"),
        )
        self.assertEqual(
            _output_dir_for_mode("filled-orders", "reports"),
            Path("reports/filled_orders"),
        )
        self.assertEqual(
            _output_dir_for_mode("unified", "custom_reports"),
            Path("custom_reports"),
        )

    def test_parser_supports_filled_orders_report_mode(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--year",
                "2025",
                "--report-mode",
                "filled-orders",
                "--filled-orders",
                "transaction_data/filled_orders/AllFilledOrders.md",
                "--filled-orders-timezone",
                "UTC",
                "--manual-adjustments",
                "transaction_data/manual_trade_list_adjustments.yaml",
                "--products",
                "BTC-PERP",
                "ETH-PERP",
            ]
        )

        self.assertEqual(args.report_mode, "filled-orders")
        self.assertEqual(
            args.filled_orders,
            "transaction_data/filled_orders/AllFilledOrders.md",
        )
        self.assertEqual(args.filled_orders_timezone, "UTC")
        self.assertEqual(
            args.manual_adjustments,
            "transaction_data/manual_trade_list_adjustments.yaml",
        )
        self.assertEqual(args.products, ["BTC-PERP", "ETH-PERP"])


if __name__ == "__main__":
    unittest.main()
