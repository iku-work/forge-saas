.PHONY: dev test lint migrate migrate-plan gen-client seed sync-plans pentest-accounts seed-load load-test prod-compose-check prod-compose-tls-check internal-certs rotate-internal-certs check-certs new-module check-docs install-hooks down update-from-template check-template-drift observe

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
	# T-307: apps/marketing is a second host, served from its own bind mount.
	# Only there once `pnpm build` has produced apps/marketing/dist and the
	# site block beside it -- same as apps/web/dist, and silent when the
	# marketing module is off.
	@[ -f apps/marketing/caddy/dev/marketing.caddy ] && \
	 echo "Marketing: https://marketing.localhost (add '127.0.0.1 marketing.localhost' to your hosts file if it does not resolve)" || true

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
	$(MAKE) check-docs

# T-252: ADR status lines, the generated ADR index, and the runbook heading
# structure. `make check-docs ARGS=--write` regenerates docs/adr/README.md's
# index instead of failing on a mismatch -- run it after adding an ADR.
check-docs:
	cd backend && python scripts/check_docs.py $(ARGS)

migrate:
	cd backend && python -m app.core.migrations

migrate-plan:
	cd backend && python -m app.core.migrations plan

# T-313: no Docker, no port, no running backend. The schema is dumped offline
# from an all-modules-on build of the app (backend/scripts/dump_openapi.py),
# so the generated client is the union of every module -- which is what
# apps/web already assumes, since it gates modules at runtime from
# /api/v1/config rather than at compile time. Needs the backend's Python
# dependencies installed; the wrapper prefers backend/.venv and says which
# interpreters it tried if it cannot find one.
gen-client:
	pnpm --filter @forge/api-client generate

seed:
	cd backend && python -m app.core.seed

sync-plans:
	cd backend && python -m app.core.sync_plans

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

# T-244: boots docker-compose.prod.yml -- the only stack that routes traffic
# through pgbouncer -- and runs the same checks CI's pgbouncer-prod-compose
# job does. Overwrites backend/.env, same as `cp .env.e2e .env` does for the
# E2E stack; restore your own afterwards. The driver-level coverage lives in
# backend/tests/test_pgbouncer.py and needs none of this.
prod-compose-check:
	cp backend/.env.prod-compose backend/.env
	node scripts/gen-test-secrets.mjs >> backend/.env
	sed 's/change-me/postgres/' .env.postgres.example > .env.postgres
	sed 's/change-me/postgres/' .env.pgbouncer.example > .env.pgbouncer
	cp .env.postgres-exporter.example .env.postgres-exporter
	sed 's/change-me/a-real-password-here/' .env.minio.example > .env.minio
	docker compose -f docker-compose.prod.yml up -d --build postgres pgbouncer redis backend
	@for i in $$(seq 1 30); do \
		docker compose -f docker-compose.prod.yml exec -T backend \
			python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
			>/dev/null 2>&1 && break; \
		sleep 2; \
	done
	docker compose -f docker-compose.prod.yml exec -T backend python -m app.core.migrations
	docker compose -f docker-compose.prod.yml exec -T backend python -c \
		"import urllib.request; r=urllib.request.urlopen('http://localhost:8000/health/ready'); print(r.status, r.read().decode())"
	docker compose -f docker-compose.prod.yml exec -T pgbouncer sh -c \
		'psql -h 127.0.0.1 -p 6432 -U "$$DB_USER" -d pgbouncer -c "SHOW POOLS"'
	docker compose -f docker-compose.prod.yml down -v

internal-certs:
	bash scripts/gen-internal-certs.sh ./certs

# Replace the internal TLS material on a running stack. Trust widens before
# the certificate changes and narrows after, so no hop ever presents something
# its verifier does not already accept -- see the script's header.
rotate-internal-certs:
	bash scripts/rotate-internal-certs.sh

# Days left on the internal material. Non-zero exit when anything is inside
# WARN_DAYS (default 30) or already expired, so it works as a cron job.
check-certs:
	bash scripts/check-cert-expiry.sh

