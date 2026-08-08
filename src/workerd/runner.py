"""workerd sandbox runner — generate TS API client from Magento schema, run code.

Flow per ``eval_in_workerd`` call:
1. Fetch the Magento OpenAPI schema **with auth** (bearer token).
2. Generate a typed TypeScript API client via ``swagger-typescript-api``.
3. Bundle entry.ts (user code + generated client) with ``esbuild``.
4. Spin up a workerd isolate, POST to it, capture the response.

Generated API clients are cached per (url, token) hash for ``schema_cache_ttl_s``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from jinja2 import Environment, FileSystemLoader

if TYPE_CHECKING:
    from src.server import MagentoSession
    from src.settings import Settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
)

# ── API client cache ────────────────────────────────────────────────────


@dataclass
class _CachedApi:
    api_ts: str  # Generated MagentoApi.ts source text
    expires_at: float


_api_cache: dict[str, _CachedApi] = {}


def _session_hash(url: str, token: str) -> str:
    """Non-sensitive cache key derived from the Magento URL + token."""
    return hashlib.sha256(f"{url}\x00{token}".encode()).hexdigest()[:16]


# ── Schema fetching ─────────────────────────────────────────────────────


async def _fetch_schema(url: str, token: str) -> dict:
    """Fetch the Magento OpenAPI schema with bearer-token auth."""
    schema_url = f"{url}/rest/all/schema?services=all"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(
            schema_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


# ── API client generation ───────────────────────────────────────────────


async def _generate_api_ts(schema: dict, workdir: str) -> str:
    """Run swagger-typescript-api against *schema* and return MagentoApi.ts."""
    schema_path = os.path.join(workdir, "schema.json")
    with open(schema_path, "w") as f:
        json.dump(schema, f)

    api_output = os.path.join(workdir, "api-out")
    os.makedirs(api_output, exist_ok=True)

    # Prefer globally-installed binary, fall back to npx
    sta = shutil.which("swagger-typescript-api") or shutil.which("sta")
    cmd: list[str] = (
        [sta, "generate"] if sta
        else ["npx", "--yes", "swagger-typescript-api@13", "generate"]
    )
    cmd += [
        "-p", schema_path,
        "-o", api_output,
        "--name", "MagentoApi.ts",
        "--single-http-client",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workdir,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(
            f"swagger-typescript-api exited {proc.returncode}: "
            + stderr.decode(errors="replace")[:3000]
        )

    api_path = os.path.join(api_output, "MagentoApi.ts")
    if not os.path.exists(api_path):
        files = os.listdir(api_output)
        raise RuntimeError(f"MagentoApi.ts not found; generated files: {files}")

    with open(api_path) as f:
        return f.read()


async def _get_api_ts(url: str, token: str, cache_ttl: int) -> str:
    """Return the generated MagentoApi.ts source, using cache when fresh."""
    key = _session_hash(url, token)
    now = time.time()

    entry = _api_cache.get(key)
    if entry and entry.expires_at > now:
        logger.debug("API client cache hit for %s", key)
        return entry.api_ts

    schema = await _fetch_schema(url, token)
    logger.info(
        "Fetched OpenAPI schema for %s (%d paths)",
        key, len(schema.get("paths", {})),
    )

    workdir = tempfile.mkdtemp(prefix="mcp_apigen_")
    try:
        api_ts = await _generate_api_ts(schema, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    _api_cache[key] = _CachedApi(api_ts=api_ts, expires_at=now + cache_ttl)
    logger.info("Generated API client for %s (cached %ds)", key, cache_ttl)
    return api_ts


# ── workerd execution ───────────────────────────────────────────────────


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_capnp_config(
    worker_filename: str,
    magento_url: str,
    magento_token: str,
    port: int,
    max_memory_mb: int,
) -> str:
    # embed paths are resolved relative to the capnp file's directory,
    # so we must use a plain filename (not an absolute path).
    return f'''using Workerd = import "/workerd/workerd.capnp";

const config :Workerd.Config = (
  services = [
    (name = "main", worker = .mainWorker),
    (name = "internet", network = (
      allow = ["public"],
      tlsOptions = (trustBrowserCas = true),
    )),
  ],
  sockets = [
    (name = "http", address = "127.0.0.1:{port}", http = (), service = "main"),
  ],
);

const mainWorker :Workerd.Worker = (
  modules = [
    (name = "worker", esModule = embed "{worker_filename}"),
  ],
  bindings = [
    (name = "MAGENTO_URL", text = "{magento_url}"),
    (name = "MAGENTO_TOKEN", text = "{magento_token}"),
  ],
  globalOutbound = "internet",
  compatibilityDate = "2024-01-01",
);
'''


async def run_in_workerd(
    code: str,
    session: "MagentoSession",
    settings: "Settings",
) -> dict | list | str | None:
    """Execute TypeScript *code* inside an isolated workerd sandbox.

    Returns a parsed Python object (dict, list, str, or None) with
    the result or an error envelope dict.
    """
    workerd_bin = shutil.which("workerd")
    if not workerd_bin:
        # check in the current directory
        workerd_bin = os.path.join(os.getcwd(), "workerd")
        if not os.path.exists(workerd_bin):
            return {
                "error": "workerd binary not found on PATH",
                "hint": "Install workerd or add it to your Docker image",
            }

    # ── 1. Obtain typed API client (cached) ─────────────────────────────
    try:
        api_ts = await _get_api_ts(
            session.url, session.token, settings.schema_cache_ttl_s,
        )
    except Exception as exc:
        logger.exception("API client generation failed")
        return {"error": f"API client generation failed: {exc}"}

    tmpdir = tempfile.mkdtemp(prefix="mcp_workerd_")
    try:
        # ── 2. Write MagentoApi.ts ──────────────────────────────────────
        with open(os.path.join(tmpdir, "MagentoApi.ts"), "w") as f:
            f.write(api_ts)

        # ── 3. Render entry.ts from Jinja2 template ────────────────────
        entry_ts = _jinja_env.get_template("entry.ts.j2").render(code=code)
        entry_path = os.path.join(tmpdir, "entry.ts")
        with open(entry_path, "w") as f:
            f.write(entry_ts)

        # ── 4. Bundle with esbuild ──────────────────────────────────────
        bundle_path = os.path.join(tmpdir, "worker.js")
        esbuild = shutil.which("esbuild")
        esbuild_cmd: list[str] = (
            [esbuild] if esbuild
            else ["npx", "--yes", "esbuild"]
        )
        esbuild_cmd += [
            entry_path,
            "--bundle",
            f"--outfile={bundle_path}",
            "--format=esm",
            "--target=esnext",
            "--platform=browser",
        ]

        proc = await asyncio.create_subprocess_exec(
            *esbuild_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmpdir,
        )
        es_out, es_err = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {
                "error": "esbuild bundle failed",
                "detail": es_err.decode(errors="replace")[:2000],
            }

        # ── 5. Prepare workerd config ───────────────────────────────────
        port = _find_free_port()
        config = _build_capnp_config(
            worker_filename=os.path.basename(bundle_path),
            magento_url=session.url,
            magento_token=session.token,
            port=port,
            max_memory_mb=settings.workerd_max_memory_mb,
        )
        config_path = os.path.join(tmpdir, "config.capnp")
        with open(config_path, "w") as f:
            f.write(config)

        # ── 6. Start workerd, send request, capture response ────────────
        timeout_s = settings.workerd_timeout_ms / 1000.0
        wd_proc = await asyncio.create_subprocess_exec(
            workerd_bin, "serve", config_path, "--experimental",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmpdir,
        )

        output: str = ""
        try:
            # Poll until workerd is ready (or crashes)
            deadline = time.monotonic() + min(timeout_s, 10)
            connected = False
            last_err: Exception | None = None
            while time.monotonic() < deadline:
                if wd_proc.returncode is not None:
                    break  # crashed
                try:
                    async with httpx.AsyncClient(timeout=timeout_s) as client:
                        resp = await client.post(
                            f"http://127.0.0.1:{port}/",
                            headers={"Content-Type": "application/json"},
                        )
                        output = resp.text.strip()
                        connected = True
                        break
                except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                    last_err = exc
                    await asyncio.sleep(0.15)

            if not connected:
                # Collect stderr for diagnostics
                try:
                    wd_proc.terminate()
                    _, stderr_data = await asyncio.wait_for(
                        wd_proc.communicate(), timeout=5
                    )
                except Exception:
                    stderr_data = b""
                err_txt = stderr_data.decode(errors="replace")[:2000] if stderr_data else str(last_err)
                return {
                    "error": "Failed to connect to workerd",
                    "detail": err_txt,
                }
        finally:
            # Always clean up the workerd process
            try:
                wd_proc.terminate()
                await asyncio.wait_for(wd_proc.wait(), timeout=5)
            except ProcessLookupError:
                pass  # Process already exited
            except Exception:
                try:
                    wd_proc.kill()
                except ProcessLookupError:
                    pass  # Process already exited
                try:
                    await wd_proc.wait()
                except Exception:
                    pass

        if not output:
            return None

        # ── 7. Guard against oversized responses ───────────────────────
        # Truncate BEFORE FastMCP's ResponseLimitingMiddleware sees the
        # result — that middleware destroys JSON structure on truncation,
        # leaving the AI with an opaque error. Instead we return a clear
        # error dict with a hint to paginate.
        max_bytes = settings.max_response_bytes
        if len(output.encode("utf-8", errors="replace")) > max_bytes:
            # Try to extract a useful preview (first N items if it's a list)
            preview = output[:2000]
            return {
                "error": "Response too large",
                "bytes": len(output.encode("utf-8", errors="replace")),
                "limit": max_bytes,
                "hint": (
                    "The response exceeded the size limit. "
                    "Do NOT return raw Magento API responses. "
                    "Process the data inside the sandbox and return ONLY "
                    "the minimal fields needed for the task. Example: "
                    "const res = await api.v1.someMethod({...}); "
                    "return { total: res.data?.total_count, "
                    "items: res.data?.items?.map(i => ({ sku: i.sku, name: i.name })) };"
                ),
                "preview": preview,
            }

        # Parse JSON into native Python objects to avoid double serialization.
        # FastMCP will set structuredContent for dict returns automatically.
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return {"result": output}

        # Wrap non-dict results so FastMCP always gets a dict for structuredContent
        if isinstance(parsed, dict):
            return parsed
        return {"result": parsed}

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
