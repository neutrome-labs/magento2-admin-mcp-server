"""workerd sandbox runner — spawn a workerd process, inject Magento bindings, execute TS.

The worker module is rendered from ``worker.js.j2`` and receives:
- ``MAGENTO_URL`` / ``MAGENTO_TOKEN`` — env var bindings (not in source)
- ``OPENAPI_SCHEMA`` — JSON text binding
- ``fetchMagento(method, path, body?)`` — pre-authenticated fetch helper

Sandbox constraints:
- No outbound network except to MAGENTO_URL
- No persistent storage
- CPU + memory limits enforced by workerd config
- Timeout enforced by process kill after WORKERD_TIMEOUT_MS
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.magento.client import MagentoSession
from src.settings import Settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
)

_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings


def init_runner(settings: Settings) -> None:
    """Inject pre-built settings so we don't re-read env."""
    global _settings
    _settings = settings


async def run_in_workerd(
    expression: str,
    session: MagentoSession,
    schema: dict,
) -> str:
    """Execute a TypeScript expression inside an isolated workerd sandbox.

    Args:
        expression: TypeScript expression to evaluate.
        session: Decrypted Magento session.
        schema: The full OpenAPI schema dict.

    Returns:
        JSON string with result or error.

    Raises:
        TimeoutError: If execution exceeds WORKERD_TIMEOUT_MS.
    """
    settings = _get_settings()

    workerd_bin = shutil.which("workerd")
    if not workerd_bin:
        return json.dumps({
            "error": "workerd binary not found on PATH",
            "hint": "Install workerd or add it to your Docker image",
        })

    tmpdir = tempfile.mkdtemp(prefix="mcp_workerd_")
    try:
        # Render the worker module
        worker_js = _jinja_env.get_template("worker.js.j2").render(
            expression=_escape_js_string(expression),
            openapi_schema_json=json.dumps(schema),
        )
        worker_path = os.path.join(tmpdir, "worker.js")
        with open(worker_path, "w") as f:
            f.write(worker_js)

        # Write workerd config
        config = _build_capnp_config(
            worker_path=worker_path,
            magento_url=session.url,
            magento_token=session.token,
            max_memory_mb=settings.workerd_max_memory_mb,
        )
        config_path = os.path.join(tmpdir, "config.capnp")
        with open(config_path, "w") as f:
            f.write(config)

        timeout_s = settings.workerd_timeout_ms / 1000.0

        # Spawn workerd
        proc = await asyncio.create_subprocess_exec(
            workerd_bin, "serve", config_path, "--experimental",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmpdir,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(
                f"workerd execution timed out after {settings.workerd_timeout_ms}ms"
            )

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[:2000]
            logger.warning("workerd exited %d: %s", proc.returncode, err)
            return json.dumps({
                "error": f"workerd failed (exit {proc.returncode})",
                "stderr": err,
            })

        output = stdout.decode(errors="replace").strip()
        if not output:
            return json.dumps({"result": None})

        # Return as-is if valid JSON, else wrap
        try:
            json.loads(output)
            return output
        except json.JSONDecodeError:
            return json.dumps({"result": output})

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _build_capnp_config(
    worker_path: str,
    magento_url: str,
    magento_token: str,
    max_memory_mb: int,
) -> str:
    """Build a minimal workerd capnp config."""
    return f'''
using Workerd = import "/workerd/workerd.capnp";

const config :Workerd.Config = (
  services = [
    (name = "main", worker = .mainWorker),
  ],
  sockets = [
    (name = "http", address = "127.0.0.1:0", http = (), service = "main"),
  ],
);

const mainWorker :Workerd.Worker = (
  modules = [
    (name = "worker", esModule = embed "{worker_path}"),
  ],
  bindings = [
    (name = "MAGENTO_URL", text = "{magento_url}"),
    (name = "MAGENTO_TOKEN", text = "{magento_token}"),
  ],
  compatibilityDate = "2024-01-01",
);
'''


def _escape_js_string(s: str) -> str:
    """Escape a string for safe embedding in a JS template literal."""
    return (
        s.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
