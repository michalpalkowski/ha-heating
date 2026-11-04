# Verification of 0.1.0

The host test suite uses the actual Home Assistant 2026.10.2 container, pinned by
image digest in `tools/check.sh`, with no network, no extra capabilities, read-only
sources and a temporary config directory. Transport callbacks are mocked; HA's
config-entry manager, device/entity registries, selectors, platforms and sensor
report events are real.

Covered: UI validation and options; credential probe (read-only); full entry setup
and unload with subscription cleanup; acknowledged climate state; commissioning
interlock; identical new sensor reports, °F conversion, invalid/stale input;
control timeout and MQTT disconnect; conflicting clients; explicit diagnostic
allowlist; HACS metadata, translations, assets and public package boundaries.

Wire tests check a shared Rust interoperability vector, all single-byte mutations,
HMAC domain separation, retained/replayed/expired data, session reset and bounded
queues. OTA tests sign synthetic images with TEST-ONLY keys, verify signature,
role, image layout, hash, numeric version, ACK offset/timeouts, one job, interrupted
transfer and trial/confirmation/normal-boot sequencing. A native HA update action
also waits for that final result and leaves heating disarmed.

Limitations: no physical actuator or boiler commissioning, power-cut OTA rollback
or upper-board RS485 acceptance is claimed. These require the installation's
separate hardware checklist. Automated tests do not make a new electrical assembly
approved. This repository is ready for HACS installation and supervised controller
integration; automatic heating still requires commissioned hardware and explicit
boiler handover.

Ruff checks code and formatting. CI performs the same isolated test command.
The source tree was created without copying private repository history. A local
pre-publication audit additionally checks against actual installation secrets;
those secrets and that audit input are never committed here.
