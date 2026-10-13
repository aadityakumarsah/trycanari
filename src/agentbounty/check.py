"""A gate for a sponsor's build: scan the agent on every change, and fail if it gives way.

    TRYCANARI_API_KEY=sk_... python -m agentbounty.check \\
        --api https://your-site --bounty <id> --config agent.json

It sends the agent as it now is (the file is the body of `PUT /v1/bounties/{id}/config`),
scans it, waits for the result and prints what each probe did: the baseline playbook,
every attack the bounty has paid for and, with the Watch plan, the shared library. It
leaves with 0 when the agent held against all of them, 1 when a canary tripped, and 2
when the check could not be carried out. Without `--config` it scans the config the
bounty already has; `--again` runs that scan afresh where it has been run before, so
that attacks added since are tried too.

It uses only the sponsor's API, as a sponsor's own script would. Point it at a draft
bounty kept for the purpose, so that the bounty hunters see is not changed by a build.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

HELD = 0
TRIPPED = 1
FAILED = 2


@dataclass(frozen=True)
class Verdict:
    code: int
    # What to print: a line for each probe, then one that says how it went.
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _refusal(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail or f"the service answered {response.status_code}")


def _summary(scan: dict[str, Any]) -> Verdict:
    lines = []
    for probe in scan["probes"]:
        tripped = probe["tripped"]
        mark = "TRIPPED" if tripped else "held   "
        said = f"  -> {', '.join(tripped)}" if tripped else ""
        lines.append(f"{mark}  {probe['name']} ({probe['technique']}){said}")
    held, total = scan["held"], scan["total"]
    if held == total:
        lines.append(f"The agent held against all {total} probes.")
        return Verdict(HELD, tuple(lines))
    lines.append(f"The agent gave way to {total - held} of {total} probes.")
    return Verdict(TRIPPED, tuple(lines))


async def check(
    http: httpx.AsyncClient,
    bounty_id: str,
    config: dict[str, Any] | None = None,
    *,
    again: bool = False,
    within: float = 300,
    wait: Callable[[], Awaitable[None]] | None = None,
) -> Verdict:
    """Scan a bounty's agent and say whether it held, giving up after `within` seconds.
    `wait` is what happens between two looks at the scan: a pause, unless the caller has
    something better to do."""
    if wait is None:

        async def wait() -> None:
            await asyncio.sleep(2)

    path = f"/v1/bounties/{bounty_id}"
    if config is not None:
        saved = await http.put(f"{path}/config", json=config)
        if saved.status_code != 200:
            return Verdict(FAILED, (f"The config was refused: {_refusal(saved)}",))
    started = await http.post(f"{path}/scan", params={"again": "true"} if again else None)
    if started.status_code != 202:
        return Verdict(FAILED, (f"The scan could not be started: {_refusal(started)}",))
    scan = started.json()
    deadline = time.monotonic() + within
    while scan["state"] in ("queued", "running"):
        if time.monotonic() > deadline:
            return Verdict(FAILED, (f"The scan was not finished after {within:.0f} seconds.",))
        await wait()
        read = await http.get(f"{path}/scan")
        if read.status_code != 200:
            return Verdict(FAILED, (f"The scan could not be read: {_refusal(read)}",))
        scan = read.json()
    if scan["state"] != "done":
        return Verdict(FAILED, ("The platform could not run the scan. Run the check again.",))
    return _summary(scan)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan an agent with every known attack and fail if a canary trips."
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="base URL of the service")
    parser.add_argument("--bounty", required=True, help="bounty ID, best a draft kept for this")
    parser.add_argument("--config", type=Path, help="a JSON file with the agent's config to send")
    parser.add_argument(
        "--again",
        action="store_true",
        help="run the scan afresh even if this config has been scanned (the Watch plan)",
    )
    parser.add_argument("--timeout", type=float, default=300, help="seconds to wait for the scan")
    args = parser.parse_args()
    key = os.environ.get("TRYCANARI_API_KEY")
    if not key:
        sys.exit("Set TRYCANARI_API_KEY to the sponsor's API key.")
    config = None
    if args.config is not None:
        try:
            config = json.loads(args.config.read_text())
        except (OSError, ValueError) as error:
            sys.stderr.write(f"The config file could not be read: {error}\n")
            sys.exit(FAILED)

    async def run() -> Verdict:
        headers = {"Authorization": f"Bearer {key}"}
        async with httpx.AsyncClient(base_url=args.api, timeout=30, headers=headers) as http:
            return await check(http, args.bounty, config, again=args.again, within=args.timeout)

    try:
        verdict = asyncio.run(run())
    except httpx.HTTPError as error:
        sys.stderr.write(f"The service could not be reached: {error}\n")
        sys.exit(FAILED)
    sys.stdout.write(verdict.text + "\n")
    sys.exit(verdict.code)


if __name__ == "__main__":
    main()
