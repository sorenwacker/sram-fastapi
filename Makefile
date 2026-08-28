.PHONY: dev test lint format deploy logs status restart

# Development
dev:
	uv run uvicorn --factory sram_fastapi.demo.app:get_demo_app --reload --port 8124

# Testing
test:
	uv run pytest

test-cov:
	uv run pytest --cov=sram_fastapi --cov-report=term-missing

# Linting
lint:
	uv run ruff check .

format:
	uv run ruff format .

# Deployment
deploy:
	cd ansible && ansible-playbook deploy.yml

deploy-check:
	cd ansible && ansible-playbook deploy.yml --check

# Server management
status:
	ssh sram-demo01.ewi "systemctl status sram-demo"

logs:
	ssh sram-demo01.ewi "journalctl -u sram-demo -n 100 --no-pager"

restart:
	ssh sram-demo01.ewi "sudo systemctl restart sram-demo"
