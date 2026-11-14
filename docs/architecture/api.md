# SaveFinder API

Aplicação Go em [`apps/api`](../../apps/api).

## Responsabilidade

- API remota do SaveFinder.
- Autenticação de usuário único.
- Persistência dos dados do usuário no servidor.
- Sincronização entre dispositivos.

## Relação com as demais aplicações

Os clientes (`apps/frontend/web` e `apps/frontend/android`) consomem a API por
HTTP. A API não compartilha código com eles nem com o Local.

## Autenticação

Sessão por cookie opaco com revogação no servidor, Argon2id para senhas e
rate limiting com risk score por IP — decisões em
[`../adr/README.md`](../adr/README.md).

## Local vs. API

O SaveFinder Local tem sua própria API JSON local
([`../api/api.md`](../api/api.md)), servida pelo FastAPI.
