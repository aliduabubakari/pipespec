.PHONY: help install lint format test sync-schema check-schema-sync validate-examples

help:
	@echo "Targets:"
	@echo "  install            Install dev dependencies"
	@echo "  lint               Ruff lint"
	@echo "  format             Ruff format + autofix"
	@echo "  test               Pytest"
	@echo "  sync-schema        Copy schema/ -> src/pipespec_validator/data/"
	@echo "  check-schema-sync  Ensure bundled schema matches schema/"
	@echo "  validate-examples  Validate schema/examples/*"

install:
	python -m pip install -U pip
	python -m pip install -e ".[dev]"

lint:
	ruff check .

format:
	ruff format .
	ruff check . --fix

test:
	pytest

sync-schema:
	python tools/sync_schema_into_package.py

check-schema-sync:
	python tools/check_schema_sync.py

validate-examples:
	python tools/validate_examples.py

gen-prompt-profile:
	python tools/make_prompt_profile.py

sync-schema:
	python tools/make_prompt_profile.py
	python tools/sync_schema_into_package.py