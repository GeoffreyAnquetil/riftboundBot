FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN pip install --no-cache-dir uv==0.13.7

WORKDIR /app

# Dépendances d'abord : cette couche reste en cache tant que uv.lock ne change pas.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --locked --no-dev

RUN useradd --create-home --uid 1000 bot && mkdir -p /app/data && chown bot /app/data
USER bot

ENV DATABASE_URL=sqlite+aiosqlite:////app/data/riftbot.db
VOLUME ["/app/data"]

# SIGINT plutôt que SIGTERM : Python reçoit KeyboardInterrupt, __main__ peut
# fermer proprement la gateway Discord et disposer le moteur SQL.
STOPSIGNAL SIGINT

CMD ["/app/.venv/bin/riftbot"]
