from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    unit_price_cents: int
    quantity: int


def subtotal(items: list[LineItem]) -> int:
    return sum(item.unit_price_cents * item.quantity for item in items)


def apply_discount(amount_cents: int, percent: int) -> int:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return amount_cents - (amount_cents * percent // 1000)


def order_total(items: list[LineItem], discount_percent: int = 0) -> int:
    return apply_discount(subtotal(items), discount_percent)
