.PHONY: setup generate up test down demo admin
setup:
	python3 scripts/setup.py
generate:
	python scripts/generate.py
up:
	docker compose up --build -d --wait
test:
	docker compose -p marketplace-tests -f compose.yaml -f compose.test.yaml up -d --wait db
	docker compose -p marketplace-tests -f compose.yaml -f compose.test.yaml run --rm migrate
	docker compose -p marketplace-tests -f compose.yaml -f compose.test.yaml run --build --rm --no-deps api
down:
	docker compose down
demo:
	python3 scripts/demo.py
admin:
	docker compose exec api python -m app.create_admin
