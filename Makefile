.PHONY: dev test lint migrate migrate-plan gen-client seed pentest-accounts seed-load load-test new-module install-hooks down update-from-template check-template-drift observe

TEMPLATE_VERSION ?= main

dev:
	# worker's send_email job shells out to this compiled CLI (T-106) --
	# docker-compose.yml bind-mounts dist/ in rather than building it inside
	# the (Python) worker image, so it must already exist on the host.
	pnpm --filter "@forge/email-templates..." build
	docker compose up -d
	@echo "Waiting for backend..."
	@for i in $$(seq 1 30); do \
		curl -sf http://localhost:8000/health >/dev/null 2>&1 && exit 0; \
		sleep 1; \
	done; \
	echo "backend did not become ready" >&2; exit 1
	$(MAKE) migrate
	$(MAKE) seed
	@(command -v open >/dev/null 2>&1 && open https://localhost) || \
	 (command -v xdg-open >/dev/null 2>&1 && xdg-open https://localhost) || \
	 echo "Visit https://localhost (self-signed cert — your browser will warn once)"

down:
	docker compose down

test:
	# No --cov-branch: coverage.py can't pair branch measurement with the
	# sysmon core (set via [tool.coverage.run] core=sysmon in pyproject.toml)
	# before Python 3.14, and without sysmon the legacy tracer loses lines
	# that resume after an `await` crossing SQLAlchemy's async-ORM greenlet
	# bridge -- every line after the first DB call in a router handler shows
	# as "missed" even though it ran. Line coverage stays accurate either way.
	cd backend && pytest --cov=app
	pnpm --filter web test --run
	pnpm --filter @forge/i18n test
	pnpm --filter @forge/email-templates test

lint:
	cd backend && ruff check . && ruff format --check .
	pnpm lint

migrate:
	cd backend && python -m app.core.migrations

migrate-plan:
	cd backend && python -m app.core.migrations plan

gen-client:
	docker compose up -d backend
	@echo "Waiting for backend..."
	@for i in $$(seq 1 30); do \
		curl -sf http://localhost:8000/health >/dev/null 2>&1 && exit 0; \
		sleep 1; \
	done; \
	echo "backend did not become ready" >&2; docker compose down; exit 1
	pnpm --filter @forge/api-client generate; status=$$?; docker compose down; exit $$status

seed:
	cd backend && python -m app.core.seed

pentest-accounts:
	cd backend && python -m app.core.seed_pentest

seed-load:
	cd backend && python -m app.core.seed_load

# T-223: assumes `make dev` is already up. Each script is its own
# `docker compose run` (not one process running all four) so a budget
# failure in one script doesn't hide whether the others also failed --
# see tests/load/README.md for why this runs against a stack you booted
# yourself rather than one this target boots and tears down itself.
load-test: seed-load
	mkdir -p tests/load/results
	@for script in login-burst browse presign mixed; do \
		echo "=== k6: $$script ==="; \
		docker compose run --rm -T k6 run --summary-export=/results/$$script.json /scripts/$$script.js || exit 1; \
	done

new-module:
	@test -n "$(NAME)" || (echo "Usage: make new-module NAME=foo" && exit 1)
	cp -r backend/app/modules/_template backend/app/modules/$(NAME)

install-hooks:
	pre-commit install

observe:
	docker compose --profile observability up -d
	@echo "Grafana: http://localhost:3001 (anonymous admin access, dev only)"

update-from-template:
	git fetch upstream
	git merge upstream/$(TEMPLATE_VERSION)

check-template-drift:
	@git remote get-url upstream >/dev/null 2>&1 || { echo "no 'upstream' remote configured -- nothing to check"; exit 0; }
	@git fetch upstream --quiet
	@git log upstream/main ^HEAD --oneline
