# AGENTS.md

Guia operacional para agentes de código que trabalharem neste repositório.
Leia também [`docs/implementation-plan.md`](docs/implementation-plan.md) antes de implementar qualquer fase.
O cronograma, as metas e o rumo futuro ficam em `docs/roadmap.local.md`, que não é versionado e pode não existir em outra máquina.

## Visão geral

**SaveFinder** é uma biblioteca local e um classificador de mídia salva em redes sociais — vídeos, imagens e carrosséis. O Instagram é a primeira fonte, mas o núcleo é agnóstico de plataforma.

O sistema lê exportações oficiais, enriquece cada link com metadados e thumbnail, baixa toda a mídia do post para um diretório **temporário**, analisa áudio e imagem, grava as tags na base local e apaga os arquivos. A interface é uma web app local com busca, filtros e um link para abrir o post na rede de origem.

A mídia nunca é arquivada. O que persiste é thumbnail, transcrição, embedding, tags e metadados.


## Restrições

Valem em qualquer versão do projeto:

1. **Zero armazenamento permanente de mídia.** Vídeo, áudio e imagens originais existem apenas em `data/tmp/`, com remoção garantida em `finally`, inclusive em erro, timeout ou Ctrl-C. Thumbnail é a única imagem que persiste.
2. **Somente leitura nas plataformas.** Nada de login, senha, sessão autenticada, API privada, curtir, salvar, criar coleção ou remover itens.
3. **Aquisição apenas por meios suportados.** Exportação oficial ou entrada explícita de URL (`source=share|manual`). Nada de scraping de páginas ou endpoints internos.
4. **Taxonomia fechada.** O classificador só pode emitir tags presentes em `config/taxonomy.yaml`.
5. **Núcleo agnóstico de plataforma.** Nenhum código específico de uma rede fora de `src/save_finder/platforms/<slug>.py`.
6. **Conteúdo não é redistribuído.** Sem recursos de publicação ou compartilhamento do material baixado.

O escopo da versão atual (local, um único usuário, um dispositivo) está definido no plano e pode mudar. A evolução prevista é o mesmo usuário em vários dispositivos — não multiusuário. Autenticação de usuário único e acesso remoto devem entrar como camadas; multi-tenancy está fora de escopo.

Se uma tarefa exigir quebrar qualquer um desses pontos, pare e registre a decisão no plano antes de codar.

## Arquitetura

```
export oficial                        (futuro: share / URL manual)
   │
   ▼
[adapter da plataforma]  parse_export → itens normalizados
   │
   ▼
[core]  import → enrich → download temporário → análise → classificação
   │                         │
   │                         └─ mídia apagada após o processamento
   ▼
[core]  SQLAlchemy + thumbnails + tags + transcript + embedding
   │
   ▼
[web]   FastAPI: API JSON + Jinja/HTMX
```

O `core` não conhece Instagram, YouTube nem TikTok. Ele fala com um `PlatformAdapter`, que resolve três coisas: converter a exportação em itens, normalizar URL/ID e obter metadados/mídia.

## Stack

| Camada | Escolha |
|---|---|
| Linguagem | Python 3.11 |
| CLI | Typer + Rich |
| Banco | SQLAlchemy 2.0 + Alembic; SQLite (FTS5) por padrão, Postgres depois |
| Web | FastAPI: API JSON + Jinja2/HTMX, CSS próprio |
| Download | yt-dlp (fallback: gallery-dl) |
| Mídia | ffmpeg (libx264, libwebp) |
| ASR | `ASRProvider`; faster-whisper como padrão local |
| Visão | `VisionProvider`; open-clip `ViT-B-32` como padrão local |
| LLM | `LLMProvider`; Ollama como padrão local |
| Testes | pytest + ruff |

Modelos, providers e versões ficam em `config/settings.toml`. Sem build step de frontend; Node não é usado no runtime.

## Estrutura do repositório

