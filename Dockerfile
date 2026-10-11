FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home app

USER app
EXPOSE 8000
CMD ["uvicorn", "agentbounty.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
