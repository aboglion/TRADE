# Makefile for TRADE repository

.PHONY: test oos dashboard clean help gp status

help:
	@echo "Available commands:"
	@echo "  make test       - Run unit and regression tests"
	@echo "  make oos        - Run true leak-free Out-of-Sample validation"
	@echo "  make dashboard  - Build and update the production HTML dashboard"
	@echo "  make clean      - Remove bytecode and temporary cache files"
	@echo "  make gp         - Git push committed changes to origin/main"

test:
	PYTHONPATH=. .venv/bin/pytest tests/

oos:
	.venv/bin/python3 run_true_oos_validation.py

dashboard:
	.venv/bin/python3 generate_dashboard.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

gp:
	git push origin main

status:
	git status
