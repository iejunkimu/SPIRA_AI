# SPIRA Server

Owner: Seojin Lee

Reads a shared order, retrieves similar cases from the knowledge base, judges the intent, creates tasks, confirms the plan, and serves the App and Home Hub APIs (S1–S7). See [`spec.md`](spec.md).

## Layout

| Path | What |
| --- | --- |
| `spira_server/` | Application code (FastAPI) |
| `migrations/` | Alembic migrations (DB tables, including `cases`) |
| `config/` | Tables and starting values as YAML (filled in step 4-⑤) |
| `tests/` | Tests |

## Run locally

```bash
cp .env.example .env          # fill in values locally; never commit .env
docker compose up --build     # api on :8000, postgres + pgvector on :5432
docker compose exec api alembic upgrade head
curl localhost:8000/health
```

## Migrations

The same migrations create the `cases` table in two databases.

| Target | Command | URL variable |
| --- | --- | --- |
| Server DB (default) | `alembic upgrade head` | `DATABASE_URL` |
| Knowledge base DB (Neon, owner account, Seojin only) | `alembic -x db=kb upgrade head` | `KB_MIGRATION_URL` |
