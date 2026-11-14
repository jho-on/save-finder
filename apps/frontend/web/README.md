# SaveFinder Web

Interface web remota do SaveFinder. Aplicação Go com templates
[Templ](https://templ.guide).

## Requisitos

- Go 1.26+ (ver `go.mod`)
- Templ CLI via `go run github.com/a-h/templ/cmd/templ` (dependência fixada em
  `tools.go`)

## Estrutura

```text
cmd/web/       ponto de entrada do binário
internal/      código privado da aplicação
templates/     templates .templ
tools.go       fixa as dependências de ferramenta (templ)
```

## Desenvolvimento

```bash
go build -o /dev/null ./cmd/web
go run ./cmd/web
go test ./...
```
