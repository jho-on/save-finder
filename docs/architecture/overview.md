# Visão geral do SaveFinder

O SaveFinder é uma biblioteca local e um classificador de mídia salva em redes
sociais (vídeos, imagens e carrosséis), com o Instagram como primeira fonte.
O projeto está organizado como um **monorepo** com quatro aplicações
independentes:

| Aplicação               | Tecnologia                | Responsabilidade                                        |
| ----------------------- | ------------------------- | ------------------------------------------------------- |
| `apps/local`            | Python                    | Aplicação local: CLI e interface web local.             |
| `apps/api`              | Go                        | API remota: autenticação, persistência e sincronização. |
| `apps/frontend/web`     | Go + Templ                | Interface web remota, consumindo a API.                 |
| `apps/frontend/android` | React Native + TypeScript | Aplicativo Android, consumindo a API.                   |

## Princípios

- **O Local não depende dos demais.** O SaveFinder Local é autossuficiente, com seu próprio ambiente Python.
