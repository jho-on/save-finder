# SaveFinder

Biblioteca local e classificador de mídia salva em redes sociais.
O Instagram é a primeira fonte, o núcleo é agnóstico de plataforma.

Cada item é enriquecido, baixado apenas para um diretório temporário, analisado e classificado.
A mídia original é apagada; persistem thumbnail, transcrição, embedding, tags e metadados.
Nada é escrito na plataforma de origem.

## Requisitos

- Python 3.11+
- `ffmpeg` e `ffprobe` (fases de mídia)
- `yt-dlp` (instalado como dependência; o binário do sistema é opcional)

## Instalação

Com `uv`:

```bash
uv sync --extra dev
```

Com `venv` e `pip`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Opcional: dependências locais de IA ficam em extras separados e não são instaladas por padrão:

```bash
.venv/bin/python -m pip install -e ".[asr,vision,llm]"  
```

## Configuração

Os padrões vivem em [`config/settings.toml`](config/settings.toml).  Variáveis de ambiente documentadas em
[`.env.example`](.env.example) sobrescrevem banco, diretório de dados, taxonomia e o caminho
do arquivo de configuração.

A taxonomia fechada fica em [`config/taxonomy.yaml`](config/taxonomy.yaml), gerada a partir de
[`docs/taxonomy.md`](docs/taxonomy.md). Nenhuma tag fora dessa lista pode ser emitida.

## Uso

```bash
# uma vez: cria data/, aplica migrations e carrega a taxonomia
.venv/bin/save-finder db init

# sobe a API local
.venv/bin/save-finder serve
```

Abra `http://127.0.0.1:8000` e arraste o ZIP da exportação. É o caminho recomendado para
uso manual: não precisa descompactar e o arquivo é apagado ao final.

A CLI faz a mesma importação sem abrir o navegador, útil para automação ou para processar
vários arquivos:

```bash
# o 1º argumento é a plataforma; o 2º é o ZIP, a pasta `saved` ou um JSON
.venv/bin/save-finder import instagram ~/Downloads/instagram-*.zip
```

O relatório mostra novos, existentes e ignorados. Reimportar não duplica itens.

```bash
# busca metadados públicos e baixa a thumbnail dos itens pendentes
.venv/bin/save-finder enrich --limit 30

# tenta de novo somente os itens que ficaram com status `error`
.venv/bin/save-finder enrich --retry-errors --limit 10

# download das peças, áudio, keyframe e thumbnail definitiva
.venv/bin/save-finder download --limit 3

# classificação automática pelas tags da taxonomia
.venv/bin/save-finder classify --limit 30
```

# contagens por status e plataforma
.venv/bin/save-finder stats

# valida a taxonomia
.venv/bin/save-finder taxonomy validate
```

`enrich` grava autor, caption, tipo do post, número de peças, duração e o caminho local da
thumbnail. A imagem de capa vai para `data/thumbs/`; nenhuma URL de CDN é persistida.
Posts privados, removidos ou bloqueados ficam como `unavailable`; falhas nossas ficam como
`error`. Reexecutar o comando pega apenas os itens ainda pendentes.

A API expõe `GET /api/health` e `POST /api/import`; documentação interativa em `/docs`.

Comandos previstos no plano (`analyze`, `classify`, `pipeline`) serão adicionados
conforme as fases avançarem.

### Export do Instagram

Configurações → Central de Contas → Suas informações e permissões →
Baixar suas informações → **Salvos** → JSON → todo o período.
Envie o ZIP pela página inicial (ou aponte a CLI `import` para ele, para a pasta `saved`
extraída ou para um JSON específico). O nome do arquivo não importa: o conteúdo é detectado,
e arquivos fora de `saved`
(curtidas, comentários) são ignorados.

O parser ignora chaves localizadas: ele localiza o link do post, o autor e o timestamp pelos
valores, e reconhece o nome da coleção por chaves equivalentes a “name”/“nome”/“nombre”.
Reimportar o mesmo export não duplica itens. A extração acontece em `data/tmp/` e é apagada
ao final, inclusive em caso de erro. Quando a exportação não traz o vínculo entre post e
coleção, o campo de coleção fica vazio.

## Desenvolvimento

```bash
make test        # pytest
make lint        # ruff check
make format      # ruff format
make check       # lint + format check + testes
```

Testes de rede e de modelo são marcados com `@pytest.mark.network` e
`@pytest.mark.model` e não fazem parte do conjunto padrão.

## Restrições do projeto

- Zero armazenamento permanente de mídia: original só em `data/tmp/`, apagado em `finally`.
- Somente leitura nas plataformas: sem login, sessão ou API privada.
- Aquisição apenas por exportação oficial ou URL explícita (`source=share|manual`).
- Taxonomia fechada e versionada.
- Núcleo sem código específico de rede fora de `platforms/<slug>.py`.
- Conteúdo não é redistribuído.
