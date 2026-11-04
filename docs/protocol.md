# UFH1 integration contract — 1

This contract precedes the HA adapter. The private controller is the authority
for valve timing, temperature policy, boiler sequencing and hardware acceptance.
The integration never provisions GPIO, changes commissioning flags or bypasses
an unverified hardware profile.

## Boundaries

* `client/wire.py`, `model.py`: bounded HMAC-SHA256 UFH1 control and evidence.
* `client/maintenance.py`, `releases.py`: separate management HMAC domain,
  Ed25519-approved UFU1 packages, one OTA job, trial confirmation and normal boot.
* `settings.py`: validated immutable entry/options records; no credentials in
  entity state, diagnostics, logs, URLs or examples.
* `runtime.py`: HA MQTT adapter, one second control lease, bounded status age,
  asynchronous executor for filesystem/crypto work, entity notifications.
* Platforms present acknowledged state. A UI request is not device confirmation.

MQTT topic prefix is currently fixed by firmware: `heating/house/v1`.
One main controller / broker is supported. A second entry is rejected. Stop the
standalone gateway before loading this integration. A foreign client ACK after
ownership was established latches a conflict until integration reload; all zone
requests are revoked. No retained control, management or sensor evidence is used.
Heating status is at most 2 s old, commands expire after 5 s, boiler request leases
after 2.5 s. Disconnect, restart and stale status disarm zones; no restored ON.

The firmware v0.2 protocol fixes nine zone slots and 14 fitted loops (6 + 8).
The public zone identifiers describe protocol slots, not a configurable layout.
Channel permutations must match the separately provisioned controller.
Disabled/unbound zones are never armed. Upper firmware is optional.

Temperature input accepts the strict original MQTT report contract, or explicit
HA temperature sensor bindings in options. HA bindings accept only new reporting
events after startup, preserve the original event time and do not poll/re-stamp
cached values. Celsius/Fahrenheit is converted to centi-degrees. The age limit
is 1–900 seconds. Invalid/unknown reports immediately invalidate that sample.
HA report time proves a report reached HA, not a fresh physical measurement:
use sensor integrations which preserve real report timing. For stronger evidence,
use timestamped MQTT reports from the actual sensor adapter (see installation).

Boiler control remains an explicit, independently expiring HA automation lease.
The integration publishes `boiler/request`, consumes timestamped `boiler/state`,
and never directly toggles an arbitrary boiler switch. No single owner selected
means no valid source evidence and consequently no autonomous heat. See the
provided blueprint and commissioning checklist. Existing house-specific boiler
handover automations can consume the same protocol without modification.

OTA files are local, signed `.ufu` artifacts in
`CONFIG/ufh_heating/releases/DEVICE_ID/`. No HTTP downloader, arbitrary path,
GitHub token, private signing key, firmware sources or USB provisioning data is
needed on HA. Catalog I/O runs in an executor. Signature, role, numeric version,
ESP32-C6 image and slot bounds are checked before streaming. Success requires a
new authenticated trial boot, confirmation, then a distinct normal boot. The
update action waits for final success/failure; service failures are explicit.
One job at a time, no automatic heating resumption. Network migration is outside
this integration's scope; use the private commissioning tools.

## Frontend identity contract — 1 (integration 0.1.1)

Native climate and loop binary-sensor attributes include `ufh_protocol=1`
and `ufh_controller`, the public main-controller identity in lowercase hex.
Climate attributes include the stable `zone_id`; loop attributes include
`zone_id`, `loop_id` and the existing schema-1 actuation evidence. A dashboard
may resolve these attributes to the CURRENT entity ID, so HA renames do not
redirect or break control. Require a single matching controller and exactly
one entity per slot. Duplicate candidates are an error, not first-match wins.
The identity is not a password or HMAC key. Diagnostics remain redacted.

This contract contains no floorplan geometry or house assets. They belong in
the private house repository. UI commands use the normal HA climate services
and inherit HA permission checks and the commissioning interlock.
