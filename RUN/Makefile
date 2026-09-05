.PHONY: gp run dry test check balance dashboard

gp:
	git add .
	git commit -m "Update live bot system and web dashboard" || true
	git push

dry:
	python3 main.py --mode DRY_RUN --once

dashboard:
	python3 main.py --mode DRY_RUN --dashboard --port 8090

test:
	pytest tests -v

check:
	python3 scripts/check_connection.py

balance:
	python3 scripts/show_balances.py

run:
	python3 -m pip install --break-system-packages -r RUN/requirements.txt
	python3 main.py --mode DRY_RUN
