# Setup de desenvolvimento

Requisitos e instruções por aplicação. A partir da raiz do monorepo, o
`Makefile` delega os comandos para cada app (`make test-local`, `make
test-api`, `make test-web`, `make test-android`, `make build`).

## apps/local — Python

Requisitos: Python 3.11+, `uv` (recomendado) ou `venv` + `pip`, `ffmpeg` e
`ffprobe` para as fases de mídia.

```bash
cd apps/local
uv sync --extra dev          # ou: python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
uv run pytest                # testes
uv run save-finder serve     # http://127.0.0.1:8000
```

Dependências locais de IA (opcionais, extras separados):

```bash
uv sync --all-extras
```

O ambiente virtual (`apps/local/.venv`) e os dados (`apps/local/data/`) são
locais e gitignored. Mais detalhes no [README do app](../../apps/local/README.md)
e no [plano de implementação](implementation-plan.md).

## apps/api — Go

Requisitos: Go 1.26+ (ver `go.mod`).

```bash
cd apps/api
go build ./...
go test ./...
go run ./cmd/api
```

## apps/frontend/web — Go + Templ

Requisitos: Go 1.26+ (ver `go.mod`). A dependência do Templ está fixada em
`tools.go`; o gerador pode ser executado via
`go run github.com/a-h/templ/cmd/templ generate`.

```bash
cd apps/frontend/web
go build ./...
go test ./...
go run ./cmd/web
```

## apps/frontend/android — React Native + TypeScript

Requisitos: Node.js >= 22.11 e npm. Para compilar no Android: JDK 17+,
Android SDK (`ANDROID_HOME`), emulador ou dispositivo físico. Veja o guia
oficial: <https://reactnative.dev/docs/set-up-environment>.

```bash
cd apps/frontend/android
npm install
npm test
npm start           # Metro bundler
npm run android     # compilar e instalar (exige Android SDK)
```

Observação: o SDK do Android não faz parte do repositório; builds Android
locais exigem o ambiente configurado na máquina.
