"""Alembic environment.

Target DB is chosen with `-x db=...`:
  (default)  DATABASE_URL       server DB
  db=kb      KB_MIGRATION_URL   knowledge base DB, owner account (Seojin only)
URLs come from the environment or a local .env file and are never written to files in git.
"""

import os
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _url() -> str:
    _load_dotenv()
    target = context.get_x_argument(as_dictionary=True).get("db", "server")
    name = {"server": "DATABASE_URL", "kb": "KB_MIGRATION_URL"}.get(target)
    if name is None:
        raise SystemExit(f"Unknown -x db={target}. Use 'server' or 'kb'.")
    url = os.environ.get(name)
    if not url:
        raise SystemExit(f"{name} is not set. Put it in server/.env (never commit it).")
    # psycopg 3 driver
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
