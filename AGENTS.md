# UFH Heating client

This is the public Home Assistant integration. Private firmware and provisioning
live separately in ../Heating; house configuration lives in ../../home-assistant.
Never copy private repository history, local/, network settings, identities,
keys, flash dumps, Rust sources or house floorplan assets into this repository.

Read docs/protocol.md before changing control/OTA behavior. Preserve typed
boundaries, strict evidence age, ACK-based state and separate management HMAC.
Do not add silent fallbacks, optimistic ON, automatic commissioning or unbounded
URL-based firmware installs. Do not run the standalone gateway with this client.

Run tools/check.sh and Ruff for behavioral changes. Tests must work without
private repositories, real secrets, broker or hardware. State physical test
limits honestly. Public releases contain the HA client only, never firmware.
