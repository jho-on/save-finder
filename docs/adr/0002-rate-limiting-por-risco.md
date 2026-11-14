# ADR-0002 — Rate limiting por risco, não por IP fixo

**Status:** aceito · 2026-11-14

## Contexto

Rotas sensíveis (login, registro, troca de senha) sofrem brute force. O IP
sozinho não identifica o usuário e é compartilhado por milhares de assinantes
CGNAT — um bloqueio rígido puniria inocentes sem impedir rotação de IP.

## Decisão

Separar as duas classes de rota. Sem identidade de usuário, o IP acumula
pontos de risco por falha, com decaimento no tempo — bloqueio temporário só em
acúmulo. Com identidade de usuário, cota de requisições por usuário, com
limite mais estrito para senha e exclusão de conta. Estado em memória.

## Consequências

- CGNAT não bloqueia usuários legítimos por erros esporádicos.
- Perder o estado ao reiniciar é aceitável hoje; escala horizontal pedirá um
  store compartilhado.
