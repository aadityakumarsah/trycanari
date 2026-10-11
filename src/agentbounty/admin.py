"""Operator commands for invite-only mode.

    python -m agentbounty.admin code --uses 10          make a referral code
    python -m agentbounty.admin ambassador a@b.com      let an account hand out codes

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


async def make_code(uses: int, code: str | None) -> str:
    engine = create_engine(load_settings().database_url)
    try:
        async with create_session_factory(engine).begin() as session:
            made = await credits.create_code(session, None, uses, code)
            return f"{made.code}   (admits {made.max_uses})"
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
    code = commands.add_parser("code", help="make a referral code")
    code.add_argument("--uses", type=int, default=10, help="how many accounts it admits")
    code.add_argument("--code", help="the code itself; one is generated if left out")
    ambassador = commands.add_parser("ambassador", help="let an account hand out codes")
    ambassador.add_argument("email")
    args = parser.parse_args()
    try:
        if args.command == "code":
            if args.uses < 1:
                sys.exit("--uses must be at least 1")
            result = asyncio.run(make_code(args.uses, args.code))
        else:
            result = asyncio.run(make_ambassador(args.email))
    except ServiceError as error:
        sys.exit(error.message)
    sys.stdout.write(result + "\n")


if __name__ == "__main__":
    main()
