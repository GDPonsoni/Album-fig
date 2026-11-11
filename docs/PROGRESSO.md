# Progresso do plano de álbuns sazonais

Acompanha o `PLANO_FABLE.md`, tarefa por tarefa. Atualizado ao fim de cada tarefa.

## Ambiente usado

- Node 22.22.1, Python 3.14.4, Postgres embutido de `dev/pg.mjs` (porta 5433).
- O `.venv` do backend tem os atalhos (`.venv/bin/alembic`, `.venv/bin/uvicorn`) apontando para um caminho antigo
  da pasta do projeto, então eles foram chamados como módulo: `.venv/bin/python -m alembic ...`,
  `.venv/bin/python -m uvicorn ...`. `.venv/bin/python -m pytest` funciona normalmente.
- Este Node não roda TypeScript direto; os testes de front são compilados pelo Vite antes (`npm test`).

## Fase 0 — UX no código atual (`feat/f0-ux`)

| Tarefa | Status | Observações |
| --- | --- | --- |
| 0.1 Colar fluido | feito | Voo começa no toque; POST em paralelo; falha cancela o voo e devolve à bandeja. |
| 0.2 Colar tudo | feito | `POST /album/paste-all`; até 12 voos escalonados. |
| 0.3 Figurinhas na tela de drops | feito | `GET /packs/history`; seção "Últimos pacotes abertos". |
| 0.4 Timer informa pacote raro | feito | `next_pack_kind` em `GET /packs`; regra única em `daily_kind`. |
| 0.5a QR nos códigos | feito | `GET /codes/preview`, rota `/r/:codigo`, folha imprimível no admin. |
| 0.5b Ler QR de troca | feito | Câmera real e iOS **não verificados** (ver abaixo). |
| 0.6 Módulo de estimativa | feito | `services/estimate.py`, com Monte Carlo contra `draw_stickers`. |

Testes rodados ao fim da fase:

- `cd backend && .venv/bin/python -m pytest` → 103 passaram.
- `cd frontend && npm run build` → compila (`tsc --noEmit` + Vite).
- `cd frontend && npm test` → 4 passaram (decodificação de QR e validação do link de troca).
- `node dev/e2e.mjs` (viewport de celular, Chrome headless) → todos os passos, incluindo os novos: colar em
  sequência com toque duplo, colar tudo, histórico de pacotes, link `/r/:codigo`, destino preservado no login,
  aviso de câmera ausente, folha de códigos com QR.

Não verificado na Fase 0:

- Leitura de QR com câmera de verdade (o ambiente não tem câmera) e comportamento no Safari/iOS. O caminho com
  câmera ausente/negada foi exercitado no E2E; a decodificação, em teste unitário.
- A sensação de fluidez da colagem em aparelho real fraco (só Chrome headless em desktop).

## Fase 1 — Fundação multi-álbum (`feat/f1-multi-album`)

| Tarefa | Status | Observações |
| --- | --- | --- |
| 1.1 Modelo e migration `0004` | feito | Backfill sem perda; exclusão `ex_album_ready_overlap` (btree_gist). |
| 1.2 Contexto de álbum | feito | `services/albums.py`; todo o jogo usa o álbum em andamento. |
| 1.3 Config por álbum | feito | `album_config` + `cfg(db, key, album_id)`. |
| 1.4 Fechamento e imutabilidade | feito | `close_if_due`, `final_stats`, `ON DELETE RESTRICT` (migration `0005`), guardas no admin. |
| 1.5 Front "Meus álbuns" | feito | `/albuns` e `/albuns/:id` (leitura). |
| 1.6 Painel de álbuns | feito | `/admin/albums`, com estimador ao vivo. |
| 1.7 Seed e CLI | feito | `seed-demo`, `create-album`, `close-albums`, `purge-campaign --album-id`. |
| 1.8 Testes | feito | `tests/test_albums.py` (41) e `tests/test_migration.py` (4). |
| Migration `0006` (limpeza) | feito | Remove colunas antigas de `user` e `org`; o downgrade as recria com os valores. |

