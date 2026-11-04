"""Exercise real HA classes with mocked transport, no broker or physical relays."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.mqtt.models import ReceiveMessage
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from test_ota import IDENTITY, wire
from test_ota import KEY as MANAGEMENT_KEY
from test_protocol import KEY, SESSION, status

from custom_components.ufh_heating import config_flow
from custom_components.ufh_heating.binary_sensor import HeatingLoop
from custom_components.ufh_heating.client.maintenance import STATUS_TOPIC, JobPhase, Maintenance
from custom_components.ufh_heating.client.model import BASE
from custom_components.ufh_heating.client.wire import decode
from custom_components.ufh_heating.climate import HeatingClimate
from custom_components.ufh_heating.diagnostics import async_get_config_entry_diagnostics
from custom_components.ufh_heating.probe import async_probe
from custom_components.ufh_heating.runtime import HeatingRuntime
from custom_components.ufh_heating.settings import Settings
from custom_components.ufh_heating.update import HeatingUpdate

DATA = {
    "device_id": IDENTITY.hex(),
    "control_key": KEY.hex(),
    "management_key": MANAGEMENT_KEY.hex(),
    "public_key": (b"P" * 32).hex(),
}
OPTIONS = {"zones": ["ground_bathroom"], "sensor_ground_bathroom": "sensor.temperature"}


def message(topic, payload, retain=False):
    return ReceiveMessage(topic, payload, 0, retain, topic, time.time())


class SettingsTests(unittest.TestCase):
    def test_no_secret_repr_and_reject_invalid_inputs(self):
        settings = Settings.parse(DATA, {})
        self.assertNotIn(KEY.hex(), repr(settings))
        self.assertNotIn(repr(KEY), repr(settings))
        self.assertEqual(settings.allowed, frozenset())
        for data, options in (
            ({**DATA, "control_key": MANAGEMENT_KEY.hex()}, {}),
            ({**DATA, "device_id": "../secrets"}, {}),
            (DATA, {"zones": ["bedroom"]}),
            (DATA, {"zones": ["office", "office"]}),
            (DATA, {"maximum_age_seconds": True}),
            (DATA, {"ground_channels": [0] * 6}),
            (
                DATA,
                {
                    "zones": ["office", "living"],
                    "sensor_office": "sensor.same",
                    "sensor_living": "sensor.same",
                },
            ),
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                Settings.parse(data, options)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hass = HomeAssistant(self.temp.name)
        self.entry = MagicMock()
        self.entry.data = DATA
        self.entry.options = OPTIONS
        self.entry.state = ConfigEntryState.LOADED
        self.settings = Settings.parse(DATA, OPTIONS)
        self.runtime = HeatingRuntime(self.hass, self.entry, self.settings)
        self.runtime.maintenance = Maintenance(
            self.settings.management_key, self.settings.nodes, Path(self.temp.name)
        )
        self.entry.runtime_data = self.runtime
        self.published = []

        async def publish(hass, topic, payload, **kwargs):
            self.published.append((topic, payload, kwargs))

        self.addCleanup(patch.stopall)
        patch("custom_components.ufh_heating.runtime.mqtt.is_connected", return_value=True).start()
        patch(
            "custom_components.ufh_heating.runtime.mqtt.async_publish", side_effect=publish
        ).start()

    def establish(self, *, commissioned=True, heat=False):
        now = time.monotonic()
        self.runtime.maintenance.accept(wire(1, session=SESSION), False, now)
        self.runtime.model.accept_status(
            status(commissioned=commissioned), retained=False, monotonic=now
        )
        packet = decode(self.runtime.model.command(monotonic=now), KEY)
        self.runtime.model.accept_status(
            status(
                client=self.runtime.model.client_session,
                accepted=packet.sequence,
                sequence=2,
                uptime=1500,
                commissioned=commissioned,
                heat=heat,
            ),
            retained=False,
            monotonic=now,
        )
        self.runtime._connected = True
        self.runtime._owned = True

    async def test_ha_entities_are_acknowledged_and_unverified_board_cannot_arm(self):
        self.establish(commissioned=False)
        climate = HeatingClimate(self.runtime, 0)
        update = HeatingUpdate(self.runtime, 1)
        loop = HeatingLoop(self.runtime, self.settings.bindings[0])
        self.assertTrue(climate.available)
        self.assertEqual(climate.hvac_mode, "off")
        self.assertEqual(update.installed_version, "0.2.0")
        self.assertFalse(loop.is_on)
        for entity in (climate, loop):
            attrs = entity.extra_state_attributes
            self.assertEqual(attrs["ufh_protocol"], 1)
            self.assertEqual(attrs["ufh_controller"], DATA["device_id"])
            self.assertNotIn(KEY.hex(), json.dumps(attrs))
            self.assertNotIn(MANAGEMENT_KEY.hex(), json.dumps(attrs))
        climate.entity_id = "climate.renamed_room"
        self.assertEqual(climate.extra_state_attributes["zone_id"], "ground_bathroom")
        self.assertEqual(loop.extra_state_attributes["loop_id"], "ground_1")
        with self.assertRaises(HomeAssistantError):
            await climate.async_turn_on()
        await climate.async_set_temperature(temperature=23)
        self.assertEqual(climate.target_temperature, 22)  # not optimistic
        await climate.async_turn_off()
        for invalid in (float("nan"), 29, 9):
            with self.assertRaises(HomeAssistantError):
                await climate.async_set_temperature(temperature=invalid)

    async def test_stale_controller_disarms_and_only_publishes_off_lease(self):
        self.establish(heat=True)
        self.runtime.model.enabled[0] = True
        await self.runtime.async_tick(time.monotonic() + 4)
        self.assertFalse(any(self.runtime.model.enabled))
        leases = [json.loads(p) for t, p, k in self.published if t.endswith("/boiler/request")]
        self.assertEqual(leases[-1]["request"], "off")
        self.assertTrue(all(k["retain"] is False for t, p, k in self.published))

    async def test_disconnect_revokes_requests_and_publishes_nothing(self):
        self.establish()
        self.runtime.model.enabled[0] = True
        with patch("custom_components.ufh_heating.runtime.mqtt.is_connected", return_value=False):
            await self.runtime.async_tick(time.monotonic())
        self.assertFalse(self.runtime.live)
        self.assertFalse(any(self.runtime.model.enabled))
        self.assertFalse(self.published)

    async def test_foreign_control_client_latches_conflict_and_stops_publication(self):
        self.establish()
        self.runtime._message(
            message(
                BASE + "/main/status", status(client=b"X" * 16, accepted=3, sequence=3, uptime=2000)
            )
        )
        self.assertTrue(self.runtime.conflict)
        await self.runtime.async_tick(time.monotonic())
        self.assertFalse(self.published)
        self.assertFalse(self.runtime.live)

    async def test_wrong_identity_or_retained_management_cannot_establish_control(self):
        for payload, retain in (
            (wire(1, session=SESSION, identity=b"X" * 16), False),
            (wire(1, session=SESSION), True),
        ):
            self.runtime._message(message(STATUS_TOPIC, payload, retain))
        self.runtime._message(message(BASE + "/main/status", status()))
        self.assertIsNone(self.runtime.model.status)
        self.assertGreater(self.runtime.rejected, 0)

    async def test_real_reports_keep_original_age_and_duplicates_do_not_refresh(self):
        self.assertIsNone(self.runtime.model.samples[0])
        state = State(
            "sensor.temperature",
            "68",
            {"unit_of_measurement": "°F"},
            last_reported=datetime.now(UTC),
        )
        self.runtime.accept_sensor(0, state)
        self.assertEqual(self.runtime.model.samples[0].temperature, 2000)
        # Replaying a cached value cannot renew its validity.
        expiry = self.runtime.model.samples[0].expires_monotonic
        self.runtime.accept_sensor(0, state)
        self.assertEqual(self.runtime.model.samples[0].expires_monotonic, expiry)
        invalid = State("sensor.temperature", "unavailable", {"unit_of_measurement": "°C"})
        self.runtime.accept_sensor(0, invalid)
        self.assertIsNone(self.runtime.model.samples[0])
        old = State(
            "sensor.temperature",
            "20",
            {"unit_of_measurement": "°C"},
            last_reported=datetime.now(UTC) - timedelta(hours=1),
        )
        self.runtime.accept_sensor(0, old)
        self.assertIsNone(self.runtime.model.samples[0])

    async def test_startup_uses_existing_real_report_with_its_original_age(self):
        reported = datetime.now(UTC) - timedelta(seconds=60)
        state = State(
            "sensor.temperature", "20.5", {"unit_of_measurement": "°C"}, last_reported=reported
        )
        with patch.object(type(self.hass.states), "get", return_value=state):
            self.runtime._prime_sensor_states()
            sample = self.runtime.model.samples[0]
            self.assertEqual(sample.temperature, 2050)
            self.assertEqual(self.runtime.model.last_report[0], int(reported.timestamp() * 1000))
            self.assertAlmostEqual(sample.expires_monotonic - time.monotonic(), 840, delta=1)
            self.runtime._prime_sensor_states()
            self.assertEqual(
                self.runtime.model.samples[0].expires_monotonic, sample.expires_monotonic
            )

    async def test_startup_does_not_promote_stale_or_restored_values(self):
        for attrs, age in (
            ({"unit_of_measurement": "°C"}, 1000),
            ({"unit_of_measurement": "°C", "restored": True}, 1),
        ):
            state = State(
                "sensor.temperature",
                "21",
                attrs,
                last_reported=datetime.now(UTC) - timedelta(seconds=age),
            )
            with patch.object(type(self.hass.states), "get", return_value=state):
                self.runtime._prime_sensor_states()
                self.assertIsNone(self.runtime.model.samples[0])

    async def test_diagnostics_do_not_export_config_topics_ids_or_keys(self):
        self.establish()
        exported = json.dumps(await async_get_config_entry_diagnostics(self.hass, self.entry))
        for secret in (KEY.hex(), MANAGEMENT_KEY.hex(), IDENTITY.hex(), "sensor.temperature", BASE):
            self.assertNotIn(secret, exported)

    async def test_update_action_refuses_arbitrary_version_or_missing_signed_release(self):
        self.establish()
        update = HeatingUpdate(self.runtime, 1)
        with self.assertRaises(HomeAssistantError):
            await update.async_install("99.0.0", False)
        with self.assertRaises(HomeAssistantError):
            await update.async_install(None, True)
        self.assertFalse(self.runtime.maintenance.active)

    async def test_read_only_probe_checks_both_keys_and_unsubscribes(self):
        callbacks = {}
        unsubs = []

        async def subscribe(hass, topic, callback, **kwargs):
            callbacks[topic] = callback

            async def report():
                await asyncio.sleep(0)
                if len(callbacks) == 2:
                    callbacks[STATUS_TOPIC](message(STATUS_TOPIC, wire(1, session=SESSION)))
                    callbacks[BASE + "/main/status"](message(BASE + "/main/status", status()))

            asyncio.create_task(report())
            return lambda: unsubs.append(topic)

        with patch(
            "custom_components.ufh_heating.probe.mqtt.async_subscribe", side_effect=subscribe
        ):
            await async_probe(self.hass, self.settings)
        self.assertEqual(len(unsubs), 2)
        self.assertFalse(self.published)  # verification sends no commands

    async def test_ui_flow_and_options_use_real_ha_selectors(self):
        flow = config_flow.HeatingConfigFlow()
        flow.hass = self.hass
        flow.context = {}
        with patch.object(flow, "_async_current_entries", return_value=[]):
            form = await flow.async_step_user()
            self.assertEqual(form["type"], "form")
            invalid = await flow.async_step_user({**DATA, "control_key": "bad"})
            self.assertEqual(invalid["errors"]["base"], "invalid_config")
            with (
                patch(
                    "custom_components.ufh_heating.config_flow.async_probe", new_callable=AsyncMock
                ),
                patch(
                    "custom_components.ufh_heating.config_flow.mqtt.async_wait_for_mqtt_client",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch.object(flow, "async_set_unique_id", new_callable=AsyncMock),
                patch.object(flow, "_abort_if_unique_id_configured"),
            ):
                result = await flow.async_step_user(DATA)
                self.assertEqual(result["type"], "create_entry")
        opts = config_flow.HeatingOptionsFlow()
        with patch.object(
            type(opts),
            "config_entry",
            new_callable=lambda: property(lambda self: MagicMock(options=OPTIONS, data=DATA)),
        ):
            result = await opts.async_step_init()
            self.assertEqual(result["type"], "form")
            result = await opts.async_step_init(OPTIONS)
            self.assertEqual(result["type"], "create_entry")

    async def test_full_entry_loads_real_platforms_and_unloads_subscriptions(self):
        import os
        from types import MappingProxyType

        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er
        from homeassistant.setup import async_setup_component

        os.symlink("/src/custom_components", Path(self.temp.name) / "custom_components")
        from homeassistant import loader

        loader.async_setup(self.hass)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.hass.config.components.add("mqtt")
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="ufh_heating",
            title="Heating",
            data=DATA,
            options=OPTIONS,
            source="user",
            unique_id=DATA["device_id"],
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        callbacks = {}
        removed = []

        async def subscribe(hass, topic, callback, **kwargs):
            callbacks[topic] = callback
            return lambda: removed.append(topic)

        with (
            patch(
                "custom_components.ufh_heating.runtime.mqtt.async_subscribe", side_effect=subscribe
            ),
            patch(
                "custom_components.ufh_heating.mqtt.async_wait_for_mqtt_client",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            self.assertTrue(await async_setup_component(self.hass, "ufh_heating", {}))
            await self.hass.config_entries.async_add(entry)
            self.assertEqual(entry.state, ConfigEntryState.LOADED)
            await self.hass.async_block_till_done()
            entities = er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)
            self.assertTrue(any(e.domain == "climate" for e in entities))
            self.assertTrue(any(e.domain == "update" for e in entities))
            self.hass.states.async_set("sensor.temperature", "20.5", {"unit_of_measurement": "°C"})
            await self.hass.async_block_till_done()
            self.assertEqual(entry.runtime_data.model.samples[0].temperature, 2050)
            original_report = entry.runtime_data.model.last_report[0]
            await asyncio.sleep(0.005)
            self.hass.states.async_set("sensor.temperature", "20.5", {"unit_of_measurement": "°C"})
            await self.hass.async_block_till_done()
            self.assertGreater(entry.runtime_data.model.last_report[0], original_report)
            self.hass.states.async_set(
                "sensor.temperature", "unavailable", {"unit_of_measurement": "°C"}
            )
            await self.hass.async_block_till_done()
            self.assertIsNone(entry.runtime_data.model.samples[0])
            devices = dr.async_entries_for_config_entry(dr.async_get(self.hass), entry.entry_id)
            self.assertEqual(len(devices), 1)
            self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
            self.assertEqual(set(removed), set(callbacks))
            await self.hass.async_stop(force=True)

    async def test_native_update_service_waits_for_confirmed_normal_boot(self):
        import struct

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from helpers import sign
        from test_ota import image

        from custom_components.ufh_heating.client.maintenance import COMMAND_TOPIC

        signing = Ed25519PrivateKey.from_private_bytes(b"K" * 32)
        settings = Settings.parse(
            {**DATA, "public_key": signing.public_key().public_bytes_raw().hex()}, OPTIONS
        )
        self.runtime.settings = settings
        self.runtime.maintenance = Maintenance(MANAGEMENT_KEY, settings.nodes, Path(self.temp.name))
        (Path(self.temp.name) / "main-floor-0.2.1.ufu").write_bytes(sign(image(), 1, signing))
        self.establish(commissioned=False)
        started = asyncio.Event()
        original_start = self.runtime.maintenance.start

        def start(*args):
            original_start(*args)
            started.set()

        with patch.object(self.runtime.maintenance, "start", side_effect=start):
            action = asyncio.create_task(self.runtime.async_install(1, None, False))
            await asyncio.wait_for(started.wait(), 2)
        self.assertFalse(action.done())
        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_install(1, None, False)
        await self.runtime.async_tick(time.monotonic())
        await self.runtime.async_tick(time.monotonic())

        def outgoing():
            return decode(
                [p for t, p, k in self.published if t == COMMAND_TOPIC][-1],
                MANAGEMENT_KEY,
                b"underfloor/manage-mqtt/v1\0",
            )

        request = outgoing()
        self.assertEqual(request.payload[0], 1)
        offset, sequence = 0, 2
        total = len(image())
        while offset < total:
            self.runtime._message(
                message(
                    STATUS_TOPIC,
                    wire(
                        sequence,
                        session=SESSION,
                        phase=1,
                        offset=offset,
                        total=total,
                        ack=request.sequence,
                        hold=1,
                    ),
                )
            )
            await self.runtime.async_tick(time.monotonic())
            request = outgoing()
            self.assertEqual(request.payload[0], 2)
            self.assertEqual(struct.unpack_from(">I", request.payload, 1)[0], offset)
            offset += len(request.payload) - 5
            sequence += 1
        self.runtime._message(
            message(
                STATUS_TOPIC,
                wire(
                    sequence,
                    session=SESSION,
                    phase=1,
                    total=total,
                    offset=offset,
                    ack=request.sequence,
                    hold=1,
                ),
            )
        )
        await self.runtime.async_tick(time.monotonic())
        finish = outgoing()
        self.assertEqual(finish.payload, b"\x03")
        self.runtime._message(
            message(
                STATUS_TOPIC,
                wire(sequence + 1, session=SESSION, phase=3, ack=finish.sequence, hold=1),
            )
        )
        await self.runtime.async_tick(time.monotonic())
        self.assertFalse(action.done())
        self.runtime._message(
            message(STATUS_TOPIC, wire(1, session=b"T" * 16, version=(0, 2, 1), phase=4, hold=1))
        )
        await self.runtime.async_tick(time.monotonic())
        self.assertEqual(outgoing().payload[0], 4)
        self.assertFalse(action.done())
        self.runtime._message(message(STATUS_TOPIC, wire(1, session=b"V" * 16, version=(0, 2, 1))))
        await self.runtime.async_tick(time.monotonic())
        self.runtime.notify()
        await asyncio.wait_for(action, 2)
        self.assertEqual(self.runtime.maintenance.job.phase, JobPhase.SUCCEEDED)
        self.assertFalse(any(self.runtime.model.enabled))
