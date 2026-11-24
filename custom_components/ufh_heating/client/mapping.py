"""Physical channel permutations and their authenticated configuration fingerprint."""

import hashlib


def mapping_fingerprint(ground, upper) -> bytes:
    for values, count in ((ground, 6), (upper, 8)):
        if (
            not isinstance(values, list | tuple)
            or len(values) != count
            or any(type(v) is not int for v in values)
            or len(set(values)) != count
            or any(not 0 <= v <= 7 for v in values)
        ):
            raise ValueError("explicit fitted-channel permutation required")
    return hashlib.sha256(b"ufh/loop-mapping/v1\0" + bytes(ground) + bytes(upper)).digest()
