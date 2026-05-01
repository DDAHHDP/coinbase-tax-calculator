from __future__ import annotations

import csv
import glob
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from .amounts import ZERO, decimal_to_str
from .coinbase_csv import load_coinbase_rows
from .filled_orders import (
    compute_filled_order_perp_pnl,
    load_forced_close_adjustments,
    parse_filled_orders_markdown,
)
from .models import CoinbaseRow


MONEY_QUANTUM = Decimal("0.00000001")
PERP_FILL_TYPES = {"Perpetual Futures Buy", "Perpetual Futures Sell"}
FUNDING_TYPES = {"Funding Fee", "Funding Fees (24 Hours)"}


@dataclass(frozen=True)
class FuturesTaxProductYear:
    report_year: int
    product: str
    realized_pnl_usd: Decimal = ZERO
    funding_income_received_usd: Decimal = ZERO
    funding_costs_paid_usd: Decimal = ZERO
    trading_fees_paid_usd: Decimal = ZERO
    ending_position_qty: Decimal = ZERO
    ending_average_entry_price: Decimal = ZERO

    @property
    def net_funding_fees_usd(self) -> Decimal:
        return _money(self.funding_income_received_usd - self.funding_costs_paid_usd)

    @property
    def taxable_or_deductible_amount_usd(self) -> Decimal:
        return _money(
            self.realized_pnl_usd
            + self.funding_income_received_usd
            - self.funding_costs_paid_usd
            - self.trading_fees_paid_usd
        )

    @property
    def result_type(self) -> str:
        return (
            "taxable"
            if self.taxable_or_deductible_amount_usd >= ZERO
            else "deductible_loss"
        )


@dataclass(frozen=True)
class FuturesTaxYearSummary:
    report_year: int | str
    product_count: int
    realized_pnl_usd: Decimal = ZERO
    funding_income_received_usd: Decimal = ZERO
    funding_costs_paid_usd: Decimal = ZERO
    trading_fees_paid_usd: Decimal = ZERO

    @property
    def net_funding_fees_usd(self) -> Decimal:
        return _money(self.funding_income_received_usd - self.funding_costs_paid_usd)

    @property
    def taxable_or_deductible_amount_usd(self) -> Decimal:
        return _money(
            self.realized_pnl_usd
            + self.funding_income_received_usd
            - self.funding_costs_paid_usd
            - self.trading_fees_paid_usd
        )

    @property
    def result_type(self) -> str:
        return (
            "taxable"
            if self.taxable_or_deductible_amount_usd >= ZERO
            else "deductible_loss"
        )


@dataclass(frozen=True)
class FuturesTaxReport:
    filled_orders_path: Path
    csv_paths: list[Path]
    product_years: dict[tuple[int, str], FuturesTaxProductYear]
    year_summaries: dict[int, FuturesTaxYearSummary]
    all_years_summary: FuturesTaxYearSummary


@dataclass
class _CsvCostTotals:
    funding_income_received_usd: Decimal = ZERO
    funding_costs_paid_usd: Decimal = ZERO
    trading_fees_paid_usd: Decimal = ZERO


def run_futures_tax_report(
    filled_orders_path: Path,
    input_glob: str,
    output_dir: Path,
    years: set[int] | None = None,
    products: set[str] | None = None,
    timezone_name: str = "Europe/Vilnius",
    manual_adjustments_path: Path | None = None,
) -> list[Path]:
    matched_files = [Path(path) for path in sorted(glob.glob(input_glob))]
    if not matched_files:
        raise FileNotFoundError(f"No Coinbase CSV files matched: {input_glob}")

    rows: list[CoinbaseRow] = []
    for path in matched_files:
        rows.extend(load_coinbase_rows(path))

    report = build_futures_tax_report(
        filled_orders_path=filled_orders_path,
        coinbase_rows=rows,
        csv_paths=matched_files,
        years=years,
        products=products,
        timezone_name=timezone_name,
        manual_adjustments_path=manual_adjustments_path,
    )
    return write_futures_tax_reports(report, output_dir)


