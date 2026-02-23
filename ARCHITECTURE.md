# Magento 2 Admin MCP Server — Architecture

## Core Principle

This server is an **OAuth 2.0 token proxy**. The standard MCP OAuth flow runs
end-to-end; the authorization screen collects the user's Magento base URL and
admin token; the issued `access_token` **is** an encrypted JWT containing those
credentials. **Zero server-side state** — no dicts, no DB, no sessions.

---

## Auth: OAuthProvider + Encrypted Artifacts

FastMCP ships `OAuthProvider` — an abstract class that implements all OAuth 2.1
endpoints, PKCE, discovery, and dynamic client registration. We subclass it and
implement only the storage methods, backed by **encrypted JWTs instead of a
database**:

```
Client                      MCP Server                   Magento
  |                              |                           |
  |-- POST /register ----------->|  Dynamic Client Reg       |
  |<- client_id, client_secret --|                           |
  |                              |                           |
  |-- GET /authorize ----------->|  Shows HTML form          |
  |                              |  (asks for Magento URL    |
  |                              |   + admin token)          |
  |-- POST /authorize ---------->|  User submits             |
  |   magento_url, magento_token  |                           |
  |                              |-- GET /rest/V1/store/ --->|
  |                              |  (validate credentials)   |
  |                              |<- 200 OK -----------------|
  |                              |                           |
  |                              |  auth_code = JWT {        |
  |                              |    url: Fernet(mgnt_url), |
  |                              |    key: Fernet(mgnt_tok), |
  |                              |    exp: now+5min,         |
  |                              |    client_id, nonce       |
  |                              |  } signed HS256           |
  |<- 302 ?code=<jwt> -----------|                           |
  |                              |                           |
  |-- POST /token (code+PKCE) -->|                           |
  |                              |  Verify JWT sig + exp     |
  |                              |  Verify PKCE S256         |
  |                              |  access_token = JWT {     |
  |                              |    url: Fernet(mgnt_url), |
  |                              |    key: Fernet(mgnt_tok), |
  |                              |    exp: now+24h           |
  |                              |  } signed HS256           |
  |<- access_token=<jwt> --------|                           |
  |                              |                           |
  |-- MCP call (Bearer <jwt>) -->|                           |
  |                              |  Decode JWT               |
  |                              |  Decrypt url+key          |
  |                              |  Call Magento REST/exec   |
  |                              |<--------------------------|
  |<- tool result ---------------|                           |
```

**Key properties:**
- The `access_token` IS the Magento credentials — opaquely encrypted
- No storage needed: `load_access_token()` just decodes and verifies the JWT
- Auth codes (5 min TTL) and access tokens (24 h TTL) are both self-contained JWTs
- Refresh tokens: same pattern, 7 d TTL
- `AES_SECRET` env var is the only server secret

### Encrypted JWT Token Structure

```
Header: { alg: HS256 }
Payload: {
  typ: "ac" | "at" | "rt",       # auth_code / access_token / refresh_token
  url: Fernet(magento_url),       # AES-128-CBC + HMAC-SHA256 encrypted
  key: Fernet(magento_token),     # AES-128-CBC + HMAC-SHA256 encrypted
  sub: sha256(magento_url)[:16],  # non-sensitive identifier
  cid: client_id,
  scp: ["mcp"],
  iat: <unix>,
  exp: <unix>
}
Signature: HMAC-SHA256(header.payload, AES_SECRET)
```

`Fernet` = AES-128-CBC + HMAC-SHA256 from `cryptography`. The outer JWT HS256
signature prevents tampering; the inner Fernet encryption prevents plaintext
credential exposure if a JWT is ever logged or leaked.

---

## Operating Modes

Server operates in exactly **one of two modes**, selected at startup via env vars.
No runtime switching. No fallback chain.

---

### Mode 1 — Featured Tools

Default mode — active whenever `FEATURED_APIS_ALL` is **not** set to `true`.

**Tools exposed:**

| Tool | Purpose |
|------|---------|
| `filter_schema` | Returns a filtered slice of the Magento OpenAPI schema matching a query |
| `fetch` | Issues a single authenticated HTTP request to any Magento REST endpoint |
| `<name>` × N | One typed tool per `FEATURED_APIS_*` entry (zero or more) |

`filter_schema` and `fetch` are always present. `FEATURED_APIS_*` entries are
optional — omitting them gives a minimal two-tool surface; adding entries gives
the LLM fully typed, documented shortcuts for the most-used operations.

**Env var format:**

