# Runs over stdio by default (docker run -i); set DISPLAY_MCP_TRANSPORT=streamable-http, or pass
# --transport streamable-http, for an always-on server. See the README.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/frizzy/display-image-mcp" \
      org.opencontainers.image.description="MCP server that renders images for small displays and hosts them over HTTP" \
      org.opencontainers.image.licenses="MIT"
RUN useradd --system --uid 10001 --no-create-home mcp \
 && mkdir /data && chown mcp /data
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DISPLAY_MCP_DATA=/data
USER mcp
VOLUME /data
EXPOSE 8099 8767
ENTRYPOINT ["display-image-mcp"]
