"""
Pseudo-random permutations for DFS exploration order shuffling.

Implements keyed bijections on [0, d) using:
- Affine permutation for small domains (d <= 32)
- Feistel network with cycle walking for larger domains

Igor Konnov, 2026
Implemented by Claude Opus 4.6
"""

from math import gcd

MASK64 = 0xFFFFFFFFFFFFFFFF
_AFFINE_MAX_D = 32  # domains up to this size use the affine branch


def rotl64(x: int, k: int) -> int:
    """64-bit left rotation."""
    x &= MASK64
    return ((x << k) | (x >> (64 - k))) & MASK64


def mix64(x: int) -> int:
    """SplitMix64 finalizer (64-bit integer mixer)."""
    x &= MASK64
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & MASK64
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & MASK64
    x ^= x >> 31
    return x & MASK64


def _permute_affine(seed: int, tweak: int, d: int, i: int) -> int:
    """Affine permutation (a*i + b) % d with gcd(a, d) = 1."""
    if d <= 1:
        return 0
    x = mix64(seed ^ rotl64(tweak, 17))
    a = (x | 1) % d
    if a == 0:
        a = 1
    while gcd(a, d) != 1:
        a = (a + 2) % d
        if a == 0:
            a = 1
    b = mix64(x ^ 0x9E3779B97F4A7C15) % d
    return (a * i + b) % d


def _feistel(x: int, nbits: int, key: int, tweak: int, rounds: int = 4) -> int:
    """Generic Feistel network on nbits-bit values (nbits must be even)."""
    half = nbits // 2
    half_mask = (1 << half) - 1
    L = x & half_mask
    R = (x >> half) & half_mask
    for r in range(rounds):
        y = mix64(key ^ rotl64(tweak, 11) ^ (r * 0x9E3779B97F4A7C15) ^ R)
        F = y & half_mask
        L, R = R, (L ^ F) & half_mask
    return L | (R << half)


def permute(seed: int, tweak: int, d: int, i: int) -> int:
    """Keyed bijection on [0, d).

    For d <= 1: returns 0.
    For d <= _AFFINE_MAX_D: uses affine permutation.
    For d > _AFFINE_MAX_D: uses Feistel network with cycle walking.
    """
    assert 0 <= i < d, f"i={i} out of range [0, {d})"

    if d <= _AFFINE_MAX_D:
        return _permute_affine(seed, tweak, d, i)

    # Feistel with cycle walking
    nbits = (d - 1).bit_length()
    if nbits % 2 != 0:
        nbits += 1  # pad to even for balanced Feistel

    x = i
    while True:
        x = _feistel(x, nbits, seed, tweak)
        if x < d:
            return x
