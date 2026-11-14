# SaveFinder monorepo — comandos comuns.
# Cada alvo delega para a aplicação correspondente.

.PHONY: test test-local test-api test-web test-android build build-local build-api build-web build-android check check-local check-api check-web

test: test-local test-api test-web test-android

test-local:
	$(MAKE) -C apps/local test

test-api:
	cd apps/api && go test ./...

test-web:
	cd apps/frontend/web && go test ./...

test-android:
	cd apps/frontend/android && npm test -- --ci

build: build-local build-api build-web build-android

build-local:
	cd apps/local && .venv/bin/python -c "import save_finder"

build-android:
	cd apps/frontend/android && npx tsc --noEmit

build-api:
	cd apps/api && go build -o /dev/null ./cmd/api

build-web:
	cd apps/frontend/web && go build -o /dev/null ./cmd/web

# Verificações sem alterar código.
check: check-local check-api check-web

check-local:
	cd apps/local && .venv/bin/ruff check . && .venv/bin/ruff format --check .

check-api:
	cd apps/api && go vet ./...

check-web:
	cd apps/frontend/web && go vet ./...