```
.
├── AGENTS.md
├── README.md
├── pyproject.toml
├── alembic.ini
├── .env.example
├── docs/
│   ├── implementation-plan.md
│   ├── taxonomy.md
│   └── roadmap.local.md   # gitignored
├── config/
│   ├── settings.toml
│   └── taxonomy.yaml
├── data/                  # gitignored
│   ├── save-finder.db
│   ├── thumbs/
│   └── tmp/
├── migrations/            # Alembic
│   ├── env.py
│   └── versions/
├── scripts/
│   └── spike.py
├── src/save_finder/
│   ├── cli.py
│   ├── config.py
│   ├── db.py              # engine e sessão
│   ├── models.py          # modelos SQLAlchemy
│   ├── repositories/      # acesso a dados
│   │   ├── items.py
│   │   ├── tags.py
│   │   └── search.py      # FTS5 isolado
│   ├── providers/         # Protocols + implementações locais
│   │   ├── base.py
│   │   ├── asr.py
│   │   ├── vision.py
│   │   └── llm.py
│   ├── pipeline.py
│   ├── platforms/
│   │   ├── base.py        # PlatformAdapter + YtDlpAdapter
│   │   ├── registry.py
│   │   └── instagram.py
│   ├── enrich.py
│   ├── download.py
│   ├── media.py
│   ├── classify/
│   │   ├── taxonomy.py
│   │   └── fuse.py
│   └── web/
│       ├── app.py
│       ├── api.py         # rotas JSON
│       ├── templates/
│       └── static/
└── tests/
    └── fixtures/
```

## Comandos