# The prod stack as it would actually run outside dev: ENVIRONMENT=prod, so
# every startup invariant is live, and TLS on all three container-to-container
# hops. `prod-compose-check` above deliberately stays on ENVIRONMENT=dev to
# isolate pgbouncer's pooling behaviour; this one proves the configuration a
# self-hosted deployment really uses, which nothing did until now.
prod-compose-tls-check: internal-certs
	sed -e 's|^ENVIRONMENT=.*|ENVIRONMENT=prod|' 	    -e 's|^FORCE_HTTPS=.*|FORCE_HTTPS=true|' 	    -e 's|^VIRUS_SCANNER=.*|VIRUS_SCANNER=clamav|' 	    -e 's|^PASSWORD_PEPPER=.*|PASSWORD_PEPPER=local-check-pepper|' 	    -e 's|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://postgres:postgres@pgbouncer:6432/forge?sslmode=require|' 	    -e 's|^MIGRATIONS_DATABASE_URL=.*|MIGRATIONS_DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/forge?sslmode=require|' 	    -e 's|^REDIS_URL=.*|REDIS_URL=rediss://redis:6379/0?ssl_ca_certs=/certs/ca.crt|' 	    backend/.env.prod-compose > backend/.env
	node scripts/gen-test-secrets.mjs >> backend/.env
	sed 's/change-me/postgres/' .env.postgres.example > .env.postgres
	sed 's/change-me/postgres/' .env.pgbouncer.example > .env.pgbouncer
	cp .env.postgres-exporter.example .env.postgres-exporter
	sed 's/change-me/a-real-password-here/' .env.minio.example > .env.minio
	docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml up -d --build 		postgres pgbouncer redis backend
	@for i in $$(seq 1 45); do 		docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml exec -T backend 			python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" 			>/dev/null 2>&1 && break; 		sleep 2; 	done
	docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml exec -T backend 		python -m app.core.migrations
	docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml exec -T backend python -c 		"import urllib.request; r=urllib.request.urlopen('http://localhost:8000/health/ready'); print('health/ready:', r.status, r.read().decode())"
	@echo "--- both hops must report TLS, not just the one the app can see ---"
	docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml exec -T postgres 		psql -U postgres -d forge -tAc 		"SELECT 'pgbouncer->postgres ssl=' || s.ssl || ' ' || coalesce(s.version,'') FROM pg_stat_ssl s JOIN pg_stat_activity a USING (pid) WHERE a.datname='forge' AND a.backend_type='client backend' AND a.client_addr IS NOT NULL LIMIT 1"
	docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml exec -T pgbouncer 		psql "postgres://postgres:postgres@127.0.0.1:6432/pgbouncer?sslmode=require" -tAc "SHOW POOLS"
	docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml down -v

# T-251: all of the logic lives in Python -- validation, substitution, and the
# follow-up checklist's file:line pointers are all things shell would do badly.
# NAME is quoted rather than guarded with `test -n` so an empty one reaches the
# validator and gets the same message as a malformed one.
new-module:
	cd backend && python scripts/new_module.py "$(NAME)"

# Repo finding 16: backend/pyproject.toml declares open-ended minimums, so
# without a lock the same commit resolves differently on different days and
# the Docker image is not reproducible. Two files, because they answer
# different questions: requirements.lock is what ships (runtime only, used
# by backend/Dockerfile with --require-hashes), requirements-dev.lock is
# what CI and a developer install (adds ruff, mypy, pytest at the exact
# versions this tree is verified green against).
#
# uv rather than pip-compile: pip-compile resolves for the platform it runs
# on, and this repo is developed on Windows and deployed on Linux -- a
# platform-specific lock would be wrong for the image it is meant to pin.
# uv resolves universally and emits environment markers.
#
# Run this after any change to pyproject.toml's dependencies, in the same
# commit. Then `pip-audit -r backend/requirements.lock` before pushing: a
# lock's whole value is that it pins something deliberately, and a
# deliberate pin of a vulnerable version is worse than a floating one.
lock:
	cd backend && uv lock --python 3.12
	cd backend && uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.lock
	cd backend && uv export --frozen --extra dev --no-emit-project --format requirements-txt -o requirements-dev.lock

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
