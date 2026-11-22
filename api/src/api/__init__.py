from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence


def _read_password(password_stdin: bool) -> str:
    if not password_stdin:
        return getpass.getpass("Password: ")
    return sys.stdin.read().rstrip("\r\n")


async def _create_member(arguments: argparse.Namespace) -> str:
    from api.models.engine import AsyncSessionLocal
    from api.schemas.auth import AccountRole, ProvisionMemberCommand
    from api.services.auth import AuthService

    async with AsyncSessionLocal() as db:
        return await AuthService(db).provision_member(
            ProvisionMemberCommand(
                email=arguments.email,
                name=arguments.name,
                role=AccountRole(arguments.role),
                password=_read_password(arguments.password_stdin),
            )
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workflow", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command")
    create_member = commands.add_parser("create-member", allow_abbrev=False)
    create_member.add_argument("--email", required=True)
    create_member.add_argument("--name", required=True)
    create_member.add_argument("--role", required=True, choices=["admin", "editor", "normal"])
    create_member.add_argument("--password-stdin", action="store_true")
    arguments = parser.parse_args(argv)

    if arguments.command == "create-member":
        try:
            account_id = asyncio.run(_create_member(arguments))
        except Exception as error:
            print(f"create-member failed: {error}", file=sys.stderr)
            return 1
        print(f"Created member {account_id} with role {arguments.role}")
        return 0

    import uvicorn

    from api.configs import app_config

    uvicorn.run(
        "api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=app_config.DEBUG,
        reload_dirs=["src"],
    )
    return 0
