<h1 align="center">FastAPI Panel</h1>

<p align="center">
  <strong>A modular admin panel for FastAPI.</strong><br>
  Register a SQLAlchemy model, get a secure back office.
</p>

<p align="center">
  <a href="https://github.com/zaman-labs/fastapi-panel/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/zaman-labs/fastapi-panel/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/fastapi-panel/"><img alt="PyPI" src="https://img.shields.io/pypi/v/fastapi-panel"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <a href="https://github.com/zaman-labs/fastapi-panel/blob/main/LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

![The product list, with search, filters, bulk actions and inline editing](https://raw.githubusercontent.com/zaman-labs/fastapi-panel/main/docs/screenshots/changelist.png)

If you know `ModelAdmin`, you already know this library: `list_display`, `search_fields`, `list_filter`, `fieldsets`, `inlines`, actions and the rest work the way you expect. There is no frontend build, no Node and no extra service. It's server-rendered pages inside your FastAPI app.

## Install

```bash
pip install fastapi-panel
```

## Quick start

```python
from fastapi import FastAPI
from fastapi_panel import ModelAdmin, ModelAuth, register, site
from sqlalchemy.ext.asyncio import create_async_engine

from myapp.models import Product, User

engine = create_async_engine("postgresql+asyncpg://...")
app = FastAPI()


@register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ("name", "category", "price", "is_published")
    list_filter = ("is_published", "category")
    search_fields = ("name", "category__name")
    ordering = ("-created_at",)


site.mount(
    app,
    engine=engine,
    auth=ModelAuth(User, superuser_field="is_superuser"),
    secret_key=settings.PANEL_SECRET_KEY,
)
```

Open `/admin`, log in, and you have a list page, add and edit forms, delete confirmation, bulk actions, CSV export and a change history. See the [getting started guide](https://github.com/zaman-labs/fastapi-panel/blob/main/docs/getting-started.md) for creating your first user with `python -m fastapi_panel createuser` and setting up migrations.

Want to try it first? The [example shop](https://github.com/zaman-labs/fastapi-panel/tree/main/examples/shop) runs in one command.

## What you get

| | |
|---|---|
| **Change lists** | Columns, computed columns, sorting, search across relations, sidebar filters, saved filters, date drill-down, pagination, editing in the list |
| **Forms** | Built from your models, with fieldsets, read-only fields, prefilled slugs, validation hooks, Pydantic schemas, a rich text editor, foreign key autocomplete, many-to-many pickers, and a **+** button to create a related record on the spot |
| **Files and images** | Uploads with previews and thumbnails, stored on disk or anywhere else through a small storage class |
| **Import and restore** | CSV import with a preview before anything is saved, and a Deleted page to restore soft-deleted records |
| **Inlines** | Tabular and stacked, with add, edit and delete on the parent's page |
| **Actions** | Your own bulk actions, plus delete with a summary of what cascades, CSV export and "select all across pages" |
| **Accounts and permissions** | Per model and per action, row-level through `get_queryset` and `has_*_permission`, with password reset by email |
| **Audit log** | Who changed which fields and when, on each record's history and a site-wide Activity page, with old and new values for the fields you choose |
| **Any SQLAlchemy setup** | Sync and async engines, SQLModel, composite primary keys |
| **Timezones** | Show and enter times in your site's timezone, while the database stays in UTC |
| **Interface** | Dark mode, works on a phone, keyboard friendly, no external assets |

<table>
  <tr>
    <td><img alt="An order with its items edited inline" src="https://raw.githubusercontent.com/zaman-labs/fastapi-panel/main/docs/screenshots/order-inline.png"></td>
    <td><img alt="A product form with fieldsets and a many-to-many picker" src="https://raw.githubusercontent.com/zaman-labs/fastapi-panel/main/docs/screenshots/product-form.png"></td>
  </tr>
  <tr>
    <td><img alt="The dashboard with recent actions" src="https://raw.githubusercontent.com/zaman-labs/fastapi-panel/main/docs/screenshots/dashboard.png"></td>
    <td><img alt="The product list in dark mode" src="https://raw.githubusercontent.com/zaman-labs/fastapi-panel/main/docs/screenshots/changelist-dark.png"></td>
  </tr>
  <tr>
    <td><img alt="Adding a category in a dialog from the product form" src="https://raw.githubusercontent.com/zaman-labs/fastapi-panel/main/docs/screenshots/related-popup.png"></td>
    <td><img alt="A CSV import preview before anything is saved" src="https://raw.githubusercontent.com/zaman-labs/fastapi-panel/main/docs/screenshots/import-preview.png"></td>
  </tr>
</table>

## Security

An admin panel can reach all your data, so these are on by default and have no off switch to forget:

- CSRF tokens and `Origin` checks on every POST
- Signed `HttpOnly`, `SameSite=Lax` session cookies, `Secure` on HTTPS, ended by a password change or by deactivating the user
- Login throttling, no way to tell a wrong username from a wrong password, scrypt password hashing
- A strict Content-Security-Policy with no inline scripts or styles, `X-Frame-Options: DENY`, `no-store` caching
- Columns named like `password`, `token` or `secret` stay hidden unless you ask for them, and the audit log stores field names, never values
- Only fields on the form can be written, and sort and filter parameters are allowlisted
- Spreadsheet formulas are neutralised in CSV exports
- Uploads get random names, images are checked against their contents, and files are only served behind login

The panel refuses to start without authentication or with a short secret key. Read [the security guide](https://github.com/zaman-labs/fastapi-panel/blob/main/docs/security.md) for what's left to you.

## Documentation

- [Getting started](https://github.com/zaman-labs/fastapi-panel/blob/main/docs/getting-started.md)
- [ModelAdmin reference](https://github.com/zaman-labs/fastapi-panel/blob/main/docs/model-admin.md): lists, forms, inlines, actions, filters
- [Authentication and permissions](https://github.com/zaman-labs/fastapi-panel/blob/main/docs/auth-and-permissions.md)
- [Modular projects and customizing](https://github.com/zaman-labs/fastapi-panel/blob/main/docs/customizing.md): autodiscovery, multiple sites, custom pages, templates, themes
- [Security](https://github.com/zaman-labs/fastapi-panel/blob/main/docs/security.md)

## Roadmap

Translations, two-factor login, and a Redis option for login throttling across workers. Ideas and pull requests are welcome, see [CONTRIBUTING](https://github.com/zaman-labs/fastapi-panel/blob/main/CONTRIBUTING.md).

## License

[MIT](https://github.com/zaman-labs/fastapi-panel/blob/main/LICENSE)
