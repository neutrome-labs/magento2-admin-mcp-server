# Magento 2 Admin MCP Server

A **stateless OAuth 2.0 token proxy** built on [FastMCP v3](https://gofastmcp.com) that gives LLM clients authenticated access to any Magento 2 REST API.

## Key Properties

- **Zero server-side state** — no database, no Redis, no sessions. The `access_token` *is* the encrypted Magento credentials (Fernet inside HS256 JWT).
- **Two operating modes** — *Featured Tools* (curated REST access) or *Execute* (full workerd sandbox).
- **Scales horizontally** — every replica is stateless. Deploy behind any load balancer.
- **Single secret** — only `AES_SECRET` is required.

## Quick Start

```bash
# Clone and install
git clone https://github.com/neutromelabs/magento2-admin-mcp-server.git
cd magento2-admin-mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env — set AES_SECRET to a random 32+ char string

# Run
AES_SECRET="your-32-char-secret-here-change-me" uvicorn src.server:app --host 0.0.0.0 --port 8000
```

The MCP endpoint is at `http://localhost:8000/mcp`.  
Health check: `GET http://localhost:8000/health`.

## Operating Modes

### Mode 1 — Featured Tools (default)

Active when `FEATURED_APIS_ALL` is **not** set to `true`.

| Tool | Purpose |
|------|---------|
| `filter_schema` | Search the Magento OpenAPI schema for relevant endpoints |
| `fetch` | Execute any authenticated REST request (method + path + body) |
| `<name>` × N | One typed tool per `FEATURED_APIS_*` env var (optional) |

```env
AES_SECRET=<32+ chars>
FEATURED_APIS_100=list_orders::get::/V1/orders
FEATURED_APIS_100_DESCRIPTION=List all orders with optional search criteria
```

### Mode 2 — Execute (workerd sandbox)

Active when `FEATURED_APIS_ALL=true`.

| Tool | Purpose |
|------|---------|
| `execute` | Run TypeScript in an isolated workerd sandbox with `fetchMagento()` and `OPENAPI_SCHEMA` bindings |

```env
AES_SECRET=<32+ chars>
FEATURED_APIS_ALL=true
```

## Auth Flow

```
Client → POST /register → client_id JWT
Client → GET  /authorize → HTML form (Magento URL + token)
Client → POST /authorize/form → validates creds → 302 ?code=<JWT>
Client → POST /token (code + PKCE) → access_token=<JWT>, refresh_token=<JWT>
Client → MCP call (Bearer <JWT>) → decode → decrypt → call Magento → respond
```

All tokens are self-contained JWTs with Fernet-encrypted Magento credentials. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AES_SECRET` | ✅ | — | ≥32 char secret for JWT signing + Fernet key derivation |
| `FEATURED_APIS_ALL` | — | `false` | Set `true` to activate Mode 2 (execute) |
| `FEATURED_APIS_<n>` | — | — | `<name>::<method>::<path>` for typed tools |
| `WORKERD_TIMEOUT_MS` | — | `30000` | Max workerd execution time |
| `WORKERD_MAX_MEMORY_MB` | — | `128` | Max workerd memory |
| `RATE_LIMIT_RPS` | — | `10` | Requests per second limit |
| `MAX_RESPONSE_BYTES` | — | `5242880` | Max response size (5 MB) |
| `SCHEMA_CACHE_TTL_S` | — | `300` | OpenAPI schema cache TTL |
| `LOG_LEVEL` | — | `INFO` | Logging level |
| `HOST` | — | `0.0.0.0` | Bind address |
| `PORT` | — | `8000` | Bind port |

## Docker

```bash
docker build -t magento2-mcp .
docker run -p 8000:8000 -e AES_SECRET="your-secret-here" magento2-mcp
```

## Development

```bash
pip install -e ".[dev]"
pytest                  # run tests
ruff check src/ tests/  # lint
ruff format src/ tests/ # format
```

## License

MIT
