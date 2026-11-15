# Compatibilidade entre componentes

O Save-Finder é composto por componentes independentes que trabalham em conjunto: API, Web, Local e Android. Embora cada um possa evoluir e receber atualizações separadamente, eles precisam continuar funcionando uns com os outros.

A compatibilidade existe para evitar que uma alteração em um componente torne outro inutilizável.

## API e clientes

A API é o principal ponto de integração entre os clientes remotos. Web, Android e Local dependem dos endpoints, dos formatos de resposta e dos mecanismos de comunicação que ela disponibiliza.

Por exemplo, se a API retorna um objeto com o campo `name`, e a Web utiliza esse campo para exibir o nome de um arquivo, renomeá-lo para `filename` pode fazer a interface deixar de funcionar.

Por isso, alterações na API devem considerar os clientes que já dependem de seu comportamento.

Sempre que possível, novas funcionalidades devem ser adicionadas sem invalidar as existentes. Quando uma mudança incompatível for necessária, os componentes afetados devem ser atualizados de maneira coordenada.

## Banco de dados

O PostgreSQL é compartilhado pela API, e suas alterações também precisam considerar as versões da aplicação que podem estar em execução durante uma atualização.

Uma migration que remove uma coluna ainda utilizada pela versão anterior da API, por exemplo, pode interromper o serviço durante um deploy.

Para evitar esse tipo de situação, as alterações no banco devem preferencialmente seguir três etapas:

1. **Expand:** adicionar a nova estrutura sem remover a antiga.
2. **Migrate:** atualizar a aplicação para utilizar a nova estrutura e, quando necessário, migrar os dados.
3. **Contract:** remover a estrutura antiga quando ela não for mais necessária.

É importante lembrar que reverter o binário da aplicação não reverte automaticamente as migrations já executadas no banco.

## Compatibilidade e versionamento

Cada componente possui seu próprio ciclo de desenvolvimento e versionamento. Portanto, versões diferentes não precisam avançar simultaneamente.

A versão global do Save-Finder representa uma composição específica dos componentes que foi considerada funcional e compatível.

Ela não significa que todos os componentes precisam ter a mesma versão, nem que precisam ser atualizados juntos. Seu propósito é registrar uma configuração conhecida do produto.

A compatibilidade deve ser avaliada pelo funcionamento real das integrações, e não apenas pela proximidade entre os números de versão.

---

Em essência, os componentes devem poder evoluir independentemente, preservando suas integrações sempre que possível. Quando isso não for possível, as mudanças necessárias devem ser coordenadas para que o produto continue funcionando como um conjunto.