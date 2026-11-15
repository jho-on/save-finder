# ADRs — decisões de arquitetura

Registro apenas de decisões arquiteturais com custo de reverter ou efeito além
de um único pacote. Em pt-BR, no formato Nygard: Contexto / Decisão /
Consequências. Cada ADR é imutável depois de aceito; mudança de rumo vira um
novo ADR que o substitui.

Detalhes de implementação (arquivos, parâmetros exatos, bibliotecas) ficam no
README da aplicação, não aqui.

| ADR | Decisão |
|---|---|
| [0001](0001-sessoes-server-side.md) | Sessões no servidor, não JWT |
| [0002](0002-rate-limiting-por-risco.md) | Rate limiting por risco, não por IP fixo |
| [0003](0003-exclusao-conta-cascade.md) | Exclusão de conta é irreversível e em cascata |
| [0004](0004-e2ee-dek-kek.md) | E2EE com DEK/KEK derivada da senha de login |

## Quando escrever um ADR

- Decisão que custa caro mudar ou se desvia da prática comum.
- Efeito além de um único pacote.

Todo o resto não precisa de ADR.
