"""Test-only Ed25519 signing (RFC 8032, section 5.1.6) and a throwaway release key.

The app only verifies (story_studio/ed25519.py). Tests sign fake release manifests with keys
generated here; the real signing key never takes part in a test.
"""
import base64
import hashlib
import os

from helpers import SRC  # noqa: F401 (puts src/ on sys.path)
from story_studio.ed25519 import BASE, L, P, _multiply


def _compress(point):
    x, y, z, _ = point
    inverse = pow(z, P - 2, P)
    x, y = x * inverse % P, y * inverse % P
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _expand(seed):
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], 'little')
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed):
    """The 32-byte public key of a 32-byte private seed."""
    return _compress(_multiply(_expand(seed)[0], BASE))


def sign(seed, message):
    scalar, prefix = _expand(seed)
    public = _compress(_multiply(scalar, BASE))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), 'little') % L
    encoded_r = _compress(_multiply(r, BASE))
    h = int.from_bytes(hashlib.sha512(encoded_r + public + message).digest(), 'little') % L
    return encoded_r + int.to_bytes((r + h * scalar) % L, 32, 'little')


class TestKey:
    """A fresh throwaway keypair: `public` (raw bytes) and `signature(data)` (base64 text, as latest.json.sig)."""

    def __init__(self, seed=None):
        self.seed = seed or os.urandom(32)
        self.public = public_key(self.seed)
        self.public_base64 = base64.b64encode(self.public).decode()

    def signature(self, data):
        return base64.b64encode(sign(self.seed, data)).decode() + '\n'
