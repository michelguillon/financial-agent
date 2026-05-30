# Personal Finance Agent — runtime image.
# Mirrors the pattern from sibling ../banking/Dockerfile but with no fixed
# ENTRYPOINT so we can `docker compose run` any module ad-hoc during
# development. The Step 5 agent loop will get its own thin CMD later.

FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install deps first so they cache across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project. .dockerignore keeps junk out (finance.db, logs, .env, etc.).
COPY . .

# Non-root user — same shape as banking image.
RUN useradd -m -u 1000 agentuser && \
    chown -R agentuser:agentuser /app
USER agentuser

# No ENTRYPOINT: callers pick a module to run.
#   docker compose run --rm agent python -m agent.tool_registry
#   docker compose run --rm agent python db/migrate.py --replace
#   docker compose run --rm -it agent bash   # interactive
