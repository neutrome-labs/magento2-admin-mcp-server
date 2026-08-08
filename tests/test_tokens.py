"""Tests for TokenEngine — JWT creation, verification, and Fernet encryption."""

from __future__ import annotations

import time

import jwt
import pytest

from src.auth.tokens import TOKEN_TTL, TokenEngine, TokenType


@pytest.fixture
def engine(aes_secret: str) -> TokenEngine:
    return TokenEngine(aes_secret)


# ── Fernet encrypt / decrypt ───────────────────────────────────────────


class TestFernetRoundTrip:
    def test_encrypt_decrypt(self, engine: TokenEngine) -> None:
        plaintext = "https://magento.example.com"
        ct = engine.encrypt(plaintext)
        assert ct != plaintext
        assert engine.decrypt(ct) == plaintext

    def test_encrypt_produces_different_ciphertext(self, engine: TokenEngine) -> None:
        """Fernet includes a timestamp → every call differs."""
        a = engine.encrypt("same")
        b = engine.encrypt("same")
        assert a != b  # nonce differs
        assert engine.decrypt(a) == engine.decrypt(b) == "same"

    def test_decrypt_invalid_raises(self, engine: TokenEngine) -> None:
        with pytest.raises(ValueError, match="Invalid or corrupted"):
            engine.decrypt("not-a-fernet-token")

    def test_empty_string(self, engine: TokenEngine) -> None:
        ct = engine.encrypt("")
        assert engine.decrypt(ct) == ""


# ── Token creation and verification ────────────────────────────────────


class TestTokenCreation:
    @pytest.mark.parametrize("ttype", list(TokenType))
    def test_create_and_decode(self, engine: TokenEngine, ttype: TokenType) -> None:
        token = engine.create_token(
            token_type=ttype,
            magento_url="https://m2.test",
            magento_token="abc123",
            client_id="client-jwt",
        )
        payload = engine.decode_token(token, expected_type=ttype)
        assert payload["typ"] == ttype.value
        assert payload["cid"] == "client-jwt"
        assert payload["scp"] == ["mcp"]
        assert payload["exp"] > time.time()

    def test_extract_credentials(self, engine: TokenEngine) -> None:
        token = engine.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url="https://m2.test",
            magento_token="secret-key",
            client_id="c1",
        )
        payload = engine.decode_token(token)
        url, key = engine.extract_credentials(payload)
        assert url == "https://m2.test"
        assert key == "secret-key"

    def test_wrong_type_raises(self, engine: TokenEngine) -> None:
        token = engine.create_token(
            token_type=TokenType.AUTH_CODE,
            magento_url="https://m2.test",
            magento_token="x",
            client_id="c1",
        )
        with pytest.raises(jwt.InvalidTokenError, match="Expected token type"):
            engine.decode_token(token, expected_type=TokenType.ACCESS_TOKEN)

    def test_ttl_values(self, engine: TokenEngine) -> None:
        for ttype in TokenType:
            token = engine.create_token(
                token_type=ttype,
                magento_url="https://m2.test",
                magento_token="x",
                client_id="c1",
            )
            payload = engine.decode_token(token)
            expected_ttl = TOKEN_TTL[ttype]
            actual_ttl = payload["exp"] - payload["iat"]
            assert actual_ttl == expected_ttl

    def test_custom_scopes(self, engine: TokenEngine) -> None:
        token = engine.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url="https://m2.test",
            magento_token="x",
            client_id="c1",
            scopes=["read", "write"],
        )
        payload = engine.decode_token(token)
        assert payload["scp"] == ["read", "write"]

    def test_extra_claims(self, engine: TokenEngine) -> None:
        token = engine.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url="https://m2.test",
            magento_token="x",
            client_id="c1",
            extra_claims={"code_challenge": "abc", "redirect_uri": "http://x"},
        )
        payload = engine.decode_token(token)
        assert payload["code_challenge"] == "abc"
        assert payload["redirect_uri"] == "http://x"

    def test_expired_token_raises(self, engine: TokenEngine) -> None:
        """Manually create an already-expired JWT and verify rejection."""
        now = int(time.time())
        payload = {
            "typ": TokenType.ACCESS_TOKEN.value,
            "url": engine.encrypt("https://m2.test"),
            "key": engine.encrypt("x"),
            "sub": "test",
            "cid": "c1",
            "scp": ["mcp"],
            "iat": now - 100,
            "exp": now - 1,  # already expired
        }
        token = jwt.encode(payload, engine._secret, algorithm="HS256")
        with pytest.raises(jwt.ExpiredSignatureError):
            engine.decode_token(token)


# ── Client registration tokens ─────────────────────────────────────────


class TestClientTokens:
    def test_client_token_roundtrip(self, engine: TokenEngine) -> None:
        meta = {
            "client_name": "Test MCP Client",
            "redirect_uris": ["http://localhost:3000/callback"],
            "client_secret": "s3cret",
        }
        token = engine.create_client_token(meta)
        decoded = engine.decode_client_token(token)
        assert decoded["client_name"] == "Test MCP Client"
        assert decoded["redirect_uris"] == ["http://localhost:3000/callback"]
        assert decoded["typ"] == "client"

    def test_non_client_token_rejected(self, engine: TokenEngine) -> None:
        """An access_token JWT should not decode as a client token."""
        token = engine.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url="https://m2.test",
            magento_token="x",
            client_id="c1",
        )
        with pytest.raises(jwt.InvalidTokenError, match="Not a client"):
            engine.decode_client_token(token)


# ── Cross-engine isolation ──────────────────────────────────────────────


class TestCrossEngine:
    def test_different_secrets_cannot_decode(self) -> None:
        engine_a = TokenEngine("secret-A-0123456789abcdef0123456789")
        engine_b = TokenEngine("secret-B-0123456789abcdef0123456789")

        token = engine_a.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url="https://m2.test",
            magento_token="x",
            client_id="c1",
        )
        with pytest.raises(Exception):
            engine_b.decode_token(token)

    def test_same_secret_same_results(self) -> None:
        engine_a = TokenEngine("shared-secret-0123456789abcdef01")
        engine_b = TokenEngine("shared-secret-0123456789abcdef01")

        token = engine_a.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url="https://m2.test",
            magento_token="key",
            client_id="c1",
        )
        payload = engine_b.decode_token(token)
        url, key = engine_b.extract_credentials(payload)
        assert url == "https://m2.test"
        assert key == "key"
