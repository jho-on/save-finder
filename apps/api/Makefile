.PHONY: build run test test-integration check swag dev-db dev-db-stop dev-db-reset

DATABASE_URL ?= postgres://postgres:postgres@localhost:55432/savefinder?sslmode=disable

build:
	go build -o /dev/null ./cmd/api

# Sobe o serviço lendo .env, se existir (o binário não lê .env sozinho).
run:
	@set -a; [ -f .env ] && . ./.env; set +a; go run ./cmd/api

test:
	go test ./...

test-integration:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	TEST_DATABASE_URL="$${TEST_DATABASE_URL:-$$DATABASE_URL}" go test ./...

check:
	go vet ./...
	@test -z "$$(gofmt -l .)" || (gofmt -l . && exit 1)

swag:
	go run github.com/swaggo/swag/cmd/swag@latest init -g cmd/api/main.go --output docs

# Postgres descartável para desenvolvimento local (dados num volume nomeado).
dev-db:
	@docker start savefinder-api-dev 2>/dev/null || \
		docker run -d --name savefinder-api-dev \
			-e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=savefinder \
			-p 55432:5432 -v savefinder-pgdata:/var/lib/postgresql/data \
			postgres:16-alpine
	@echo "Postgres de dev em localhost:55432 (usuário postgres, senha postgres, banco savefinder)"

dev-db-stop:
	docker stop savefinder-api-dev

# Apaga TODOS os dados de dev.
dev-db-reset:
	docker rm -f savefinder-api-dev
	docker volume rm savefinder-pgdata
