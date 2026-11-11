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
