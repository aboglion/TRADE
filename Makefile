.PHONY: gp run run_dry run_bg live_bg stop status logs test check balance

gp:
	git add .
	git commit -m "Update trading bot system and web dashboard" || true
	git push

# Foreground execution (interactive)
run_dry:
	python3 RUN/main.py --mode DRY_RUN --dashboard --port 8090

run:
	python3 RUN/main.py --mode LIVE --dashboard --port 8090

# Background execution (24/7 persistent)
run_bg:
	mkdir -p logs
	pkill -f main.py || true
	nohup python3 RUN/main.py --mode DRY_RUN --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "Bot started in DRY_RUN mode in background (Port 8090)"

live_bg:
	mkdir -p logs
	pkill -f main.py || true
	nohup python3 RUN/main.py --mode LIVE --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "Bot started in LIVE mode in background (Port 8090)"

stop:
	pkill -f main.py || true
	@echo "Stopped all bot background processes"

status:
	@ps aux | grep main.py | grep -v grep || echo "No bot process running"

logs:
	tail -n 100 -f logs/bot.log

test:
	pytest RUN/tests -v

check:
	python3 RUN/scripts/check_connection.py

balance:
	python3 RUN/scripts/show_balances.py
