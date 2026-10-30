# Plano de implementação — SaveFinder

> Nome do projeto: **SaveFinder** (slug `save-finder`).
> Este documento registra escopo e decisões — não implementação.
> Cronograma, metas e rumo futuro ficam em `roadmap.local.md`, fora do git.
> Regras permanentes estão em [`AGENTS.md`](../AGENTS.md).

## 1. Objetivo

Classificar automaticamente mídia salva em redes sociais — vídeos, imagens e carrosséis — com IA local, e oferecer busca e organização por tags, sem arquivar os arquivos e sem escrever nada nas contas.

O MVP começa pelo Instagram, mas a arquitetura nasce multi-plataforma: importação, enriquecimento e download ficam atrás de um adaptador por rede, e o núcleo não sabe de onde o item veio.

Fluxo:

```
export oficial → adapter → import → enrich → download temporário → análise → tags → API/UI
                                               │
                                               └─ mídia apagada após o processamento
```

## 2. Escopo

### Dentro

- Importar a exportação oficial do Instagram (salvos e coleções).
- Definir o contrato de adaptador de plataforma e implementar só o do Instagram.
- Enriquecer cada link com autor, caption, tipo de mídia, duração e thumbnail local.
- Tratar os três formatos de post: vídeo único, imagem única e carrossel.
- Baixar toda a mídia do item temporariamente, analisar cada peça e apagar os arquivos.
- Transcrever o áudio dos vídeos com detecção automática de idioma.
- Gerar tags visuais e textuais, fundir os sinais e persistir o resultado.
- UI web local para computador: grid com thumbnail, busca textual, filtros por tag, coleção e plataforma, revisão manual e link para a origem.
- Rodar em lote, de forma retomável, com relatório de falhas.

### Fora

- Salvar, curtir, criar coleção ou remover itens nas plataformas.
- Login, sessão, API privada, scraping de páginas.
- Arquivar mídia, player local, transcodificação.
- Multiusuário, multi-tenancy, deploy público.
- Acesso remoto e cliente mobile no MVP; a API já nasce pronta para eles.
- Adaptadores de outras redes no MVP; só o contrato.
- Treinar modelo próprio.

## 3. Decisões

| # | Decisão | Motivo |
|---|---|---|
| D1 | Mídia só em diretório temporário, apagada após o processamento | Requisito do produto; elimina o custo de armazenamento |
| D2 | Persistir transcrição e embedding, não a mídia | Permite reclassificar e buscar sem baixar de novo |
| D3 | SQLAlchemy + Alembic; SQLite por padrão, Postgres quando houver necessidade | Portabilidade sem reescrever a camada de dados |
| D4 | Taxonomia fechada, versionada e de dois níveis | Evita explosão de tags e mantém coleções consistentes |
| D5 | LLM para texto, modelo visual para frames e imagens | Boa relação qualidade/custo em CPU; VLM por frame seria lento |
| D6 | Downloader isolado atrás de interface | É o componente mais frágil; permite trocar de ferramenta |
| D7 | API JSON como fonte de verdade; Jinja/HTMX como primeiro cliente | Um cliente mobile futuro reusa a API sem reescrever o backend |
| D8 | Identidade do item é `(plataforma, id externo)` | Estável e única por rede |
| D9 | Adaptador por plataforma | O núcleo não cresce a cada nova rede |
| D10 | Origem explícita do item (exportação, share ou manual) | Redes sem exportação entram pelo mesmo pipeline |
| D11 | Interfaces para transcrição, visão e LLM | Trocar local por GPU ou API sem tocar no pipeline |
| D12 | Um dono, vários dispositivos — não multiusuário | O objetivo futuro é PC e celular para a mesma pessoa; sem workspace ou multi-tenancy |
| D13 | Multilíngue por padrão | O conteúdo pode estar em pt-BR, inglês ou qualquer outro idioma |
| D14 | O post é a unidade; toda a mídia dele é analisada | Carrossel é comum no Instagram e cada peça pode mudar a classificação |
| D15 | Imagem é cidadã de primeira classe, não resíduo | Boa parte dos salvos é foto ou carrossel sem vídeo; ignorar isso perderia o acervo |
| D16 | Um embedding por item, agregando as mídias | Mantém o custo por item previsível; embedding por peça só quando a UI exibir carrossel |
| D17 | MVP é web para computador | Evita construir autenticação e transporte antes do produto estar de pé |
| D18 | Limiares com padrões iniciais, calibrados na Fase 4 | Permite codar antes da amostra rotulada existir, sem congelar números no chute |

