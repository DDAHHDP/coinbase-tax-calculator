from __future__ import annotations

import csv
import glob
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from .amounts import ZERO, decimal_to_str, parse_decimal
from .coinbase_csv import load_coinbase_rows
from .models import CoinbaseRow


MONEY_QUANTUM = Decimal("0.00000001")
BTC_BUY_TYPES = {"Advanced Trade Buy", "Buy"}
BTC_SELL_TYPES = {"Intx Spot Fill"}


@dataclass
class SpotBtcBuyLot:
    lot_id: str
    acquired_at: datetime
    size_btc: Decimal
    unit_price_usd: Decimal
    total_cost_usd: Decimal
    fees_usd: Decimal
    remaining_size_btc: Decimal
    source: str
    source_file: Path | str
    source_row_number: int
    notes: str


@dataclass(frozen=True)
class SpotBtcSell:
    sell_id: str
    sold_at: datetime
    size_btc: Decimal
    gross_proceeds_usd: Decimal
    fees_usd: Decimal
    net_proceeds_usd: Decimal
    unit_price_usd: Decimal
    source_file: Path | str
    source_row_number: int
    notes: str


@dataclass(frozen=True)
class SpotBtcTaxLot:
    sell_id: str
    buy_lot_id: str
    sold_at: datetime
    acquired_at: datetime
    size_btc: Decimal
    buy_unit_price_usd: Decimal
    cost_basis_usd: Decimal
    buy_source: str


@dataclass(frozen=True)
class SpotBtcSellTax:
    sell_id: str
    sold_at: datetime
    tax_year: int
    size_btc: Decimal
    net_proceeds_usd: Decimal
    fees_usd: Decimal
    cost_basis_usd: Decimal
    missing_basis_size_btc: Decimal
    taxable_gain_loss_usd: Decimal | None
    basis_status: str


@dataclass(frozen=True)
class SpotBtcYearSummary:
    report_year: int | str
    sell_count: int
    complete_sell_count: int
    incomplete_sell_count: int
    size_sold_btc: Decimal
    net_proceeds_usd: Decimal
    fees_usd: Decimal
    cost_basis_usd: Decimal
    taxable_gain_loss_known_usd: Decimal
    missing_basis_size_btc: Decimal


@dataclass(frozen=True)
class SpotBtcTaxReport:
    cost_basis_path: Path
    csv_paths: list[Path]
    buys: list[SpotBtcBuyLot]
    sells: list[SpotBtcSell]
    tax_by_sell: list[SpotBtcSellTax]
    tax_lots: list[SpotBtcTaxLot]
    year_summaries: dict[int, SpotBtcYearSummary]
    all_years_summary: SpotBtcYearSummary


def run_spot_btc_tax_report(
    input_glob: str,
    cost_basis_path: Path,
    output_dir: Path,
    years: set[int] | None = None,
) -> list[Path]:
    matched_files = [Path(path) for path in sorted(glob.glob(input_glob))]
    if not matched_files:
        raise FileNotFoundError(f"No Coinbase CSV files matched: {input_glob}")

    rows: list[CoinbaseRow] = []
    for path in matched_files:
        rows.extend(load_coinbase_rows(path))

    report = build_spot_btc_tax_report(
        coinbase_rows=rows,
        cost_basis_path=cost_basis_path,
        csv_paths=matched_files,
        years=years,
    )
    return write_spot_btc_tax_reports(report, output_dir)


