# ADR-0003 — Exclusão de conta é irreversível e em cascata

**Status:** aceito · 2026-11-14

## Contexto

Excluir a conta é irreversível e apaga os dados do usuário junto. Precisa ser
difícil de disparar por acidente ou por sessão roubada, e não pode deixar
dados órfãos.

## Decisão

Exclusão exige autenticação e reautenticação com a senha atual. A remoção do
usuário cascateia para todos os dados dependentes; não há "soft delete" nem
período de carência nesta fase.

## Consequências

- Sessão roubada não basta para apagar a conta.
- Tabelas futuras de dados de usuário nascem com exclusão em cascata, ou
  justificam a exceção em ADR próprio.
- Recuperação de conta excluída é impossível por design.
