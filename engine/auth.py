"""
auth.py -- password hashing for character login.

Stdlib-only (hashlib + secrets + hmac), no external crypto libraries, per
CLAUDE.md's "standard library only" rule. PBKDF2-HMAC-SHA256 with a random
per-character salt and a deliberately slow work factor -- expensive enough to
resist offline brute-forcing of a stolen database, cheap enough that a human
typing a password at login never notices.

This module is pure functions: no networking, no database, no game state. It
doesn't know what a Character or a Session is -- connection.py calls in with
plain strings and gets a hash (or a yes/no) back.
"""

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000   # PBKDF2 work factor; raise later if hardware outpaces it
_SALT_BYTES = 16

# Shared floor for chargen + setpass (mortals and staff). Staff also need
# letter + digit + symbol -- see password_policy_error(for_gm=True).
MIN_PASSWORD_LEN = 6


def password_policy_error(password, *, for_gm=False):
    """Return a short refusal string if password is too weak, else None.

    Mortals: length only (lazy OK). GM / head_gm: also letter + digit +
    symbol (non-alphanumeric). Callers pass for_gm from gm_rank.
    """
    if not password or len(password) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if for_gm:
        has_letter = any(c.isalpha() for c in password)
        has_digit = any(c.isdigit() for c in password)
        has_symbol = any(not c.isalnum() for c in password)
        missing = []
        if not has_letter:
            missing.append("a letter")
        if not has_digit:
            missing.append("a digit")
        if not has_symbol:
            missing.append("a symbol")
        if missing:
            need = ", ".join(missing)
            return (
                f"GM passwords need {need} "
                f"(min {MIN_PASSWORD_LEN} chars, letter + digit + symbol)."
            )
    return None


def hash_password(password):
    """Turn a plaintext password into a salted hash string safe to store.

    The stored format is "<salt-hex>$<hash-hex>" -- the salt rides along with
    its hash, so verifying never needs a separate salt column or lookup.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    """Check a plaintext password attempt against a hash_password() string.

    Recomputes the hash using the SAME salt embedded in `stored`, then
    compares with hmac.compare_digest instead of '==' -- a plain equality
    check leaks timing information about how many leading bytes matched,
    which is a real (if slow) way to guess a hash byte by byte. Returns
    False rather than raising for a missing/malformed stored value, so a
    corrupt or blank password field just fails closed.
    """
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return hmac.compare_digest(actual, expected)
