.PHONY: lint lint-fix test test-all test-integration setup install-hooks clean help install update

help:
	@echo "Available targets:"
	@echo "  make test          - Run unit tests (fast, no external dependencies)"
	@echo "  make test-all      - Run all tests including integration tests"
	@echo "  make test-integration - Run integration tests (requires gog CLI + network)"
	@echo "  make lint          - Run all linters (pytest + skillsaw + claudelint + markdownlint)"
	@echo "  make lint-fix      - Run linters with auto-fix where possible"
	@echo "  make setup         - Install pre-commit hooks"
	@echo "  make install-hooks - Install pre-commit git hooks"
	@echo "  make clean         - Remove generated files"
	@echo "  make install       - Install plugin into Claude Code (first-time setup)"
	@echo "  make update        - Update plugin to latest pushed commit (then restart Claude Code)"

test:
	uv run --with pytest --with pytest-mock pytest tests/ -m "not integration and not vault" -v

test-all:
	uv run --with pytest --with pytest-mock pytest tests/ -v

test-integration:
	uv run --with pytest --with pytest-mock pytest tests/ -m integration -v

lint:
	uvx pre-commit run --all-files

lint-fix:
	npx claude-code-lint check-all --fix || true
	uvx pre-commit run markdownlint --all-files -- --fix || true
	uvx pre-commit run --all-files

setup: install-hooks

install-hooks:
	uvx pre-commit install

install:
	claude plugins marketplace update obsidian-claude-plugins
	claude plugins install obsidian-productivity@obsidian-claude-plugins
	@echo "Restart Claude Code to apply changes."

update:
	claude plugins marketplace update obsidian-claude-plugins
	claude plugins update obsidian-productivity@obsidian-claude-plugins
	@echo "Restart Claude Code to apply changes."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
