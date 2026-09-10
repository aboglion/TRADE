PYTHON := $(shell \
    if [ -n "$$VIRTUAL_ENV" ] && [ -x "$$VIRTUAL_ENV/bin/python3" ]; then echo "$$VIRTUAL_ENV/bin/python3"; \
    elif [ -x /root/TRADE/venv/bin/python3 ]; then echo "/root/TRADE/venv/bin/python3"; \
    elif [ -x "$(CURDIR)/../venv/bin/python3" ]; then echo "$(CURDIR)/../venv/bin/python3"; \
    elif [ -x "$(CURDIR)/../.venv/bin/python3" ]; then echo "$(CURDIR)/../.venv/bin/python3"; \
    elif [ -x "$(CURDIR)/venv/bin/python3" ]; then echo "$(CURDIR)/venv/bin/python3"; \
    elif [ -x "$(CURDIR)/.venv/bin/python3" ]; then echo "$(CURDIR)/.venv/bin/python3"; \
    elif [ -x ../venv/bin/python3 ]; then echo ../venv/bin/python3; \
    elif [ -x ../.venv/bin/python3 ]; then echo ../.venv/bin/python3; \
    elif command -v python3 >/dev/null 2>&1; then echo python3; \
    else echo python; fi)

.PHONY: gp dry run stop status logs test check balance pull restart restart-dry

gp:
	git add .
	git commit -m "Update trading bot system and web dashboard" || true
	git push

pull:
	@scripts/safe_pull.sh

pull-safe:
	@scripts/safe_pull.sh

# 24/7 Background execution modes
dry:
	@mkdir -p logs
	@echo "DRY_RUN" > ../logs/last_mode
	@scripts/start_bot.sh DRY_RUN 8090

run:
	@mkdir -p logs
	@echo "LIVE" > ../logs/last_mode
	@scripts/start_bot.sh LIVE 8090

# Management and Diagnostics
stop:
	@scripts/stop_bot.sh 8090

kill:
	@scripts/stop_bot.sh 8090 --hard

status:
	@if [ -f ../logs/bot.pid ] && kill -0 $$(cat ../logs/bot.pid 2>/dev/null) 2>/dev/null; then \
		echo "🟢 Trading bot is running (PID: $$(cat ../logs/bot.pid))"; \
	elif [ -f logs/bot.pid ] && kill -0 $$(cat logs/bot.pid 2>/dev/null) 2>/dev/null; then \
		echo "🟢 Trading bot is running (PID: $$(cat logs/bot.pid))"; \
	elif pgrep -f "(bot_runner\.py|main\.py)" >/dev/null 2>&1; then \
		echo "🟡 Bot process detected:"; \
		ps aux | grep -E "(bot_runner\.py|main\.py)" | grep -v grep; \
	else \
		echo "🔴 No bot process currently running"; \
	fi

logs:
	tail -n 100 -f logs/bot.log

test:
	$(PYTHON) -m pytest tests -v

check:
	$(PYTHON) scripts/check_connection.py

balance:
	$(PYTHON) scripts/show_balances.py

restart: pull-safe
	@MODE=$$(cat ../logs/last_mode 2>/dev/null || grep -E '^\s*run_mode:' config.yaml 2>/dev/null | awk '{print $$2}' | tr -d '"' | tr -d "'"); \
	MODE=$${MODE:-DRY_RUN}; \
	echo "🔄 Detected run mode: $$MODE"; \
	@scripts/stop_bot.sh 8090 --hard >/dev/null 2>&1 || true; \
	if [ "$$MODE" = "LIVE" ]; then \
		$(MAKE) run; \
	else \
		$(MAKE) dry; \
	fi

restart-dry: pull-safe
	@scripts/stop_bot.sh 8090 --hard >/dev/null 2>&1 || true; \
	$(MAKE) dry
