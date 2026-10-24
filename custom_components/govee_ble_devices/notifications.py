"""Bound plain notification memory and retain observation receipt times."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic
from typing import Any

from govee_ble_client import GattConnection
from govee_ble_client.exceptions import DeviceNotConnectedError, GoveeBLEError


class Readings(dict[str, Any]):
    """Values and receipt times from one transaction, published together."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: dict[str, float] = {}

    def observe(self, key: str, value: Any, received: float) -> None:
        if value is not None:
            self[key] = value
            self.seen[key] = received


class NotificationBuffer:
    """Fail on overflow or link loss instead of accumulating or hiding replies."""

    def __init__(self, connection: GattConnection) -> None:
        self.connection = connection
        self.queue: asyncio.Queue[tuple[bytes, float] | None] = asyncio.Queue(64)
        self.overflow = False

    def notification(self, _sender: Any, data: bytearray) -> None:
        if self.queue.full():
            self.overflow = True
            return
        self.queue.put_nowait((bytes(data), monotonic()))

    def disconnected(self) -> None:
        if not self.queue.full():
            self.queue.put_nowait(None)

    async def next(
        self, parser: Callable[[bytes], Any], deadline: float
    ) -> tuple[Any, float]:
        while True:
            if self.overflow:
                raise GoveeBLEError("Notification buffer overflow")
            if not self.connection.is_connected:
                raise DeviceNotConnectedError("Disconnected while awaiting a reply")
            if not self.queue.empty():
                item = self.queue.get_nowait()
            else:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError
                async with asyncio.timeout(remaining):
                    item = await self.queue.get()
            if item is None:
                raise DeviceNotConnectedError("Disconnected while awaiting a reply")
            raw, received = item
            try:
                return parser(raw), received
            except ValueError:
                # No raw payload or backend exception text in routine logs.
                continue