```bash
uv sync                      # alternativa: python -m venv .venv && pip install -e ".[dev]"

uv run save-finder db init
uv run save-finder import instagram ~/Downloads/instagram-*/your_instagram_activity/saved
uv run save-finder enrich --limit 30
uv run save-finder analyze --limit 5
uv run save-finder classify --limit 5
uv run save-finder pipeline --limit 30      # enrich + analyze + classify
uv run save-finder serve                    # http://127.0.0.1:8000
uv run save-finder stats

uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

## Invariantes de dados

- A chave de um item é `(platform, external_id)`, **única**. Todo o pipeline deduplica por ela.
- `platform` é o slug do adaptador (`instagram`, futuramente `youtube`, `tiktok`...).
- `source` ∈ `export | share | manual`.
- Cada adaptador normaliza a URL canônica e descarta query string de rastreio.
- Datas em ISO 8601 UTC, string.
- `thumb_path` é sempre caminho local. **Nunca** guardar URL de CDN: ela expira em dias e a UI quebra.
- `items.status` ∈ `pending | enriched | analyzed | classified | skipped | unavailable | error`.
  - `skipped`: post sem mídia analisável. Imagem e carrossel **não** são ignorados; só o são quando o downloader não expõe nenhuma peça.
  - `unavailable`: deletado, privado ou bloqueado por região.
  - `error`: falha nossa; guarda a mensagem em `error`.
- `item_tags.source` ∈ `auto | manual`. Correção manual nunca é sobrescrita por classificação automática.
- Persistir `transcripts` e `embeddings` é obrigatório: permite reclassificar e buscar sem baixar a mídia de novo.
- Toda operação de lote é **idempotente e retomável**.

## Regras do pipeline

- Um item com falha não interrompe o lote: registrar status e seguir.
- Timeout por item: 180 s no download, 300 s na análise.
- Concorrência máxima de download: 3, com jitter entre requisições.
- Remover arquivos temporários em `finally` e verificar `data/tmp` no início e no fim do lote.
- Logs estruturados por item (`platform`, `external_id`, etapa, duração, resultado). Nunca logar cookie.
- Modelos, prompts e taxonomia versionados em config. Mudança deve permitir reprocessar a partir de `transcripts` + `embeddings`, sem rede.
- A transcrição detecta o idioma automaticamente; não fixar pt-BR. O conteúdo pode estar em qualquer idioma.

## Adicionando uma plataforma

1. Implementar `PlatformAdapter` em `src/save_finder/platforms/<slug>.py`:
   - `parse_export(path)` → `Iterable[SavedItem]`
   - `normalize_url(url)` → `(permalink, external_id)`
   - `enrich(item)` → `Metadata`
   - `download(item, dest)` → `Path`
2. Registrar em `platforms/registry.py`.
3. Adicionar fixture de export em `tests/fixtures/<slug>/`.
4. Não tocar no `core`, no schema nem na UI, exceto pelo campo `platform`.

Se a plataforma não oferecer exportação oficial, o adaptador pode se limitar a `normalize_url` + `enrich`, recebendo itens via `source=manual` ou `source=share`.

## Convenções de código

- Identificadores, tabelas e comentários em inglês. UI e documentação em pt-BR.
- Type hints em todas as funções públicas.
- I/O recebe config explícita, nunca caminho global.
- Sem `print` em biblioteca; `logging` ou Rich no CLI.
- Acesso a dados só via `repositories/`; nenhum SQL solto no core ou na web.
- Schema e mudanças via Alembic em `migrations/versions/`; nunca `create_all` em runtime.
- Chamadas de modelo passam pelos Protocols em `providers/base.py`; nenhum módulo importa Ollama, faster-whisper ou open-clip diretamente.
- Toda funcionalidade da UI tem rota JSON equivalente em `/api`.
- Exceções de domínio próprias (`EnrichError`, `DownloadError`, `ClassifierError`).
- Dependência nova só com justificativa no plano.

## Commits

Formato [Conventional Commits](https://www.conventionalcommits.org), em inglês e no imperativo:

```
type(scope): descrição curta
```

- **Tipos:** `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`, `chore`, `style`.
- **Escopo:** o módulo afetado — `core`, `db`, `pipeline`, `platforms`, `providers`, `web`, `cli`, `taxonomy`, `docs`.
- **Descrição:** minúscula, sem ponto final, até ~72 caracteres. Explica o quê, não o como.
- **Corpo:** opcional, em inglês, para o porquê. Uma linha em branco depois do título.
- **Breaking change:** `type(scope)!:` no título e bloco `BREAKING CHANGE:` no corpo.
- Um commit por mudança lógica. Não misture refatoração, funcionalidade e formatação no mesmo commit.
- Nunca commite `data/`, `roadmap.local.md`, `.env` ou credenciais.

Exemplos: `feat(platforms): add instagram export parser`, `fix(pipeline): remove temp files on timeout`, `docs(taxonomy): add 74 specific tags`.

## Testes

- Fixtures reais de export por plataforma, incluindo chaves localizadas.
- Obrigatórios: normalização de URL, parser do export, validação da taxonomia, regra pai-filho, limpeza de `data/tmp` em falha.
- Rede e modelo marcados com `@pytest.mark.network` / `@pytest.mark.model`; não rodam no CI padrão.
- Nenhum teste depende de Ollama, yt-dlp ou das plataformas.

## Não faça

- Não implemente login, 2FA, sessão ou escrita nas plataformas.
- Não guarde vídeo, áudio, imagens ou frames em massa.
- Não adicione player local, streaming ou transcodificação sob demanda.
- Não altere o schema sem uma migration Alembic em `migrations/versions/`.
- Não use a coleção de origem como tag automática; ela é o campo `items.collection`.
- Não espalhe `if platform == "instagram"` pelo core.
- Não antecipe funcionalidades de fases futuras sem decisão registrada no plano.

## Referências

- Plano detalhado: [`docs/implementation-plan.md`](docs/implementation-plan.md)
- Taxonomia inicial: [`docs/taxonomy.md`](docs/taxonomy.md)
- Execução local (não versionada): `docs/roadmap.local.md`
- Export do Instagram: Configurações → Central de Contas → Suas informações e permissões → Baixar suas informações → **Salvos** → JSON → todo o período.
