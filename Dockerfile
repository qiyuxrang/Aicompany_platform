# syntax=docker/dockerfile:1.7

FROM node:24.14.0-bookworm-slim@sha256:d8e448a56fc63242f70026718378bd4b00f8c82e78d20eefb199224a4d8e33d8 AS frontend-build

WORKDIR /build/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM node:24.14.0-bookworm-slim@sha256:d8e448a56fc63242f70026718378bd4b00f8c82e78d20eefb199224a4d8e33d8 AS mermaid-build
WORKDIR /build/mermaid
RUN corepack enable
COPY backend/portal/product_assets/mermaid-runtime/package.json backend/portal/product_assets/mermaid-runtime/pnpm-lock.yaml ./
RUN pnpm install --prod --frozen-lockfile

FROM ghcr.io/astral-sh/uv:0.11.17@sha256:03bdc89bb9798628846e60c3a9ad19006c8c3c724ccd2985a33145c039a0577b AS uv

FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /root/.cache/uv

# Rich uploads/OCR and Office artifact generation use isolated Python 3.12 runtimes.
COPY backend/portal/source_parsers/requirements.txt /tmp/intake-requirements.txt
COPY backend/portal/product_assets/document-runtime.txt /tmp/document-runtime.txt
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    PORTAL_PRODUCT_PARSER_PYTHON=/opt/product-parser/bin/python \
    PORTAL_PRODUCT_DOCUMENT_PYTHON=/opt/product-documents/bin/python \
    PORTAL_MERMAID_NODE=/usr/local/bin/node \
    PORTAL_MERMAID_CHROMIUM=/usr/bin/chromium
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-noto-cjk libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && uv python install 3.12 \
    && uv venv --python 3.12 /opt/product-parser \
    && uv pip sync --python /opt/product-parser/bin/python /tmp/intake-requirements.txt \
    && uv venv --python 3.12 /opt/product-documents \
    && uv pip sync --python /opt/product-documents/bin/python /tmp/document-runtime.txt \
    && rm -rf /root/.cache/uv

COPY --from=mermaid-build /usr/local/bin/node /usr/local/bin/node
COPY --from=mermaid-build /build/mermaid/node_modules /app/backend/portal/product_assets/mermaid-runtime/node_modules

RUN groupadd --system portal && useradd --system --gid portal --home-dir /app portal
COPY --chown=portal:portal backend/ ./backend/
COPY --chown=portal:portal model_gateway/ ./model_gateway/
COPY --from=frontend-build --chown=portal:portal /build/frontend/dist/ ./frontend/dist/
RUN mkdir -p /app/staticfiles /app/.runtime/product-private /app/.runtime/hr-private \
    && chown portal:portal /app/staticfiles /app/.runtime /app/.runtime/product-private /app/.runtime/hr-private

USER portal
WORKDIR /app/backend
EXPOSE 8100

CMD ["waitress-serve", "--listen=0.0.0.0:8100", "--threads=4", "config.wsgi:application"]