def build_spot_btc_tax_report(
    coinbase_rows: list[CoinbaseRow],
    cost_basis_path: Path,
    csv_paths: list[Path] | None = None,
    years: set[int] | None = None,
) -> SpotBtcTaxReport:
    buys = [
        *_load_template_buys(cost_basis_path),
        *_load_coinbase_buys(coinbase_rows),
    ]
    buys.sort(key=lambda buy: (buy.acquired_at, str(buy.source_file), buy.source_row_number))
    for index, buy in enumerate(buys, start=1):
        buy.lot_id = f"BTC-BUY-{index:04d}"

    sells = _load_coinbase_intx_sells(coinbase_rows)
    tax_by_sell: list[SpotBtcSellTax] = []
    tax_lots: list[SpotBtcTaxLot] = []

    for sell in sells:
        sell_tax, allocations = _apply_sell(sell, buys)
        tax_by_sell.append(sell_tax)
        tax_lots.extend(allocations)

    if years is not None:
        selected_years = years
        sells = [sell for sell in sells if sell.sold_at.year in selected_years]
        tax_by_sell = [
            sell_tax for sell_tax in tax_by_sell if sell_tax.tax_year in selected_years
        ]
        tax_lots = [lot for lot in tax_lots if lot.sold_at.year in selected_years]

    year_summaries = _build_year_summaries(tax_by_sell)
    return SpotBtcTaxReport(
        cost_basis_path=cost_basis_path,
        csv_paths=csv_paths or [],
        buys=buys,
        sells=sells,
        tax_by_sell=tax_by_sell,
        tax_lots=tax_lots,
        year_summaries=year_summaries,
        all_years_summary=_build_summary("ALL", tax_by_sell),
    )