def build_futures_tax_report(
    filled_orders_path: Path,
    coinbase_rows: list[CoinbaseRow],
    csv_paths: list[Path] | None = None,
    years: set[int] | None = None,
    products: set[str] | None = None,
    timezone_name: str = "Europe/Vilnius",
    manual_adjustments_path: Path | None = None,
) -> FuturesTaxReport:
    selected_products = {_normalize_product(product) for product in products or set()}
    filled_orders = [
        order
        for order in parse_filled_orders_markdown(
            filled_orders_path, timezone_name=timezone_name
        )
        if not selected_products or order.product in selected_products
    ]
    forced_close_adjustments = [
        adjustment
        for adjustment in load_forced_close_adjustments(
            manual_adjustments_path, timezone_name=timezone_name
        )
        if not selected_products or adjustment.product in selected_products
    ]
    csv_costs = _aggregate_csv_futures_costs(coinbase_rows, selected_products)
    detected_years = {
        order.timestamp.year for order in filled_orders if order.status == "Filled"
    } | {year for year, _product in csv_costs} | {
        adjustment.timestamp.year for adjustment in forced_close_adjustments
    }
    report_years = sorted(years if years is not None else detected_years)

    product_years: dict[tuple[int, str], FuturesTaxProductYear] = {}
    for report_year in report_years:
        pnl_report = compute_filled_order_perp_pnl(
            filled_orders_path,
            report_year=report_year,
            products=selected_products or None,
            timezone_name=timezone_name,
            manual_adjustments_path=manual_adjustments_path,
        )
        products_for_year = sorted(
            set(pnl_report.product_summaries)
            | {
                product
                for year, product in csv_costs
                if year == report_year
            }
        )
        for product in products_for_year:
            pnl_summary = pnl_report.product_summaries.get(product)
            costs = csv_costs[(report_year, product)]
            product_years[(report_year, product)] = FuturesTaxProductYear(
                report_year=report_year,
                product=product,
                realized_pnl_usd=(
                    pnl_summary.realized_pnl_usd if pnl_summary is not None else ZERO
                ),
                funding_income_received_usd=_money(
                    costs.funding_income_received_usd
                ),
                funding_costs_paid_usd=_money(costs.funding_costs_paid_usd),
                trading_fees_paid_usd=_money(costs.trading_fees_paid_usd),
                ending_position_qty=(
                    pnl_summary.ending_position_qty
                    if pnl_summary is not None
                    else ZERO
                ),
                ending_average_entry_price=(
                    pnl_summary.ending_average_entry_price
                    if pnl_summary is not None
                    else ZERO
                ),
            )

    year_summaries = {
        year: _summarize_year(
            year,
            [
                product_year
                for (product_year_year, _product), product_year in product_years.items()
                if product_year_year == year
            ],
        )
        for year in report_years
    }
    all_years_summary = _summarize_all_years(product_years.values())
    return FuturesTaxReport(
        filled_orders_path=filled_orders_path,
        csv_paths=csv_paths or [],
        product_years=product_years,
        year_summaries=year_summaries,
        all_years_summary=all_years_summary,
    )


