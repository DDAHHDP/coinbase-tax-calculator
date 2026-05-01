from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from zoneinfo import ZoneInfo

from .amounts import ZERO, decimal_to_str


MONEY_QUANTUM = Decimal("0.00000001")
ORDER_RECORD_SIZE = 11
FORCED_CLOSE_SOURCE_PATTERN = re.compile(
    r"^suspended_(?P<product>.+-PERP)_(?P<timestamp>\d{4}-\d{2}-\d{2}-\d{2}:\d{2})$"
)


@dataclass(frozen=True)
class FilledOrder:
    timestamp: datetime
    category: str
    product: str
    order_type: str
    side: str
    price_usdc: Decimal
    quantity: Decimal
    fill_percent: str
    trigger: str
    notional_usdc: Decimal
    status: str
    source_record_number: int

    @property
    def signed_quantity(self) -> Decimal:
        return self.quantity if self.side == "buy" else -self.quantity


@dataclass(frozen=True)
class ForcedCloseAdjustment:
    source_id: str
    product: str
    timestamp: datetime
    open_pos_type: str
    close_price_usdc: Decimal = ZERO


@dataclass
class FilledOrderPosition:
    position_id: str
    product: str
    side: str
    opened_at: datetime
    opening_price: Decimal
    current_qty: Decimal
    average_entry_price: Decimal
    max_abs_position_qty: Decimal
    closed_at: datetime | None = None
    closing_price_usdc: Decimal | None = None
    close_reason: str = ""
    close_source: str = ""
    realized_pnl_by_year: dict[int, Decimal] = field(
        default_factory=lambda: defaultdict(lambda: ZERO)
    )
    lifetime_realized_pnl: Decimal = ZERO

    @property
    def status(self) -> str:
        return "closed" if self.closed_at is not None else "open"

    def realized_pnl_for_year(self, year: int) -> Decimal:
        return self.realized_pnl_by_year.get(year, ZERO)


@dataclass(frozen=True)
class FilledOrderProductSummary:
    product: str
    realized_pnl_usd: Decimal
    closed_position_count: int
    ending_position_qty: Decimal
    ending_average_entry_price: Decimal
    max_abs_position_qty: Decimal


@dataclass(frozen=True)
class FilledOrderPerpReport:
    report_year: int
    source_path: Path
    product_summaries: dict[str, FilledOrderProductSummary]
    positions: list[FilledOrderPosition]


