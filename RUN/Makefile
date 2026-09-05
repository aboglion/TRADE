.PHONY: gp run live stop status logs test check balance dry

gp:
	git add .
	git commit -m "Update trading bot system and web dashboard" || true
	git push

# Default 24/7 background execution
run:
	@mkdir -p logs
	@pkill -f "python3.*main.py" || true
	@nohup python3 main.py --mode DRY_RUN --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "⚡ Bot started in DRY_RUN mode 24/7 in background on port 8090"
	@echo "👉 Dashboard: http://localhost:8090"

live:
	@mkdir -p logs
	@pkill -f "python3.*main.py" || true
	@nohup python3 main.py --mode LIVE --dashboard --port 8090 > logs/bot.log 2>&1 &
	@echo "🔥 Bot started in LIVE mode 24/7 in background on port 8090"
	@echo "👉 Dashboard: http://localhost:8090"

stop:
	@pkill -f "python3.*main.py" || true
	@echo "🛑 Stopped all bot background processes"

status:
	@ps aux | grep "python3.*main.py" | grep -v grep || echo "No bot process currently running"

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
