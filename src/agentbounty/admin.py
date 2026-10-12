"""Operator commands for invite-only mode.

    python -m agentbounty.admin code --count 10         make ten single-use referral codes
    python -m agentbounty.admin ambassador a@b.com      let an account hand out codes
    python -m agentbounty.admin admin a@b.com           let an account run the platform

Owners are named in ADMIN_EMAILS and need none of this: they are admins as soon as
they sign in with Google, and do the rest from the Admin page.

Ambassadors make their own codes from the dashboard. The first code, and the
first ambassador, have to come from here.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from agentbounty.db import create_engine, create_session_factory
from agentbounty.models import Sponsor
from agentbounty.services import credits
from agentbounty.services.errors import ServiceError
from agentbounty.settings import load_settings


async def make_codes(count: int, code: str | None, kind: str = credits.HUNTER) -> str:
    engine = create_engine(load_settings().database_url)
    try:
        async with create_session_factory(engine).begin() as session:
            made = await credits.create_codes(session, None, count, code, kind=kind)
            return "\n".join(item.code for item in made)
    finally:
        await engine.dispose()


async def make_admin(email: str) -> str:
    engine = create_engine(load_settings().database_url)
    try:
        async with create_session_factory(engine).begin() as session:
            account = await session.scalar(select(Sponsor).where(Sponsor.email == email.lower()))
            if account is None:
                raise ServiceError(404, f"no account with the email {email}")
            account.is_admin = True
            return f"{account.email} is now an admin"
    finally:
        await engine.dispose()


async def make_ambassador(email: str) -> str:
    engine = create_engine(load_settings().database_url)
    try:
        async with create_session_factory(engine).begin() as session:
            account = await session.scalar(select(Sponsor).where(Sponsor.email == email.lower()))
            if account is None:
                raise ServiceError(404, f"no account with the email {email}")
            account.is_ambassador = True
            return f"{account.email} is now an ambassador"
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator commands for invite-only mode.")
    commands = parser.add_subparsers(dest="command", required=True)
    code = commands.add_parser("code", help="make single-use referral codes")
    code.add_argument("--count", type=int, default=1, help="how many single-use codes to make")
    code.add_argument("--code", help="the code itself, when making one")
    code.add_argument(
        "--kind",
        choices=[credits.HUNTER, credits.SPONSOR],
        default=credits.HUNTER,
        help="who the codes are for: a hunter, or a sponsor (who may also hunt)",
    )
    ambassador = commands.add_parser("ambassador", help="let an account hand out codes")
    ambassador.add_argument("email")
    admin = commands.add_parser("admin", help="let an account run the platform")
    admin.add_argument("email")
    args = parser.parse_args()
    try:
        if args.command == "code":
            result = asyncio.run(make_codes(args.count, args.code, args.kind))
        elif args.command == "admin":
            result = asyncio.run(make_admin(args.email))
        else:
            result = asyncio.run(make_ambassador(args.email))
    except ServiceError as error:
        sys.exit(error.message)
    sys.stdout.write(result + "\n")


if __name__ == "__main__":
    main()
