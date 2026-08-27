from decimal import Decimal

from app.models import OrderItem


def parse_order_line(value: str) -> OrderItem:
    sku, quantity, unit_price = (part.strip() for part in value.split(","))
    return OrderItem(
        sku=sku,
        quantity=int(quantity),
        unit_price_cents=int(Decimal(unit_price)),
    )
