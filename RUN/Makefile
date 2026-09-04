.PHONY: gp run dry test check balance

gp:
	git add .
	git commit -m "Update live bot system" || true
	git push

dry:
	python3 main.py --mode DRY_RUN --once

test:
	pytest tests -v

check:
	python3 scripts/check_connection.py

balance:
	python3 scripts/show_balances.py

run:
	python3 main.py --mode DRY_RUN
