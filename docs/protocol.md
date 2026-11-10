# UFH1 integration contract — 2 (client 0.2 / firmware 0.3)

This contract precedes the HA adapter. The private controller is the authority
for valve timing, temperature policy and boiler sequencing. Operator permission
is separate from installation facts; it does not certify electrical tests.
No automatic arming, GPIO bypass or invented watchdog evidence is permitted.

## Boundaries

* `client/wire.py`, `model.py`: bounded HMAC-SHA256 UFH1 control and evidence.
* `client/maintenance.py`, `releases.py`: separate management HMAC domain,
  Ed25519-approved UFU1 packages, one OTA job, trial confirmation and normal boot.
* `settings.py`: validated immutable entry/options records; no credentials in
  entity state, diagnostics, logs, URLs or examples.
* `runtime.py`: HA MQTT adapter, 0.5-second control cadence, bounded status age,
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
HA temperature sensor bindings in options. HA bindings subscribe before priming a current real report with its ORIGINAL
`last_reported`; subsequent events preserve their own report time. Restored or
stale values are rejected. Timers never re-stamp cached temperature values. Celsius/Fahrenheit is converted to centi-degrees. The age limit
is 1–900 seconds. Invalid/unknown reports immediately invalidate that sample.
HA report time proves a report reached HA, not a fresh physical measurement:
use sensor integrations which preserve real report timing. For stronger evidence,
use timestamped MQTT reports from the actual sensor adapter (see installation).

Boiler control belongs to one explicitly configured HA coordinator. The client
exposes a fresh `heat_request` binary sensor and accepts `report_source` only
from system contexts (`user_id=None`). That port reads the actual configured
on/off entity inside HA; arbitrary MQTT `boiler/state` is not subscribed.
The authority flag expires after 3 s, independently of the source state. A v2
control includes the authority byte; the MCU masks zone enables when absent.
The coordinator must revoke authority for AC/off/manual mode and enforce CO OFF
on stale/unavailable requests. Never run two independent boiler owners.
The MQTT `boiler/request` lease remains informational for compatible observers;
it is not a second boiler owner in this installation.

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
and inherit HA entity control permissions and the explicit operation state.

## Production payloads

UFH1 envelope and HMAC domains are unchanged. Control v2 is 145 bytes: existing
144-byte layout plus authority boolean at offset 144. Status v2 is 60 bytes:
offsets 0–55 retain their meaning except byte 49 is operator permission;
56=2, 57=installation fact bits (electrical test / external cutoff),
58=relay profile (0 unspecified, 1 direct LOW, 2 inverting buffer), 59=UFC schema.
Reject invalid booleans, reserved bits, unknown profiles and enabled/profile-0.
Old 58-byte status is recognized only as a legacy read-only device for OTA;
its facts are unknown and only legacy all-OFF control is transmitted.

Management opcode 8 has two bytes: enabled (0/1), interface (0/1/2). Only the
management key authorizes it. It changes UFC3→UFC4 plus operator permission and
interface, preserving identities, credentials, mappings, timings and facts.
It requires quiescence, journal staging, trial boot and distinct confirmed
normal boot with incremented generation. UI state follows authenticated evidence.
`set_operation` additionally checks HA entity CONTROL permission for user calls.
No user-supplied source-state JSON is accepted by that service.

First migration from firmware 0.2 uses an explicitly signed eligibility range
[3,3], accepted by its old verifier. The 0.3 image understands UFC3 and UFC4;
operation command migrates the configuration only AFTER successful OTA.
Subsequent release manifests may declare [3,4] or [4,4]. Out-of-range device
configurations are rejected by firmware before writing an application.
Targets persist in HA storage; active zone modes do not survive transport loss,
restart, options change or maintenance. Each zone must be explicitly re-enabled.

## Commit and scheduling (0.2.1)

Sending Finish (or SetOperation) transitions the job to waiting for reboot
BEFORE yielding to MQTT publication. An old-session commit ACK can report a
rejection; it is not required if TCP closes during reset. Success still requires
a fresh authenticated trial of the exact version/generation, confirmation and a
distinct normal boot. Random reboot mid-stream remains an error.
Management ACK/offset/phase changes wake the transport task immediately. The
0.5 s timeout remains a lease/liveness timer, never a delay between firmware
blocks. Duplicate status packets do not create duplicate chunk sends.

## Operator feedback (0.2.2)

Temperature targets may be edited while controller operation is OFF. This does
not grant operating authority or arm a zone. HEAT rejects maintenance, obsolete
status schema and operation OFF as separate HA translation keys. Setting a
target never implicitly changes the provisioned relay interface or permission.

Boot confirmation for the main controller additionally requires a live own
control ACK from THAT boot, all zone requests/output masks OFF, no heat request
and (when operation is enabled) fresh source OFF plus Idle. A signed management
trial announcement alone is not readiness: source evidence may still be unknown
after reconnect. The same quiescence predicate applies before maintenance and
before confirming a trial. No confirmation is sent early and retried as a bypass.

## Source evidence transport (0.2.3)

`report_source` binds the actual HA switch and grants/revokes the existing
3-second owner-authority lease. Before each control publication the adapter
reads that bound entity again; unknown/unavailable/absent is Unknown, never OFF.
Source evidence gets its own 3-second deadline from that actual read. Reading
the switch cannot renew the owner's authority, temperature reports or MCU ACKs.
This remains evidence of an HA switch state, not of a physical flame or pump.

Control refresh is bounded at two frames per second under normal scheduling,
not one near-expiry cached frame. No TTL, firmware overrun timer, admission,
boot confirmation or fail-off protection is relaxed. Receiver-side expiry is
still authoritative; a genuine Unknown/ON interrupts a continuous OFF overrun.

## Explicit shared room references (client 0.2.4)

`shared_reference_zones` is one optional list of distinct protocol zone IDs.
An empty list preserves the independent-reference contract. A nonempty list
requires at least two zones and the same explicit HA temperature entity bound
to every member. Only declared members may duplicate an installed reference;
unknown members, missing/mismatched references or undeclared duplicates fail
configuration validation. This is not an inferred group or a fallback sensor.

The reference declaration and bindings may be saved before upper hardware is
installed. Installed zone permissions still require the actual upper identity
and signing key; this option grants no node identity, installed zone, enable,
source authority or GPIO permission. Changing options retains existing safe
reloading/disarming behavior. Restart does not restore zone ON.

Each member receives the same original HA report timestamp, value, age limit
and invalidation. No report is made newer by sharing it. Invalid/stale evidence
revokes every affected sample. Setpoints, HVAC enable state and firmware loop
assignments remain independent. Sharing room air never binds a floor surface
probe to the room thermostat. No firmware or UFH1 wire change is needed.
