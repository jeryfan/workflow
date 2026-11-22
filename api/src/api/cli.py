from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

from api.config import get_settings
from api.db.store import DuplicateAccountError, SetupRequiredError, Store
from api.services.auth import (
    AccountRole,
    AuthService,
    InvalidSetupInput,
    ProvisionMemberCommand,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m api.cli", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    create_member = commands.add_parser("create-member", allow_abbrev=False)
    create_member.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    create_member.add_argument("--email", required=True)
    create_member.add_argument("--name", required=True)
    create_member.add_argument("--role", required=True, choices=[role.value for role in AccountRole])
    create_member.add_argument("--password-stdin", action="store_true")
    return parser


def _read_password(password_stdin: bool) -> str:
    if not password_stdin:
        return getpass.getpass("Password: ")
    password = sys.stdin.read()
    if password.endswith("\n"):
        password = password[:-1]
    if password.endswith("\r"):
        password = password[:-1]
    return password


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    database_url = arguments.database_url or get_settings().database_url
    command = ProvisionMemberCommand(
        email=arguments.email,
        name=arguments.name,
        role=AccountRole(arguments.role),
        password=_read_password(arguments.password_stdin),
    )
    store = Store(database_url)
    try:
        account_id = AuthService(store).provision_member(command)
    except (InvalidSetupInput, SetupRequiredError, DuplicateAccountError) as error:
        message = str(error) or error.__class__.__name__
        print(f"create-member failed: {message}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"Created member {account_id} with role {command.role.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
