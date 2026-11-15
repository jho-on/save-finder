# SaveFinder API

API remota do SaveFinder: autenticação e persistência cifrada (E2EE) da biblioteca.

## Requisitos

- Go 1.26+ (ver `go.mod` para a versão mínima)
- Postgres 13+

## Estrutura

```text
cmd/api/        ponto de entrada do binário
internal/       código privado da aplicação
  keys/       metadados E2EE (DEK/KEK) e validação de blobs opacos
  library/    armazenamento E2EE da biblioteca (itens cifrados + thumb)
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
  (`HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`). O banco guarda apenas o
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
| `PUT /auth/account/password` | Troca a senha (re-wrap atômico da DEK, se informado); revoga as demais sessões |
| `GET /auth/crypto` | Metadados da chave E2EE do usuário (404 se ainda não criada) |
| `POST /auth/crypto` | Armazena a DEK cifrada + parâmetros KDF (uma vez por conta; 409 se já existe) |
| `POST /library/items/batch` | Upsert E2EE de até 50 itens (`{item_key, payload[, transcript][, thumb]}` em base64); idempotente |
| `GET /library/items?limit=&cursor=` | Lista paginada (keyset) de `item_key` + `payload` (sem transcript/thumb) |
| `GET /library/items/{key}` | Detalhe de um item (`payload` + `transcript`) |
| `GET /library/items/{key}/thumb` | Bytes cifrados da thumbnail (download opaco) |
| `GET /library/manifest` | Lista leve `(item_key, updated_at)` para caches de cliente |
| `DELETE /library` | Apaga **todos** os itens da biblioteca remota; exige senha atual no body |
| `GET /healthz` / `GET /readyz` | Liveness / readiness (ping no banco) |

As rotas de leitura da biblioteca (`GET /library/*`) usam um limiter dedicado
mais generoso (600 req/min por usuário) para comportar thumbs lazy-loaded;
`DELETE /library` cai no limiter sensível (10 req/min). Todas as respostas de
`/library` vão com `Cache-Control: no-store`. O único campo em claro é
`item_key` (`platform:external_id`), necessário para upsert idempotente e
paginação keyset; todo o resto (payload, transcript, thumb) é ciphertext
XChaCha20-Poly1305 produzido e consumido apenas pelos clientes.

Erros: `{"error":{"code":"...","message":"..."}}` — códigos estáveis:
`validation_failed`, `invalid_credentials`, `email_taken`, `username_taken`,
`unauthorized`, `session_expired`, `csrf_invalid`, `not_found`,
`key_metadata_not_found`, `key_metadata_exists`, `library_item_not_found`,
`rate_limited`, `internal_error`.

## Criptografia ponta a ponta (E2EE)

O servidor **nunca** vê a DEK em texto claro nem os dados descriptografados:
ele guarda apenas metadados opacos em `user_key_metadata` (tabela criada pela
migration `00002`): `salt`, `wrapped_dek` (DEK cifrada pela KEK), `dek_nonce`
(XChaCha20-Poly1305, 24 bytes) e os limites do Argon2id (`kdf_ops_limit`,
`kdf_mem_limit` em bytes). Modelo DEK + KEK: o cliente deriva a KEK da
**senha de login** via Argon2id, gera a DEK aleatória e cifra a DEK com a KEK.
A senha de login nunca é armazenada; ela só vive em memória durante o
login/operações. Se o usuário perder a senha, os dados cifrados são
irrecuperáveis — não há recuperação.

- `POST /auth/crypto` só aceita a primeira gravação (re-setup → 409). Exige a
  senha atual no corpo (`current_password`) para impedir que uma sessão
  roubada sequestre o setup.
- `PUT /auth/account/password` aceita campos opcionais de re-wrap
  (`new_salt`, `new_wrapped_dek`, `new_dek_nonce`, `new_kdf_ops_limit`,
  `new_kdf_mem_limit`). Presentes juntos ou ausentes por completo; o servidor
  aplica a troca de senha e a substituição dos metadados **na mesma
  transação** (users + user_key_metadata), então conta e metadados nunca
  dessincronizam. Os dados do usuário não são re-cifrados (a DEK permanece a
  mesma).

A interface web implementa todo o fluxo de lado do cliente
([`apps/frontend/web`](../frontend/web)).

Documentação dos fluxos: [`docs/adr/0004-e2ee-dek-kek.md`](../../docs/adr/0004-e2ee-dek-kek.md).

## Migrations

Aplicadas automaticamente no startup (goose, embed em `migrations/`).
Para desenvolver: `cp .env.example .env && make dev-db && make run`.
