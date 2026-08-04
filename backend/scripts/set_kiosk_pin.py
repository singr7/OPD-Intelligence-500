"""Set, rotate, clear or unlock a coordinator's kiosk staff PIN.

The PIN unlocks the staff strip on the kiosk's last screen, where a coordinator
confirms a returning patient's identity and assigns the doctor. `make seed` gives
the seeded coordinator a **committed, world-readable** default on local and test
boxes only; every other box gets its PIN from here, typed by a human.

    # who has one, and is anyone locked out?
    make kiosk-pin

    # set or rotate (prompts, never echoes, never takes the PIN as an argument)
    make kiosk-pin ARGS="--phone +915550000002 --set"

    # forgot it / walked off with it — clear, then set again
    make kiosk-pin ARGS="--phone +915550000002 --clear"

    # locked out after five wrong tries and does not want to wait 15 minutes
    make kiosk-pin ARGS="--phone +915550000002 --unlock"

Or directly:

    DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
        .venv/bin/python -m scripts.set_kiosk_pin --phone +91... --set

The PIN is never accepted as a command-line argument on purpose: argv is visible
to every process on the box and lands in shell history, which is a poor home for
a credential someone types in a public corridor.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.auth import kiosk_pin as kp
from app.db import build_engine, build_sessionmaker
from app.models.org import User


async def _find(session, phone: str) -> User:
    user = await session.scalar(select(User).where(User.phone == phone, User.deleted_at.is_(None)))
    if user is None:
        raise SystemExit(f"no user with phone {phone!r}")
    return user


async def _list(session) -> int:
    users = await session.scalars(
        select(User)
        .where(User.role.in_(tuple(kp.PIN_ROLES)), User.deleted_at.is_(None))
        .order_by(User.name)
    )
    rows = list(users)
    if not rows:
        print("no coordinators or admins exist yet")
        return 0

    now = datetime.now(UTC)
    print(f"{'name':<24} {'phone':<16} {'active':<7} {'pin':<6} state")
    for u in rows:
        if u.kiosk_pin_locked_until and u.kiosk_pin_locked_until > now:
            mins = int((u.kiosk_pin_locked_until - now).total_seconds() // 60) + 1
            state = f"LOCKED for ~{mins} min ({u.kiosk_pin_attempts} bad tries)"
        elif u.kiosk_pin_hash is None:
            state = "cannot unlock a kiosk"
        elif u.kiosk_pin_attempts:
            state = f"{u.kiosk_pin_attempts} bad tries since last success"
        else:
            state = "ok"
        print(
            f"{u.name:<24} {u.phone:<16} {str(u.active):<7} "
            f"{('yes' if u.kiosk_pin_hash else 'no'):<6} {state}"
        )
    return 0


def _prompt() -> str:
    first = getpass.getpass("New kiosk PIN (not echoed): ")
    again = getpass.getpass("Again: ")
    if first != again:
        raise SystemExit("the two PINs did not match — nothing was changed")
    return first


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--phone", help="the staff phone number, e.g. +915550000002")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--set", action="store_true", help="set or rotate the PIN")
    action.add_argument("--clear", action="store_true", help="remove the PIN entirely")
    action.add_argument(
        "--unlock",
        action="store_true",
        help="clear a lockout without changing the PIN",
    )
    args = parser.parse_args()

    engine = build_engine()
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            if not (args.set or args.clear or args.unlock):
                return await _list(session)

            if not args.phone:
                raise SystemExit("--phone is required for --set/--clear/--unlock")
            user = await _find(session, args.phone)

            if args.clear:
                await kp.clear_pin(session, user=user)
                print(f"{user.name} can no longer unlock a kiosk")
            elif args.unlock:
                user.kiosk_pin_attempts = 0
                user.kiosk_pin_locked_until = None
                await session.flush()
                print(f"lockout cleared for {user.name}; their PIN is unchanged")
            else:
                try:
                    await kp.set_pin(session, user=user, pin=_prompt())
                except kp.PinError as exc:
                    raise SystemExit(str(exc)) from exc
                print(f"kiosk PIN set for {user.name}")

            await session.commit()
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
