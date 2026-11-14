# SaveFinder API

API remota do SaveFinder: autenticação, persistência e sincronização.

## Requisitos

- Go 1.26+ (ver `go.mod` para a versão mínima)

## Estrutura

```text
cmd/api/        ponto de entrada do binário
internal/       código privado da aplicação
migrations/     migrations de banco de dados
```

## Desenvolvimento

```bash
go build -o /dev/null ./cmd/api
go run ./cmd/api
go test ./...
```
