# Getting started

FastAPI Panel needs Python 3.10+, SQLAlchemy 2.0 models (SQLModel works too) and a FastAPI or Starlette app.

```bash
pip install fastapi-panel
```

## 1. Register your models

Put registrations in an `admin.py` next to your models.

```python
# shop/admin.py
from fastapi_panel import ModelAdmin, register, site

from .models import Category, Product

site.register(Category)  # defaults are fine


@register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ("name", "category", "price", "is_published")
    list_filter = ("is_published", "category")
    search_fields = ("name",)
```

## 2. Mount the site

```python
# main.py
from fastapi import FastAPI
from fastapi_panel import ModelAuth, autodiscover, site

from .db import engine
from .models import User
from .settings import settings

app = FastAPI()

autodiscover("shop")  # imports every admin.py under the package

site.mount(
    app,
    engine=engine,  # Engine, AsyncEngine or a sessionmaker
    auth=ModelAuth(User, superuser_field="is_superuser"),
    secret_key=settings.PANEL_SECRET_KEY,
)
```

`mount` checks your configuration, so a typo in `list_display` fails when the app starts and not when someone opens the page.

Generate the key once and keep it in your environment:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 3. Create a user

`ModelAuth` logs in against your own table. By default it needs `username`, `password`, `is_active` and `is_staff` columns. Pass other names, or `None` to skip the active and staff checks on purpose:

```python
ModelAuth(User, username_field="email", password_field="hashed_password", staff_field="is_admin")
```

It uses scrypt by default. If your app already hashes with something else, plug it in:

```python
from passlib.hash import bcrypt

ModelAuth(User, verify=bcrypt.verify, hasher=bcrypt.hash)
```

To make the first user, point `createuser` at the module that calls `site.mount`:

```bash
python -m fastapi_panel createuser main
python -m fastapi_panel createuser app.main:support_site --username ana --no-superuser
```

It asks for a username and password, and for any other column a user can't be saved without. In a deploy script, set the password in `FASTAPI_PANEL_PASSWORD` so it doesn't prompt. The tables must already exist.

If you'd rather insert the row yourself, `python -m fastapi_panel hash-password` prints a password hash.

No user table? `StaticAuth` keeps accounts in your configuration:

```python
from fastapi_panel import StaticAuth

auth = StaticAuth({"admin": settings.PANEL_PASSWORD_HASH})
```

## 4. Migrations

The panel uses two tables, `fastapi_panel_log` for the audit log and `fastapi_panel_saved_filter` for saved filters, plus `fastapi_panel_group` and `fastapi_panel_group_member` if you turn on staff groups. They're created on the first request if they don't exist. If your database user can't create tables, or you want Alembic to own it, add its metadata to `env.py` and generate a migration as usual:

```python
from fastapi_panel.log import Base as PanelBase

target_metadata = [Base.metadata, PanelBase.metadata]
```

If you create them by hand and don't include that metadata, tell Alembic to leave it alone with `include_name`, or autogenerate will want to drop them. To turn the log off, use `AdminSite(audit_log=False)`.

## 5. Try the example

```bash
git clone https://github.com/zaman-labs/fastapi-panel
cd fastapi-panel
uv sync
cd examples/shop
uv run uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/admin> and log in with `admin` / `admin`.

Next, the [ModelAdmin reference](model-admin.md) covers everything you can configure.