def parse_filled_orders_markdown(
    path: Path, timezone_name: str = "Europe/Vilnius"
) -> list[FilledOrder]:
    nonblank_lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if len(nonblank_lines) % ORDER_RECORD_SIZE != 0:
        raise ValueError(
            f"Filled order file does not contain {ORDER_RECORD_SIZE}-line records: {path}"
        )

    timezone = ZoneInfo(timezone_name)
    orders: list[FilledOrder] = []
    for index in range(0, len(nonblank_lines), ORDER_RECORD_SIZE):
        record = nonblank_lines[index : index + ORDER_RECORD_SIZE]
        product = _normalize_product(record[2])
        if not product.endswith("-PERP"):
            continue
        orders.append(
            FilledOrder(
                timestamp=datetime.strptime(record[0], "%m/%d/%y %H:%M:%S").replace(
                    tzinfo=timezone
                ),
                category=record[1],
                product=product,
                order_type=record[3],
                side=record[4].lower(),
                price_usdc=_parse_filled_order_decimal(record[5]),
                quantity=_parse_filled_order_decimal(record[6]),
                fill_percent=record[7],
                trigger=record[8],
                notional_usdc=_parse_filled_order_decimal(record[9]),
                status=record[10],
                source_record_number=(index // ORDER_RECORD_SIZE) + 1,
            )
        )
    return orders


def load_forced_close_adjustments(
    path: Path | None, timezone_name: str = "Europe/Vilnius"
) -> list[ForcedCloseAdjustment]:
    if path is None or not path.exists():
        return []

    timezone = ZoneInfo(timezone_name)
    adjustments: list[ForcedCloseAdjustment] = []
    seen_source_ids: set[str] = set()
    for entry in _manual_adjustment_entries(path):
        source_id = entry.get("fill_id", "")
        if source_id in seen_source_ids:
            continue

        match = FORCED_CLOSE_SOURCE_PATTERN.match(source_id)
        if match is None or entry.get("order_function", "").upper() != "CLOSE":
            continue

        suspended_at = datetime.strptime(
            match.group("timestamp"), "%Y-%m-%d-%H:%M"
        ).replace(tzinfo=timezone)
        adjustments.append(
            ForcedCloseAdjustment(
                source_id=source_id,
                product=_normalize_product(match.group("product")),
                timestamp=suspended_at,
                open_pos_type=entry.get("open_pos_type", "NA").upper(),
                close_price_usdc=ZERO,
            )
        )
        seen_source_ids.add(source_id)

    adjustments.sort(key=lambda adjustment: (adjustment.timestamp, adjustment.product))
    return adjustments


def compute_filled_order_perp_pnl(
    path: Path,
    report_year: int,
    products: set[str] | None = None,
    timezone_name: str = "Europe/Vilnius",
    manual_adjustments_path: Path | None = None,
) -> FilledOrderPerpReport:
    selected_products = {_normalize_product(product) for product in products or set()}
    orders = [
        order
        for order in parse_filled_orders_markdown(path, timezone_name=timezone_name)
        if order.timestamp.year <= report_year
        and (not selected_products or order.product in selected_products)
    ]
    forced_closes = [
        adjustment
        for adjustment in load_forced_close_adjustments(
            manual_adjustments_path, timezone_name=timezone_name
        )
        if adjustment.timestamp.year <= report_year
        and (not selected_products or adjustment.product in selected_products)
    ]
    positions = _build_positions(orders, forced_closes)
    active_by_product = {
        position.product: position
        for position in positions
        if position.closed_at is None
    }
    product_names = sorted(
        {order.product for order in orders}
        | {adjustment.product for adjustment in forced_closes}
    )
    summaries: dict[str, FilledOrderProductSummary] = {}
    for product in product_names:
        product_positions = [
            position for position in positions if position.product == product
        ]
        active = active_by_product.get(product)
        summaries[product] = FilledOrderProductSummary(
            product=product,
            realized_pnl_usd=_money(
                sum(
                    (
                        position.realized_pnl_for_year(report_year)
                        for position in product_positions
                    ),
                    start=ZERO,
                )
            ),
            closed_position_count=sum(
                1
                for position in product_positions
                if position.closed_at is not None
                and position.closed_at.year == report_year
            ),
            ending_position_qty=active.current_qty if active is not None else ZERO,
            ending_average_entry_price=(
                active.average_entry_price if active is not None else ZERO
            ),
            max_abs_position_qty=max(
                (position.max_abs_position_qty for position in product_positions),
                default=ZERO,
            ),
        )

    reported_positions = [
        position
        for position in positions
        if position.realized_pnl_for_year(report_year) != ZERO
        or (position.closed_at is not None and position.closed_at.year == report_year)
    ]
    reported_positions.sort(
        key=lambda position: (
            position.closed_at or datetime.max.replace(tzinfo=ZoneInfo(timezone_name)),
            position.product,
            position.position_id,
        )
    )
    return FilledOrderPerpReport(
        report_year=report_year,
        source_path=path,
        product_summaries=summaries,
        positions=reported_positions,
    )


def write_filled_order_perp_reports(
    report: FilledOrderPerpReport, output_dir: Path
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f"filled_order_perp_product_summary_{report.report_year}.csv"
    positions_path = output_dir / f"filled_order_perp_positions_{report.report_year}.csv"
    _write_product_summary(report, summary_path)
    _write_positions(report, positions_path)
    return [summary_path, positions_path]


def run_filled_order_perp_report(
    report_year: int,
    filled_orders_path: Path,
    output_dir: Path,
    products: set[str] | None = None,
    timezone_name: str = "Europe/Vilnius",
    manual_adjustments_path: Path | None = None,
) -> list[Path]:
    report = compute_filled_order_perp_pnl(
        filled_orders_path,
        report_year=report_year,
        products=products,
        timezone_name=timezone_name,
        manual_adjustments_path=manual_adjustments_path,
    )
    return write_filled_order_perp_reports(report, output_dir)


def _build_positions(
    orders: list[FilledOrder],
    forced_closes: list[ForcedCloseAdjustment] | None = None,
) -> list[FilledOrderPosition]:
    positions: list[FilledOrderPosition] = []
    active_by_product: dict[str, FilledOrderPosition] = {}
    sequence_by_product: dict[str, int] = defaultdict(int)

    for event in sorted([*orders, *(forced_closes or [])], key=_position_event_sort_key):
        if isinstance(event, ForcedCloseAdjustment):
            _apply_forced_close(event, active_by_product)
            continue

        order = event
        if order.status != "Filled":
            continue
        active = active_by_product.get(order.product)
        if active is None:
            active = _open_position(order, sequence_by_product)
            positions.append(active)
            active_by_product[order.product] = active
            continue

        signed_qty = order.signed_quantity
        if active.current_qty * signed_qty > ZERO:
            active.average_entry_price = _weighted_average_entry_price(
                active.average_entry_price,
                abs(active.current_qty),
                order.price_usdc,
                order.quantity,
            )
            active.current_qty += signed_qty
            active.max_abs_position_qty = max(
                active.max_abs_position_qty, abs(active.current_qty)
            )
            continue

        closed_quantity = min(abs(active.current_qty), abs(signed_qty))
        realized_pnl = _realized_pnl(
            active.current_qty,
            active.average_entry_price,
            closed_quantity,
            order.price_usdc,
        )
        active.realized_pnl_by_year[order.timestamp.year] += realized_pnl
        active.lifetime_realized_pnl += realized_pnl

        residual_quantity = abs(signed_qty) - closed_quantity
        if residual_quantity == ZERO:
            active.current_qty = active.current_qty + signed_qty
            if active.current_qty == ZERO:
                _mark_position_closed(
                    active,
                    closed_at=order.timestamp,
                    closing_price=order.price_usdc,
                    close_reason="filled_order",
                    close_source=f"filled_order_record_{order.source_record_number}",
                )
                active_by_product.pop(order.product, None)
        else:
            active.current_qty = ZERO
            _mark_position_closed(
                active,
                closed_at=order.timestamp,
                closing_price=order.price_usdc,
                close_reason="filled_order",
                close_source=f"filled_order_record_{order.source_record_number}",
            )
            active_by_product.pop(order.product, None)

            residual_order = FilledOrder(
                timestamp=order.timestamp,
                category=order.category,
                product=order.product,
                order_type=order.order_type,
                side=order.side,
                price_usdc=order.price_usdc,
                quantity=residual_quantity,
                fill_percent=order.fill_percent,
                trigger=order.trigger,
                notional_usdc=order.notional_usdc,
                status=order.status,
                source_record_number=order.source_record_number,
            )
            new_position = _open_position(residual_order, sequence_by_product)
            positions.append(new_position)
            active_by_product[order.product] = new_position

    return positions


def _apply_forced_close(
    adjustment: ForcedCloseAdjustment,
    active_by_product: dict[str, FilledOrderPosition],
) -> None:
    active = active_by_product.get(adjustment.product)
    if active is None:
        return

    expected_side = adjustment.open_pos_type.lower()
    if expected_side in {"long", "short"} and active.side != expected_side:
        raise ValueError(
            "Forced close side mismatch for "
            f"{adjustment.product} at {adjustment.timestamp.isoformat()}: "
            f"adjustment says {expected_side}, active position is {active.side}."
        )

    closed_quantity = abs(active.current_qty)
    realized_pnl = _realized_pnl(
        active.current_qty,
        active.average_entry_price,
        closed_quantity,
        adjustment.close_price_usdc,
    )
    active.realized_pnl_by_year[adjustment.timestamp.year] += realized_pnl
    active.lifetime_realized_pnl += realized_pnl
    active.current_qty = ZERO
    _mark_position_closed(
        active,
        closed_at=adjustment.timestamp,
        closing_price=adjustment.close_price_usdc,
        close_reason="forced_close_adjustment",
        close_source=adjustment.source_id,
    )
    active_by_product.pop(adjustment.product, None)


def _open_position(
    order: FilledOrder, sequence_by_product: dict[str, int]
) -> FilledOrderPosition:
    sequence_by_product[order.product] += 1
    return FilledOrderPosition(
        position_id=f"{order.product}-{sequence_by_product[order.product]:04d}",
        product=order.product,
        side="long" if order.side == "buy" else "short",
        opened_at=order.timestamp,
        opening_price=order.price_usdc,
        current_qty=order.signed_quantity,
        average_entry_price=order.price_usdc,
        max_abs_position_qty=abs(order.signed_quantity),
    )


def _mark_position_closed(
    position: FilledOrderPosition,
    closed_at: datetime,
    closing_price: Decimal,
    close_reason: str,
    close_source: str,
) -> None:
    position.closed_at = closed_at
    position.closing_price_usdc = closing_price
    position.close_reason = close_reason
    position.close_source = close_source


def _write_product_summary(report: FilledOrderPerpReport, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "product",
                "realized_pnl_usd",
                "closed_position_count",
                "ending_position_qty",
                "ending_average_entry_price",
                "max_abs_position_qty",
            ],
        )
        writer.writeheader()
        for product, summary in report.product_summaries.items():
            writer.writerow(
                {
                    "product": product,
                    "realized_pnl_usd": decimal_to_str(summary.realized_pnl_usd),
                    "closed_position_count": summary.closed_position_count,
                    "ending_position_qty": decimal_to_str(summary.ending_position_qty),
                    "ending_average_entry_price": decimal_to_str(
                        summary.ending_average_entry_price
                    ),
                    "max_abs_position_qty": decimal_to_str(summary.max_abs_position_qty),
                }
            )


def _write_positions(report: FilledOrderPerpReport, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "position_id",
                "product",
                "side",
                "opened_at",
                "closed_at",
                "status",
                "max_abs_position_qty",
                "ending_position_qty",
                "average_entry_price",
                "closing_price_usdc",
                "close_reason",
                "close_source",
                "realized_pnl_usd",
                "lifetime_realized_pnl_usd",
            ],
        )
        writer.writeheader()
        for position in report.positions:
            writer.writerow(
                {
                    "position_id": position.position_id,
                    "product": position.product,
                    "side": position.side,
                    "opened_at": position.opened_at.isoformat(),
                    "closed_at": position.closed_at.isoformat()
                    if position.closed_at is not None
                    else "",
                    "status": position.status,
                    "max_abs_position_qty": decimal_to_str(position.max_abs_position_qty),
                    "ending_position_qty": decimal_to_str(position.current_qty),
                    "average_entry_price": decimal_to_str(position.average_entry_price),
                    "closing_price_usdc": (
                        decimal_to_str(position.closing_price_usdc)
                        if position.closing_price_usdc is not None
                        else ""
                    ),
                    "close_reason": position.close_reason,
                    "close_source": position.close_source,
                    "realized_pnl_usd": decimal_to_str(
                        _money(position.realized_pnl_for_year(report.report_year))
                    ),
                    "lifetime_realized_pnl_usd": decimal_to_str(
                        _money(position.lifetime_realized_pnl)
                    ),
                }
            )


