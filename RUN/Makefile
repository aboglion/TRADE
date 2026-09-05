.PHONY: gp run run_live run_bg live_bg stop status logs dry test check balance

gp:
	git add .
	git commit -m "Update trading bot system and web dashboard" || true
	git push

# Foreground execution
run:
	python3 main.py --mode DRY_RUN --dashboard --port 8090

run_live:
	python3 main.py --mode LIVE --dashboard --port 8090

# Background execution (24/7 persistent)
run_bg:
	mkdir -p logs
	pkill -f main.py || true
	nohup python3 main.py --mode DRY_RUN --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "Bot started in DRY_RUN mode in background (Port 8090)"

live_bg:
	mkdir -p logs
	pkill -f main.py || true
	nohup python3 main.py --mode LIVE --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "Bot started in LIVE mode in background (Port 8090)"

stop:
	pkill -f main.py || true
	@echo "Stopped all bot background processes"

status:
	@ps aux | grep main.py | grep -v grep || echo "No bot process running"

logs:
	tail -n 100 -f logs/bot.log

dry:
	python3 main.py --mode DRY_RUN --once

test:
	pytest tests -v

check:
	python3 scripts/check_connection.py

balance:
	python3 scripts/show_balances.py
