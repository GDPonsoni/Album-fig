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
