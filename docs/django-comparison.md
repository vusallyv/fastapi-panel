# Coming from Django

Most `ModelAdmin` options keep their names and meaning. The differences come from SQLAlchemy: there's an explicit session, and async engines can't run blocking code freely.

## What changes in your code

| Django | FastAPI Panel |
|---|---|
| `admin.site.register(M, A)` | `site.register(M, A)` |
| `@admin.register(M)` | `@register(M)` |
| `@admin.display(description="x")` | `@display("x")` |
| `@admin.action(description="x")` | `@action("x")` |
| `def act(self, request, queryset)` | `def act(self, session, request, objects)` |
| `def save_model(self, request, obj, form, change)` | `def save_model(self, session, request, obj, change)` |
| `def delete_model(self, request, obj)` | `def delete_model(self, session, request, obj)` |
| `def get_queryset(self, request)` returns a QuerySet | Returns a `select()` statement |
| `queryset.filter(x=1)` in a filter | `stmt.where(Model.x == 1)` |
| `def clean(self)` on a `ModelForm` | `def clean(self, data, obj=None)` on the admin |
| `form = MyModelForm` | `field_overrides`, `clean` or `form_schema` |
| `formfield_overrides` | `field_overrides`, by field name |
| `def get_urls(self)` | `def get_routes(self)` |
| `admin.site.urls` | `site.mount(app, ...)` |
| `self.message_user(request, "x", messages.ERROR)` | `self.message_user(request, "x", "error")` |
| `SimpleListFilter` | `SimpleFilter` |
| `parameter_name` | `parameter` |
| `app_label` | `group` |
| `change_list_template` | `list_template` |
| `change_form_template` | `form_template` |
| `python manage.py createsuperuser` | `python -m fastapi_panel hash-password`, then insert the user |
| `python manage.py migrate` | Created on the first request, or use Alembic |

## Options

Same as Django: `list_display`, `list_display_links`, `list_editable`, `list_filter`, `list_per_page`, `search_fields`, `search_help_text`, `ordering`, `date_hierarchy`, `show_full_result_count`, `actions`, `fields`, `exclude`, `fieldsets`, `readonly_fields`, `prepopulated_fields`, `autocomplete_fields`, `raw_id_fields`, `filter_horizontal`, `inlines`, `save_as`, `save_on_top`, `empty_value_display`, `view_on_site` (a method), `has_*_permission`, `get_fields`, `get_fieldsets`, `get_readonly_fields`, `get_inlines`, `get_actions`, `get_ordering`, `get_list_display`.

Same on inlines: `model`, `fk_name`, `extra`, `min_num`, `max_num`, `can_delete`, `verbose_name`, `verbose_name_plural`, `ordering`, plus the form options.

## New here

- `form_schema`: validate with a Pydantic model
- `export_csv` built in
- `action(confirm="...")`: a confirmation prompt for any action
- `builtin_actions`, `action_max_rows`, `export_fields`
- Automatic autocomplete on foreign keys with many rows
- Login throttling, a strict CSP, sessions that end when a password changes
- Dark mode

## Not here yet

- `FileField` and `ImageField`. There's no SQLAlchemy equivalent, but a custom field can do it
- `radio_fields`, `filter_vertical`, `list_max_show_all`, `actions_on_top`, `actions_on_bottom`, `preserve_filters = False`
- Groups and a permission editor: permissions are strings you store however you like, see [permissions](auth-and-permissions.md#permissions)
- Translations, per-user timezones. Times are shown in UTC
- Password reset by email
- Related-object popups ("+" next to a dropdown)

## Things to watch for

**No ORM magic in hooks on async engines.** Hooks are ordinary functions, and lazy loading works in them, but they run on the event loop. Keep slow blocking work out of them.

**Deletes follow your SQLAlchemy cascades.** Django emulates `on_delete` in Python. Here, what the confirmation page lists comes from `cascade="..."` on relationships. A database-level `ON DELETE CASCADE` works but isn't listed, and a foreign key that blocks a delete shows an error message.

**Bulk deletes load the rows.** `delete_selected` calls `delete_model` per row so that your hooks and cascades run, and it's capped by `action_max_rows`.
