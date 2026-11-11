# Decisões tomadas durante a execução

Só o que foi decidido além das seções 3 e 4 do `PLANO_FABLE.md`. Formato: decisão, alternativa descartada, motivo.

## Fase 0

1. **Colagem otimista com reconciliação única.** Durante a animação só um voo corre por vez, mas os POSTs são
   independentes; o estado do servidor só substitui o local quando não há POST pendente.
   *Descartado:* recarregar índice e página a cada resposta. *Motivo:* uma resposta antiga desfaria na tela uma
   colagem mais recente (a figurinha "voltaria" para a bandeja por um instante).
2. **Toque duplo é barrado no cliente e no servidor.** O cliente esconde a figurinha da bandeja no toque; o
   servidor continua respondendo 409 para a segunda colagem. *Motivo:* defesa em duas camadas, sem novo estado.
3. **"Colar tudo": quem está na página aberta voa até o espaço; as demais voam para dentro do álbum e somem.**
   *Descartado:* folhear o álbum página por página. *Motivo:* com dezenas de figurinhas levaria muitos segundos;
   o plano pede no máximo ~12 voos e o resto de uma vez.
4. **`paste-all` ignora figurinha inativa.** `paste` individual não checa `active`, mas a bandeja só mostra
   ativas; colar tudo cola exatamente o que a bandeja mostra.
5. **0.3 e 0.4 saíram no mesmo commit.** Mexem nas mesmas funções de `api/game.py`, `services/packs.py` e
   `Packs.tsx`; separar exigiria um estado intermediário artificial.
6. **`next_pack_kind` prevê a entrega no instante `next_pack_at`.** A gaveta decide pelo dia local do momento em
   que o jogador abre o app; se ele só voltar em outro dia, vale a regra desse outro dia. Há teste que confere a
   previsão contra a entrega real nesse instante e teste do acúmulo offline (um premium só).
7. **A prévia de código exige login e divide o limite de erros com o resgate.** *Descartado:* prévia pública.
   *Motivo:* pública, serviria de oráculo para adivinhar códigos sem custo; assim ela não dá nenhuma tentativa a
   mais do que o resgate já dava.
8. **Destino guardado em `localStorage` (`album.next`), só caminhos internos.** *Descartado:* só o `state` do
   roteador. *Motivo:* o link de confirmação de e-mail abre em outra aba, onde o `state` não existe. Caminhos que
   não começam com uma única `/` são recusados (evita redirecionamento aberto).
9. **QR do código aponta para `/r/<código>`** e o texto do código continua impresso ao lado. A folha é uma página
   imprimível (`window.print()`), não um PNG por código. *Motivo:* imprime o lote inteiro de uma vez.
10. **Testes de front com `node:test` compilados pelo Vite** (`npm test`). *Descartado:* adicionar Vitest/Jest.
    *Motivo:* o projeto não tem framework de teste de front e o Node instalado não executa TypeScript; assim não
    entra dependência nova além do `jsqr` pedido no plano.
11. **`jsqr` fica num chunk separado**, baixado só por navegador sem `BarcodeDetector`.
12. **Estimativa: premium como produto de duas potências**, `(1−p_comum)^k_comum · (1−p_premium)^k_premium`, em
    vez da média ponderada das probabilidades. *Motivo:* é o valor esperado exato quando a quantidade de pacotes
    de cada tipo é conhecida, e difere da média ponderada só na segunda casa. **Muda um detalhe da seção 4/0.6.**
13. **Estimativa: limite da gaveta por `visits_per_day` (padrão 1).** Aproveitamento dos ciclos =
    `min(1, stock_cap · refill_minutes / intervalo_entre_visitas)`. Documentado no módulo.
14. **Estimativa: "100%" é faltar menos de meia figurinha em valor esperado**, porque o valor esperado nunca
    alcança o total exato. O resultado diz também se o dia cai dentro da duração do álbum.
15. **Estimativa: alerta extra `unreachable`** quando alguma raridade com figurinhas não é sorteada por nenhum
    pacote da gaveta (o álbum seria impossível de completar sem código ou troca).

## Fase 1

16. **A migration só cria o álbum "Calourada" se houver campanha para herdar** (alguma org, figurinha, pacote,
    código ou troca). Banco vazio fica só com a universidade e os tipos de pacote modelo; o `seed-demo` ou o
    painel criam o álbum. *Motivo:* um álbum pronto e vazio num banco novo bloquearia o período sem ter conteúdo.
17. **Os tipos de pacote existentes viram os do álbum e ganham uma cópia como modelo global.** Pacotes e códigos
    já concedidos continuam apontando para os mesmos ids; o modelo (`album_id` nulo) serve aos álbuns futuros.
18. **Slug de pacote é único por álbum** (e único entre os modelos), por dois índices parciais.
19. **Participação para todo usuário existente** na migration, como pede o plano; no jogo, a participação nasce
    no primeiro acerto da gaveta (conta confirmada).
20. **Sem álbum em andamento, as leituras devolvem vazio e as escritas respondem 409.** `GET /album` e
    `GET /packs` trazem `album: null`. *Motivo:* o app precisa abrir e mostrar o estado vazio.
21. **Mensagem do 409:** "Álbum encerrado." se a universidade já teve álbum que acabou; senão "Nenhum álbum em
    andamento.". Objeto de álbum antigo (pacote, figurinha) responde "Álbum encerrado.".
