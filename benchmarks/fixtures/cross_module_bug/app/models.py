from dataclasses import dataclass


@dataclass(frozen=True)
class OrderItem:
    sku: str
    quantity: int
    unit_price_cents: int
