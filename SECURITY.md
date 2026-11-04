# Security policy

This repository is the public HA client only. Do not attach house configuration,
HA backups, device provisioning records, broker passwords, HMAC keys, private
signing keys or complete flash dumps to issues. Use a private GitHub security
advisory for a vulnerability and describe the affected version and behavior.

Control and management use distinct HMAC keys/domains, receiver boot nonces,
monotonic sequences and bounded deadlines. Firmware packages use Ed25519 and
SHA-256. Native HA diagnostics use an explicit field allowlist. Tests use synthetic,
clearly identified test-only keys and images.

HMAC is authentication, not transport encryption. The current ESP firmware uses
MQTT on a trusted isolated LAN. Secure the broker with authentication and ACLs;
do not expose it to the Internet. HA's existing MQTT integration owns connection
credentials. Protect HA's config-entry storage and encrypted backups.

A/B rollback and signed updates are not ESP secure boot or flash encryption.
This integration does not change eFuses or claim resistance to physical access.
GPIO latch evidence is not valve-position, current, water-flow or flame sensing.
Hardware acceptance and independent watchdog cutoff remain mandatory before
commissioning the heating controller.