22. **Código e link de troca de outro álbum respondem como inválidos**, com a mesma mensagem genérica.
23. **Álbum encerrado só é visível a quem participou; rascunho e agendado não aparecem para jogador.** Tudo o
    mais responde a mesma 404 de álbum inexistente (sem IDOR e sem revelar que o álbum existe).
24. **Fechamento em três gatilhos**, todos idempotentes: a varredura de 3 s que já existia, o primeiro acesso a
    "Meus álbuns" e o comando `close-albums`. O resumo é gravado com `WHERE final_stats IS NULL`.
25. **`received_total` conta figurinhas recebidas em pacotes** (não as vindas de troca); `duplicates` é a soma do
    que passa de um exemplar no momento do fechamento.
26. **`purge-campaign` exige `--album-id` ou `--all-accounts`.** Com álbum, apaga só o que foi jogado nele e
    mantém contas e conteúdo; `--all-accounts` é o comportamento antigo. *Motivo:* sem escolha explícita, o
    comando irreversível poderia apagar mais do que se queria.
27. **Admin, álbum em andamento:** só título, capa e data de fim mudam no álbum; nas figurinhas ficam
    congelados `org_id`, `number`, `rarity_id` e `active`; não se cria nem remove figurinha, capítulo ou mosaico.
    Tipos de pacote e parâmetros continuam editáveis pelo superadmin; em álbum encerrado, nada muda.
28. **Apagar álbum só se ninguém jogou** (sem participantes, pacotes, códigos ou trocas) e se não começou.
29. **Imagem só é apagada do armazenamento quando nenhum registro mais aponta para ela**, porque álbum duplicado
    compartilha os arquivos do original.
30. **Mosaico e ordem saíram do cadastro de org** e foram para o capítulo, em `/admin/albums`.
31. **Migration `0005` separada** para o `ON DELETE RESTRICT` de `user_sticker`, `pack_opening_item` e
    `trade_offer`. Teste antigo que apagava figurinha com inventário foi reescrito (comportamento mudou de propósito).
32. **O downgrade da `0004` recusa rodar com mais de um álbum**: o esquema antigo não comporta.
33. **`daily.timezone` e os dois TTLs de troca também podem variar por álbum**; limites de tentativa e de
    cadastro são sempre globais.
34. **Cinco abas no rodapé** (Álbum, Pacotes, Trocar, Álbuns, Conta).

## Fases 2 e 3

35. **O 2FA é por sessão (`user_session.mfa_at`) e vale 15 minutos**, não é exigido no login. *Descartado:* exigir no login.
    *Motivo:* o login serve a todos os usuários; o reforço só pesa onde há risco (aprovar, publicar, desfazer, conceder papel).
    Rejeitar e revogar não pedem 2FA: negar e tirar acesso não são ações a proteger.
36. **Ativar o 2FA pede a senha**; desligar pede senha e o segundo fator em dia. *Motivo:* uma sessão aberta e esquecida não
    basta para trocar o fator.
37. **O código TOTP não vale duas vezes** (`totp_last_step`), aceita o passo atual e os vizinhos, e 5 erros bloqueiam por 15 min
    (somando código do app e de recuperação).
38. **O conteúdo do editor rejeita campo desconhecido** (`extra=forbid`), o que barra `number`, `rarity_id`, `image_url` e afins por
    construção. Texto é guardado como texto: só se removem caracteres de controle e de direção (o front nunca usa HTML).
39. **Links do verso**: só `https`, sem usuário/senha, host com ponto e nome válido (sem IP nem `localhost`), porta omitida ou 443,
    até 300 caracteres. É mais estrito que o admin, que aceita qualquer `HttpUrl`.
40. **Ativos pendentes são privados por prefixo (`capitulos/`)**: só o superadmin e os editores do capítulo os baixam; os demais
    recebem a mesma 404 de arquivo inexistente. Publicar marca o ativo como `approved`.
41. **Um capítulo tem uma única revisão em aberto**, imposto por índice parcial único no banco. Rejeitada continua "aberta" e volta a
    rascunho quando o editor mexe.
42. **Rollback cria uma revisão nova** (já publicada) com o conteúdo anterior e marca a revertida como `superseded`, em vez de
    reabrir a antiga. *Motivo:* o histórico fica linear e dá para desfazer o rollback.
43. **O snapshot do rollback guarda número e raridade de cada figurinha**, para restaurar exatamente o que havia, e é reaplicado pelas
    mesmas regras (com o álbum em andamento, um rollback que removeria figurinha é recusado).
44. **Figurinha removida pela publicação (só antes do início)**: sem histórico, é apagada; com histórico, fica inativa.
45. **Com o álbum em andamento a publicação ignora a raridade sugerida** e recusa override de raridade ou número, até do superadmin.
46. **Imagens que saem de produção não são apagadas** na publicação, porque o rollback precisa delas.
47. **A prévia mostra a ordem, não o número**, das figurinhas novas: o número só nasce na publicação.
48. **Capítulos têm `layout` (orientação, títulos, textos, fundos) e `published_revision_id`** na própria linha de `album_chapter`.
49. **Branch única para F2 e F3** (ver `PROGRESSO.md`).
50. **Recorte no cliente em data URL**, não `blob:`, para não afrouxar a CSP.
51. **Autosave do editor** 1,5 s depois da última mudança; linhas de link em branco ficam de fora do que é enviado.
