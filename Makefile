.PHONY: gp run live stop status logs test check balance

gp:
	git add .
	git commit -m "Update trading bot system and web dashboard" || true
	git push

# Default 24/7 background execution
run:
	@mkdir -p logs
	@fuser -k 8090/tcp >/dev/null 2>&1 || true
	@nohup python3 RUN/main.py --mode DRY_RUN --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "⚡ Bot started in DRY_RUN mode 24/7 in background on port 8090"

live:
	@mkdir -p logs
	@fuser -k 8090/tcp >/dev/null 2>&1 || true
	@nohup python3 RUN/main.py --mode LIVE --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "🔥 Bot started in LIVE mode 24/7 in background on port 8090"

stop:
	@fuser -k 8090/tcp >/dev/null 2>&1 || pkill -f "python3.*main\.py" || true
	@echo "🛑 Stopped bot process"

status:
	@ps aux | grep "python3.*main\.py" | grep -v grep || echo "No bot process currently running"

logs:
	tail -n 100 -f logs/bot.log

test:
	pytest RUN/tests -v

check:
	python3 RUN/scripts/check_connection.py

balance:
	python3 RUN/scripts/show_balances.py
