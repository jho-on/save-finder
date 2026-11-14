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

## Local vs. API

O SaveFinder Local tem sua própria API JSON local
([`../api/api.md`](../api/api.md)), servida pelo FastAPI.
