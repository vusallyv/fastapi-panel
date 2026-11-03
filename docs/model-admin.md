# ModelAdmin reference

Subclass `ModelAdmin` and set what you need. Everything has a default.

## The list page

| Option | Default | What it does |
|---|---|---|
| `list_display` | `("__str__",)` | Columns: a column, relationship, `related__field`, an admin method, a model method or a function |
| `list_display_links` | first column | Columns that link to the edit page |
| `list_editable` | `()` | Columns edited directly in the list. They must also be in `list_display` |
| `list_filter` | `()` | Sidebar filters, see [Filters](#filters) |
| `list_per_page` | `50` | Rows per page |
| `search_fields` | `()` | Searched columns, may cross relations: `category__name` |
| `search_help_text` | `""` | Hint under the search box |
| `ordering` | primary key, newest first | Default sort: `("-created_at", "name")` |
| `date_hierarchy` | `None` | A date column for year, month and day drill-down |
| `show_full_result_count` | `True` | Run a second count for "3 of 1,000". Turn off on huge tables |

Search prefixes follow Django: `"^name"` starts with, `"=email"` exact, no prefix means contains. Each word has to match at least one field.

Relationships in `list_display` are loaded up front, so a list of 50 rows doesn't run 50 extra queries. If a computed column reaches further, add the loading yourself in `get_queryset`.

### Computed columns

```python
from fastapi_panel import ModelAdmin, display
from markupsafe import Markup


class OrderAdmin(ModelAdmin):
    list_display = ("id", "customer", "total", "is_paid", "badge")

    @display("Total", ordering="amount")
    def total(self, order):
        return f"${order.amount:,.2f}"

    @display("Paid", boolean=True)
    def is_paid(self, order):
        return order.paid_at is not None

    @display("Status")
    def badge(self, order):
        return Markup('<span class="tag tag-add">{}</span>').format(order.status)
```

Values are escaped. Return `Markup` only for HTML you built yourself, and use `Markup(...).format(...)` so that the inserted values still get escaped. Never put user input in the template string.

## Forms

| Option | Default | What it does |
|---|---|---|
| `fields` | every editable column | Which fields to show and in what order. A tuple puts fields on one row |
| `exclude` | `()` | Remove fields from the default set |
| `fieldsets` | `None` | Sections, see below |
| `readonly_fields` | `()` | Shown but not editable. May include admin methods |
| `prepopulated_fields` | `{}` | `{"slug": ("title",)}` fills a slug as you type |
| `autocomplete_fields` | `()` | Search box for a relation. The related admin needs `search_fields` |
| `raw_id_fields` | `()` | Plain ID input for a relation |
| `filter_horizontal` | `()` | Two-column picker for a many-to-many |
| `field_overrides` | `{}` | Replace a generated field: `{"price": DecimalField(min_value=0)}` |
| `form_schema` | `None` | A Pydantic model to validate with |
| `inlines` | `()` | Related rows on the same page |
| `save_as` | `False` | Adds "Save as new" |
| `save_on_top` | `False` | Repeats the buttons above the form |

```python
fieldsets = (
    (None, {"fields": ("name", "slug")}),
    ("Pricing", {"fields": (("price", "stock"),), "description": "Prices include tax."}),
    ("Advanced", {"fields": ("metadata",), "classes": ("collapse",)}),
)
```

Fields are generated from column types: text, numbers, decimals, booleans, dates, times, enums, UUIDs and JSON. Foreign keys become a dropdown, and above 200 options they switch to autocomplete or an ID input so large tables stay fast.

These columns are left out of the default form. List one in `fields` to bring it back:

- Autoincrement primary keys and columns with database-generated values
- Timestamps with a default or `onupdate`
- Anything whose name looks like a credential: `password`, `secret`, `token`, `api_key`, `private_key`

### Validation

Required, type, length, uniqueness and "does this foreign key exist" checks are automatic. For your own rules, use `clean`:

```python
from fastapi_panel import ValidationError


class OrderAdmin(ModelAdmin):
    def clean(self, data, obj=None):
        if data["status"] is Status.CANCELLED and not data.get("note"):
            raise ValidationError("Say why it was cancelled.", field="note")
```

Without `field` the error shows at the top of the form. `data` holds clean values and you can change it. `obj` is the existing row, or `None` when adding.

Field validators work too: `CharField(validators=[check_sku])`. To validate with an existing schema, set `form_schema = ProductCreate`, and its errors appear next to the matching fields.

## Inlines

```python
from fastapi_panel import ModelAdmin, TabularInline


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 1
    max_num = 50
    autocomplete_fields = ("product",)


class OrderAdmin(ModelAdmin):
    inlines = (OrderItemInline,)
```

Use `StackedInline` for one form per row. Inlines take the form options above, and also `extra`, `min_num`, `max_num`, `can_delete`, `ordering` and `fk_name`. You need `fk_name` when the model has two foreign keys to the parent.

## Actions

```python
from fastapi_panel import ModelAdmin, action


class ProductAdmin(ModelAdmin):
    actions = ("publish",)

    @action("Publish selected", permissions=("change",), confirm="Publish these products?")
    def publish(self, session, request, objects):
        for product in objects:
            product.is_published = True
        self.message_user(request, f"Published {len(objects)} products.")
```

The panel commits for you. Return a `Response` to send something else back, such as a file. `delete_selected` and `export_csv` are included. Change that with `builtin_actions = ()`, choose the exported columns with `export_fields`, and adjust the limit with `action_max_rows` (10,000 by default).

## Filters

`list_filter` accepts column names, relationship names and classes. The right filter is picked from the type: yes/no for booleans, a list for enums, date ranges for dates, related objects for relationships, and distinct values for anything else.

```python
from fastapi_panel import SimpleFilter


class StockFilter(SimpleFilter):
    title = "Stock"
    parameter = "stock_level"

    def lookups(self, request):
        return [("out", "Out of stock"), ("low", "Running low")]

    def queryset(self, request, stmt, value):
        if value == "out":
            return stmt.where(Product.stock == 0)
        return stmt.where(Product.stock.between(1, 9))
```

Only registered filters read the query string, so a URL can't filter on a column you didn't expose.

## Hooks

Hooks are normal synchronous functions, even on an async engine. They run in the request's transaction, so raising an exception rolls back the save.

```python
class ProductAdmin(ModelAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).where(Product.tenant_id == request.state.user.tenant_id)

    def save_model(self, session, request, obj, change):
        obj.updated_by = request.state.user.id
        super().save_model(session, request, obj, change)

    def delete_model(self, session, request, obj):
        obj.deleted = True  # soft delete

    def view_on_site(self, obj):
        return f"/products/{obj.slug}"
```

Also available: `get_fields`, `get_fieldsets`, `get_readonly_fields`, `get_list_display`, `get_ordering`, `get_inlines`, `get_actions`, `get_list_filters`, `has_view_permission`, `has_add_permission`, `has_change_permission`, `has_delete_permission`, `has_module_permission` and `get_routes`.

Hooks run on the event loop when you use an async engine, so database work through `session` is fine but slow blocking calls, like an HTTP request, aren't. Queue those for a background worker.

## Naming and navigation

| Option | Default |
|---|---|
| `verbose_name` | The class name, spaced out |
| `verbose_name_plural` | `verbose_name` + s |
| `group` | From the module: `shop.models` becomes "Shop" |
| `slug` | The class name in lowercase, used in URLs |
