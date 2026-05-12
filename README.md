# Floussy Backend

Production-ready FastAPI skeleton for a personal finance app.

## Requirements
- Python 3.11+
- Docker (for Postgres)

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

docker compose up -d
alembic upgrade head
uvicorn app.main:app --reload
```

## Tests

```bash
createdb floussy_test 2>/dev/null || true
DATABASE_URL=postgresql+asyncpg://floussy:floussy@localhost:5432/floussy_test pytest
```

Important safety note:
- Never run tests against your local app database (for example `.../floussy`).
- The test suite truncates tables by design and must use a dedicated test database.

## Useful commands

```bash
make run
make test
make migrate
```
