# ADR-0004 — Criptografia ponta a ponta com DEK/KEK, a partir da senha de login

**Status:** aceito · 2026-11-14

## Contexto

Queremos que o servidor armazene apenas dados que não consiga ler. A
criptografia deve acontecer exclusivamente nos clientes; o backend nunca pode
ter acesso à chave de cifragem nem aos dados claros. A senha de login, sozinha,
derivável de algo que o servidor nunca vê, é a semente natural da chave.

## Decisão

- Modelo DEK + KEK. A DEK cifra os dados do usuário (XChaCha20-Poly1305) e é
  gerada aleatoriamente no cliente. A KEK deriva da senha de login via
  Argon2id (salt aleatório por usuário, parâmetros armazenados) e cifra a DEK.
- O servidor guarda somente metadados opacos (salt, DEK cifrada, nonce,
  parâmetros do Argon2id), por usuário, apagados em cascata com a conta.
- A senha de login entra tanto na autenticação quanto na derivação da KEK,
  sem campos adicionais na interface; a senha nunca é armazenada.
- Troca de senha: o cliente desembrulha a DEK com a senha atual, deriva nova
  KEK e re-cifra a mesma DEK. A troca de senha e a substituição dos metadados
  acontecem na mesma transação no servidor, sem re-cifrar dados do usuário.
- Perda da senha implica perda dos dados. Não há recuperação, nem pelo
  operador do serviço.

## Consequências

- O servidor administra metadados que jamais usa para decifrar nada; sua
  credencial não basta para ler dados.
- Um atacante com a DEK e a senha de login do usuário lê tudo, mas não existe
  caminho sem um dos dois.
- Se o usuário quiser trocar a "senha de criptografia", na prática troca a
  senha de login (o que preserva os dados via re-wrap da DEK).
- Não há como oferecer recuperação de dados sem abandonar este modelo; foi
  uma escolha de escopo explícita.
