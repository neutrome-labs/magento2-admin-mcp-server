"""Tests for the OAuth provider — client lifecycle, authorization, token exchange."""

from __future__ import annotations

import pytest
from mcp.server.auth.provider import AuthorizationCode, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull

from src.auth.provider import StatelessOAuthProvider
from src.auth.tokens import TokenEngine, TokenType
from src.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(aes_secret="a" * 32, port=9999)  # type: ignore[call-arg]


@pytest.fixture
def provider(settings: Settings) -> StatelessOAuthProvider:
    return StatelessOAuthProvider(settings)


@pytest.fixture
def engine(settings: Settings) -> TokenEngine:
    return TokenEngine(settings.aes_secret)


# ── Client registration ────────────────────────────────────────────────


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_register_and_get_client(self, provider: StatelessOAuthProvider) -> None:
        client_info = OAuthClientInformationFull(
            client_id="placeholder",
            redirect_uris=["http://localhost:3000/callback"],
            client_name="Test Client",
        )
        await provider.register_client(client_info)

        # client_id should have been mutated to a JWT
        assert client_info.client_id != "placeholder"
        assert client_info.client_secret is not None

        # get_client should recover the metadata
        recovered = await provider.get_client(client_info.client_id)
        assert recovered is not None
        assert recovered.client_name == "Test Client"
        assert "http://localhost:3000/callback" in [str(u) for u in recovered.redirect_uris]

    @pytest.mark.asyncio
    async def test_get_client_invalid_jwt(self, provider: StatelessOAuthProvider) -> None:
        result = await provider.get_client("not-a-jwt")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_client_wrong_secret(self) -> None:
        """A client_id JWT from a different secret should fail."""
        s1 = Settings(aes_secret="x" * 32)  # type: ignore[call-arg]
        s2 = Settings(aes_secret="y" * 32)  # type: ignore[call-arg]
        p1 = StatelessOAuthProvider(s1)
        p2 = StatelessOAuthProvider(s2)

        client = OAuthClientInformationFull(
            client_id="tmp",
            redirect_uris=["http://localhost/cb"],
        )
        await p1.register_client(client)

        # p2 cannot decode p1's client_id
        result = await p2.get_client(client.client_id)
        assert result is None


# ── Authorization code flow ─────────────────────────────────────────────


class TestAuthorizationCode:
    @pytest.mark.asyncio
    async def test_load_authorization_code(
        self, provider: StatelessOAuthProvider, engine: TokenEngine
    ) -> None:
        code = engine.create_token(
            token_type=TokenType.AUTH_CODE,
            magento_url="https://m2.test",
            magento_token="key123",
            client_id="cid",
            extra_claims={
                "code_challenge": "challenge",
                "redirect_uri": "http://localhost/cb",
            },
        )

        client = OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://localhost/cb"],
        )
        ac = await provider.load_authorization_code(client, code)
        assert ac is not None
        assert ac.code == code
        assert ac.client_id == "cid"
        assert ac.code_challenge == "challenge"

    @pytest.mark.asyncio
    async def test_load_authorization_code_invalid(
        self, provider: StatelessOAuthProvider
    ) -> None:
        client = OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://localhost/cb"],
        )
        result = await provider.load_authorization_code(client, "garbage-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_exchange_authorization_code(
        self, provider: StatelessOAuthProvider, engine: TokenEngine
    ) -> None:
        code = engine.create_token(
            token_type=TokenType.AUTH_CODE,
            magento_url="https://m2.test",
            magento_token="key123",
            client_id="cid",
            extra_claims={
                "code_challenge": "challenge",
                "redirect_uri": "http://localhost/cb",
            },
        )

        client = OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://localhost/cb"],
        )
        ac = AuthorizationCode(
            code=code,
            client_id="cid",
            scopes=["mcp"],
            expires_at=9999999999,
            code_challenge="challenge",
            redirect_uri="http://localhost/cb",
            redirect_uri_provided_explicitly=True,
        )

        oauth_token = await provider.exchange_authorization_code(client, ac)
        assert oauth_token.access_token
        assert oauth_token.refresh_token
        assert oauth_token.token_type.lower() == "bearer"
        assert "mcp" in (oauth_token.scope or "")

        # Verify the access_token is a valid JWT with correct credentials
        payload = engine.decode_token(
            oauth_token.access_token, expected_type=TokenType.ACCESS_TOKEN
        )
        url, key = engine.extract_credentials(payload)
        assert url == "https://m2.test"
        assert key == "key123"


# ── Refresh token flow ──────────────────────────────────────────────────


class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_load_refresh_token(
        self, provider: StatelessOAuthProvider, engine: TokenEngine
    ) -> None:
        rt_jwt = engine.create_token(
            token_type=TokenType.REFRESH_TOKEN,
            magento_url="https://m2.test",
            magento_token="key",
            client_id="cid",
        )
        client = OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://localhost/cb"],
        )
        rt = await provider.load_refresh_token(client, rt_jwt)
        assert rt is not None
        assert rt.client_id == "cid"

    @pytest.mark.asyncio
    async def test_load_refresh_token_invalid(
        self, provider: StatelessOAuthProvider
    ) -> None:
        client = OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://localhost/cb"],
        )
        result = await provider.load_refresh_token(client, "bad")
        assert result is None

    @pytest.mark.asyncio
    async def test_exchange_refresh_token(
        self, provider: StatelessOAuthProvider, engine: TokenEngine
    ) -> None:
        rt_jwt = engine.create_token(
            token_type=TokenType.REFRESH_TOKEN,
            magento_url="https://m2.test",
            magento_token="key",
            client_id="cid",
        )
        client = OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://localhost/cb"],
        )
        rt = RefreshToken(
            token=rt_jwt,
            client_id="cid",
            scopes=["mcp"],
        )

        oauth_token = await provider.exchange_refresh_token(client, rt, ["mcp"])
        assert oauth_token.access_token
        assert oauth_token.refresh_token
        # New refresh_token should differ from original
        assert oauth_token.refresh_token != rt_jwt


# ── Access token verification ───────────────────────────────────────────


class TestAccessToken:
    @pytest.mark.asyncio
    async def test_load_access_token(
        self, provider: StatelessOAuthProvider, engine: TokenEngine
    ) -> None:
        at_jwt = engine.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url="https://m2.test",
            magento_token="secret",
            client_id="cid",
        )
        at = await provider.load_access_token(at_jwt)
        assert at is not None
        assert at.client_id == "cid"
        assert at.claims is not None
        assert "url" in at.claims  # Fernet-encrypted magento_url
        assert "key" in at.claims  # Fernet-encrypted magento_token

    @pytest.mark.asyncio
    async def test_load_access_token_invalid(
        self, provider: StatelessOAuthProvider
    ) -> None:
        result = await provider.load_access_token("invalid-jwt")
        assert result is None


# ── Revocation (no-op) ──────────────────────────────────────────────────


class TestRevocation:
    @pytest.mark.asyncio
    async def test_revoke_is_noop(
        self, provider: StatelessOAuthProvider, engine: TokenEngine
    ) -> None:
        from fastmcp.server.auth import AccessToken

        at_jwt = engine.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url="https://m2.test",
            magento_token="x",
            client_id="cid",
        )
        at = AccessToken(
            token=at_jwt,
            client_id="cid",
            scopes=["mcp"],
        )
        # Should not raise
        await provider.revoke_token(at)

        # Token should still be valid after "revocation"
        result = await provider.load_access_token(at_jwt)
        assert result is not None
