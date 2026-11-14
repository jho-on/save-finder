# ADR-0001 — Sessões no servidor, não JWT

**Status:** aceito · 2026-11-14

## Contexto

A API atende um único usuário em múltiplos dispositivos. Queríamos revogação
simples (sessão roubada, logout-all, troca de senha) sem blocklist nem rotação
de segredo.

## Decisão

Sessões server-side com identificador opaco em cookie. Múltiplas sessões
simultâneas por dispositivo, revogáveis individualmente ou em massa.
Inatividade expira (deslizante) e um limite absoluto sempre vale; dois perfis:
padrão (1 dia/7 dias) e "lembrar de mim" (7 dias/30 dias). Mutações
autenticadas exigem token anti-CSRF por sessão, além de cookie SameSite.

## Consequências

- Revogação é uma operação imediata, sem blocklist.
- Cada request autenticado paga uma consulta ao banco.
- Clientes precisam buscar o token anti-CSRF numa resposta antes de mutar.
