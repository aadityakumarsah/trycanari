# Stage 1: build the web app with Bun.
FROM oven/bun:1.3 AS web
WORKDIR /build/web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web ./
# build.ts writes to ../src/agentbounty/static/app
RUN bun run build

# Stage 2: the service. One image runs the API, the replay worker and the payment process.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY deploy ./deploy
COPY --from=web /build/src/agentbounty/static/app ./src/agentbounty/static/app
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home app

USER app
EXPOSE 8000
CMD ["uvicorn", "agentbounty.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
