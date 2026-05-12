.PHONY: run test lint format migrate db

run:
	uvicorn app.main:app --reload

test:
	DATABASE_URL=$${DATABASE_URL:-postgresql+asyncpg://floussy:floussy@localhost:5432/floussy_test} pytest

lint:
	ruff check .

format:
	ruff format .

migrate:
	alembic upgrade head

db:
	docker compose up -d
