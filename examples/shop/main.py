"""Run with: uvicorn main:app --reload (from this folder), then open /admin and log in as admin / admin."""

import datetime as dt
import random
from contextlib import asynccontextmanager
from decimal import Decimal

import admin  # noqa: F401  (registers the models)
from fastapi import FastAPI
from models import Base, Category, Customer, Order, OrderItem, OrderStatus, Product, Staff, Tag
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from fastapi_panel import ModelAuth, hash_password, site

engine = create_async_engine("sqlite+aiosqlite:///shop.db")

# Demo only. In production, read this from the environment.
SECRET_KEY = "demo-only-secret-key-replace-me-in-production"

PRODUCT_NAMES = (
    "Aurora Desk Lamp",
    "Basalt Water Bottle",
    "Cedar Notebook",
    "Drift Backpack",
    "Ember Mug",
    "Fjord Rain Jacket",
    "Glacier Thermos",
    "Harbor Tote",
    "Indigo Throw",
    "Juniper Candle",
    "Kestrel Headphones",
    "Lumen Reading Light",
    "Meadow Planter",
    "Nimbus Pillow",
    "Orchard Apron",
    "Pine Cutting Board",
    "Quartz Coaster Set",
    "Ridge Hiking Socks",
    "Summit Sunglasses",
    "Tundra Beanie",
    "Umbra Umbrella",
    "Vale Wool Scarf",
    "Willow Basket",
    "Yarrow Tea Set",
    "Zephyr Desk Fan",
)
CUSTOMERS = (
    ("Maya Chen", "CA"),
    ("Leo Martins", "BR"),
    ("Aisha Khan", "AE"),
    ("Tom Becker", "DE"),
    ("Sofia Rossi", "IT"),
    ("Noah Smith", "US"),
    ("Elif Demir", "TR"),
    ("Kenji Sato", "JP"),
)


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        if await session.scalar(select(Staff).limit(1)):
            return
        rng = random.Random(7)
        session.add(Staff(username="admin", password=hash_password("admin"), is_superuser=True))
        categories = [
            Category(name=name, slug=name.lower()) for name in ("Home", "Outdoors", "Kitchen", "Office", "Apparel")
        ]
        tags = [Tag(name=name) for name in ("New", "Sale", "Eco", "Gift", "Limited")]
        session.add_all([*categories, *tags])
        now = dt.datetime(2026, 11, 3, 12, 0)
        products = [
            Product(
                name=name,
                slug=name.lower().replace(" ", "-"),
                description=f"{name}, made to last.",
                price=Decimal(rng.randint(900, 18900)) / 100,
                stock=rng.choice((0, 4, 8, 15, 42, 120)),
                is_published=rng.random() > 0.25,
                category=rng.choice(categories),
                tags=rng.sample(tags, rng.randint(0, 3)),
                created_at=now - dt.timedelta(days=rng.randint(0, 500)),
            )
            for name in PRODUCT_NAMES
        ]
        customers = [
            Customer(name=name, email=f"{name.split()[0].lower()}@example.com", country=country)
            for name, country in CUSTOMERS
        ]
        session.add_all([*products, *customers])
        for _ in range(40):
            chosen = rng.sample(products, rng.randint(1, 3))
            session.add(
                Order(
                    customer=rng.choice(customers),
                    status=rng.choice(list(OrderStatus)),
                    note="Cancelled by the customer." if rng.random() < 0.1 else None,
                    created_at=now - dt.timedelta(days=rng.randint(0, 200)),
                    items=[OrderItem(product=p, quantity=rng.randint(1, 4), unit_price=p.price) for p in chosen],
                )
            )
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed()
    yield


app = FastAPI(title="Acme Shop API", lifespan=lifespan)


@app.get("/")
async def home() -> dict[str, str]:
    return {"admin": "/admin"}


def print_reset_link(user: Staff, url: str) -> None:
    # Demo only. A real app emails this to the user.
    print(f"Password reset link for {user.username}: {url}")


site.mount(
    app,
    engine=engine,
    auth=ModelAuth(Staff, superuser_field="is_superuser", groups=True),
    secret_key=SECRET_KEY,
    password_reset=print_reset_link,
    public_url="http://127.0.0.1:8000/admin",
)
