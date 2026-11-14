# SaveFinder API

API remota do SaveFinder: autenticação, persistência e sincronização.

## Requisitos

- Go 1.26+ (ver `go.mod` para a versão mínima)
- Postgres 13+

## Estrutura

```text
cmd/api/        ponto de entrada do binário
internal/       código privado da aplicação
  auth/         handlers, middleware e hashing Argon2id
  config/       configuração via variáveis de ambiente
  db/           pool pgx e migrations goose
  httpserver/   router chi, middlewares, /healthz e /readyz
  httpx/        envelope de erro padrão
  ratelimit/    risk score por IP + limite por identidade
  sessions/     sessões server-side (hash do token, expiração deslizante)
  users/        acesso a dados de usuários
  validation/   validação de email, username e senha
migrations/     migrations goose (embed) + spec swaggo em docs/
docs/           especificação OpenAPI gerada pelo swaggo
```

## Desenvolvimento

```bash
cp .env.example .env   # uma vez; .env é local (gitignored)
make dev-db             # Postgres de dev via Docker (localhost:55432, volume savefinder-pgdata)
make run                # aplica migrations e sobe o serviço lendo .env
make test               # testes unitários
make test-integration   # testes com Postgres real (usa TEST_DATABASE_URL ou DATABASE_URL do .env)
make check              # go vet + gofmt
make swag               # regenerar docs/ a partir das anotações
make dev-db-stop        # para o Postgres de dev
make dev-db-reset       # APAGA o Postgres e o volume de dev
```

O binário **não** lê `.env` sozinho; `make run` e `make test-integration` carregam
o arquivo com `set -a; . ./.env`. Se for rodar `go run ./cmd/api` direto,
exporte as variáveis antes.

> Nota: a porta **55432** é usada em dev porque a 5432 (e a 5433) desta máquina
> já estão ocupadas por outros projetos. Em produção o Postgres fica
> instalado diretamente na máquina do host; basta apontar `DATABASE_URL` no
> `.env` de produção (porta 5432) com usuário/senha/banco próprios.

## Variáveis de ambiente

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `DATABASE_URL` | sim | — | DSN do Postgres, ex. `postgres://user:pass@localhost:55432/savefinder?sslmode=disable` |
| `PORT` | não | `8080` | Porta HTTP |
| `LOG_LEVEL` | não | `info` | `debug`, `info`, `warn`, `error` |
| `LOG_FORMAT` | não | `json` | `json` ou `text` |
| `COOKIE_SECURE` | não | `true` | Secure no cookie de sessão; use `false` em dev sem TLS |
| `TRUST_PROXY` | não | `false` | Confiar em `X-Forwarded-For`/`X-Real-IP` (só atrás de proxy reverso confiável) |

## Autenticação

- Senhas: **Argon2id** (19 MiB memória, 2 iterações, paralelismo 1, salt individual),
  conforme recomendação da OWASP. Mínimo de 8 caracteres, sem regras de composição.
- Sessão: opaca (32 bytes, base64url) em cookie `save_finder_session`
  (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`). O banco guarda apenas o
  SHA-256 do token. Múltiplas sessões simultâneas (uma por dispositivo), com
  revogação individual.
- Expiração: padrão **1 dia** de inatividade / **7 dias** absoluto;
  com `remember_me` no login, **7 dias** / **30 dias**. A inatividade é deslizante
  (atualizada por request, com throttle de 60 s).
- CSRF: synchronizer token por sessão, retornado no registro/login e em
  `GET /auth/account`; envie-o em `X-CSRF-Token` em toda mutação autenticada.
- Rate limiting: risk score por IP nas rotas sem identidade (falhas somam pontos,
  decay pela metade a cada 10 min; CGNAT-friendly, sem bloqueio permanente);
  token bucket por identidade nas rotas autenticadas (120 req/min geral,
  10 req/min para troca de senha e exclusão de conta).
- Login: `identifier` aceita email ou username (case-insensitive).

## Rotas

Documentadas via swaggo em [`docs/swagger.yaml`](docs/swagger.yaml) e
servidas com Swagger UI em `/swagger/` quando `cmd/api` roda. Resumo:

| Método e rota | Descrição |
|---|---|
| `POST /auth/register` | Cria conta e já loga (201) |
| `POST /auth/login` | Login com `identifier` + `password`; `remember_me` opcional |
| `POST /auth/logout` | Revoga a sessão atual |
| `POST /auth/logout-all` | Revoga todas as sessões, inclusive a atual |
| `GET /auth/sessions` | Lista sessões ativas |
| `DELETE /auth/sessions/{id}` | Revoga uma sessão |
| `GET /auth/account` | Dados da conta + CSRF token |
| `PATCH /auth/account` | Atualiza email e/ou username |
| `DELETE /auth/account` | Exclui a conta (exige senha atual); dados e sessões caem em cascata |
| `PUT /auth/account/password` | Troca a senha; revoga as demais sessões |
| `GET /healthz` / `GET /readyz` | Liveness / readiness (ping no banco) |

Erros: `{"error":{"code":"...","message":"..."}}` — códigos estáveis:
`validation_failed`, `invalid_credentials`, `email_taken`, `username_taken`,
`unauthorized`, `session_expired`, `csrf_invalid`, `not_found`,
`rate_limited`, `internal_error`.

## Migrations

Aplicadas automaticamente no startup (goose, embed em `migrations/`).
Para desenvolver: `cp .env.example .env && make dev-db && make run`.
