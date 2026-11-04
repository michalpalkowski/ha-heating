"""Async HA ports. Pure client policy remains independent of HA and MQTT I/O."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components import mqtt
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.helpers.storage import Store

from .client.maintenance import COMMAND_TOPIC, STATUS_TOPIC, JobPhase, Maintenance
from .client.model import BASE, Gateway
from .settings import Settings

if TYPE_CHECKING:
    from . import HeatingEntry

_LOGGER = logging.getLogger(__name__)


class HeatingRuntime:
    def __init__(self, hass: HomeAssistant, entry: HeatingEntry, settings: Settings):
        self.hass, self.entry, self.settings = hass, entry, settings
        self.model = Gateway(settings.control_key, settings.zones)
        self.directory = Path(hass.config.path("ufh_heating", "releases", settings.device_id))
        self.maintenance: Maintenance
        self._listeners: set[Callable[[], None]] = set()
        self._unsubs: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None
        self._connected = False
        self._owned = False
        self.conflict = False
        self.rejected = 0
        self.last_error: str | None = None
        self._next_control = 0.0
        self._next_catalog = 0.0
        self._notify_at = 0.0
        self._stopping = False
        self._installing = False
        self._source_authorized_until = 0.0
        self._job_done = asyncio.Event()
        self._transport_wake = asyncio.Event()
        self._catalog_lock = asyncio.Lock()
        self._targets = Store(hass, 1, f"ufh_heating.{entry.entry_id}.targets")
        self._targets_loaded = False

    @callback
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def notify(self) -> None:
        if not self.maintenance.active or self._stopping:
            self._job_done.set()
        for listener in tuple(self._listeners):
            listener()

    @property
    def live(self) -> bool:
        state = self.maintenance.fresh(1, time.monotonic())
        return bool(
            not self.conflict
            and self._connected
            and state
            and state.session == self.model.node_session
            and self.model.is_live(time.monotonic())
        )

    def _prepare(self) -> Maintenance:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("release directory must be a real directory")
        client = Maintenance(self.settings.management_key, self.settings.nodes, self.directory)
        client.refresh_catalog(time.monotonic())
        return client

    async def async_start(self) -> None:
        saved = await self._targets.async_load()
        if saved is not None:
            if (
                not isinstance(saved, dict)
                or set(saved) != {"targets"}
                or not isinstance(saved["targets"], list)
                or len(saved["targets"]) != 9
            ):
                raise ValueError("invalid saved heating targets")
            for i, value in enumerate(saved["targets"]):
                if type(value) is not int or not 1000 <= value <= 2800:
                    raise ValueError("invalid saved target")
                self.model.target[i] = value
        self._targets_loaded = True
        self.maintenance = await self.hass.async_add_executor_job(self._prepare)
        topics = {STATUS_TOPIC, BASE + "/main/status"}
        topics.update(
            self.settings.zones[i].report_topic
            for i in self.settings.allowed
            if self.settings.sensors[i] is None
        )
        try:
            for topic in sorted(topics):
                self._unsubs.append(
                    await mqtt.async_subscribe(
                        self.hass, topic, self._message, qos=0, encoding=None
                    )
                )
            entities = [
                self.settings.sensors[i] for i in self.settings.allowed if self.settings.sensors[i]
            ]
            if entities:
                self._unsubs.append(
                    async_track_state_change_event(self.hass, entities, self._sensor_event)
                )
                self._unsubs.append(
                    async_track_state_report_event(self.hass, entities, self._sensor_event)
                )
            # Reuse an existing real report with its ORIGINAL timestamp and age.
            # Subscriptions are registered first, so a concurrent report is not lost.
            self._prime_sensor_states()
            self._task = self.entry.async_create_background_task(
                self.hass, self._run(), "UFH control and maintenance"
            )
        except Exception:
            await self.async_stop()
            raise

    @callback
    def _prime_sensor_states(self) -> None:
        for index in self.settings.allowed:
            entity_id = self.settings.sensors[index]
            if entity_id is not None:
                self.accept_sensor(index, self.hass.states.get(entity_id))

    @callback
    def _sensor_event(self, event: Event) -> None:
        state = event.data.get("new_state")
        if not isinstance(state, State):
            entity_id = event.data["entity_id"]
            state = self.hass.states.get(entity_id)
        else:
            entity_id = state.entity_id
        for index in self.settings.allowed:
            if self.settings.sensors[index] == entity_id:
                self.accept_sensor(index, state)

    @callback
    def accept_sensor(self, index: int, state: State | None) -> None:
        """Qualify the report's original timestamp; never renew it from a timer."""
        # Invalid evidence revokes a sample; an identical duplicate cannot renew it.
        if state is None or state.attributes.get("restored") is True:
            self.model.samples[index] = None
            return
        timestamp = int(state.last_reported.timestamp() * 1000)
        if (
            timestamp == self.model.last_report[index]
            and self.model.last_event[index] == f"ha:{state.last_reported.isoformat()}"
        ):
            return
        self.model.samples[index] = None
        try:
            value = Decimal(state.state)
            unit = state.attributes.get("unit_of_measurement")
            if unit == "°F":
                value = (value - 32) * 5 / 9
            elif unit != "°C":
                raise ValueError("temperature unit missing")
            if not value.is_finite() or not -20 <= value <= 60:
                raise ValueError("temperature outside sensor range")
            zone = self.settings.zones[index]
            payload = json.dumps(
                {
                    "schema": 1,
                    "sensor_id": zone.sensor_id,
                    "event_id": f"ha:{state.last_reported.isoformat()}",
                    "reported_at_unix_ms": timestamp,
                    "temperature_centi_c": int((value * 100).to_integral_value()),
                    "quality": "valid",
                }
            ).encode()
            self.model.measurement(
                index,
                payload,
                retained=False,
                unix_ms=time.time_ns() // 1_000_000,
                monotonic=time.monotonic(),
            )
        except (ValueError, InvalidOperation, TypeError, OverflowError):
            self.rejected += 1

    @callback
    def _message(self, message: mqtt.ReceiveMessage) -> None:
        if self._stopping or not isinstance(message.payload, bytes):
            return
        now = time.monotonic()
        try:
            if message.topic == STATUS_TOPIC:
                previous = (
                    self.maintenance.states.get(message.payload[5])
                    if len(message.payload) > 5
                    else None
                )
                state = self.maintenance.accept(message.payload, message.retain, now)

                def progress(item):
                    return (
                        None
                        if item is None
                        else (item.session, item.ack, item.phase, item.error, item.offset)
                    )

                if progress(previous) != progress(state):
                    self._transport_wake.set()
            elif message.topic == BASE + "/main/status":
                # The management MAC binds the control session to the configured identity.
                evidence = self.maintenance.fresh(1, now)
                if evidence is None:
                    raise ValueError("device identity not established")
                state = self.model.accept_status(
                    message.payload, retained=message.retain, monotonic=now
                )
                if self.model.node_session != evidence.session:
                    self.model.disconnected()
                    raise ValueError("control/management boot mismatch")
                if state.client_session == self.model.client_session:
                    self._owned = True
                elif self._owned and any(state.client_session):
                    self.conflict = True
                    self.model.disconnected()
                    self.last_error = (
                        "Another control client acknowledged; stop it and reload this integration"
                    )
            else:
                for index in self.settings.allowed:
                    if (
                        message.topic == self.settings.zones[index].report_topic
                        and self.settings.sensors[index] is None
                    ):
                        self.model.measurement(
                            index,
                            message.payload,
                            retained=message.retain,
                            unix_ms=time.time_ns() // 1_000_000,
                            monotonic=now,
                        )
                        break
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            self.rejected += 1

    async def _publish(self, topic: str, payload: str | bytes) -> None:
        if not mqtt.is_connected(self.hass):
            raise HomeAssistantError("MQTT disconnected; command was not sent")
        await mqtt.async_publish(self.hass, topic, payload, qos=0, retain=False)

    async def async_tick(self, now: float) -> None:
        connected = mqtt.is_connected(self.hass)
        if not connected:
            if self._connected:
                self.model.disconnected()
                self.maintenance.disconnected()
                self._owned = False
            self._connected = False
            self.notify()
            return
        self._connected = True
        if self.model.status is not None and now - self.model.status_at >= 3:
            self.model.disconnected()
            self._owned = False
        if self.conflict:
            self.notify()
            return  # leases expire; never fight another controller with new commands
        if now >= self._next_catalog:
            self._next_catalog = now + 30
            await self._refresh_catalog(now)
        for raw in self.maintenance.tick(self.model, now):
            await self._publish(COMMAND_TOPIC, raw)
        if now >= self._next_control:
            self._next_control = now + 1
            if self.maintenance.fresh(1, now) is not None:
                command = self.model.command(
                    monotonic=now, source_authority=now < self._source_authorized_until
                )
                if command is not None:
                    await self._publish(BASE + "/main/command", command)
            lease = {
                "schema": 1,
                "request": "on" if self.live and self.model.heat_permitted(now) else "off",
                "expires_at_unix_ms": time.time_ns() // 1_000_000 + 2500,
            }
            await self._publish(BASE + "/boiler/request", json.dumps(lease))
        if now >= self._notify_at:
            self._notify_at = now + 0.5
            self.notify()

    async def _run(self) -> None:
        while not self._stopping:
            self._transport_wake.clear()
            try:
                await self.async_tick(time.monotonic())
            except (HomeAssistantError, OSError, ValueError):
                # No raw exceptions/payloads in logs; no queued command replay.
                self.last_error = "Transport or maintenance failure; zone requests revoked"
                self.model.disconnected()
                self.maintenance.disconnected()
                self.notify()
                _LOGGER.warning("UFH transport or maintenance failed; zone requests revoked")
            try:
                async with asyncio.timeout(0.5):
                    await self._transport_wake.wait()
            except TimeoutError:
                pass  # Periodic lease and timeout enforcement, not per-block pacing.

    @callback
    def request_operation(self, enabled: bool, relay_profile: int) -> None:
        if not self.live or self._stopping:
            raise HomeAssistantError("Controller unavailable")
        try:
            self.maintenance.start_operation(enabled, relay_profile, self.model, time.monotonic())
        except ValueError as exc:
            raise HomeAssistantError(str(exc)) from exc
        self._next_control = 0
        self._transport_wake.set()
        self.notify()

    @callback
    def observe_source(self, entity_id: str, authority: bool) -> None:
        self._source_authorized_until = time.monotonic() + 3 if authority else 0.0
        state = self.hass.states.get(entity_id)
        value = state.state if state is not None and state.state in ("on", "off") else "unknown"
        self.model.observe_source(value, monotonic=time.monotonic())

    @callback
    def set_mode(self, index: int, mode: str) -> None:
        if index not in self.settings.allowed or not self.live:
            raise HomeAssistantError("Controller unavailable or zone not configured")
        if mode != "off" and (
            self.maintenance.active
            or not self.model.status
            or self.model.status.schema != 2
            or not self.model.status.operation_enabled
        ):
            raise HomeAssistantError(
                "Enable controller operation first; firmware 0.3.0+ is required"
            )
        self.model.set_mode(index, mode)
        self._next_control = 0

    @callback
    def set_target(self, index: int, target: float) -> None:
        if index not in self.settings.allowed or not self.live:
            raise HomeAssistantError("Controller unavailable or zone not configured")
        try:
            self.model.set_target(index, str(target))
        except ValueError as exc:
            raise HomeAssistantError("Setpoint must be between 10 and 28 °C") from exc
        self._next_control = 0
        if self._targets_loaded:
            self._targets.async_delay_save(lambda: {"targets": list(self.model.target)}, 2)

    async def async_install(self, node: int, version: str | None, backup: bool) -> None:
        if backup:
            raise HomeAssistantError("Firmware backup is not supported by this protocol")
        if self.conflict or not self.live:
            raise HomeAssistantError("Fresh authenticated controller status required")
        if self._installing or self.maintenance.active:
            raise HomeAssistantError("Another update is already running")
        self._installing = True
        try:
            await self._async_install(node, version)
        finally:
            self._installing = False

    async def _refresh_catalog(self, now: float, force: bool = False) -> None:
        async with self._catalog_lock:
            await self.hass.async_add_executor_job(self.maintenance.refresh_catalog, now, force)

    async def _async_install(self, node: int, version: str | None) -> None:
        await self._refresh_catalog(time.monotonic(), True)
        package = self.maintenance.packages.get(node)
        if version is not None and (package is None or str(package.version) != version):
            raise HomeAssistantError("Only the latest approved signed release can be installed")
        try:
            self.maintenance.start(node, self.model, time.monotonic())
        except ValueError as exc:
            raise HomeAssistantError("No newer approved release, or controller not ready") from exc
        job = self.maintenance.job
        self._transport_wake.set()
        self._job_done.clear()
        self.notify()
        try:
            await self._job_done.wait()
        except asyncio.CancelledError:
            self.maintenance.fail("Update caller cancelled; verify device version before retrying")
            raise
        finally:
            self.notify()
        if job is None or job.phase != JobPhase.SUCCEEDED:
            raise HomeAssistantError(job.error if job else "Update did not complete")

    async def async_stop(self) -> None:
        self._stopping = True
        if self._targets_loaded:
            await self._targets.async_save({"targets": list(self.model.target)})
        self.model.enabled = [False] * 9
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.maintenance.disconnected()
        # Revoke the external boiler lease on unload; hardware also has its own deadlines.
        if mqtt.is_connected(self.hass) and not self.conflict:
            try:
                command = self.model.command(monotonic=time.monotonic())
                if command is not None:
                    await self._publish(BASE + "/main/command", command)
                await self._publish(
                    BASE + "/boiler/request",
                    json.dumps(
                        {
                            "schema": 1,
                            "request": "off",
                            "expires_at_unix_ms": time.time_ns() // 1_000_000 + 2500,
                        }
                    ),
                )
            except HomeAssistantError:
                _LOGGER.warning("Could not publish shutdown; existing leases will expire")
        for unsubscribe in self._unsubs:
            unsubscribe()
        self._unsubs.clear()
        self.model.disconnected()
        self._connected = False
        self.notify()
