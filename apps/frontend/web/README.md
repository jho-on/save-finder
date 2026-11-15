# SaveFinder Web

Interface web remota do SaveFinder. Aplicação Go com templates
[Templ](https://templ.guide), paleta Protea (clara e escura) e
criptografia ponta a ponta feita exclusivamente no navegador.

O web **não** fala diretamente com o banco: ele serve as páginas, os estáticos
e faz reverse proxy de `/auth/*` e `/library/*` para a API remota (`apps/api`). Assim a
sessão (cookie) e o CSRF token fluem same-origin, sem CORS.

## Requisitos

- Go 1.26+ (ver `go.mod` para a versão mínima)
- API remota rodando (por padrão em `http://localhost:8080`)
- Templ CLI via `go run github.com/a-h/templ/cmd/templ` (dependência fixada em `tools.go`)

## Desenvolvimento

```bash
# terminal 1 — API
cd ../api && make run

# terminal 2 — web (porta 3000)
make generate   # gera templates/*_templ.go a partir dos .templ
make run        # sobe o web em http://localhost:3000
make test       # testes (páginas, proxy, estáticos)
make build
make check
```

Variáveis de ambiente:

| Variável | Padrão | Descrição |
|---|---|---|
| `PORT` | `3000` | Porta HTTP do web |
| `API_UPSTREAM` | `http://localhost:8080` | Endereço da API remota (proxy de `/auth/*` e `/library/*`) |
| `LOG_FORMAT` | `json` | `json` ou `text` |

## Identidade visual (Protea)

Os tokens semânticos (claro/escuro) vivem em [`static/style.css`](static/style.css);
adaptados de `apps/local/src/save_finder/web/static/style.css`. O tema segue o
`prefers-color-scheme` do navegador e pode ser trocado no header (persistido
em `localStorage`).

## Criptografia ponta a ponta (E2EE)

Modelo DEK + KEK, com **a senha de login como entrada da KEK**. O servidor
nunca vê a DEK nem os dados cifrados:

1. **Cadastro:** o navegador autentica a conta normalmente e, com a senha
   digitada, deriva a KEK (Argon2id, 19 MiB/2 iterações/1 lane). Gera uma DEK
   aleatória, cifra-a com a KEK (XChaCha20-Poly1305, nonce aleatório) e envia
   à API apenas `salt`, `wrapped_dek`, `dek_nonce` e os parâmetros do Argon2id
   — junto com `current_password`, para provar posse da conta.
2. **Login:** o navegador autentica, busca os metadados (`GET /auth/crypto`),
   deriva a KEK com a senha digitada e abre a DEK localmente. A DEK vive
   somente na memória da página; nunca em storage.
3. **Troca de senha** (`/change-password`): abre a DEK com a senha atual,
   gera nova KEK (novo salt) e re-cifra a mesma DEK. O navegador envia à API
   os novos metadados junto com a troca de senha — tudo numa única transação
   (`PUT /auth/account/password`), então conta e metadados nunca ficam
   dessincronizados. Os dados do usuário não são re-cifrados.
4. Se a senha for perdida, **os dados cifrados são irrecuperáveis** — não há
   recuperação de DEK. Isso é intencional.

O Argon2id roda via [`hash-wasm`](https://github.com/Daninet/hash-wasm)
(o build de navegador do libsodium não inclui Argon2id) e o XChaCha20-Poly1305
via libsodium.js; ambos vendored em `static/vendor/` — sem CDN em runtime.

## Rotas

| Rota | Descrição |
|---|---|
| `GET /` | Casca única autenticada (biblioteca, tags, detalhe) — sem dados no HTML; ver "A casca e as views" |
| `GET /login` | Redirect para `/` (a tela de login vive na casca) |
| `GET /register` | Tela de cadastro (e-mail, usuário, senha + criação da DEK) |
| `GET /change-password` | Troca de senha com re-wrap atômico da DEK |
| `GET /static/*` | Estáticos embarcados (CSS, JS, vendor) |
| `ANY /auth/*` | Reverse proxy para a API remota |
| `ANY /library[/*]` | Reverse proxy para os endpoints cifrados da biblioteca |

As páginas fazem fetch próprio para `/auth/*` (mesma origem) usando o CSRF
token retornado pelo login/registro; a sessão vai em cookie
`save_finder_session` (`HttpOnly`, `SameSite=Lax`; `Secure` em produção).

## A casca e as views (`/`)

Após o login, **todo o app vive numa única página**, sem
navegação de documento. As telas trocam por JavaScript via hash:

| Hash | Tela |
|---|---|
| `#/biblioteca` | Grade de posts: busca **100% client-side** (legenda, autor, tags, coleção), filtros por tipo e status |
| `#/tags` | Tags amplas com contagem de itens e pré-visualização |
| `#/tag/{slug}` | Posts da tag ampla + chips de tags específicas com contagens |
| `#/item/{platform}/{external_id}` | Detalhe: thumb, legenda, metadados, tags, link para a origem e transcrição (se houver) |

Como o servidor não decifra nada, o HTML da casca não contém nenhum dado da
conta; quem decide entre tela de login, desbloqueio e biblioteca é o client:

- Sem sessão → **login** (mesma UX de antes).
- Com sessão mas sem DEK (ex.: após refresh/aba nova) → **desbloqueio**: a
  senha é pedida uma vez para desembrulhar a DEK localmente.
- DEK em memória → biblioteca.

Thumbnails são buscadas sob demanda (`IntersectionObserver`) e decifradas no
navegador antes de virar `blob:` URL (CSP `img-src` inclui `blob:`).

## Cache local (ciphertext only)

Para revisitas rápidas a biblioteca grava **somente o ciphertext** em
IndexedDB (stores `items` e `thumbs`):

1. Na abertura, o client busca `GET /library/manifest` (lista leve de
   `item_key` + `updated_at`).
2. Itens que mudaram (ou novos) são re-buscados; os que sumiram são
   descartados; o restante é decifrado direto do cache.
3. Plaintext nunca é gravado em disco; a DEK nunca sai da memória da página.

Se IndexedDB falhar (quota/indisponível), o app degrada para rede pura, sem
erro. As respostas da API vão com `Cache-Control: no-store`.
