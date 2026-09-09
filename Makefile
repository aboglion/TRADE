PYTHON := $(shell \
    if [ -n "$$VIRTUAL_ENV" ] && [ -x "$$VIRTUAL_ENV/bin/python3" ]; then echo "$$VIRTUAL_ENV/bin/python3"; \
    elif [ -x /root/TRADE/venv/bin/python3 ]; then echo "/root/TRADE/venv/bin/python3"; \
    elif [ -x "$(CURDIR)/venv/bin/python3" ]; then echo "$(CURDIR)/venv/bin/python3"; \
    elif [ -x "$(CURDIR)/.venv/bin/python3" ]; then echo "$(CURDIR)/.venv/bin/python3"; \
    elif [ -x "$(CURDIR)/../venv/bin/python3" ]; then echo "$(CURDIR)/../venv/bin/python3"; \
    elif [ -x "$(CURDIR)/../.venv/bin/python3" ]; then echo "$(CURDIR)/../.venv/bin/python3"; \
    elif [ -x venv/bin/python3 ]; then echo venv/bin/python3; \
    elif [ -x .venv/bin/python3 ]; then echo .venv/bin/python3; \
    elif command -v python3 >/dev/null 2>&1; then echo python3; \
    else echo python; fi)

.PHONY: gp dry run stop status logs test check balance pull restart restart-dry watch watch-stop watch-status watch-logs

gp:
	git add .
	git commit -m "Update trading bot system and web dashboard" || true
	git push

# 24/7 Background execution modes
dry:
	@mkdir -p logs
	@RUN/scripts/stop_bot.sh 8090 >/dev/null 2>&1 || true
	@RUN/scripts/auto_updater.sh start >/dev/null 2>&1 || true
	@nohup $(PYTHON) RUN/scripts/bot_runner.py --mode DRY_RUN --dashboard --port 8090 >> logs/bot.log 2>&1 &
	@echo "⚡ Bot started in DRY_RUN mode with Emergency Crash Fallback Server (24/7 on port 8090)"

run:
	@mkdir -p logs
	@RUN/scripts/stop_bot.sh 8090 >/dev/null 2>&1 || true
	@RUN/scripts/auto_updater.sh start >/dev/null 2>&1 || true
	@CONFIRM_LIVE=YES_I_UNDERSTAND nohup $(PYTHON) RUN/scripts/bot_runner.py --mode LIVE --dashboard --port 8090 >> logs/bot.log 2>&1 &
	@echo "🔥 Bot started in LIVE mode with Emergency Crash Fallback Server (24/7 on port 8090)"

# Management and Diagnostics
stop:
	@RUN/scripts/stop_bot.sh 8090

status:
	@if [ -f logs/bot.pid ] && kill -0 $$(cat logs/bot.pid 2>/dev/null) 2>/dev/null; then \
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
	$(PYTHON) -m pytest RUN/tests -v

check:
	$(PYTHON) RUN/scripts/check_connection.py

pull:
	@RUN/scripts/safe_pull.sh

pull-safe:
	@RUN/scripts/safe_pull.sh

restart: stop pull-safe
	@MODE=$$(grep -E '^\s*run_mode:' RUN/config.yaml 2>/dev/null | awk '{print $$2}' | tr -d '"' | tr -d "'"); \
	echo "🔄 Detected configured run_mode: $${MODE:-DRY_RUN}"; \
	if [ "$$MODE" = "LIVE" ]; then \
		$(MAKE) run; \
	else \
		$(MAKE) dry; \
	fi

restart-dry: stop pull-safe dry

# Git Auto-Updater Watcher
watch:
	@RUN/scripts/auto_updater.sh start

watch-stop:
	@RUN/scripts/auto_updater.sh stop

watch-status:
	@RUN/scripts/auto_updater.sh status

watch-logs:
	tail -n 100 -f logs/updater.log
