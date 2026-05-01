from __future__ import annotations

import argparse
from pathlib import Path

from .filled_orders import run_filled_order_perp_report
from .futures_tax import run_futures_tax_report
from .spot_btc_tax import run_spot_btc_tax_report
from .unified_tax import run_unified_tax_report


DEFAULT_OUTPUT_DIR = "reports"
DEFAULT_INPUT_GLOB = "transaction_data/coinbase_pro_transactions_*.csv"
DEFAULT_FILLED_ORDERS_PATH = "transaction_data/filled_orders/AllFilledOrders.md"
DEFAULT_MANUAL_ADJUSTMENTS_PATH = "transaction_data/manual_trade_list_adjustments.yaml"
DEFAULT_COST_BASIS_PATH = "transaction_data/btc_cost_basis.csv"
DEFAULT_REPORT_SUBDIRS = {
    "filled-orders": "filled_orders",
    "futures-tax": "futures_tax",
    "spot-btc": "spot_btc",
    "unified": "final",
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate year-scoped Coinbase tax reports."
    )
    parser.add_argument(
        "--year",
        type=int,
        help=(
            "Optional report year. Omit it to write all detected years in "
            "futures-tax, spot-btc, and unified modes."
        ),
    )
    parser.add_argument("--input-glob", default=DEFAULT_INPUT_GLOB)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--report-mode",
        choices=[
            "filled-orders",
            "futures-tax",
            "spot-btc",
            "unified",
        ],
        default="unified",
        help=(
            "unified writes the final Intx BTC plus perp tax summary and "
            "supporting reports; futures-tax combines filled-order realized PnL "
            "with CSV funding and trading fees; spot-btc writes HIFO BTC spot "
            "disposal reports; filled-orders reconstructs perp PnL from Coinbase "
            "filled orders markdown."
        ),
    )
    parser.add_argument(
        "--cost-basis",
        help="Optional BTC external cost basis CSV used by unified and spot-btc modes.",
    )
    parser.add_argument(
        "--filled-orders",
        default=DEFAULT_FILLED_ORDERS_PATH,
        help="Coinbase filled orders markdown used by filled-orders mode.",
    )
    parser.add_argument(
        "--filled-orders-timezone",
        default="Europe/Vilnius",
        help="Timezone for Coinbase filled-orders timestamps.",
    )
    parser.add_argument(
        "--manual-adjustments",
        default=DEFAULT_MANUAL_ADJUSTMENTS_PATH,
        help=(
            "Manual trade adjustment YAML used by filled-orders and futures-tax "
            "modes for delisted perpetual forced closes."
        ),
    )
    parser.add_argument(
        "--products",
        nargs="*",
        help="Optional product filter for filled-orders mode, e.g. BTC-PERP ETH-PERP.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    output_dir = _output_dir_for_mode(args.report_mode, args.output_dir)

    if args.report_mode == "filled-orders":
        if args.year is None:
            parser.error("--year is required for --report-mode filled-orders")
        output_paths = run_filled_order_perp_report(
            report_year=args.year,
            filled_orders_path=Path(args.filled_orders),
            output_dir=output_dir,
            products=set(args.products) if args.products else None,
            timezone_name=args.filled_orders_timezone,
            manual_adjustments_path=Path(args.manual_adjustments),
        )
    elif args.report_mode == "futures-tax":
        output_paths = run_futures_tax_report(
            filled_orders_path=Path(args.filled_orders),
            input_glob=args.input_glob,
            output_dir=output_dir,
            years={args.year} if args.year is not None else None,
            products=set(args.products) if args.products else None,
            timezone_name=args.filled_orders_timezone,
            manual_adjustments_path=Path(args.manual_adjustments),
        )
    elif args.report_mode == "unified":
        output_paths = run_unified_tax_report(
            input_glob=args.input_glob,
            filled_orders_path=Path(args.filled_orders),
            cost_basis_path=Path(args.cost_basis or DEFAULT_COST_BASIS_PATH),
            output_dir=output_dir,
            years={args.year} if args.year is not None else None,
            products=set(args.products) if args.products else None,
            timezone_name=args.filled_orders_timezone,
            manual_adjustments_path=Path(args.manual_adjustments),
        )
    elif args.report_mode == "spot-btc":
        output_paths = run_spot_btc_tax_report(
            input_glob=args.input_glob,
            cost_basis_path=Path(args.cost_basis or DEFAULT_COST_BASIS_PATH),
            output_dir=output_dir,
            years={args.year} if args.year is not None else None,
        )
    else:
        parser.error(f"Unsupported report mode: {args.report_mode}")
    for path in output_paths:
        print(path)
    return 0


def _output_dir_for_mode(report_mode: str, output_dir: str) -> Path:
    if output_dir == DEFAULT_OUTPUT_DIR:
        subdir = DEFAULT_REPORT_SUBDIRS.get(report_mode)
        if subdir is not None:
            return Path(DEFAULT_OUTPUT_DIR) / subdir
    return Path(output_dir)
