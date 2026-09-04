.PHONY: gp run dry test check balance dashboard

gp:
	git add .
	git commit -m "Update live bot system and web dashboard" || true
	git push

dry:
	python3 RUN/main.py --mode DRY_RUN --once

dashboard:
	python3 RUN/main.py --mode DRY_RUN --dashboard --port 8090

test:
	pytest RUN/tests -v

check:
	python3 RUN/scripts/check_connection.py

balance:
	python3 RUN/scripts/show_balances.py

run:
	python3 RUN/main.py --mode DRY_RUN
