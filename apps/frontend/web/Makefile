.PHONY: generate build run test check

generate:
	go run github.com/a-h/templ/cmd/templ generate

build: generate
	go build -o /dev/null ./cmd/web

run: generate
	go run ./cmd/web

test:
	go test ./...

check:
	go vet ./...
	@test -z "$$(gofmt -l .)" || (gofmt -l . && exit 1)