def write_spot_btc_tax_reports(
    report: SpotBtcTaxReport, output_dir: Path
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    buys_path = output_dir / "Spot_BTC_buys.csv"
    sells_path = output_dir / "Spot_BTC_sells.csv"
    tax_by_sell_path = output_dir / "Spot_BTC_tax_by_sell.csv"
    tax_lots_path = output_dir / "Spot_BTC_tax_lots.csv"
    summary_path = output_dir / "Spot_BTC_tax_summary_by_year.csv"

    _write_buys(report.buys, buys_path)
    _write_sells(report.sells, sells_path)
    _write_tax_by_sell(report.tax_by_sell, tax_by_sell_path)
    _write_tax_lots(report.tax_lots, tax_lots_path)
    _write_year_summaries(
        report.year_summaries, report.all_years_summary, summary_path
    )
    return [buys_path, sells_path, tax_by_sell_path, tax_lots_path, summary_path]


def _load_template_buys(path: Path) -> list[SpotBtcBuyLot]:
    if not path.exists():
        raise FileNotFoundError(f"BTC cost basis file not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as csv_file:
        sample = csv_file.read(4096)
        csv_file.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        reader = csv.DictReader(csv_file, dialect=dialect)
        lots: list[SpotBtcBuyLot] = []
        for row_number, row in enumerate(reader, start=2):
            if _cell(row, "Product").upper() != "BTC":
                continue
            size = parse_decimal(_cell(row, "Size"))
            unit_price = parse_decimal(_cell(row, "Cost"))
            if size is None or size <= ZERO:
                raise ValueError(f"Invalid BTC size in {path}:{row_number}")
            if unit_price is None or unit_price < ZERO:
                raise ValueError(f"Invalid BTC unit cost in {path}:{row_number}")
            total_cost = _money(size * unit_price)
            lots.append(
                SpotBtcBuyLot(
                    lot_id="",
                    acquired_at=_parse_datetime(_cell(row, "Date")),
                    size_btc=size,
                    unit_price_usd=unit_price,
                    total_cost_usd=total_cost,
                    fees_usd=ZERO,
                    remaining_size_btc=size,
                    source="cost_basis_template",
                    source_file=path,
                    source_row_number=row_number,
                    notes=_cell(row, "Notes"),
                )
            )
    return lots


def _load_coinbase_buys(rows: list[CoinbaseRow]) -> list[SpotBtcBuyLot]:
    buys: list[SpotBtcBuyLot] = []
    for row in rows:
        quantity = row.quantity_transacted or ZERO
        if row.asset != "BTC" or row.transaction_type not in BTC_BUY_TYPES:
            continue
        if quantity <= ZERO:
            continue

        total_cost = _money(abs(row.total_amount or ZERO))
        unit_price = total_cost / quantity if quantity != ZERO else ZERO
        buys.append(
            SpotBtcBuyLot(
                lot_id="",
                acquired_at=row.timestamp,
                size_btc=quantity,
                unit_price_usd=unit_price,
                total_cost_usd=total_cost,
                fees_usd=_money(abs(row.fees_amount or ZERO)),
                remaining_size_btc=quantity,
                source="coinbase_csv",
                source_file=row.source_file,
                source_row_number=row.source_row_number,
                notes=row.notes,
            )
        )
    return buys


def _load_coinbase_intx_sells(rows: list[CoinbaseRow]) -> list[SpotBtcSell]:
    sells: list[SpotBtcSell] = []
    sorted_rows = sorted(
        rows, key=lambda row: (row.timestamp, str(row.source_file), row.source_row_number)
    )
    for row in sorted_rows:
        quantity = row.quantity_transacted or ZERO
        if row.asset != "BTC" or row.transaction_type not in BTC_SELL_TYPES:
            continue
        if quantity >= ZERO:
            continue

        size = abs(quantity)
        net_proceeds = _money(abs(row.total_amount or ZERO))
        gross_proceeds = _money(abs(row.subtotal_amount or row.total_amount or ZERO))
        unit_price = net_proceeds / size if size != ZERO else ZERO
        sells.append(
            SpotBtcSell(
                sell_id=f"BTC-SELL-{len(sells) + 1:04d}",
                sold_at=row.timestamp,
                size_btc=size,
                gross_proceeds_usd=gross_proceeds,
                fees_usd=_money(abs(row.fees_amount or ZERO)),
                net_proceeds_usd=net_proceeds,
                unit_price_usd=unit_price,
                source_file=row.source_file,
                source_row_number=row.source_row_number,
                notes=row.notes,
            )
        )
    return sells


def _apply_sell(
    sell: SpotBtcSell, buys: list[SpotBtcBuyLot]
) -> tuple[SpotBtcSellTax, list[SpotBtcTaxLot]]:
    remaining = sell.size_btc
    cost_basis = ZERO
    allocations: list[SpotBtcTaxLot] = []

    while remaining > ZERO:
        candidates = [
            buy
            for buy in buys
            if buy.acquired_at <= sell.sold_at and buy.remaining_size_btc > ZERO
        ]
        if not candidates:
            break
        selected = sorted(
            candidates,
            key=lambda buy: (-buy.unit_price_usd, buy.acquired_at, buy.lot_id),
        )[0]
        size_from_lot = min(remaining, selected.remaining_size_btc)
        allocation_cost = _money(size_from_lot * selected.unit_price_usd)
        selected.remaining_size_btc -= size_from_lot
        remaining -= size_from_lot
        cost_basis += allocation_cost
        allocations.append(
            SpotBtcTaxLot(
                sell_id=sell.sell_id,
                buy_lot_id=selected.lot_id,
                sold_at=sell.sold_at,
                acquired_at=selected.acquired_at,
                size_btc=size_from_lot,
                buy_unit_price_usd=selected.unit_price_usd,
                cost_basis_usd=allocation_cost,
                buy_source=selected.source,
            )
        )

    basis_status = "complete" if remaining == ZERO else "incomplete"
    taxable_gain_loss = (
        _money(sell.net_proceeds_usd - cost_basis)
        if basis_status == "complete"
        else None
    )
    return (
        SpotBtcSellTax(
            sell_id=sell.sell_id,
            sold_at=sell.sold_at,
            tax_year=sell.sold_at.year,
            size_btc=sell.size_btc,
            net_proceeds_usd=sell.net_proceeds_usd,
            fees_usd=sell.fees_usd,
            cost_basis_usd=_money(cost_basis),
            missing_basis_size_btc=remaining,
            taxable_gain_loss_usd=taxable_gain_loss,
            basis_status=basis_status,
        ),
        allocations,
    )


def _build_year_summaries(
    tax_by_sell: list[SpotBtcSellTax],
) -> dict[int, SpotBtcYearSummary]:
    return {
        year: _build_summary(
            year, [sell for sell in tax_by_sell if sell.tax_year == year]
        )
        for year in sorted({sell.tax_year for sell in tax_by_sell})
    }


def _build_summary(
    report_year: int | str, tax_by_sell: list[SpotBtcSellTax]
) -> SpotBtcYearSummary:
    complete_sells = [sell for sell in tax_by_sell if sell.basis_status == "complete"]
    incomplete_sells = [
        sell for sell in tax_by_sell if sell.basis_status != "complete"
    ]
    return SpotBtcYearSummary(
        report_year=report_year,
        sell_count=len(tax_by_sell),
        complete_sell_count=len(complete_sells),
        incomplete_sell_count=len(incomplete_sells),
        size_sold_btc=sum((sell.size_btc for sell in tax_by_sell), start=ZERO),
        net_proceeds_usd=_money(
            sum((sell.net_proceeds_usd for sell in tax_by_sell), start=ZERO)
        ),
        fees_usd=_money(sum((sell.fees_usd for sell in tax_by_sell), start=ZERO)),
        cost_basis_usd=_money(
            sum((sell.cost_basis_usd for sell in tax_by_sell), start=ZERO)
        ),
        taxable_gain_loss_known_usd=_money(
            sum(
                (sell.taxable_gain_loss_usd or ZERO for sell in complete_sells),
                start=ZERO,
            )
        ),
        missing_basis_size_btc=sum(
            (sell.missing_basis_size_btc for sell in tax_by_sell), start=ZERO
        ),
    )


def _write_buys(buys: list[SpotBtcBuyLot], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "lot_id",
                "acquired_at",
                "size_btc",
                "remaining_size_btc",
                "unit_price_usd",
                "total_cost_usd",
                "fees_usd",
                "source",
                "source_file",
                "source_row_number",
                "notes",
            ],
        )
        writer.writeheader()
        for buy in buys:
            writer.writerow(
                {
                    "lot_id": buy.lot_id,
                    "acquired_at": buy.acquired_at.isoformat(),
                    "size_btc": decimal_to_str(buy.size_btc),
                    "remaining_size_btc": decimal_to_str(buy.remaining_size_btc),
                    "unit_price_usd": decimal_to_str(buy.unit_price_usd),
                    "total_cost_usd": decimal_to_str(buy.total_cost_usd),
                    "fees_usd": decimal_to_str(buy.fees_usd),
                    "source": buy.source,
                    "source_file": Path(buy.source_file).name,
                    "source_row_number": buy.source_row_number,
                    "notes": buy.notes,
                }
            )


def _write_sells(sells: list[SpotBtcSell], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "sell_id",
                "sold_at",
                "size_btc",
                "gross_proceeds_usd",
                "fees_usd",
                "net_proceeds_usd",
                "unit_price_usd",
                "source_file",
                "source_row_number",
                "notes",
            ],
        )
        writer.writeheader()
        for sell in sells:
            writer.writerow(
                {
                    "sell_id": sell.sell_id,
                    "sold_at": sell.sold_at.isoformat(),
                    "size_btc": decimal_to_str(sell.size_btc),
                    "gross_proceeds_usd": decimal_to_str(sell.gross_proceeds_usd),
                    "fees_usd": decimal_to_str(sell.fees_usd),
                    "net_proceeds_usd": decimal_to_str(sell.net_proceeds_usd),
                    "unit_price_usd": decimal_to_str(sell.unit_price_usd),
                    "source_file": Path(sell.source_file).name,
                    "source_row_number": sell.source_row_number,
                    "notes": sell.notes,
                }
            )


