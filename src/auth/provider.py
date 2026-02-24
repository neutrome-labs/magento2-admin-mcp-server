"""StatelessOAuthProvider — OAuthProvider subclass backed entirely by encrypted JWTs.

No database, no sessions, no server-side state.  The ``access_token`` IS the
Magento credentials — Fernet-encrypted inside a HS256-signed JWT.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastmcp.server.auth import AccessToken, OAuthProvider
from jinja2 import Environment, FileSystemLoader
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from src.auth.tokens import TOKEN_TTL, TokenEngine, TokenType
from src.settings import Settings

logger = logging.getLogger(__name__)

# ── Jinja2 template environment ────────────────────────────────────────
_TEMPLATE_DIR = Path(__file__).resolve().parent / "ui"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
)


class StatelessOAuthProvider(OAuthProvider):
    """Full OAuth 2.1 Authorization Server — zero server-side storage.

    Every artifact (client registrations, auth codes, access tokens, refresh
    tokens) is a self-contained JWT with Fernet-encrypted Magento credentials.
    """

    def __init__(self, settings: Settings) -> None:
        base = settings.effective_base_url
        super().__init__(
            base_url=base,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        self._settings = settings
        self._engine = TokenEngine(settings.aes_secret)

    # ── Route extension (serve the authorize form) ──────────────────────

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Extend OAuthProvider routes with our custom authorize form."""
        routes = super().get_routes(mcp_path)

        # Add GET /authorize/form — renders the HTML consent form
        routes.append(
            Route(
                "/authorize/form",
                endpoint=self._handle_authorize_form_get,
                methods=["GET"],
            )
        )
        # Add POST /authorize/form — processes the submitted form
        routes.append(
            Route(
                "/authorize/form",
                endpoint=self._handle_authorize_form_post,
                methods=["POST"],
            )
        )
        return routes

    # ── Client management ───────────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Decode the client_id JWT to recover stored client metadata."""
        try:
            payload = self._engine.decode_client_token(client_id)
        except Exception:
            logger.debug("get_client: invalid client_id JWT")
            return None

        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=payload.get("client_secret"),
            redirect_uris=payload.get("redirect_uris", []),
            client_name=payload.get("client_name"),
            grant_types=payload.get(
                "grant_types", ["authorization_code", "refresh_token"]
            ),
            response_types=payload.get("response_types", ["code"]),
            scope=payload.get("scope"),
            token_endpoint_auth_method=payload.get(
                "token_endpoint_auth_method", "none"
            ),
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Encode client metadata into a JWT and set it as the client_id.

        The MCP SDK will use the mutated ``client_info.client_id`` for all
        subsequent operations — so the JWT *is* the persistent registration.

        Respects the client's requested ``token_endpoint_auth_method``.
        Public clients (method ``"none"``) receive no secret — this ensures
        they send ``client_id`` in the form body (required by the SDK's
        ``ClientAuthenticator``) instead of via ``Authorization: Basic``.
        """
        auth_method = client_info.token_endpoint_auth_method or "none"

        # Only generate a secret when the client actually needs one
        raw_secret: str | None = None
        if auth_method in ("client_secret_post", "client_secret_basic"):
            raw_secret = hashlib.sha256(
                (self._settings.aes_secret + str(time.time())).encode()
            ).hexdigest()

        meta: dict[str, Any] = {
            "redirect_uris": [str(u) for u in (client_info.redirect_uris or [])],
            "client_name": client_info.client_name,
            "grant_types": client_info.grant_types
            or ["authorization_code", "refresh_token"],
            "response_types": client_info.response_types or ["code"],
            "scope": client_info.scope,
            "token_endpoint_auth_method": auth_method,
        }
        if raw_secret:
            meta["client_secret"] = raw_secret

        client_id_jwt = self._engine.create_client_token(meta)
        # Mutate in-place so the SDK picks up the JWT
        client_info.client_id = client_id_jwt
        if raw_secret:
            client_info.client_secret = raw_secret

    # ── Authorization flow ──────────────────────────────────────────────

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Return the URL of our HTML authorize form.

        OAuthProvider expects a redirect URL string.  We redirect to our
        ``/authorize/form`` endpoint which renders the HTML consent form.
        """
        base = str(self.base_url).rstrip("/")
        qs = urlencode(
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "state": params.state or "",
                "code_challenge": params.code_challenge,
                "scopes": " ".join(params.scopes) if params.scopes else "mcp",
            }
        )
        return f"{base}/authorize/form?{qs}"

    # ── Custom route handlers for the authorize form ────────────────────

    async def _handle_authorize_form_get(self, request: Request) -> HTMLResponse:
        """Serve the HTML authorize form."""
        context = {
            "client_id": request.query_params.get("client_id", ""),
            "redirect_uri": request.query_params.get("redirect_uri", ""),
            "state": request.query_params.get("state", ""),
            "code_challenge": request.query_params.get("code_challenge", ""),
            "scopes": request.query_params.get("scopes", "mcp"),
        }
        try:
            html = _jinja_env.get_template("authorize.html").render(**context)
        except Exception:
            html = self._fallback_authorize_html(context)
        return HTMLResponse(html)

    async def _handle_authorize_form_post(
        self, request: Request,
    ) -> RedirectResponse | HTMLResponse:
        """Process submitted authorize form: validate Magento creds, issue auth code."""
        form = await request.form()
        form_data = {k: str(v) for k, v in form.items()}

        magento_url = form_data.get("magento_url", "").rstrip("/")
        magento_token = form_data.get("magento_token", "")
        client_id = form_data.get("client_id", "")
        redirect_uri = form_data.get("redirect_uri", "")
        state = form_data.get("state", "")
        code_challenge = form_data.get("code_challenge", "")
        scopes_str = form_data.get("scopes", "mcp")

        if not magento_url or not magento_token:
            return HTMLResponse(
                self._render_error("Magento URL and admin token are required."),
                status_code=400,
            )

        # Validate credentials against Magento
        try:
            await self._validate_magento_credentials(magento_url, magento_token)
        except ValueError as exc:
            return HTMLResponse(
                self._render_error(str(exc)),
                status_code=400,
            )

        # Issue auth code JWT
        auth_code = self._engine.create_token(
            token_type=TokenType.AUTH_CODE,
            magento_url=magento_url,
            magento_token=magento_token,
            client_id=client_id,
            scopes=scopes_str.split(),
            extra_claims={
                "code_challenge": code_challenge,
                "redirect_uri": redirect_uri,
            },
        )

        # Build redirect with code + state
        sep = "&" if "?" in redirect_uri else "?"
        redirect = f"{redirect_uri}{sep}code={auth_code}"
        if state:
            redirect += f"&state={state}"
        return RedirectResponse(url=redirect, status_code=302)

    async def _validate_magento_credentials(
        self, magento_url: str, magento_token: str
    ) -> None:
        """Hit ``GET /rest/V1/store/storeConfigs`` to prove the credentials work."""
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    f"{magento_url}/rest/V1/store/storeConfigs",
                    headers={"Authorization": f"Bearer {magento_token}"},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ValueError(
                    f"Magento returned {exc.response.status_code}: "
                    f"check your URL and token"
                ) from exc
            except httpx.RequestError as exc:
                raise ValueError(
                    f"Cannot reach {magento_url}: {exc}"
                ) from exc

    # ── Authorization code lifecycle ────────────────────────────────────

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        """Decode the auth-code JWT. Returns None if expired/invalid."""
        try:
            payload = self._engine.decode_token(
                authorization_code, expected_type=TokenType.AUTH_CODE
            )
        except Exception:
            logger.debug("load_authorization_code: invalid JWT")
            return None

        return AuthorizationCode(
            code=authorization_code,
            client_id=payload["cid"],
            scopes=payload.get("scp", ["mcp"]),
            expires_at=payload["exp"],
            code_challenge=payload["code_challenge"],
            redirect_uri=payload["redirect_uri"],
            redirect_uri_provided_explicitly=True,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Decode auth-code JWT, issue access_token + refresh_token JWTs."""
        payload = self._engine.decode_token(
            authorization_code.code, expected_type=TokenType.AUTH_CODE
        )
        magento_url, magento_token = self._engine.extract_credentials(payload)

        access_token = self._engine.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url=magento_url,
            magento_token=magento_token,
            client_id=client.client_id,
            scopes=payload.get("scp", ["mcp"]),
        )
        refresh_token = self._engine.create_token(
            token_type=TokenType.REFRESH_TOKEN,
            magento_url=magento_url,
            magento_token=magento_token,
            client_id=client.client_id,
            scopes=payload.get("scp", ["mcp"]),
        )

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=TOKEN_TTL[TokenType.ACCESS_TOKEN],
            refresh_token=refresh_token,
            scope=" ".join(payload.get("scp", ["mcp"])),
        )

    # ── Refresh token lifecycle ─────────────────────────────────────────

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        try:
            payload = self._engine.decode_token(
                refresh_token, expected_type=TokenType.REFRESH_TOKEN
            )
        except Exception:
            logger.debug("load_refresh_token: invalid JWT")
            return None

        return RefreshToken(
            token=refresh_token,
            client_id=payload["cid"],
            scopes=payload.get("scp", ["mcp"]),
            expires_at=payload.get("exp"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        payload = self._engine.decode_token(
            refresh_token.token, expected_type=TokenType.REFRESH_TOKEN
        )
        magento_url, magento_token = self._engine.extract_credentials(payload)

        new_scopes = scopes or payload.get("scp", ["mcp"])
        access_token = self._engine.create_token(
            token_type=TokenType.ACCESS_TOKEN,
            magento_url=magento_url,
            magento_token=magento_token,
            client_id=client.client_id,
            scopes=new_scopes,
        )
        new_refresh_token = self._engine.create_token(
            token_type=TokenType.REFRESH_TOKEN,
            magento_url=magento_url,
            magento_token=magento_token,
            client_id=client.client_id,
            scopes=new_scopes,
        )

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=TOKEN_TTL[TokenType.ACCESS_TOKEN],
            refresh_token=new_refresh_token,
            scope=" ".join(new_scopes),
        )

    # ── Access token verification ───────────────────────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Decode + verify an access_token JWT and return a FastMCP AccessToken.

        ``claims`` is populated so ``CurrentAccessToken()`` exposes the
        encrypted Magento URL/key for downstream dependency injection.
        """
        try:
            payload = self._engine.decode_token(
                token, expected_type=TokenType.ACCESS_TOKEN
            )
        except Exception:
            logger.debug("load_access_token: invalid JWT")
            return None

        return AccessToken(
            token=token,
            client_id=payload.get("cid", ""),
            scopes=payload.get("scp", ["mcp"]),
            expires_at=payload.get("exp"),
            claims=payload,
        )

    # ── Revocation ──────────────────────────────────────────────────────

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        """No-op — JWT expiry is the revocation mechanism."""

    # ── Template helpers ────────────────────────────────────────────────

    def _render_error(self, error_message: str) -> str:
        try:
            return _jinja_env.get_template("error.html").render(
                error_message=error_message
            )
        except Exception:
            return self._fallback_error_html(error_message)

    @staticmethod
    def _fallback_authorize_html(ctx: dict[str, str]) -> str:
        return f"""<!DOCTYPE html>
<html><head><title>Authorize — Magento 2 Admin MCP</title>
<style>
body{{font-family:system-ui;max-width:480px;margin:60px auto;padding:0 20px}}
input{{width:100%;padding:8px;margin:4px 0 16px;box-sizing:border-box}}
button{{background:#1a73e8;color:#fff;border:none;padding:10px 24px;
cursor:pointer;border-radius:4px}}
</style></head><body>
<h2>Connect Magento Admin</h2>
<form method="POST">
<input type="hidden" name="client_id" value="{ctx.get('client_id','')}">
<input type="hidden" name="redirect_uri" value="{ctx.get('redirect_uri','')}">
<input type="hidden" name="state" value="{ctx.get('state','')}">
<input type="hidden" name="code_challenge" value="{ctx.get('code_challenge','')}">
<input type="hidden" name="scopes" value="{ctx.get('scopes','mcp')}">
<label>Magento Base URL</label>
<input name="magento_url" placeholder="https://magento.example.com" required>
<label>Admin Token</label>
<input name="magento_token" type="password" required>
<button type="submit">Authorize</button>
</form></body></html>"""

    @staticmethod
    def _fallback_error_html(msg: str) -> str:
        return f"""<!DOCTYPE html>
<html><head><title>Error — Magento 2 Admin MCP</title>
<style>body{{font-family:system-ui;max-width:480px;
margin:60px auto;padding:0 20px;color:#c00}}</style>
</head><body><h2>Authorization Error</h2><p>{msg}</p>
<a href="javascript:history.back()">← Go back</a></body></html>"""
