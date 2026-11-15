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

Requisitos: Go 1.26+ (ver `go.mod`) e Postgres (Docker para dev).

O binário **não** lê `.env` sozinho; `make run` e `make test-integration`
carregam o arquivo com `set -a; . ./.env`. Configuração via `apps/api/.env`
(gitignored; modelo em `.env.example`):

```bash
cd apps/api
cp .env.example .env    # uma vez
make dev-db             # Postgres descartável em localhost:55432 (volume savefinder-pgdata)
make run                # http://localhost:8080 — aplica migrations e sobe
```

- API: http://localhost:8080 (`/healthz`, `/readyz`, Swagger UI em `/swagger/`).
- Testes: `make test` (unitários) · `make test-integration` (contra o Postgres
  de dev; `TEST_DATABASE_URL` ou `DATABASE_URL`).
- `make dev-db-stop` para o container · `make dev-db-reset` apaga container e volume.
- Produção: Postgres instalado na máquina do host; basta apontar `DATABASE_URL`
  no `.env` de produção (porta 5432). Nada de Docker em produção.
- Migrations goose são aplicadas automaticamente no startup.

Mais detalhes no [README da API](../../apps/api/README.md).

## apps/frontend/web — Go + Templ

Requisitos: Go 1.26+ (ver `go.mod`). A dependência do Templ está fixada em
`tools.go`; o gerador pode ser executado via
`go run github.com/a-h/templ/cmd/templ generate`.

O web **não** fala direto com o banco: serve as páginas + estáticos e faz
reverse proxy de `/auth/*` → a API remota (`API_UPSTREAM`, padrão
`http://localhost:8080`). Sessão e CSRF fluem same-origin, sem CORS.

```bash
cd apps/frontend/web
make run       # http://localhost:3000 (já com a API rodando em :8080)
make test
```

Variáveis: `PORT` (3000), `API_UPSTREAM` (http://localhost:8080), `LOG_FORMAT`.

## Rodando tudo junto (dev)

```bash
cd apps/api && make dev-db && make run          # terminal 1 — API em :8080
cd apps/frontend/web && make run                 # terminal 2 — web em :3000
```

Abra <http://localhost:3000>: tela de login/cadastro com a identidade Protea.
Cadastro cria a conta + a DEK no navegador (E2EE); a senha de login também
deriva a chave que cifra os dados sobretudo no cliente.

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
