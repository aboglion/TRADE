PYTHON := $(shell if [ -f .venv/bin/python3 ]; then echo .venv/bin/python3; else echo python3; fi)

.PHONY: gp dry run stop status logs test check balance pull restart restart-dry watch watch-stop watch-status watch-logs

gp:
	git add .
	git commit -m "Update trading bot system and web dashboard" || true
	git push

# 24/7 Background execution modes
dry:
	@mkdir -p logs
	@fuser -k 8090/tcp >/dev/null 2>&1 || true
	@RUN/scripts/auto_updater.sh start >/dev/null 2>&1 || true
	@nohup $(PYTHON) RUN/scripts/bot_runner.py --mode DRY_RUN --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "⚡ Bot started in DRY_RUN mode with Emergency Crash Fallback Server (24/7 on port 8090)"

run:
	@mkdir -p logs
	@fuser -k 8090/tcp >/dev/null 2>&1 || true
	@RUN/scripts/auto_updater.sh start >/dev/null 2>&1 || true
	@CONFIRM_LIVE=YES_I_UNDERSTAND nohup $(PYTHON) RUN/scripts/bot_runner.py --mode LIVE --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "🔥 Bot started in LIVE mode with Emergency Crash Fallback Server (24/7 on port 8090)"

# Management and Diagnostics
stop:
	@fuser -k 8090/tcp >/dev/null 2>&1 || pkill -f "python3.*(main\.py|bot_runner\.py)" || true
	@echo "🛑 Stopped all bot background processes"

status:
	@ps aux | grep -E "[p]ython3.*(main\.py|bot_runner\.py)" || echo "No bot process currently running"

logs:
	tail -n 100 -f logs/bot.log

test:
	$(PYTHON) -m pytest RUN/tests -v

check:
	$(PYTHON) RUN/scripts/check_connection.py

pull:
	git pull

restart: stop pull run

restart-dry: stop pull dry

# Git Auto-Updater Watcher
watch:
	@RUN/scripts/auto_updater.sh start

watch-stop:
	@RUN/scripts/auto_updater.sh stop

watch-status:
	@RUN/scripts/auto_updater.sh status

watch-logs:
	tail -n 100 -f logs/updater.log

