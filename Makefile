.PHONY: install dev test lint fmt typecheck check docker memory-sync

install:
	uv sync

dev:
	uv run uvicorn app.main:app --reload --port 8000

test:
	uv run pytest -q

lint:
	uv run ruff check .

fmt:
	uv run ruff format . && uv run ruff check . --fix

typecheck:
	uv run mypy app

check: lint typecheck test
	uv run ruff format --check .

docker:
	docker compose up --build

# Copy Claude's project memory into the repo (.claude/memory/) so it is versioned.
CLAUDE_MEMORY := $(HOME)/.claude/projects/-Users-muhammad-hamza-Desktop-untitled-folder/memory
memory-sync:
	mkdir -p .claude/memory && cp "$(CLAUDE_MEMORY)"/*.md .claude/memory/ && ls .claude/memory
