"""The monthly spend cap: a second fence behind the providers' own limits.

Every paid call goes through a guard that refuses it when this month's recorded
spend plus the call's worst-case cost would pass the cap, and records the real
cost as soon as the call returns (so a stage that fails half way still counts
what it spent). The cap is REEL_BOT_MONTHLY_CAP_USD.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from reel_bot import script_write, store_videos, visuals_write

# Worst-case cost of one call, checked before it is made.
SCRIPT_USD = 0.35  # 16k output tokens at the fallback model's price
PICTURE_USD = visuals_write.PRICE_PER_IMAGE


class BudgetError(Exception):
    pass


@dataclass(frozen=True)
class Budget:
    conn: sqlite3.Connection
    video_id: str
    cap_usd: float
    clock: Callable[[], datetime]

    def spent(self) -> float:
        now = self.clock().astimezone(UTC)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return store_videos.spent_since(self.conn, start)

    def allow(self, what: str, worst_usd: float) -> None:
        spent = self.spent()
        if spent + worst_usd > self.cap_usd:
            raise BudgetError(
                f"{what} could cost ${worst_usd:.2f} and ${spent:.2f} of this month's "
                f"${self.cap_usd:.2f} is spent (REEL_BOT_MONTHLY_CAP_USD)"
            )

    def record(self, what: str, cost_usd: float) -> None:
        store_videos.add_spend(self.conn, self.video_id, what, cost_usd, self.clock())

    def claude(self, generate: script_write.Generate) -> script_write.Generate:
        def call(system: str, user: str, schema: dict[str, Any]) -> script_write.Reply:
            self.allow("a script", SCRIPT_USD)
            reply = generate(system, user, schema)
            self.record("script", reply.cost_usd())
            return reply

        return call

    def draw(self, draw: visuals_write.Draw) -> visuals_write.Draw:
        def call(prompt: str) -> visuals_write.Drawn:
            self.allow("a picture", PICTURE_USD)
            drawn = draw(prompt)
            self.record("picture", drawn.cost_usd)
            return drawn

        return call
