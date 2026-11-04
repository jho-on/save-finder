# API — SaveFinder (Fase 5)

Base local: `http://127.0.0.1:8000`

Todos os endpoints retornam JSON. Nenhum endpoint exige autenticação (escopo local, usuário único).

---

## Sistema

### `GET /api/health`

Estado da aplicação.

```json
{"status":"ok","version":"0.1.0","platforms":["instagram"]}
```

---

## Biblioteca

### `GET /api/items`

Lista itens com filtros e paginação.

**Query params:**

- `status` — `pending`, `enriched`, `analyzed`, `classified`, `skipped`, `unavailable`, `error`
- `platform` — slug do adaptador (ex.: `instagram`)
- `tag` — slug da tag; retorna apenas itens com essa tag
- `limit` — máximo 100, padrão 30
- `offset` — padrão 0

```json
{
  "items": [
    {
      "id": 1,
      "platform": "instagram",
      "external_id": "DeJ-...",
      "status": "enriched",
      "post_type": "video",
      "thumb_path": "thumbs/instagram-DeJ-....jpg",
      "tags": [
        {"slug":"video","name":"Vídeo","level":"broad","confidence":0.6,"source":"auto"}
      ]
    }
  ],
  "total": 2182,
  "limit": 30,
  "offset": 0
}
```

`thumb_path` é sempre caminho local (nunca URL de CDN). Se o valor começa com `http`, a UI deve
ignorar. As thumbnails são servidas em `/data/<thumb_path>`.

### `GET /api/items/search`

Busca por `caption`, `author` ou `external_id` (case-insensitive, sem FTS5).

**Query params:** `q` (obrigatório), `status`, `limit` (máx. 100, padrão 20), `offset`.

**Resposta:** mesma estrutura de `/api/items`, com o campo extra `query`.

### `GET /api/items/{item_id}`

Detalhe de um item, incluindo as tags de classificação.

```json
{
  "id": 1,
  "platform": "instagram",
  "external_id": "DeJ-...",
  "url": "https://www.instagram.com/p/DeJ-.../",
  "author": "alice",
  "caption": "...",
  "post_type": "video",
  "media_count": 1,
  "thumb_path": "thumbs/instagram-DeJ-....jpg",
  "tags": [
    {"slug":"comida-e-bebida","name":"Comida e bebida","level":"broad","confidence":0.6,"source":"auto"},
    {"slug":"receitas","name":"Receitas","level":"specific","confidence":0.6,"source":"auto"}
  ]
}
```

Regra de persistência: cada item tem **uma única tag ampla**, sempre acompanhada das específicas
que pertencem a ela. Se `source` == `manual`, a reclassificação automática (`auto`) não sobrescreve.

---

## Pipeline

Cada execução cria um `PipelineJob` persistido e roda em background. O `job_id` retornado é usado
para acompanhar o progresso; jobs interrompidos por reinício do servidor ficam como `interrupted`.

### `POST /api/pipeline/import`

Recebe o ZIP oficial via `multipart/form-data` (`file`) e `platform` (padrão `instagram`).
O upload respeita o limite de tamanho de `config/settings.toml` e é apagado ao final.

```json
{"job_id":"...","status":"started","operation":"import"}
```

### `POST /api/pipeline/enrich`

Enriquece todos os itens `pending` (ou apenas de `platform`). Form field opcional: `platform`.

### `POST /api/pipeline/download`

Baixa itens `enriched` que ainda não têm mídia registrada e roda a análise antes de
apagar o workspace: transcrição do áudio e embedding visual agregado. Os artefatos
persistidos são `transcripts` e `embeddings`; a mídia original não sobrevive à etapa.

### `POST /api/pipeline/classify`

Classifica itens `enriched`/`analyzed` que já têm mídia e ainda não têm tags. Form fields
opcionais: `platform` e `reclassify=true`. Reclassificar usa apenas caption, transcrição e
embedding do banco e substitui somente tags automáticas, preservando as manuais.

### `POST /api/pipeline/run`

Executa as três etapas em sequência contínua.

### `GET /api/pipeline/stats`

Contagens gerais e percentuais por etapa, usando o total de itens como denominador.

### `GET /api/pipeline/{job_id}`

Estado do job (`running`, `done`, `error`, `interrupted`), progresso, resultado e erro.

---

## Interface web

- `/` — apresentação e atalhos.
- `/library` — navegação com busca, filtro por tag e paginação.
- `/tags` — visão agrupada **apenas pelas tags amplas**.
- `/tag/{slug}` — itens de uma tag (ampla ou específica).
- `/pipeline` — importação e execução das etapas.
- `/item/{id}` — detalhe do item e link para a origem.

---

## Restrições e notas

- Nenhum endpoint persiste mídia original. `thumb_path` é o único caminho de imagem servido localmente.
- A busca atual usa `ilike`; FTS5 pode ser adicionada em `repositories/search.py` sem alterar a API.
- A classificação usa LLM (`ollama`) para texto e CLIP (`open-clip`) para visão; sem os
  provedores configurados, `classify/fuse.py` cai na heurística de palavras-chave sem
  alterar os contratos JSON. Específicas só podem vir do texto, e o resultado sempre
  respeita uma única tag ampla.
- Os endpoints usam `app.state.settings`/`app.state.session_factory`, então respeitam o `Settings`
  injetado em `create_app` (incluindo bancos de teste).
