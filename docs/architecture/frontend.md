# Clientes remotos do SaveFinder

Os clientes remotos vivem em [`apps/frontend`](../../apps/frontend).

## Web — `apps/frontend/web`

- Go + [Templ](https://templ.guide).
- Interface web remota, consumindo a API (`apps/api`) por HTTP.
- Estrutura: `cmd/web`, `internal/`, `templates/`.
- Casca única (`/`) com as views da biblioteca (biblioteca, tags, detalhe)
  renderizadas no cliente; DEK só em memória; IndexedDB guarda só ciphertext.
  Ver `apps/frontend/web/README.md`.

## Android — `apps/frontend/android`

- React Native + TypeScript.
- Aplicativo Android, consumindo a API (`apps/api`) por HTTP.