```
FEATURED_APIS_<priority>=<name>::<method>::<path>
FEATURED_APIS_<priority>_DESCRIPTION=<human description>
FEATURED_APIS_<priority>_PARAMS=<json schema fragment>   # optional
```

Example:

```env
AES_SECRET=<32+ char random string>
# FEATURED_APIS_ALL not set → Mode 1

FEATURED_APIS_100=list_modules::get::/V1/neutromelabs_mcp/modules
FEATURED_APIS_100_DESCRIPTION=List all enabled Magento modules

FEATURED_APIS_200=get_order::get::/V1/orders/{id}
FEATURED_APIS_200_DESCRIPTION=Fetch a single order by ID
FEATURED_APIS_200_PARAMS={"id":{"type":"integer","required":true}}
```

---

### Mode 2 — Execute

Activated by setting `FEATURED_APIS_ALL=true`.

**Tools exposed:**

| Tool | Purpose |
|------|---------|
| `execute` | Runs a TypeScript expression inside a workerd sandbox with full Magento API access |

The LLM writes a TypeScript expression. The workerd sandbox receives:
- `fetchMagento(method, path, body?)` — pre-authenticated Magento REST helper
- `OPENAPI_SCHEMA` — full Magento OpenAPI spec as a JS object (injected at worker
  boot, cached per session)

The sandbox is short-lived (per invocation), memory-isolated, and has no outbound
network access except to the Magento instance associated with the current token.

```env
AES_SECRET=<32+ char random string>
FEATURED_APIS_ALL=true
```

---

## Module Structure

```
src/
├── server.py                   # FastMCP assembly + mode selection
├── settings.py                 # Pydantic Settings from env
│
├── auth/
│   ├── provider.py             # StatelessOAuthProvider(OAuthProvider)
│   │                           #   all abstract methods backed by JWT
│   ├── tokens.py               # TokenEngine: create / decode / verify JWTs
│   └── ui/
│       ├── authorize.html      # Auth form (Magento URL + token input)
│       └── error.html
│
├── magento/
│   ├── client.py               # httpx client factory
│   └── schema.py               # OpenAPI schema fetch + TTL cache (per session)
│
├── providers/
│   └── featured.py             # MagentoFeaturedProvider
│                               #   reads FEATURED_APIS_* → typed Tool objects
│
├── tools/
│   ├── execute.py              # execute() — workerd sandbox entry point (Mode 2)
│   ├── filter_schema.py        # filter_schema() — schema narrowing (Mode 1)
│   └── fetch.py                # fetch() — single authenticated REST call (Mode 1)
│
├── workerd/
│   ├── runner.py               # run_in_workerd(): spawn + communicate + timeout
│   └── worker.js.j2            # Jinja2 template: injects session + schema bindings
│
└── dependencies.py             # Depends() factories:
                                #   get_magento_session() — decrypt JWT claims
                                #   get_magento_client() — pre-authed httpx.AsyncClient
                                #   get_openapi_schema() — fetched + cached per session
```

---

## FastMCP Internals

### StatelessOAuthProvider

```python
class StatelessOAuthProvider(OAuthProvider):

    async def get_client(self, client_id) -> OAuthClientInformationFull | None:
        # client_id is a JWT containing client metadata; decode and return

    async def register_client(self, client_info):
        # no-op: client metadata is encoded into the client_id JWT at registration

    async def authorize(self, client, params) -> str:
        # render authorize.html; on POST: validate Magento creds, issue auth code JWT

    async def load_authorization_code(self, client, code) -> AuthorizationCode | None:
        # decode auth code JWT, verify sig + exp

    async def exchange_authorization_code(self, client, code) -> OAuthToken:
        # decode auth code JWT → issue access_token + refresh_token JWTs

    async def load_refresh_token(self, client, token) -> RefreshToken | None:
        # decode refresh token JWT, verify sig + exp

    async def exchange_refresh_token(self, client, rt, scopes) -> OAuthToken:
        # decode refresh token → issue new access_token JWT

    async def load_access_token(self, token) -> AccessToken | None:
        # decode + verify access_token JWT, populate claims

    async def revoke_token(self, token):
        # no-op: JWT expiry is the revocation mechanism
```

### Dependency Injection

```python
# dependencies.py

async def get_magento_session(
    token: AccessToken = CurrentAccessToken(),
) -> MagentoSession:
    url = fernet.decrypt(token.claims["url"])
    key = fernet.decrypt(token.claims["key"])
    return MagentoSession(url=url, token=key)

async def get_magento_client(
    session: MagentoSession = Depends(get_magento_session),
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=session.url,
        headers={"Authorization": f"Bearer {session.token}"},
    )

async def get_openapi_schema(
    session: MagentoSession = Depends(get_magento_session),
) -> dict:
    return await fetch_and_cache_schema(session)  # TTL cache keyed by session hash
```

