# Versionamento — Save-Finder

O monorepo usa **dois níveis de SemVer**, intencionalmente independentes.

## 1. Versões individuais dos componentes

Cada componente versiona no seu próprio ciclo. As tags Git seguem o formato
`<componente>/vX.Y.Z`:

| Componente | Tag | Estado atual |
|---|---|---|
| Local (CLI) | `local/vX.Y.Z` | declara `0.1.0` |
| API | `api/vX.Y.Z` | sem versão |
| Web | `web/vX.Y.Z` | sem versão |
| Mobile (Android) | `mobile/vX.Y.Z` | fontes de versão divergentes |

Regras:

- MAJOR/MINOR/PATCH seguem SemVer em relação ao **contrato público do
  componente** (ver [compatibility.md](compatibility.md)), não ao tamanho da
  mudança interna.
- Não há sincronização forçada de números entre componentes. `api/v2.0.0` não
  implica nada sobre `web` ou `local`.
- Pré-releases são permitidos: `api/v2.1.0-rc.1`.
- Tags são anotadas e criadas manualmente. Nenhum workflow cria tags ou
  releases automaticamente.
- Tag de componente marca um ponto de evolução técnica; **não** dispara
  deploy.
- A divergência do mobile (fontes de versão do app discordantes) deve ser
  resolvida antes da primeira tag `mobile/v*`.

## 2. Versão global do produto

Convenção: `save-finder/vX.Y.Z`.

Significa: *esta composição de componentes foi integrada, testada e é
considerada compatível*. Não é a soma das versões nem um "deploy mais
recente".

- MAJOR: mudança incompatível no produto (contrato entre componentes quebrado,
  ou composição que exige migração coordenada).
- MINOR: novas funcionalidades compatíveis.
- PATCH: correções compatíveis.
- Só muda quando uma nova composição é formalmente estabelecida — nunca a
  cada commit ou deploy individual.

### Manifesto: `product.yaml`

O manifesto na raiz do repositório é a referência da composição publicada:

```yaml
product: save-finder
version: 0.0.0
status: unreleased
components:
  local: 0.1.0
  api: ~
  web: ~
  mobile: ~
```

- `null` (`~`) = componente ainda sem versão definida e **fora de qualquer
  composição lançada**. Não inventar versões.
- O manifesto só muda via PR que estabelece uma nova composição, junto com a
  tag `save-finder/vX.Y.Z` (criada manualmente).
- O APK é publicado exclusivamente na tag `mobile/vX.Y.Z`; a Release de
  produto nunca carrega binários de API ou Web.

## 3. Relação entre os níveis

```text
local/v1.8.0  api/v2.0.0  web/v1.5.0  mobile/v1.2.0
        \          |          /             /
         \         |         /             /
          save-finder/v1.2.0  (product.yaml)
```

- Uma tag de produto **referencia**, não contém, as tags de componente.
- Componente com MAJOR próprio não força MAJOR de produto, desde que o
  contrato integrado (API ↔ web/android/local, migrações, E2EE) se mantenha —
  ver [compatibility.md](compatibility.md).
- Produto MAJOR implica que pelo menos um contrato entre componentes mudou de
  forma incompatível (ou a composição exige migração coordenada).

## 4. Fluxo de release

1. Componentes evoluem em `develop`; cada um recebe sua tag quando um marco é
   atingido.
2. Quando se decide publicar uma composição: PR atualizando o manifesto com
   as versões testadas.
3. Após o merge em `main`, criar a tag anotada `save-finder/vX.Y.Z` e a
   GitHub Release correspondente (notas + manifesto).
