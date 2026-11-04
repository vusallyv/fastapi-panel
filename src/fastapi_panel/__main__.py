import argparse
import asyncio
import getpass
import importlib
import os
import sys
from typing import Any

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from .auth import ModelAuth
from .introspect import coerce_to_column
from .security import hash_password
from .site import MIN_PASSWORD_LENGTH, AdminSite, site


def read_password() -> str:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Again: "):
        raise SystemExit("The passwords don't match.")
    return password


def find_site(target: str) -> AdminSite:
    """`main` or `main:panel`. Importing runs your `site.mount(...)`, which is where the engine comes from."""
    sys.path.insert(0, os.getcwd())
    module_name, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SystemExit(f"Couldn't import {module_name!r}: {exc}") from None
    if attr:
        found = getattr(module, attr, None)
        if not isinstance(found, AdminSite):
            raise SystemExit(f"{target} is not an AdminSite.")
        return found
    return next((value for value in vars(module).values() if isinstance(value, AdminSite)), site)


async def save_user(panel: AdminSite, *args: Any, **kwargs: Any) -> Any:
    try:
        return await panel.db.run(lambda session: panel.auth.create_user(session, *args, **kwargs))
    finally:
        # An open async connection would keep the process from exiting.
        bind = panel.db.maker.kw.get("bind")
        if isinstance(bind, AsyncEngine):
            await bind.dispose()


def create_user(args: argparse.Namespace) -> int:
    panel = find_site(args.target)
    if not hasattr(panel, "db"):
        raise SystemExit(f"{args.target} doesn't call site.mount(), so there's no database to use.")
    auth = panel.auth
    if not isinstance(auth, ModelAuth):
        raise SystemExit("createuser only works with ModelAuth. Other backends keep their users elsewhere.")

    username = args.username or input("Username: ").strip()
    if not username:
        raise SystemExit("A username is required.")
    extra = {}
    for prop in auth.required_fields():
        try:
            extra[prop.key] = coerce_to_column(prop.columns[0], input(f"{prop.key}: ").strip())
        except ValueError:
            raise SystemExit(f"That's not a valid value for {prop.key}.") from None
    password = os.environ.get("FASTAPI_PANEL_PASSWORD") or read_password()
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Use at least {MIN_PASSWORD_LENGTH} characters.")

    try:
        asyncio.run(save_user(panel, username, password, superuser=not args.no_superuser, **extra))
    except DBAPIError as exc:
        raise SystemExit(f"Couldn't create the user: {exc.orig}. Do the tables exist, and is the name free?") from None
    print(f"Created {username}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m fastapi_panel")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hash-password", help="hash a password for StaticAuth or your own user table")
    creating = commands.add_parser("createuser", help="add a user to the table behind ModelAuth")
    creating.add_argument("target", help="the module that mounts the panel, like main or app.main:panel")
    creating.add_argument("--username")
    creating.add_argument("--no-superuser", action="store_true", help="create a regular staff user")
    args = parser.parse_args()

    if args.command == "createuser":
        return create_user(args)
    print(hash_password(read_password()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
