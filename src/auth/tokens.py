"""TokenEngine — create / decode / verify stateless JWTs with Fernet-encrypted payloads.

Every token (auth_code, access_token, refresh_token) is a self-contained JWT.
Inner credential fields (Magento URL + admin token) are Fernet-encrypted so they
never appear in plaintext even if a JWT is logged or leaked.
"""

from __future__ import annotations

import hashlib
import time
from enum import Enum
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken

# Fernet requires a 32-byte URL-safe base64-encoded key.
# We derive it deterministically from AES_SECRET via SHA-256.
_FERNET_KEY_CACHE: dict[str, Fernet] = {}


class TokenType(str, Enum):
    AUTH_CODE = "ac"
    ACCESS_TOKEN = "at"
    REFRESH_TOKEN = "rt"


# TTLs in seconds
TOKEN_TTL: dict[TokenType, int] = {
    TokenType.AUTH_CODE: 5 * 60,        # 5 min
    TokenType.ACCESS_TOKEN: 24 * 3600,  # 24 h
    TokenType.REFRESH_TOKEN: 7 * 86400, # 7 d
}


def _derive_fernet(secret: str) -> Fernet:
    """Derive a Fernet instance from a human‐readable secret string."""
    if secret not in _FERNET_KEY_CACHE:
        # SHA-256 → 32 raw bytes → url-safe base64 (44 chars)
        import base64
        raw = hashlib.sha256(secret.encode()).digest()
        key = base64.urlsafe_b64encode(raw)
        _FERNET_KEY_CACHE[secret] = Fernet(key)
    return _FERNET_KEY_CACHE[secret]


class TokenEngine:
    """Stateless JWT factory backed by HS256 + Fernet encryption."""

    def __init__(self, aes_secret: str) -> None:
        self._secret = aes_secret
        self._fernet = _derive_fernet(aes_secret)

    # ── Fernet helpers ──────────────────────────────────────────────────

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string and return it as a URL-safe token."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a Fernet token back to plaintext."""
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Invalid or corrupted Fernet token") from exc

    # ── JWT creation ────────────────────────────────────────────────────

    def create_token(
        self,
        token_type: TokenType,
        magento_url: str,
        magento_token: str,
        client_id: str,
        scopes: list[str] | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """Create a signed JWT with Fernet-encrypted credential payloads."""
        now = int(time.time())
        ttl = TOKEN_TTL[token_type]

        payload: dict[str, Any] = {
            "typ": token_type.value,
            "url": self.encrypt(magento_url),
            "key": self.encrypt(magento_token),
            "sub": hashlib.sha256(magento_url.encode()).hexdigest()[:16],
            "cid": client_id,
            "scp": scopes or ["mcp"],
            "iat": now,
            "exp": now + ttl,
        }

        if extra_claims:
            payload.update(extra_claims)

        return jwt.encode(payload, self._secret, algorithm="HS256")

    # ── JWT verification ────────────────────────────────────────────────

    def decode_token(
        self,
        token: str,
        expected_type: TokenType | None = None,
    ) -> dict[str, Any]:
        """
        Decode and verify a JWT.  Raises jwt.PyJWTError on failure.
        If *expected_type* is given, verify the ``typ`` claim matches.
        """
        payload = jwt.decode(token, self._secret, algorithms=["HS256"])

        if expected_type is not None and payload.get("typ") != expected_type.value:
            raise jwt.InvalidTokenError(
                f"Expected token type {expected_type.value!r}, "
                f"got {payload.get('typ')!r}"
            )
        return payload

    def extract_credentials(self, payload: dict[str, Any]) -> tuple[str, str]:
        """Decrypt and return ``(magento_url, magento_token)`` from JWT claims."""
        return self.decrypt(payload["url"]), self.decrypt(payload["key"])

    # ── Client registration tokens ──────────────────────────────────────

    def create_client_token(self, client_info: dict[str, Any]) -> str:
        """Encode client registration metadata into a JWT (no Fernet needed)."""
        now = int(time.time())
        payload = {
            "typ": "client",
            "iat": now,
            "exp": now + 365 * 86400,  # 1 year
            **client_info,
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def decode_client_token(self, token: str) -> dict[str, Any]:
        """Decode a client registration JWT."""
        payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        if payload.get("typ") != "client":
            raise jwt.InvalidTokenError("Not a client registration token")
        return payload
