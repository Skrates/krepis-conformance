# krepis-conformance task runner. `just check` is deterministic and network-free after `uv sync`.
default:
    @just --list

setup sync:
    uv sync

lock-check:
    uv lock --check

fmt:
    uv run ruff format .

fmt-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

lint-fix:
    uv run ruff check . --fix

typecheck:
    uv run ty check

test:
    uv run python -m pytest tests/ -v

# The full gate. CI runs exactly this.
check:
    just lock-check
    just fmt-check
    just lint
    just typecheck
    just test
