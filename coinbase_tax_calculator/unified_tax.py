from __future__ import annotations

import csv
import glob
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from .amounts import ZERO, decimal_to_str
from .coinbase_csv import load_coinbase_rows
from .futures_tax import (
    FuturesTaxReport,
    FuturesTaxYearSummary,
    build_futures_tax_report,
    write_futures_tax_reports,
)
from .models import CoinbaseRow
from .spot_btc_tax import (
    SpotBtcTaxReport,
    SpotBtcYearSummary,
    build_spot_btc_tax_report,
    write_spot_btc_tax_reports,
)


MONEY_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True)
class UnifiedTaxYearSummary:
    report_year: int | str
    perpetual_product_count: int = 0
    perpetual_realized_pnl_usd: Decimal = ZERO
    perpetual_funding_income_received_usd: Decimal = ZERO
    perpetual_funding_costs_paid_usd: Decimal = ZERO
    perpetual_trading_fees_paid_usd: Decimal = ZERO
    perpetual_taxable_or_deductible_amount_usd: Decimal = ZERO
    intx_btc_sell_count: int = 0
    intx_btc_complete_sell_count: int = 0
    intx_btc_incomplete_sell_count: int = 0
    intx_btc_size_sold: Decimal = ZERO
    intx_btc_net_proceeds_usd: Decimal = ZERO
    intx_btc_fees_usd: Decimal = ZERO
    intx_btc_cost_basis_usd: Decimal = ZERO
    intx_btc_taxable_gain_loss_known_usd: Decimal = ZERO
    intx_btc_missing_basis_size: Decimal = ZERO

    @property
    def perpetual_net_funding_fees_usd(self) -> Decimal:
        return _money(
            self.perpetual_funding_income_received_usd
            - self.perpetual_funding_costs_paid_usd
        )

    @property
    def unified_taxable_or_deductible_known_usd(self) -> Decimal:
        return _money(
            self.perpetual_taxable_or_deductible_amount_usd
            + self.intx_btc_taxable_gain_loss_known_usd
        )

    @property
    def result_type(self) -> str:
        return (
            "taxable"
            if self.unified_taxable_or_deductible_known_usd >= ZERO
            else "deductible_loss"
        )


@dataclass(frozen=True)
class UnifiedTaxReport:
    futures_tax_report: FuturesTaxReport
    spot_btc_tax_report: SpotBtcTaxReport
    year_summaries: list[UnifiedTaxYearSummary]
    all_years_summary: UnifiedTaxYearSummary


