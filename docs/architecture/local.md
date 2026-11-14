# SaveFinder Local

Aplicação Python em [`apps/local`](../../apps/local). É o produto existente do
SaveFinder: biblioteca local e classificador de mídia salva em redes sociais.

## Responsabilidade

- Importar exportações oficiais das plataformas
- Enriquecer cada item com metadados e thumbnail.
- Baixar a mídia apenas para um diretório temporário, analisar e apagar.
- Persistir thumbnail, transcrição, embedding, tags e metadados em banco local
  (SQLite por padrão).
- Expor CLI (Typer + Rich) e interface web local (FastAPI + Jinja/HTMX) com
  busca, filtros e link para o post na origem.
- A mídia nunca é arquivada; nada é escrito na plataforma de origem.

## Stack

Python 3.11, SQLAlchemy 2.0 + Alembic, FastAPI, yt-dlp, ffmpeg, faster-whisper
(ASR local), open-clip (visão local), Ollama (LLM local). Detalhes em
[`../development/implementation-plan.md`](../development/implementation-plan.md) e
no [`README` do app](../../apps/local/README.md).

## Independência

O Local é autossuficiente: não depende da API remota nem dos clientes. Sua
migração para o monorepo não alterou comportamento, dependências ou dados.

## Dados

Vivem em `apps/local/data/` (gitignored): banco SQLite, thumbnails e diretório
temporário. Não mover para a raiz do monorepo.