def _write_tax_by_sell(tax_by_sell: list[SpotBtcSellTax], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "sell_id",
                "sold_at",
                "tax_year",
                "size_btc",
                "net_proceeds_usd",
                "fees_usd",
                "cost_basis_usd",
                "taxable_gain_loss_usd",
                "basis_status",
                "missing_basis_size_btc",
            ],
        )
        writer.writeheader()
        for sell_tax in tax_by_sell:
            writer.writerow(
                {
                    "sell_id": sell_tax.sell_id,
                    "sold_at": sell_tax.sold_at.isoformat(),
                    "tax_year": sell_tax.tax_year,
                    "size_btc": decimal_to_str(sell_tax.size_btc),
                    "net_proceeds_usd": decimal_to_str(sell_tax.net_proceeds_usd),
                    "fees_usd": decimal_to_str(sell_tax.fees_usd),
                    "cost_basis_usd": decimal_to_str(sell_tax.cost_basis_usd),
                    "taxable_gain_loss_usd": decimal_to_str(
                        sell_tax.taxable_gain_loss_usd
                    ),
                    "basis_status": sell_tax.basis_status,
                    "missing_basis_size_btc": decimal_to_str(
                        sell_tax.missing_basis_size_btc
                    ),
                }
            )


def _write_tax_lots(tax_lots: list[SpotBtcTaxLot], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "sell_id",
                "buy_lot_id",
                "sold_at",
                "acquired_at",
                "size_btc",
                "buy_unit_price_usd",
                "cost_basis_usd",
                "buy_source",
            ],
        )
        writer.writeheader()
        for lot in tax_lots:
            writer.writerow(
                {
                    "sell_id": lot.sell_id,
                    "buy_lot_id": lot.buy_lot_id,
                    "sold_at": lot.sold_at.isoformat(),
                    "acquired_at": lot.acquired_at.isoformat(),
                    "size_btc": decimal_to_str(lot.size_btc),
                    "buy_unit_price_usd": decimal_to_str(lot.buy_unit_price_usd),
                    "cost_basis_usd": decimal_to_str(lot.cost_basis_usd),
                    "buy_source": lot.buy_source,
                }
            )


