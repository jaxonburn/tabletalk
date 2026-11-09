FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services services
RUN uv sync --locked --all-packages --no-dev
ARG APP_VERSION=development
ENV APP_VERSION=${APP_VERSION}
EXPOSE 8000 8001
