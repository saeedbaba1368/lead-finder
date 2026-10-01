"""API key generation and hashing. The plaintext secret is shown once and never stored."""

import hashlib
import secrets
from dataclasses import dataclass

KEY_PREFIX_LENGTH = 8  # hex chars, stored in api_keys.key_prefix (non-secret lookup id)


@dataclass(frozen=True)
class GeneratedApiKey:
    secret: str  # give to the user once; never persist
    key_prefix: str
    key_hash: str


def hash_api_key(secret: str) -> str:
    """sha256 hex digest. API keys are high-entropy random strings, so a fast hash suffices."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_api_key() -> GeneratedApiKey:
    prefix = secrets.token_hex(KEY_PREFIX_LENGTH // 2)
    secret = f"lf_{prefix}_{secrets.token_urlsafe(32)}"
    return GeneratedApiKey(secret=secret, key_prefix=prefix, key_hash=hash_api_key(secret))