Tools declare what they need; the framework resolves it from the current token:

```python
# tools/execute.py
@mcp.tool
async def execute(
    expression: str,
    session: MagentoSession = Depends(get_magento_session),
    schema: dict = Depends(get_openapi_schema),
) -> str:
    return await run_in_workerd(expression, session, schema)

# tools/fetch.py
@mcp.tool
async def fetch(
    method: str,
    path: str,
    body: dict | None = None,
    client: httpx.AsyncClient = Depends(get_magento_client),
) -> str:
    response = await client.request(method, path, json=body)
    response.raise_for_status()
    return response.text

# tools/filter_schema.py
@mcp.tool
async def filter_schema(
    query: str,
    session: MagentoSession = Depends(get_magento_session),
    schema: dict = Depends(get_openapi_schema),
) -> str:
    return extract_relevant_schema_paths(schema, query)
```

### server.py Assembly

```python
mcp = FastMCP(
    "Magento 2 Admin MCP",
    auth=StatelessOAuthProvider(settings),
)

mcp.add_middleware(ErrorHandlingMiddleware())
mcp.add_middleware(RateLimitingMiddleware(max_requests_per_second=settings.rate_limit))
mcp.add_middleware(ResponseLimitingMiddleware(max_size=settings.max_response_bytes))

if settings.featured_apis_all:
    # Mode 2: execute via workerd
    mcp.add_tool(execute_tool)
else:
    # Mode 1: filter_schema + fetch + optional typed tools from FEATURED_APIS_*
    mcp.add_tool(filter_schema_tool)
    mcp.add_tool(fetch_tool)
    if settings.has_featured_apis:
        mcp.add_provider(MagentoFeaturedProvider(settings))

app = FastAPI(lifespan=mcp.http_app(path="/mcp").lifespan)
app.mount("/mcp", mcp.http_app(path="/mcp"))
```

---

## workerd Sandbox (Mode 2)

The workerd process is spawned per `execute` call. The worker module is rendered
from a Jinja2 template and receives:

- `MAGENTO_URL` and `MAGENTO_TOKEN` — injected as environment bindings (never in
  source code, so the LLM expression cannot read them via source inspection)
- `OPENAPI_SCHEMA` — serialised JSON, injected as a text binding
- `fetchMagento(method, path, body?)` — thin wrapper over `fetch()` that sets the
  Authorization header and base URL automatically

Sandbox constraints:
- **No outbound network** except to `MAGENTO_URL`
- **No persistent storage**
- CPU + memory limits enforced by workerd config
- Timeout enforced by `runner.py` via process kill after `WORKERD_TIMEOUT_MS`

```
runner.py
  │
  ├── render worker.js.j2  →  ephemeral worker module (deleted after exec)
  ├── spawn workerd process
  ├── send TypeScript expression over stdin
  ├── read result from stdout (JSON)
  └── kill process on timeout or completion
```

---

## Configuration Reference

```env
# Required in all modes
AES_SECRET=<32+ char random string>

# Mode 2 — set to true to activate Execute (workerd) mode
FEATURED_APIS_ALL=true

# Mode 1 — optional typed tool entries (zero or more; Mode 1 is active when FEATURED_APIS_ALL is unset)
FEATURED_APIS_<n>=<name>::<method>::<path>
FEATURED_APIS_<n>_DESCRIPTION=<text>
FEATURED_APIS_<n>_PARAMS=<json>            # optional

# Mode 2 tunables (workerd)
WORKERD_TIMEOUT_MS=30000                   # default 30 s
WORKERD_MAX_MEMORY_MB=128                  # default 128 MB

# Common tunables
RATE_LIMIT_RPS=10
MAX_RESPONSE_BYTES=5242880                 # default 5 MB
SCHEMA_CACHE_TTL_S=300                     # default 5 min
LOG_LEVEL=INFO
DEBUG=false
```

---

## Deployment

Single Docker image. No external dependencies (no Redis, no DB, no message queue).
The only required secret is `AES_SECRET`. Mode is determined entirely by env vars
present at startup.

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -e ".[prod]"
# workerd binary bundled in image
COPY --from=cloudflare/workerd /usr/local/bin/workerd /usr/local/bin/workerd
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

Scale horizontally without coordination — every replica is stateless.
