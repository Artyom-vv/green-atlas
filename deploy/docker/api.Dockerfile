# syntax=docker/dockerfile:1
FROM python:3.14.3-slim-bookworm@sha256:f21c0d5a44c56805654c15abccc1b2fd576c8d93aca0a3f74b4aba2dc92510e2 AS python-base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

FROM python-base AS api-build
COPY --from=ghcr.io/astral-sh/uv:0.12.11@sha256:79c6f4776b851471cc73b7d21d0cc834bb94383c292e83640d27eff512864df7 /uv /usr/local/bin/uv
WORKDIR /workspace/apps/api
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY apps/api/app ./app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM api-build AS api-test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --group dev --no-editable
COPY apps/api/tests ./tests
COPY fixtures /workspace/fixtures
ENV PATH="/workspace/apps/api/.venv/bin:$PATH" GREEN_ATLAS_LOCAL_MODEL=""
CMD ["python", "-m", "pytest"]

FROM python-base AS api
WORKDIR /workspace/apps/api
COPY --from=api-build /workspace/apps/api/.venv ./.venv
RUN useradd --system --uid 10001 --home-dir /var/lib/green-atlas atlas \
    && install -d -o atlas -g atlas /var/lib/green-atlas
ENV PATH="/workspace/apps/api/.venv/bin:$PATH" \
    GREEN_ATLAS_DB_PATH=/var/lib/green-atlas/projects.sqlite3 \
    GREEN_ATLAS_LOCAL_MODEL=""
USER atlas
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
