FROM python:3.12-slim AS base

WORKDIR /app

# ── System deps: Node.js (for swagger-typescript-api + esbuild) ────────
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── workerd binary (downloaded from GitHub releases) ───────────────────
RUN ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "arm64" ]; then WORKERD_ARCH="arm64"; else WORKERD_ARCH="64"; fi \
    && curl -fsSL "https://github.com/cloudflare/workerd/releases/latest/download/workerd-linux-${WORKERD_ARCH}.gz" \
       -o /tmp/workerd.gz \
    && gunzip /tmp/workerd.gz \
    && install -m 755 /tmp/workerd /usr/local/bin/workerd \
    && rm -f /tmp/workerd

# ── Pre-install Node tools globally (avoids npx download per request) ──
RUN npm install -g swagger-typescript-api@13 esbuild

# ── Python project ─────────────────────────────────────────────────────
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e ".[prod]"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
