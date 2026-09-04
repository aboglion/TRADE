.PHONY: gp run dry test check balance help

gp:
	git add .
	git commit -m "Update live bot system" || true
	git push

dry:
	python3 RUN/main.py --mode DRY_RUN --once

test:
	pytest RUN/tests -v

check:
	python3 RUN/scripts/check_connection.py

balance:
	python3 RUN/scripts/show_balances.py

run:
	python3 RUN/main.py --mode DRY_RUN
