# Makefile for TRADE repository

# Default commit message if not specified (usage: make gp MSG="your message")
MSG ?= update

.PHONY: gp status help

help:
	@echo "Available commands:"
	@echo "  make gp [MSG=\"your message\"]  - Stage all changes, commit, and git push"
	@echo "  make status                    - Run git status"

# Shortcut for Git Add, Commit, and Push
gp:
	git add .
	@git commit -m "$(MSG)" || echo "No changes to commit"
	git push origin main
pg:
	git add .
	@git commit -m "$(MSG)" || echo "No changes to commit"
	git push origin main

status:
	git status