def _write_year_summaries(
    summaries: dict[int, SpotBtcYearSummary],
    all_years_summary: SpotBtcYearSummary,
    path: Path,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "report_year",
                "sell_count",
                "complete_sell_count",
                "incomplete_sell_count",
                "size_sold_btc",
                "net_proceeds_usd",
                "fees_usd",
                "cost_basis_usd",
                "taxable_gain_loss_known_usd",
                "missing_basis_size_btc",
            ],
        )
        writer.writeheader()
        for summary in [
            *[summaries[year] for year in sorted(summaries)],
            all_years_summary,
        ]:
            writer.writerow(
                {
                    "report_year": summary.report_year,
                    "sell_count": summary.sell_count,
                    "complete_sell_count": summary.complete_sell_count,
                    "incomplete_sell_count": summary.incomplete_sell_count,
                    "size_sold_btc": decimal_to_str(summary.size_sold_btc),
                    "net_proceeds_usd": decimal_to_str(summary.net_proceeds_usd),
                    "fees_usd": decimal_to_str(summary.fees_usd),
                    "cost_basis_usd": decimal_to_str(summary.cost_basis_usd),
                    "taxable_gain_loss_known_usd": decimal_to_str(
                        summary.taxable_gain_loss_known_usd
                    ),
                    "missing_basis_size_btc": decimal_to_str(
                        summary.missing_basis_size_btc
                    ),
                }
            )


def _cell(row: dict[str, str], name: str) -> str:
    for key, value in row.items():
        if key.strip().lower() == name.lower():
            return value.strip()
    return ""


def _parse_datetime(value: str) -> datetime:
    for date_format in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y %m %d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(value, date_format).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unsupported BTC cost basis date: {value}")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
