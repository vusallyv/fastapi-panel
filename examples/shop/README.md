# Example shop

A small store with products, categories, tags, customers and orders. It uses most features: list editing, custom filters, date drill-down, fieldsets, inlines, autocomplete, custom actions and permissions.

```bash
# from the repository root
uv sync
cd examples/shop
uv run uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/admin> and log in with `admin` / `admin`. The data is created on first start, in `shop.db`.

- `models.py` has plain SQLAlchemy models
- `admin.py` is the panel configuration, and the part worth reading
- `main.py` creates the app and the demo data

The password and secret key here are for the demo only.
