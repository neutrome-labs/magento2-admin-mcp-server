FROM python:3.12-slim AS base

WORKDIR /app

# Install workerd binary for Mode 2
COPY --from=cloudflare/workerd:latest /usr/local/bin/workerd /usr/local/bin/workerd

# Install project
COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir -e ".[prod]"

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