Testes rodados ao fim da fase:

- `cd backend && .venv/bin/python -m pytest` → 148 passaram.
- `cd frontend && npm run build` e `npm test` → compila; 4 passaram.
- `node dev/e2e.mjs` → todos os passos, incluindo: estimador reagindo a parâmetro, sobreposição recusada com
  mensagem clara, rascunho criado e apagado, "Meus álbuns" com timer, álbum encerrado em leitura e 409 na escrita.
- Migration sobre dados reais: cópia do banco de dev (7 usuários, 125 figurinhas, 23 pacotes, 65 linhas de
  inventário, 2 trocas, 12 códigos, 541 linhas de auditoria) → `upgrade`, conferência de contagens e de que nada
  ficou sem `album_id`, `downgrade` até `0003` com os dados idênticos, `upgrade` de novo, e `downgrade -1`/`upgrade`.
- `seed-demo`, `create-album --ready`, `close-albums` e a validação de argumentos do `purge-campaign` num banco
  descartável criado do zero.
- Carga: `python ../dev/load.py 50 5 0` (pior caso, sem pausa) → 1300 requisições, 0 erros. Medianas: álbum 444 ms,
  página 427 ms, colar 517 ms, estoque 872 ms, abrir pacote 1236 ms; login 10,9 s (Argon2 com 50 logins
  simultâneos, igual a antes). **Não há medição anterior no mesmo ambiente para comparar.**

Não verificado na Fase 1:

- `seed-demo` com `APP_ENV=prod` num banco vazio (o trecho que omite o admin e sorteia a senha não mudou).
- A migration contra o Neon/produção, por regra do plano. Ver "o que revisar" no relatório final.

## Fase 2 — Papéis, rascunho e publicação (`feat/f2-publicacao`)

| Tarefa | Status | Observações |
| --- | --- | --- |
| 2.1 Papéis | feito | `role_grant`, conferido no banco a cada requisição; conceder (com 2FA), listar, revogar. |
| 2.2 Revisões e ativos | feito | Esquema do conteúdo em Pydantic (`extra=forbid`); upload reencodado, privado, com cota e rate limit. |
| 2.3 Fluxo | feito | Rascunho → enviar → aprovar/rejeitar (nota) → publicar. |
| 2.4 Publicação | feito | Transação única; regras por estado do álbum; `previous_content` para rollback. |
| 2.5 Painel de revisão | feito | `/admin/revisoes`: fila, diff, prévia do publicado e do proposto, raridade/número, publicar, rollback, histórico, auditoria. |
| 2.6 2FA do superadmin | feito | TOTP (`pyotp`), 10 códigos de recuperação em hash, `reset-2fa`, tela em Conta. |
| 2.7 Testes de segurança | feito | `tests/test_publicacao.py` (67 testes). |

## Fase 3 — Interface do editor (`feat/f2-publicacao`, mesma branch)

| Tarefa | Status | Observações |
| --- | --- | --- |
| 3.1 Renderização compartilhada | feito | `components/AlbumPages.tsx` serve o jogo, a prévia do editor e o painel de revisão. |
| 3.2 Área do editor | feito | `/editor/:capitulo`: orientação, grades predefinidas, formulários, recorte no cliente, prévia ao vivo, autosave, aviso ao sair. |
| 3.3 Painel do superadmin completo | feito | Editores por capítulo (conceder/revogar), visão do que cada org faz, fila de revisões; métricas mantidas. |

**Decisão de branch:** o plano pedia `feat/f3-editor` a partir da F2. Como a F3 foi feita em seguida e o front da F2 (painel de
revisão e prévia) depende da extração da renderização (3.1), ficou tudo em `feat/f2-publicacao`, em commits separados.

Testes rodados ao fim:

