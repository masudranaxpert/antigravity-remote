"""RFC 6238 Time-Based One-Time Password (TOTP) Engine for Antigravity Remote.

Provides standard 2-Factor Authentication (compatible with Google Authenticator,
Microsoft Authenticator, 1Password, and Authy) using pure Python standard library.
Zero third-party pip dependencies.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def generate_totp_secret(length: int = 16) -> str:
    """Generate cryptographically secure 16-character Base32 TOTP secret."""
    return "".join(secrets.choice(BASE32_ALPHABET) for _ in range(length))


def compute_totp(secret: str, for_time: float | None = None) -> str:
    """Compute 6-digit TOTP token for given timestamp according to RFC 6238."""
    if for_time is None:
        for_time = time.time()

    counter = int(for_time // 30)
    counter_bytes = struct.pack(">Q", counter)

    # Normalize and decode Base32 secret with padding if needed
    cleaned_secret = secret.replace(" ", "").upper()
    missing_padding = len(cleaned_secret) % 8
    if missing_padding:
        cleaned_secret += "=" * (8 - missing_padding)

    secret_bytes = base64.b32decode(cleaned_secret, casefold=True)
    digest = hmac.new(secret_bytes, counter_bytes, hashlib.sha1).digest()

    offset = digest[-1] & 0x0F
    code_int = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code_int:06d}"


def verify_totp(secret: str, user_code: str, window: int = 2) -> bool:
    """Verify 6-digit TOTP token allowing clock skew of window steps (+-60s with window=2)."""
    if not secret or not user_code:
        return False

    user_code = user_code.strip().replace(" ", "")
    if len(user_code) != 6 or not user_code.isdigit():
        return False

    now = time.time()
    for offset in range(-window, window + 1):
        test_time = now + (offset * 30)
        expected_code = compute_totp(secret, test_time)
        if hmac.compare_digest(user_code, expected_code):
            return True

    return False


def get_totp_uri(secret: str, account_name: str = "Host-PC", issuer: str = "Antigravity Remote") -> str:
    """Generate standard otpauth:// URI for Authenticator apps."""
    cleaned_secret = secret.replace(" ", "").upper()
    label = quote(f"{issuer}:{account_name}")
    issuer_param = quote(issuer)
    return f"otpauth://totp/{label}?secret={cleaned_secret}&issuer={issuer_param}&algorithm=SHA1&digits=6&period=30"
