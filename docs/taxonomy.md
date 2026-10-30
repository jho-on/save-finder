# Taxonomia inicial — SaveFinder

> Lista fechada de tags. O classificador só pode emitir o que está aqui.
> Regras em [`implementation-plan.md`](implementation-plan.md#5-taxonomia): dois níveis, específica propaga a ampla, fechada e versionada.
> A descrição visual em inglês é o texto comparado com os embeddings de imagem (CLIP só entende inglês). Os nomes exibidos ficam em pt-BR.
> Esta lista é uma proposta inicial: revise antes da Fase 4 e ajuste com o uso real.

## Amplas

| Slug | Nome | Descrição visual (EN) |
|---|---|---|
| comida-e-bebida | Comida e bebida | food, cooking, restaurant dishes, drinks, desserts |
| viagem-e-lugares | Viagem e lugares | travel destinations, landscapes, hotels, city views, tours |
| tecnologia | Tecnologia | computers, phones, gadgets, code on screens, AI, software |
| ciencia-e-educacao | Ciência e educação | classroom, books, diagrams, experiments, explainers |
| saude-e-fitness | Saúde e fitness | gym workout, running, yoga, exercise, medical care |
| beleza-e-cuidados | Beleza e cuidados | makeup application, skincare, hair styling, nails |
| moda-e-estilo | Moda e estilo | fashion outfits, clothing, accessories, fashion shoot |
| casa-e-decoracao | Casa e decoração | interior design, furniture, home decor, plants indoors |
| dinheiro-e-negocios | Dinheiro e negócios | money, charts, business meeting, investing, storefront |
| trabalho-e-carreira | Trabalho e carreira | office desk, job interview, resume, teamwork, presentation |
| arte-e-design | Arte e design | drawing, painting, graphic design, craft, photography |
| musica-e-audio | Música e áudio | musical instruments, concert stage, dancing, studio recording |
| humor-e-memes | Humor e memes | comedy sketch, funny reaction, meme text, prank |
| esportes | Esportes | sports match, athletes, ball games, combat sports, racing |
| games | Games | video game footage, game characters, esports, consoles |
| noticias-e-politica | Notícias e política | news broadcast, press conference, protests, government |
| entretenimento | Entretenimento | movie scenes, celebrities, TV shows, anime, trailers |
| animais | Animais | pets, dogs, cats, wildlife, farm animals |
| automotivo | Automotivo | cars, motorcycles, engines, car repair, driving |
| bem-estar-e-espiritualidade | Bem-estar e espiritualidade | meditation, astrology, religious practice, self-care routine |

## Específicas

| Slug | Nome | Pai |
|---|---|---|
| receitas | Receitas | comida-e-bebida |
| restaurantes | Restaurantes | comida-e-bebida |
| doces-e-sobremesas | Doces e sobremesas | comida-e-bebida |
| drinks | Drinks | comida-e-bebida |
| destinos | Destinos | viagem-e-lugares |
| dicas-de-viagem | Dicas de viagem | viagem-e-lugares |
| hospedagem | Hospedagem | viagem-e-lugares |
| gadgets | Gadgets | tecnologia |
| programacao | Programação | tecnologia |
| ia | IA | tecnologia |
| apps-e-produtividade | Apps e produtividade | tecnologia |
| seguranca-digital | Segurança digital | tecnologia |
| curiosidades | Curiosidades | ciencia-e-educacao |
| historia | História | ciencia-e-educacao |
| idiomas | Idiomas | ciencia-e-educacao |
| musculacao | Musculação | saude-e-fitness |
| corrida | Corrida | saude-e-fitness |
| yoga-e-mobilidade | Yoga e mobilidade | saude-e-fitness |
| saude-mental | Saúde mental | saude-e-fitness |
| nutricao | Nutrição | saude-e-fitness |
| maquiagem | Maquiagem | beleza-e-cuidados |
| skincare | Skincare | beleza-e-cuidados |
| cabelo | Cabelo | beleza-e-cuidados |
| unhas | Unhas | beleza-e-cuidados |
| looks | Looks | moda-e-estilo |
| acessorios | Acessórios | moda-e-estilo |
| compras | Compras | moda-e-estilo |
| organizacao | Organização | casa-e-decoracao |
| reforma | Reforma | casa-e-decoracao |
| plantas | Plantas | casa-e-decoracao |
| limpeza | Limpeza | casa-e-decoracao |
| investimentos | Investimentos | dinheiro-e-negocios |
| empreendedorismo | Empreendedorismo | dinheiro-e-negocios |
| economia-domestica | Economia doméstica | dinheiro-e-negocios |
| marketing | Marketing | dinheiro-e-negocios |
| produtividade | Produtividade | trabalho-e-carreira |
| entrevistas | Entrevistas | trabalho-e-carreira |
| curriculo | Currículo | trabalho-e-carreira |
| lideranca | Liderança | trabalho-e-carreira |
| ilustracao | Ilustração | arte-e-design |
| fotografia | Fotografia | arte-e-design |
| design-grafico | Design gráfico | arte-e-design |
| artesanato | Artesanato | arte-e-design |
| arquitetura | Arquitetura | arte-e-design |
| instrumentos | Instrumentos | musica-e-audio |
| producao-musical | Produção musical | musica-e-audio |
| dancas | Danças | musica-e-audio |
| musica-ao-vivo | Música ao vivo | musica-e-audio |
| memes | Memes | humor-e-memes |
| esquetes | Esquetes | humor-e-memes |
| stand-up | Stand-up | humor-e-memes |
| pegadinhas | Pegadinhas | humor-e-memes |
| futebol | Futebol | esportes |
| luta | Luta | esportes |
| basquete | Basquete | esportes |
| esportes-radicais | Esportes radicais | esportes |
| jogos-eletronicos | Jogos eletrônicos | games |
| e-sports | E-sports | games |
| retrogames | Retrogames | games |
| noticias | Notícias | noticias-e-politica |
| politica | Política | noticias-e-politica |
| filmes-e-series | Filmes e séries | entretenimento |
| celebridades | Celebridades | entretenimento |
| reality-shows | Reality shows | entretenimento |
| anime | Anime | entretenimento |
| pets | Pets | animais |
| animais-selvagens | Animais selvagens | animais |
| carros | Carros | automotivo |
| motos | Motos | automotivo |
| mecanica | Mecânica | automotivo |
| meditacao | Meditação | bem-estar-e-espiritualidade |
| astrologia | Astrologia | bem-estar-e-espiritualidade |
| espiritualidade | Espiritualidade | bem-estar-e-espiritualidade |
| autocuidado | Autocuidado | bem-estar-e-espiritualidade |

## Como evoluir

- Tag específica nova exige entrada na lista e revisão; nunca é criada pelo classificador.
- Uma específica pode receber outra específica como filha (terceiro nível) só se o uso real exigir; hoje a profundidade é 2.
- Remover ou renomear tag é migration com reclassificação dos itens afetados.
- A descrição visual de cada ampla é o que o encoder compara; específicas herdam o contexto da ampla mais o próprio nome.
