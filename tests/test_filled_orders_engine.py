import csv
import shutil
import unittest
from pathlib import Path

from coinbase_tax_calculator.filled_orders import (
    compute_filled_order_perp_pnl,
    load_forced_close_adjustments,
    parse_filled_orders_markdown,
    write_filled_order_perp_reports,
)


def workspace_output_dir(name: str) -> Path:
    path = Path("tests") / ".tmp" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_fixture(text: str, name: str = "filled-orders.md") -> Path:
    path = workspace_output_dir("filled-orders-fixtures") / name
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def write_adjustments_fixture(text: str, name: str = "manual-adjustments.yaml") -> Path:
    path = workspace_output_dir("filled-orders-adjustments") / name
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


class FilledOrdersEngineTests(unittest.TestCase):
    def test_parse_filled_orders_markdown_reads_perpetual_records(self) -> None:
        path = write_fixture(
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
            """
        )

        orders = parse_filled_orders_markdown(path)

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].product, "BTC-PERP")
        self.assertEqual(orders[0].side, "sell")
        self.assertEqual(str(orders[0].price_usdc), "120")
        self.assertEqual(str(orders[0].quantity), "1")

    def test_compute_filled_order_perp_pnl_tracks_long_position_close(self) -> None:
        path = write_fixture(
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

        report = compute_filled_order_perp_pnl(path, report_year=2025)

        self.assertEqual(report.product_summaries["BTC-PERP"].realized_pnl_usd, 20)
        self.assertEqual(len(report.positions), 1)
        self.assertEqual(report.positions[0].side, "long")
        self.assertEqual(report.positions[0].realized_pnl_for_year(2025), 20)
        self.assertEqual(report.positions[0].status, "closed")

    def test_compute_filled_order_perp_pnl_tracks_short_position_close(self) -> None:
        path = write_fixture(
            """
            1/03/25 10:00:00
            Perpetuals
            BTC PERP
            Limit
            Buy
            80 USDC
            1
            100%
            \\-- / --
            80 USDC
            Filled
            1/02/25 10:00:00
            Perpetuals
            BTC PERP
            Limit
            Sell
            100 USDC
            1
            100%
            \\-- / --
            100 USDC
            Filled
            """
        )

        report = compute_filled_order_perp_pnl(path, report_year=2025)

        self.assertEqual(report.product_summaries["BTC-PERP"].realized_pnl_usd, 20)
        self.assertEqual(report.positions[0].side, "short")
        self.assertEqual(report.positions[0].realized_pnl_for_year(2025), 20)

    def test_compute_filled_order_perp_pnl_uses_weighted_average_for_adds_and_reductions(
        self,
    ) -> None:
        path = write_fixture(
            """
            1/05/25 10:00:00
            Perpetuals
            ETH PERP
            Limit
            Sell
            90 USDC
            1.5
            100%
            \\-- / --
            135 USDC
            Filled
            1/04/25 10:00:00
            Perpetuals
            ETH PERP
            Limit
            Sell
            130 USDC
            0.5
            100%
            \\-- / --
            65 USDC
            Filled
            1/02/25 10:00:00
            Perpetuals
            ETH PERP
            Limit
            Buy
            120 USDC
            1
            100%
            \\-- / --
            120 USDC
            Filled
            1/01/25 10:00:00
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

        report = compute_filled_order_perp_pnl(path, report_year=2025)
        position = report.positions[0]

        self.assertEqual(report.product_summaries["ETH-PERP"].realized_pnl_usd, -20)
        self.assertEqual(position.side, "long")
        self.assertEqual(position.realized_pnl_for_year(2025), -20)
        self.assertEqual(str(position.average_entry_price), "110")
        self.assertEqual(str(position.max_abs_position_qty), "2")

    def test_compute_filled_order_perp_pnl_opens_residual_position_on_flip(self) -> None:
        path = write_fixture(
            """
            1/02/25 10:00:00
            Perpetuals
            SOL PERP
            Limit
            Sell
            110 USDC
            3
            100%
            \\-- / --
            330 USDC
            Filled
            1/01/25 10:00:00
            Perpetuals
            SOL PERP
            Limit
            Buy
            100 USDC
            2
            100%
            \\-- / --
            200 USDC
            Filled
            """
        )

        report = compute_filled_order_perp_pnl(path, report_year=2025)

        self.assertEqual(report.product_summaries["SOL-PERP"].realized_pnl_usd, 20)
        self.assertEqual(str(report.product_summaries["SOL-PERP"].ending_position_qty), "-1")
        self.assertEqual(
            str(report.product_summaries["SOL-PERP"].ending_average_entry_price),
            "110",
        )
        self.assertEqual(len(report.positions), 1)
        self.assertEqual(report.positions[0].side, "long")

    def test_load_forced_close_adjustments_reads_suspended_close_events(self) -> None:
        path = write_adjustments_fixture(
            """
            version: 1
            order_classifications:
              - fill_id: csv_ignored_regular_fill
                order_function: CLOSE
                open_pos_type: LONG
                classified_at: 2026-04-22T06:23:48Z
              - fill_id: suspended_PROMPT-PERP_2026-02-20-13:00
                order_function: CLOSE
                open_pos_type: LONG
                classified_at: 2026-04-22T06:23:48Z
            """
        )

        adjustments = load_forced_close_adjustments(path, timezone_name="UTC")

        self.assertEqual(len(adjustments), 1)
        self.assertEqual(adjustments[0].product, "PROMPT-PERP")
        self.assertEqual(adjustments[0].timestamp.isoformat(), "2026-02-20T13:00:00+00:00")
        self.assertEqual(adjustments[0].open_pos_type, "LONG")
        self.assertEqual(adjustments[0].source_id, "suspended_PROMPT-PERP_2026-02-20-13:00")
        self.assertEqual(adjustments[0].close_price_usdc, 0)

    def test_compute_filled_order_perp_pnl_applies_forced_long_close_adjustment(
        self,
    ) -> None:
        filled_orders_path = write_fixture(
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

        report = compute_filled_order_perp_pnl(
            filled_orders_path,
            report_year=2026,
            manual_adjustments_path=adjustments_path,
            timezone_name="UTC",
        )

        summary = report.product_summaries["PROMPT-PERP"]
        self.assertEqual(summary.realized_pnl_usd, -10)
        self.assertEqual(summary.ending_position_qty, 0)
        self.assertEqual(summary.closed_position_count, 1)
        self.assertEqual(len(report.positions), 1)
        self.assertEqual(report.positions[0].status, "closed")
        self.assertEqual(report.positions[0].close_reason, "forced_close_adjustment")
        self.assertEqual(
            report.positions[0].close_source,
            "suspended_PROMPT-PERP_2026-02-20-13:00",
        )

    def test_compute_filled_order_perp_pnl_applies_forced_short_close_adjustment(
        self,
    ) -> None:
        filled_orders_path = write_fixture(
            """
            2/19/26 10:00:00
            Perpetuals
            PROMPT PERP
            Limit
            Sell
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
                open_pos_type: SHORT
                classified_at: 2026-04-22T06:23:48Z
            """
        )

        report = compute_filled_order_perp_pnl(
            filled_orders_path,
            report_year=2026,
            manual_adjustments_path=adjustments_path,
            timezone_name="UTC",
        )

        summary = report.product_summaries["PROMPT-PERP"]
        self.assertEqual(summary.realized_pnl_usd, 10)
        self.assertEqual(summary.ending_position_qty, 0)
        self.assertEqual(report.positions[0].side, "short")

    def test_compute_filled_order_perp_pnl_rejects_forced_close_side_mismatch(
        self,
    ) -> None:
        filled_orders_path = write_fixture(
            """
            2/19/26 10:00:00
            Perpetuals
            PROMPT PERP
            Limit
            Sell
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

        with self.assertRaisesRegex(ValueError, "Forced close side mismatch"):
            compute_filled_order_perp_pnl(
                filled_orders_path,
                report_year=2026,
                manual_adjustments_path=adjustments_path,
                timezone_name="UTC",
            )

    def test_write_filled_order_perp_reports_outputs_positions_and_product_summary(self) -> None:
        path = write_fixture(
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
        report = compute_filled_order_perp_pnl(path, report_year=2025)
        output_dir = workspace_output_dir("filled-orders-report")

        paths = write_filled_order_perp_reports(report, output_dir)

        self.assertEqual(
            {path.name for path in paths},
            {
                "filled_order_perp_product_summary_2025.csv",
                "filled_order_perp_positions_2025.csv",
            },
        )
        with (output_dir / "filled_order_perp_product_summary_2025.csv").open(
            newline="", encoding="utf-8"
        ) as csv_file:
            summary_rows = list(csv.DictReader(csv_file))
        self.assertEqual(summary_rows[0]["product"], "BTC-PERP")
        self.assertEqual(summary_rows[0]["realized_pnl_usd"], "20.00000000")
        with (output_dir / "filled_order_perp_positions_2025.csv").open(
            newline="", encoding="utf-8"
        ) as csv_file:
            position_rows = list(csv.DictReader(csv_file))
        self.assertEqual(position_rows[0]["ending_position_qty"], "0")
        self.assertEqual(position_rows[0]["average_entry_price"], "100")
        self.assertEqual(position_rows[0]["close_reason"], "filled_order")


if __name__ == "__main__":
    unittest.main()