A preparação para multi-dispositivo se limita a: API JSON separada da UI, camada de dados portável e providers desacoplados. Acesso remoto e autenticação de usuário único entram quando o uso em celular justificar; nada de workspace ou multi-tenancy.

## 4. Modelo de dados (conceitual)

- **Item** — identidade `(plataforma, id externo)`, URL canônica, origem, autor, caption, tipo do post (vídeo, imagem ou carrossel), quantidade de peças, data de salvamento, coleção de origem, caminho da thumbnail, status e timestamps de cada etapa.
- **Peça de mídia** — ordem dentro do post, tipo (vídeo ou imagem) e duração quando houver. Existe para relatório e para a futura navegação de carrossel; não gera classificação própria.
- **Transcrição** — idioma detectado, texto, segmentos e modelo usado. Só existe quando o item tem áudio.
- **Embedding** — vetor visual agregado do item, com o modelo usado.
- **Tag** — slug estável, nome de exibição, tag pai e nível (ampla ou específica).
- **Tag do item** — confiança, origem (automática ou manual) e evidência que justificou a escolha.
- **Execução** — registro de cada lote: etapa, configuração, contagem de sucessos e falhas.
- **Índice de busca** — derivado e reconstruível a partir dos dados acima; nunca é fonte de verdade.

Estados do item: pendente, enriquecido, analisado, classificado, ignorado, indisponível e erro.

- **Ignorado** — o post não expõe mídia analisável (ex.: conteúdo que o downloader não consegue resolver). Deixou de significar "post sem vídeo": imagem também é analisada.
- **Indisponível** — removido, privado ou bloqueado por região.
- **Erro** — falha nossa; guarda a mensagem.

Não existe campo de workspace ou locatário. O modelo assume um único dono.

## 5. Taxonomia

- Dois níveis: amplas e específicas subordinadas.
- Cerca de 20 amplas no início; específicas entram conforme a necessidade real.
- Fechada e versionada: o modelo não cria tag. Sugestão fora da lista é descartada; tag nova exige edição e revisão.
- Tag específica selecionada sempre propaga a ampla correspondente.
- Nomes exibidos em pt-BR; os slugs são neutros de idioma.
- O conteúdo pode estar em qualquer idioma e é enviado ao classificador no idioma original.
- As descrições usadas pelo modelo visual ficam em inglês, por limitação do encoder; isso não afeta os nomes exibidos.
- Correções manuais nunca são sobrescritas e servem de exemplo para melhorar a classificação.

A lista inicial, com os slugs e as descrições visuais, está em [`taxonomy.md`](taxonomy.md). É ponto de partida sujeito a revisão com o uso real, e não pode divergir do que estiver em `config/` na implementação.

## 6. Pipeline

### 6.1 Importação

Ler a exportação oficial, normalizar links e identificar cada item pela chave `(plataforma, id externo)`. Preservar a coleção original como metadado, não como tag. A operação é idempotente: reimportar não duplica e só acrescenta o que é novo. O relatório deve separar novos, existentes e ignorados.

Aceite: duas execuções sobre o mesmo export resultam em zero itens novos na segunda.

### 6.2 Enriquecimento

Obter metadados públicos do item (autor, caption, tipo do post, quantidade de peças, duração) e baixar a thumbnail para armazenamento local. Nunca guardar URL de CDN, que expira. O tipo detectado aqui define o caminho da análise adiante: vídeo, imagem ou carrossel. Falhas viram status explícito. Retentativas só para erro de rede, com limite e espera crescente.

Aceite: amostra de 30 itens com caption e thumbnail local, cobrindo os três formatos de post; nenhuma URL remota persistida; falhas classificadas corretamente.

### 6.3 Download e análise

Baixar todas as peças do item para o diretório temporário, gerar a thumbnail definitiva e apagar tudo ao final — inclusive em erro. Cada tipo segue um caminho:

