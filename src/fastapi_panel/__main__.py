import argparse
import getpass
import sys

from .security import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m fastapi_panel")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hash-password", help="hash a password for StaticAuth or your own user table")
    parser.parse_args()

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Again: "):
        print("The passwords don't match.", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
