"""Read-only credential check against unsolicited, authenticated device reports."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from .client.maintenance import STATUS_TOPIC, Maintenance
from .client.model import BASE
from .client.wire import Status, decode
from .settings import Settings


async def async_probe(hass: HomeAssistant, settings: Settings) -> None:
    # Constructor touches the filesystem; all blocking work belongs off the loop.
    client = await hass.async_add_executor_job(
        Maintenance, settings.management_key, settings.nodes, Path(hass.config.path())
    )
    result = asyncio.get_running_loop().create_future()
    control = None

    @callback
    def receive(message):
        nonlocal control
        try:
            if message.retain or not isinstance(message.payload, bytes):
                return
            now = time.monotonic()
            if message.topic == STATUS_TOPIC:
                client.accept(message.payload, False, now)
            else:
                packet = decode(message.payload, settings.control_key)
                state = Status.decode(packet.payload)
                if packet.kind != 2 or packet.node != 1 or packet.deadline != state.uptime_ms:
                    return
                control = (packet.session, now)
            device = client.fresh(1, now)
            if (
                device
                and control
                and control[0] == device.session
                and now - control[1] < 3
                and not result.done()
            ):
                result.set_result(None)
        except (ValueError, TypeError, KeyError, UnicodeError):
            return

    unsubs = []
    try:
        for topic in (STATUS_TOPIC, BASE + "/main/status"):
            unsubs.append(await mqtt.async_subscribe(hass, topic, receive, qos=0, encoding=None))
        async with asyncio.timeout(8):
            await result
    finally:
        for unsubscribe in unsubs:
            unsubscribe()
