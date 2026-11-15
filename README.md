# SaveFinder

Biblioteca e classificador de mídia salva em redes sociais (vídeos, imagens e
carrosséis), com o Instagram como primeira fonte. O núcleo é agnóstico de
plataforma. Vídeo, áudio e imagens originais existem apenas em diretórios
temporários; o que persiste são thumbnail, transcrição, embedding, tags e
metadados. Nada é escrito na plataforma de origem.

Este repositório é um **monorepo** com quatro aplicações independentes:

| Aplicação | Tecnologia | Responsabilidade |
|---|---|---|
| [`apps/local`](apps/local) | Python | Aplicação local: CLI e interface web local. |
| [`apps/api`](apps/api) | Go | API remota: autenticação, persistência e sincronização. |
| [`apps/frontend/web`](apps/frontend/web) | Go + Templ | Interface web remota, consumindo a API. |
| [`apps/frontend/android`](apps/frontend/android) | React Native + TypeScript | Aplicativo Android, consumindo a API. |

Não há compartilhamento de código entre as aplicações. A integração entre os
clientes remotos e a API se dá por HTTP.

## Estrutura

```text
apps/
  local/             # Python: CLI + web local (SQLite, FastAPI)
  api/               # Go: API remota (cmd/, internal/, migrations/)
  frontend/
    web/             # Go + Templ: interface web remota
    android/         # React Native + TypeScript: app Android
docs/
  architecture/      # visão geral e documentos por produto
  api/               # referência da API JSON local
  development/       # setup e plano de implementação
```

## Desenvolvimento

Os requisitos e instruções de cada aplicação estão em
[`docs/development/setup.md`](docs/development/setup.md). Comandos comuns pela
raiz:

```bash
make test           # testes de todas as aplicações
make test-local     # testes do Local (pytest)
make test-api       # testes da API (go test)
make test-web       # testes do Web (go test)
make test-android   # testes do Android (jest)
make build          # verificações de build/importação de todas as aplicações
make check          # lint/vet (Local: ruff; Go: go vet + gofmt)
```

## CI e versionamento

Workflows de CI independentes por componente em `.github/workflows/`
(`local`, `api`, `web`), executados em PRs e pushes para `develop`/`main`
com filtros de paths. Não há deploy nem publicação de artefatos nesta etapa.
Versionamento em dois níveis (componente `<nome>/vX.Y.Z` e produto
`save-finder/vX.Y.Z`), documentado em
[`docs/development/versioning.md`](docs/development/versioning.md);
compatibilidade entre componentes em
[`docs/development/compatibility.md`](docs/development/compatibility.md);
composição do produto em [`product.yaml`](product.yaml).

Para o fluxo remoto (API + web) funcionar localmente:

```bash
cd apps/api && cp .env.example .env && make dev-db && make run   # API :8080
cd apps/frontend/web && make run                                 # web :3000
# abra http://localhost:3000 — cadastro + login com E2EE no navegador
```

## Documentação

- [`docs/architecture/overview.md`](docs/architecture/overview.md) — visão geral dos quatro produtos
- [`docs/architecture/local.md`](docs/architecture/local.md) — SaveFinder Local
- [`docs/architecture/api.md`](docs/architecture/api.md) — API remota
- [`docs/adr/README.md`](docs/adr/README.md) — decisões de arquitetura (ADRs)
- [`docs/architecture/frontend.md`](docs/architecture/frontend.md) — clientes remotos
- [`docs/api/api.md`](docs/api/api.md) — referência da API JSON do Local
- [`apps/local/README.md`](apps/local/README.md) — uso da aplicação local

## Restrições do projeto

Valem para o monorepo inteiro; ver [`AGENTS.md`](AGENTS.md):

1. Zero armazenamento permanente de mídia.
2. Somente leitura nas plataformas.
3. Aquisição apenas por exportação oficial ou URL explícita.
4. Taxonomia fechada (`apps/local/config/taxonomy.yaml`).
5. Núcleo agnóstico de plataforma.
6. Conteúdo não é redistribuído.
