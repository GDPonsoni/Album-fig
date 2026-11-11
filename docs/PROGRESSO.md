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
