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
	@nohup python3 RUN/main.py --mode DRY_RUN --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "⚡ Bot started in DRY_RUN mode (24/7 background on port 8090 with AUTO_PULL active)"

run:
	@mkdir -p logs
	@fuser -k 8090/tcp >/dev/null 2>&1 || true
	@RUN/scripts/auto_updater.sh start >/dev/null 2>&1 || true
	@CONFIRM_LIVE=YES_I_UNDERSTAND nohup python3 RUN/main.py --mode LIVE --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "🔥 Bot started in LIVE mode (24/7 background on port 8090 with AUTO_PULL active)"

# Management and Diagnostics
stop:
	@fuser -k 8090/tcp >/dev/null 2>&1 || pkill -f "python3.*main\.py" || true
	@echo "🛑 Stopped all bot background processes"

status:
	@ps aux | grep "python3.*main\.py" | grep -v grep || echo "No bot process currently running"

logs:
	tail -n 100 -f logs/bot.log

test:
	pytest RUN/tests -v

check:
	python3 RUN/scripts/check_connection.py

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