- **Vídeo** — extrair áudio e keyframes; transcrever o áudio com detecção automática de idioma; calcular o embedding dos frames.
- **Imagem** — calcular o embedding direto da imagem, sem transcrição.
- **Carrossel** — repetir o caminho de cada peça e agregar os embeddings em um vetor do item.

A transcrição nunca fixa idioma: o conteúdo pode estar em pt-BR, inglês ou qualquer outro. Se não houver fala útil, o sinal de texto é descartado. O lote deve abortar se o espaço livre cair abaixo de uma margem de segurança.

Aceite: após qualquer execução, inclusive com falha forçada, o diretório temporário fica vazio; um item de cada formato é analisado.

### 6.4 Classificação

Sinais: transcrição, caption, frames e imagens. O texto tem peso maior quando há fala; o visual assume quando não há — o caso típico de imagem e carrossel sem áudio. A saída é multi-label, limitada às tags da taxonomia, com confiança e evidência gravadas.

Limiares nascem com padrões iniciais e são calibrados sobre a amostra rotulada da Fase 4; não ficam presos a números escolhidos no chute. Reclassificar usa apenas transcrição e embedding já persistidos, sem rede.

Aceite: 30 itens classificados, com vídeo e imagem na amostra; regra pai-filho respeitada; reclassificação offline reproduz o resultado.

### 6.5 API e UI

A API JSON é a interface principal e cobre listagem, detalhe, filtros, busca e edição de tags. A UI web consome essa API e é o primeiro cliente. Busca textual sobre autor, caption, transcrição e tags; filtros por tag, coleção, plataforma, status e tipo de post. O card indica se o item é vídeo, imagem ou carrossel, e quantas peças tem. Cada item leva à origem em nova aba. Itens sem thumbnail recebem placeholder.

A UI é desenhada para tela de computador. Responsividade fina e uso em celular ficam para a fase de acesso remoto, sem mudar a API.

Aceite: 1.000 itens navegáveis, busca abaixo de 300 ms e edição de tag persistida.

## 7. Testes

| Tipo | Cobertura |
|---|---|
| Unitário | normalização de URL, parser do export, taxonomia, fusão, regra pai-filho, contratos de provider, limpeza |
| Integração | pipeline com providers e downloader falsos, sem rede, cobrindo vídeo, imagem e carrossel |
| Manual | 30 itens do spike; 50 itens rotulados para acurácia, com pt e inglês e os três formatos |
| Regressão | reimportar export, reclassificar do banco, item inválido, item sem mídia |

Testes de rede e de modelo ficam marcados e fora do conjunto padrão. Nenhum teste depende de serviço externo.

## 8. Riscos

| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| Downloader não cobre parte do acervo | Alta | Alto | Cookies opcionais, ferramenta alternativa, status explícito, medir no spike de viabilidade |
| Rate limit ou bloqueio temporário | Média | Médio | Concorrência baixa, jitter, backoff e retomada |
| Áudio só com música | Alta | Médio | Detecção de fala; cair para o visual |
| Modelo visual fraco em tags específicas | Alta | Médio | Específicas vêm do texto; visual só reforça |
| Item só de imagem depende de caption + visual | Alta | Médio | Caption entra com peso alto quando não há fala; revisão manual para corrigir |
| Carrossel com muitas peças aumenta o custo por item | Média | Baixo | Limite de peças analisadas por item, configurável, medido no spike |
| Idioma não suportado ou misturado | Média | Médio | Detecção automática, avaliar amostra multilíngue, permitir revisão manual |
| Tags específicas inconsistentes | Média | Médio | Taxonomia fechada e revisão humana |
| Link morto após remoção do post | Média | Baixo | UI indica indisponível; decisão aceita |
| Disco insuficiente | Baixa | Alto | Guarda de espaço; só thumbnail é persistida |
| Lote lento em CPU | Média | Baixo | Execução noturna; modelo de transcrição menor como fallback |

## 9. Pendências

- Revisar a lista de tags depois dos primeiros lotes reais; hoje é proposta, não consenso.
- Definir o transporte de acesso remoto quando o uso em celular entrar na pauta, com dois critérios já fixados: serviço não exposto publicamente e autenticação de usuário único.
- Confirmar na Fase 0 se o downloader devolve, para carrossel, todas as peças na ordem correta; se não devolver, o adaptador precisa reconstruir a ordem.
