"""Signed release format. No network, arbitrary URLs, private keys or side effects."""

from __future__ import annotations

import hashlib
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SLOT_SIZE = 0x1F0000
MANIFEST_SIZE = 144
DOMAIN = b"underfloor/firmware/v1\0"


@dataclass(frozen=True, order=True, slots=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> Version:
        if not re.fullmatch(
            r"(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})", value
        ):
            raise ValueError("release requires numeric major.minor.patch")
        parts = tuple(map(int, value.split(".")))
        if any(v > 65535 for v in parts):
            raise ValueError("version exceeds u16")
        return cls(*parts)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def pack(self) -> bytes:
        return struct.pack(">HHH", self.major, self.minor, self.patch)


def image_version(image: bytes, role: int) -> Version:
    if (
        not 288 <= len(image) <= SLOT_SIZE
        or image[0] != 0xE9
        or not 1 <= image[1] <= 16
        or image[12:14] != b"\x0d\x00"
        or image[23] != 1
    ):
        raise ValueError("not a bounded ESP32-C6 image with appended digest")
    if image[32:36] != struct.pack("<I", 0xABCD5432):
        raise ValueError("missing app descriptor")
    name = {1: b"underfloor-main", 2: b"underfloor-upper"}.get(role)
    if name is None or image[80:112].split(b"\0", 1)[0] != name:
        raise ValueError("image/board role mismatch")
    field = image[48:80]
    text, _, tail = field.partition(b"\0")
    if any(tail):
        raise ValueError("malformed version field")
    version = Version.parse(text.decode("ascii"))
    offset = 24
    checksum = 0xEF
    for _ in range(image[1]):
        if offset + 8 > len(image):
            raise ValueError("truncated image segment")
        length = struct.unpack_from("<I", image, offset + 4)[0]
        offset += 8
        if offset + length > len(image):
            raise ValueError("segment exceeds image")
        for value in image[offset : offset + length]:
            checksum ^= value
        offset += length
    end = (offset | 15) + 1
    if (
        end + 32 != len(image)
        or image[end - 1] != checksum
        or hashlib.sha256(image[:end]).digest() != image[end:]
    ):
        raise ValueError("ESP checksum/digest or image length mismatch")
    return version


@dataclass(frozen=True, slots=True)
class Package:
    role: int
    version: Version
    manifest: bytes
    image: bytes

    @classmethod
    def parse(cls, data: bytes, public_key: bytes, role: int) -> Package:
        if not MANIFEST_SIZE + 288 <= len(data) <= MANIFEST_SIZE + SLOT_SIZE:
            raise ValueError("release size out of bounds")
        m, image = data[:MANIFEST_SIZE], data[MANIFEST_SIZE:]
        if (
            m[:8] != b"UFU1" + bytes((1, role, 2, 2))
            or not (3 <= m[14] <= m[15] <= 4)
            or m[68:70] != b"\x00\x01"
            or any(m[70:80])
        ):
            raise ValueError("release compatibility mismatch")
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(m[80:], DOMAIN + m[:80])
        except InvalidSignature as exc:
            raise ValueError("release signature invalid") from exc
        version = Version(*struct.unpack_from(">HHH", m, 8))
        if (
            struct.unpack_from(">I", m, 16)[0] != len(image)
            or hashlib.sha256(image).digest() != m[20:52]
            or not any(m[52:68])
        ):
            raise ValueError("release hash/length mismatch")
        if image_version(image, role) != version:
            raise ValueError("manifest/image version mismatch")
        return cls(role, version, m, image)


class Catalog:
    def __init__(self, directory: Path, public_key: bytes, role: int):
        if not directory.is_dir():
            raise ValueError("approved release directory does not exist")
        self.directory = directory.resolve()
        self.public_key = public_key
        self.role = role
        self.cached: tuple[str, int, int, Package] | None = None

    def latest(self) -> Package | None:
        prefix = {1: "main-floor-", 2: "upper-floor-"}[self.role]
        candidates = []
        for path in self.directory.glob(prefix + "*.ufu"):
            if len(candidates) >= 64:
                raise ValueError("too many approved releases; archive old versions")
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("release must be a regular file")
            if path.stat().st_size > MANIFEST_SIZE + SLOT_SIZE:
                raise ValueError("oversized release")
            version = Version.parse(path.name.removeprefix(prefix).removesuffix(".ufu"))
            candidates.append((version, path))
        if not candidates:
            self.cached = None
            return None
        version, path = max(candidates, key=lambda item: item[0])
        metadata = path.stat()
        identity = (path.name, metadata.st_mtime_ns, metadata.st_size)
        if self.cached is not None and self.cached[:3] == identity:
            return self.cached[3]
        package = Package.parse(path.read_bytes(), self.public_key, self.role)
        if package.version != version:
            raise ValueError("release filename/version mismatch")
        self.cached = (*identity, package)
        return package
