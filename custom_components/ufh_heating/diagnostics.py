"""Explicit allowlist: secrets, topics, sensors and raw frames are never exported."""

from __future__ import annotations

import time

from .const import VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    now = time.monotonic()
    return {
        "integration_version": VERSION,
        "live": runtime.live,
        "configured_zones": len(runtime.settings.allowed),
        "rejected_messages": runtime.rejected,
        "client_conflict": runtime.conflict,
        "last_error": runtime.last_error,
        "nodes": {
            str(number): {
                "fresh": runtime.maintenance.fresh(number, now) is not None,
                "firmware": str(state.version),
                "generation": state.generation,
                "slot": state.slot,
                "phase": state.phase,
                "error": state.error,
                "config_rollback": state.config_rollback,
                "hold": state.hold,
            }
            for number, state in runtime.maintenance.states.items()
        },
    }
