"""Ed25519 signature verification (RFC 8032, section 5.1.7), in pure Python.

Only verification: the app checks release manifests and never signs anything. It is not
constant-time, which does not matter for verifying public data with a public key.
"""
import hashlib

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493  # Order of the base point.
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _add(a, b):
    """Sum of two points in extended coordinates (X, Y, Z, T), x = X/Z, y = Y/Z, x*y = T/Z."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    k = (y1 - x1) * (y2 - x2) % P
    l = (y1 + x1) * (y2 + x2) % P
    m = 2 * D * t1 * t2 % P
    n = 2 * z1 * z2 % P
    e, f, g, h = l - k, n - m, n + m, l + k
    return e * f % P, g * h % P, f * g % P, e * h % P


def _multiply(scalar, point):
    result = (0, 1, 1, 0)  # The neutral element.
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a, b):
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % P == 0 and (y1 * z2 - y2 * z1) % P == 0


def _recover_x(y, sign):
    if y >= P:
        return None
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if xx == 0:
        return None if sign else 0
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P:
        x = x * SQRT_M1 % P
    if (x * x - xx) % P:
        return None
    return P - x if x & 1 != sign else x


def _decompress(data):
    """The point encoded in 32 bytes, or None when they encode no point on the curve."""
    y = int.from_bytes(data, 'little')
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


_GY = 4 * pow(5, P - 2, P) % P
BASE = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % P)


def verify(public_key, message, signature):
    """True when `signature` (64 bytes) is a valid Ed25519 signature of `message` by `public_key` (32 bytes)."""
    if not all(isinstance(value, (bytes, bytearray)) for value in (public_key, message, signature)):
        return False
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    s = int.from_bytes(signature[32:], 'little')
    if a is None or r is None or s >= L:
        return False
    h = int.from_bytes(hashlib.sha512(bytes(signature[:32]) + bytes(public_key) + bytes(message)).digest(), 'little') % L
    return _equal(_multiply(s, BASE), _add(r, _multiply(h, a)))