def _manual_adjustment_entries(path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("- fill_id:"):
            if current is not None:
                entries.append(current)
            current = {"fill_id": _yaml_scalar_value(line.split(":", 1)[1])}
            continue

        if current is None or ":" not in line:
            continue

        key, value = line.split(":", 1)
        current[key.strip()] = _yaml_scalar_value(value)

    if current is not None:
        entries.append(current)
    return entries


def _yaml_scalar_value(value: str) -> str:
    return value.strip().strip("'\"")


def _position_event_sort_key(
    event: FilledOrder | ForcedCloseAdjustment,
) -> tuple[datetime, int, int, str]:
    if isinstance(event, ForcedCloseAdjustment):
        return (event.timestamp, 1, 0, event.source_id)
    return (event.timestamp, 0, -event.source_record_number, "")


def _normalize_product(value: str) -> str:
    normalized = value.strip().upper().replace("/", "-").replace("_", "-")
    if normalized.endswith(" PERP"):
        return normalized.removesuffix(" PERP") + "-PERP"
    return normalized


def _parse_filled_order_decimal(value: str) -> Decimal:
    cleaned = (
        value.strip()
        .replace(",", "")
        .replace("USDC", "")
        .replace("USD", "")
        .replace("BTC", "")
        .replace("$", "")
        .replace("€", "")
        .strip()
    )
    return Decimal(cleaned)


def _weighted_average_entry_price(
    current_price: Decimal,
    current_quantity: Decimal,
    added_price: Decimal,
    added_quantity: Decimal,
) -> Decimal:
    total_quantity = current_quantity + added_quantity
    if total_quantity == ZERO:
        return ZERO
    return (
        current_price * current_quantity + added_price * added_quantity
    ) / total_quantity


def _realized_pnl(
    current_position_qty: Decimal,
    average_entry_price: Decimal,
    closed_quantity: Decimal,
    closing_price: Decimal,
) -> Decimal:
    if current_position_qty > ZERO:
        pnl = closed_quantity * (closing_price - average_entry_price)
    else:
        pnl = closed_quantity * (average_entry_price - closing_price)
    return _money(pnl)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
