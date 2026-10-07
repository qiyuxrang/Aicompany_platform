# syntax=docker/dockerfile:1.7
# Use a licensed official *PostgreSQL* Agent Server image, pinned by digest.
# This image preserves the official API/queue entrypoint; it is not inmem/dev.
ARG AGENT_BASE_IMAGE
FROM ghcr.io/astral-sh/uv:0.11.17@sha256:03bdc89bb9798628846e60c3a9ad19006c8c3c724ccd2985a33145c039a0577b AS uv
FROM ${AGENT_BASE_IMAGE}
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /deps/portal
COPY pyproject.toml uv.lock langgraph.json ./
COPY backend/ ./backend/
COPY model_gateway/ ./model_gateway/
# Keep the production server's constraints. A conflicting lock must fail the
# build; never replace its licensed API with the developer dependency group.
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/portal-requirements.txt \
    && pip install --no-cache-dir -c /api/constraints.txt -r /tmp/portal-requirements.txt
ENV PYTHONPATH=/deps/portal/backend:/deps/portal \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    LANGSERVE_GRAPHS='{"main":"portal.agent_harness:main","researcher":"portal.agent_harness:researcher","reviewer":"portal.agent_harness:reviewer"}' \
    LANGGRAPH_AUTH='{"path":"portal.agent_runtime:auth","disable_studio_auth":true}' \
    LANGGRAPH_HTTP='{"app":"portal.agent_runtime:runtime_app"}' \
    LANGSMITH_TRACING=false \
    LANGCHAIN_TRACING_V2=false \
    LANGGRAPH_METRICS_ENABLED=false \
    LANGGRAPH_LOGS_ENABLED=false
# Database, service identity, license and model credentials are runtime Secrets.
# The base image owns the server CMD/ENTRYPOINT and its drain behavior.
USER 10001:10001
