"""Synthetic ESP fixture and a TEST-ONLY signing helper. No production keys."""

import hashlib
import struct

from custom_components.ufh_heating.client.releases import DOMAIN, image_version


def sign(image, role, key):
    version = image_version(image, role)
    fields = bytearray(80)
    fields[:8] = b"UFU1" + bytes((1, role, 2, 2))
    fields[8:14] = version.pack()
    fields[14:16] = b"\x03\x03"
    struct.pack_into(">I", fields, 16, len(image))
    fields[20:52] = hashlib.sha256(image).digest()
    fields[52:68] = bytes([0x44]) * 16
    fields[68:70] = b"\x00\x01"
    return bytes(fields) + key.sign(DOMAIN + fields) + image