def write_futures_tax_reports(
    report: FuturesTaxReport, output_dir: Path
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "futures_tax_summary_by_year.csv"
    product_year_path = output_dir / "futures_tax_by_product_year.csv"
    _write_year_summary(report, summary_path)
    _write_product_years(report, product_year_path)
    return [summary_path, product_year_path]


def _aggregate_csv_futures_costs(
    rows: list[CoinbaseRow], selected_products: set[str]
) -> dict[tuple[int, str], _CsvCostTotals]:
    costs: dict[tuple[int, str], _CsvCostTotals] = defaultdict(_CsvCostTotals)
    for row in rows:
        product = _normalize_product(row.asset)
        if selected_products and product not in selected_products:
            continue

        key = (row.timestamp.year, product)
        if row.transaction_type in FUNDING_TYPES:
            funding = row.total_amount or ZERO
            if funding >= ZERO:
                costs[key].funding_income_received_usd += funding
            else:
                costs[key].funding_costs_paid_usd += -funding
            continue

        if row.transaction_type in PERP_FILL_TYPES:
            costs[key].trading_fees_paid_usd += abs(row.fees_amount or ZERO)

    return costs


def _summarize_year(
    report_year: int, product_years: list[FuturesTaxProductYear]
) -> FuturesTaxYearSummary:
    return FuturesTaxYearSummary(
        report_year=report_year,
        product_count=len(product_years),
        realized_pnl_usd=_money(
            sum(
                (product_year.realized_pnl_usd for product_year in product_years),
                start=ZERO,
            )
        ),
        funding_income_received_usd=_money(
            sum(
                (
                    product_year.funding_income_received_usd
                    for product_year in product_years
                ),
                start=ZERO,
            )
        ),
        funding_costs_paid_usd=_money(
            sum(
                (
                    product_year.funding_costs_paid_usd
                    for product_year in product_years
                ),
                start=ZERO,
            )
        ),
        trading_fees_paid_usd=_money(
            sum(
                (product_year.trading_fees_paid_usd for product_year in product_years),
                start=ZERO,
            )
        ),
    )


def _summarize_all_years(
    product_years: object,
) -> FuturesTaxYearSummary:
    rows = list(product_years)
    return FuturesTaxYearSummary(
        report_year="ALL",
        product_count=len({row.product for row in rows}),
        realized_pnl_usd=_money(
            sum((row.realized_pnl_usd for row in rows), start=ZERO)
        ),
        funding_income_received_usd=_money(
            sum((row.funding_income_received_usd for row in rows), start=ZERO)
        ),
        funding_costs_paid_usd=_money(
            sum((row.funding_costs_paid_usd for row in rows), start=ZERO)
        ),
        trading_fees_paid_usd=_money(
            sum((row.trading_fees_paid_usd for row in rows), start=ZERO)
        ),
    )


def _write_year_summary(report: FuturesTaxReport, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=_summary_fieldnames())
        writer.writeheader()
        for summary in [
            *[report.year_summaries[year] for year in sorted(report.year_summaries)],
            report.all_years_summary,
        ]:
            writer.writerow(_summary_row(summary))


def _write_product_years(report: FuturesTaxReport, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "report_year",
                "product",
                "realized_pnl_usd",
                "funding_income_received_usd",
                "funding_costs_paid_usd",
                "net_funding_fees_usd",
                "trading_fees_paid_usd",
                "taxable_or_deductible_amount_usd",
                "result_type",
                "ending_position_qty",
                "ending_average_entry_price",
            ],
        )
        writer.writeheader()
        for (_year, _product), product_year in sorted(report.product_years.items()):
            writer.writerow(
                {
                    "report_year": product_year.report_year,
                    "product": product_year.product,
                    "realized_pnl_usd": decimal_to_str(product_year.realized_pnl_usd),
                    "funding_income_received_usd": decimal_to_str(
                        product_year.funding_income_received_usd
                    ),
                    "funding_costs_paid_usd": decimal_to_str(
                        product_year.funding_costs_paid_usd
                    ),
                    "net_funding_fees_usd": decimal_to_str(
                        product_year.net_funding_fees_usd
                    ),
                    "trading_fees_paid_usd": decimal_to_str(
                        product_year.trading_fees_paid_usd
                    ),
                    "taxable_or_deductible_amount_usd": decimal_to_str(
                        product_year.taxable_or_deductible_amount_usd
                    ),
                    "result_type": product_year.result_type,
                    "ending_position_qty": decimal_to_str(
                        product_year.ending_position_qty
                    ),
                    "ending_average_entry_price": decimal_to_str(
                        product_year.ending_average_entry_price
                    ),
                }
            )


def _summary_fieldnames() -> list[str]:
    return [
        "report_year",
        "product_count",
        "realized_pnl_usd",
        "funding_income_received_usd",
        "funding_costs_paid_usd",
        "net_funding_fees_usd",
        "trading_fees_paid_usd",
        "taxable_or_deductible_amount_usd",
        "result_type",
    ]


def _summary_row(summary: FuturesTaxYearSummary) -> dict[str, object]:
    return {
        "report_year": summary.report_year,
        "product_count": summary.product_count,
        "realized_pnl_usd": decimal_to_str(summary.realized_pnl_usd),
        "funding_income_received_usd": decimal_to_str(
            summary.funding_income_received_usd
        ),
        "funding_costs_paid_usd": decimal_to_str(summary.funding_costs_paid_usd),
        "net_funding_fees_usd": decimal_to_str(summary.net_funding_fees_usd),
        "trading_fees_paid_usd": decimal_to_str(summary.trading_fees_paid_usd),
        "taxable_or_deductible_amount_usd": decimal_to_str(
            summary.taxable_or_deductible_amount_usd
        ),
        "result_type": summary.result_type,
    }


def _normalize_product(value: str) -> str:
    normalized = value.strip().upper().replace("/", "-").replace("_", "-")
    if normalized.endswith(" PERP"):
        return normalized.removesuffix(" PERP") + "-PERP"
    return normalized


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
