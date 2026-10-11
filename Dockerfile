# syntax=docker/dockerfile:1

# Two images from one file:
#   python-app  runs the Alarm API simulator, the MCP server and the copilot backend (one image, three commands)
#   gui         serves the built React app with nginx and forwards /api to the backend
# docker-compose.yml picks the target for each service.

# ---------------------------------------------------------------- Python: build
FROM ghcr.io/astral-sh/uv:0.12.13-python3.12-trixie-slim AS python-build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, from the lockfile only, so code changes do not reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Download the embedding model and build the document index now, so a container starts without internet access.
RUN /app/.venv/bin/python -m rag.ingestion

# ---------------------------------------------------------------- Python: run
# Must match the builder's Python, because the virtual environment points at its interpreter.
FROM python:3.12-slim-trixie AS python-app

RUN groupadd --system --gid 999 app \
 && useradd --system --gid 999 --uid 999 --no-create-home app

# Code, model and index stay owned by root (read-only for the app); only the chat store is writable.
COPY --from=python-build /app /app
RUN mkdir -p /app/.data && chown app:app /app/.data

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1

USER app
EXPOSE 8000 9000 8080
CMD ["python", "-m", "copilot"]

# ---------------------------------------------------------------- GUI: build
FROM node:24-alpine AS gui-build

WORKDIR /gui
COPY apps/frontend/package.json apps/frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY apps/frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- GUI: serve
FROM nginx:1.27-alpine AS gui

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=gui-build /gui/dist /usr/share/nginx/html
EXPOSE 80
