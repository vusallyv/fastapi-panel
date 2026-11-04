# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Staff groups: `ModelAuth(..., groups=True)` adds a page where superusers tick permissions per model and choose members. It creates `fastapi_panel_group` and `fastapi_panel_group_member`

- `object_actions`: buttons that run custom code on one record, with `get_object_actions` to choose which records get them

- Live settings: `site.live_settings(...)` adds a Settings page, and `aget` and `get` read the values. It creates `fastapi_panel_setting`

### Fixed

- Adding a user from the panel failed because the password column wasn't on the form. Admins for the `ModelAuth` model now get a hashed **Password** field. Only superusers can replace an existing user's password

### Changed

- The delete page shows a collapsible tree of what will be removed, with counts per model and links to records. It now includes database cascades and many-to-many links
- Records that stop a delete are shown up front in place of the delete button. This includes foreign keys with no relationship on the deleted side, so a delete that used to leave orphans on a database without foreign key checks, such as default SQLite, is now refused

## [0.2.0] - 2026-11-04

### Added

- Password reset: `mount(password_reset=..., public_url=...)`, and a `changepassword` command
- `RichTextField` and `sanitize_html`
- Saved filters on every list, kept per user
- An Activity page with everyone's changes, behind the `activity.view` permission
- `log_values`, to keep old and new values of chosen fields in the history

### Changed

- Fixed: the many-to-many picker wrapped onto two rows when it had a **+** button
- The panel now creates a second table, `fastapi_panel_saved_filter`. If your database user can't create tables, add it through your migrations. Lists keep working without it

## [0.1.0] - 2026-11-04

First release.

### Added

- `AdminSite`, `ModelAdmin`, `register` and `autodiscover`, with startup checks for configuration mistakes
- List pages with sorting, search, filters, date drill-down, pagination, list editing, bulk actions and CSV export
- Forms generated from SQLAlchemy models, with fieldsets, read-only fields, validation hooks and Pydantic schemas
- Foreign key dropdowns and autocomplete, many-to-many pickers, tabular and stacked inlines
- `ModelAuth`, `StaticAuth`, and an `AuthBackend` base class for your own
- Per-model, per-action and row-level permissions
- Audit log and per-record history
- CSRF protection, signed sessions, login throttling, a strict Content-Security-Policy
- Sync and async engines, SQLModel, composite primary keys
- Dark mode and a responsive layout
- `FileField`, `ImageField`, `Storage` and `LocalStorage` for uploads, with previews, thumbnails and login-protected links
- A **+** button next to relation fields, to create a related record in a dialog and select it
- CSV import with `csv_import`: a preview first, updates through a primary key column, all or nothing
- `soft_delete_field`, with a Deleted page to restore rows or remove them for good
- `python -m fastapi_panel createuser`
- `AdminSite(timezone=...)` and `naive_utc`
- Collapsible sidebar groups
