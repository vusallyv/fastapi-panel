# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

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