def run_unified_tax_report(
    input_glob: str,
    filled_orders_path: Path,
    cost_basis_path: Path,
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

    futures_tax_report = build_futures_tax_report(
        filled_orders_path=filled_orders_path,
        coinbase_rows=rows,
        csv_paths=matched_files,
        years=years,
        products=products,
        timezone_name=timezone_name,
        manual_adjustments_path=manual_adjustments_path,
    )
    spot_btc_tax_report = build_spot_btc_tax_report(
        coinbase_rows=rows,
        cost_basis_path=cost_basis_path,
        csv_paths=matched_files,
        years=years,
    )
    unified_report = build_unified_tax_report(
        futures_tax_report=futures_tax_report,
        spot_btc_tax_report=spot_btc_tax_report,
    )

    futures_paths = write_futures_tax_reports(futures_tax_report, output_dir)
    spot_paths = write_spot_btc_tax_reports(spot_btc_tax_report, output_dir)
    unified_paths = write_unified_tax_report(unified_report, output_dir)
    return [*unified_paths, *futures_paths, *spot_paths]


def build_unified_tax_report(
    futures_tax_report: FuturesTaxReport,
    spot_btc_tax_report: SpotBtcTaxReport,
) -> UnifiedTaxReport:
    report_years = sorted(
        set(futures_tax_report.year_summaries) | set(spot_btc_tax_report.year_summaries)
    )
    year_summaries = [
        _build_unified_year_summary(
            report_year=year,
            futures_summary=futures_tax_report.year_summaries.get(year),
            spot_summary=spot_btc_tax_report.year_summaries.get(year),
        )
        for year in report_years
    ]
    all_years_summary = _build_unified_year_summary(
        report_year="ALL",
        futures_summary=futures_tax_report.all_years_summary,
        spot_summary=spot_btc_tax_report.all_years_summary,
    )
    return UnifiedTaxReport(
        futures_tax_report=futures_tax_report,
        spot_btc_tax_report=spot_btc_tax_report,
        year_summaries=year_summaries,
        all_years_summary=all_years_summary,
    )


def write_unified_tax_report(
    report: UnifiedTaxReport,
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "unified_tax_summary_by_year.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=_summary_fieldnames())
        writer.writeheader()
        for summary in [*report.year_summaries, report.all_years_summary]:
            writer.writerow(_summary_row(summary))
    return [summary_path]


def _build_unified_year_summary(
    report_year: int | str,
    futures_summary: FuturesTaxYearSummary | None,
    spot_summary: SpotBtcYearSummary | None,
) -> UnifiedTaxYearSummary:
    return UnifiedTaxYearSummary(
        report_year=report_year,
        perpetual_product_count=(
            futures_summary.product_count if futures_summary is not None else 0
        ),
        perpetual_realized_pnl_usd=(
            _money(futures_summary.realized_pnl_usd)
            if futures_summary is not None
            else ZERO
        ),
        perpetual_funding_income_received_usd=(
            _money(futures_summary.funding_income_received_usd)
            if futures_summary is not None
            else ZERO
        ),
        perpetual_funding_costs_paid_usd=(
            _money(futures_summary.funding_costs_paid_usd)
            if futures_summary is not None
            else ZERO
        ),
        perpetual_trading_fees_paid_usd=(
            _money(futures_summary.trading_fees_paid_usd)
            if futures_summary is not None
            else ZERO
        ),
        perpetual_taxable_or_deductible_amount_usd=(
            futures_summary.taxable_or_deductible_amount_usd
            if futures_summary is not None
            else ZERO
        ),
        intx_btc_sell_count=spot_summary.sell_count if spot_summary is not None else 0,
        intx_btc_complete_sell_count=(
            spot_summary.complete_sell_count if spot_summary is not None else 0
        ),
        intx_btc_incomplete_sell_count=(
            spot_summary.incomplete_sell_count if spot_summary is not None else 0
        ),
        intx_btc_size_sold=(
            spot_summary.size_sold_btc if spot_summary is not None else ZERO
        ),
        intx_btc_net_proceeds_usd=(
            _money(spot_summary.net_proceeds_usd)
            if spot_summary is not None
            else ZERO
        ),
        intx_btc_fees_usd=(
            _money(spot_summary.fees_usd) if spot_summary is not None else ZERO
        ),
        intx_btc_cost_basis_usd=(
            _money(spot_summary.cost_basis_usd) if spot_summary is not None else ZERO
        ),
        intx_btc_taxable_gain_loss_known_usd=(
            _money(spot_summary.taxable_gain_loss_known_usd)
            if spot_summary is not None
            else ZERO
        ),
        intx_btc_missing_basis_size=(
            spot_summary.missing_basis_size_btc if spot_summary is not None else ZERO
        ),
    )


def _summary_fieldnames() -> list[str]:
    return [
        "report_year",
        "perpetual_product_count",
        "perpetual_realized_pnl_usd",
        "perpetual_funding_income_received_usd",
        "perpetual_funding_costs_paid_usd",
        "perpetual_net_funding_fees_usd",
        "perpetual_trading_fees_paid_usd",
        "perpetual_taxable_or_deductible_amount_usd",
        "intx_btc_sell_count",
        "intx_btc_complete_sell_count",
        "intx_btc_incomplete_sell_count",
        "intx_btc_size_sold",
        "intx_btc_net_proceeds_usd",
        "intx_btc_fees_usd",
        "intx_btc_cost_basis_usd",
        "intx_btc_taxable_gain_loss_known_usd",
        "intx_btc_missing_basis_size",
        "unified_taxable_or_deductible_known_usd",
        "result_type",
    ]


def _summary_row(summary: UnifiedTaxYearSummary) -> dict[str, object]:
    return {
        "report_year": summary.report_year,
        "perpetual_product_count": summary.perpetual_product_count,
        "perpetual_realized_pnl_usd": decimal_to_str(
            summary.perpetual_realized_pnl_usd
        ),
        "perpetual_funding_income_received_usd": decimal_to_str(
            summary.perpetual_funding_income_received_usd
        ),
        "perpetual_funding_costs_paid_usd": decimal_to_str(
            summary.perpetual_funding_costs_paid_usd
        ),
        "perpetual_net_funding_fees_usd": decimal_to_str(
            summary.perpetual_net_funding_fees_usd
        ),
        "perpetual_trading_fees_paid_usd": decimal_to_str(
            summary.perpetual_trading_fees_paid_usd
        ),
        "perpetual_taxable_or_deductible_amount_usd": decimal_to_str(
            summary.perpetual_taxable_or_deductible_amount_usd
        ),
        "intx_btc_sell_count": summary.intx_btc_sell_count,
        "intx_btc_complete_sell_count": summary.intx_btc_complete_sell_count,
        "intx_btc_incomplete_sell_count": summary.intx_btc_incomplete_sell_count,
        "intx_btc_size_sold": decimal_to_str(summary.intx_btc_size_sold),
        "intx_btc_net_proceeds_usd": decimal_to_str(
            summary.intx_btc_net_proceeds_usd
        ),
        "intx_btc_fees_usd": decimal_to_str(summary.intx_btc_fees_usd),
        "intx_btc_cost_basis_usd": decimal_to_str(summary.intx_btc_cost_basis_usd),
        "intx_btc_taxable_gain_loss_known_usd": decimal_to_str(
            summary.intx_btc_taxable_gain_loss_known_usd
        ),
        "intx_btc_missing_basis_size": decimal_to_str(
            summary.intx_btc_missing_basis_size
        ),
        "unified_taxable_or_deductible_known_usd": decimal_to_str(
            summary.unified_taxable_or_deductible_known_usd
        ),
        "result_type": summary.result_type,
    }


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