- `cd backend && .venv/bin/python -m pytest` → 215 passaram.
- `cd frontend && npm run build` e `npm test` → compila; 4 passaram.
- `node dev/e2e.mjs` → todos os passos. `node dev/e2e-editor.mjs` → editora entra pelo papel, estrutura travada com o álbum
  em andamento, autosave, upload com recorte, link `javascript:` recusado, envio trava, aprovar pede o código de 2FA, publicação,
  o jogador vê o título publicado, revogar bloqueia o editor na recarga seguinte.
- Migration `0007`: sobe e desce (`tests/test_migration.py::test_cada_migration_desce_e_sobe` anda por todas as revisões).

Bugs achados pelo E2E e corrigidos: recorte quebrado pela CSP (`blob:`) → trocado por data URL; autosave não retomava depois de
erro se a pessoa corrigia durante o salvamento; linha de link em branco impedia salvar o resto.

# Relatório final

## O que ficou pronto

Todas as tarefas das Fases 0 a 3, com testes. Resumo: UX (colar fluido, colar tudo, histórico de pacotes, tipo do próximo pacote,
QR de códigos, leitor de QR de troca), estimador, álbuns sazonais (universidade, álbum, capítulo, participação, config por álbum,
fechamento e imutabilidade, Meus álbuns, painel de álbuns), papéis, rascunho/revisão/publicação/rollback, 2FA e editor por capítulo.

## O que não foi verificado

- **Câmera real e iOS/Safari**: o leitor de QR só foi exercitado sem câmera (aviso) e em teste unitário de decodificação. Nenhum
  teste em aparelho físico, nem da fluidez da colagem em celular fraco (só Chrome headless).
- **Migração em produção (Neon)**: por regra do plano, não foi rodada. Foi validada em cópia do banco de dev, em banco de teste
  com campanha sintética e no caminho de volta.
- **TOTP com app autenticador real**: testado com `pyotp` nos dois lados; o QR foi gerado, mas não lido por um app.
- **Imagens em R2**: o fluxo de ativos privados foi testado no disco local; o bucket (`R2_*`) não foi exercitado com ativos de editor.
- `seed-demo` com `APP_ENV=prod` em banco vazio.

## Riscos conhecidos

- A 2FA vale por sessão, por 15 minutos; `revogar papel` não pede 2FA de propósito (tirar acesso tem que ser rápido).
- Imagens que saem de produção ficam guardadas (o rollback precisa delas) e só vão embora quando a revisão deixa de existir; há
  crescimento lento de armazenamento por capítulo, limitado pela cota de 100 ativos / 12 MB.
- O estimador ignora trocas e a garantia mínima de raridade: é conservador.
- Participação nasce no primeiro acerto da gaveta; quem nunca abriu o app num álbum não tem resumo dele.
- Hub de trocas continua em memória (processo único), como antes.

## O que revisar manualmente antes de mesclar

1. **Migration `0004`** (`backend/migrations/versions/0004_albuns_sazonais.py`): o backfill, a exclusão de período e a decisão de só
   criar o álbum "Calourada" se houver campanha. Rode numa cópia do banco real.
2. **Publicação** (`services/revisions.py::apply_content`): é o único caminho de conteúdo de editor para produção; confira as regras por
   estado do álbum e o tratamento de figurinhas removidas com histórico (viram inativas).
3. **Permissões**: `api/deps.py` (`require_chapter_editor`, `admin_mfa`), `api/editor.py` (só escreve em `chapter_revision` e `asset`)
   e a privacidade das imagens pendentes em `main.py` (`/media`).
4. **2FA** (`services/mfa.py`): janela de ±1 passo, anti-replay, bloqueio e códigos de recuperação.
5. A troca de comportamento em teste existente: remover figurinha deixou de apagar inventários (`test_admin_nao_apaga_figurinha_que_alguem_tem`).
6. Decisões 1 a 40 em `docs/DECISOES.md`, em especial a 12 (estimativa muda um detalhe do plano) e a 26 (`purge-campaign` exige escopo).
