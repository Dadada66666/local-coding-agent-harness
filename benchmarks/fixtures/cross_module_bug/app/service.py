from app.models import OrderItem


def total_cents(items: list[OrderItem]) -> int:
    return sum(item.quantity * item.unit_price_cents for item in items)
