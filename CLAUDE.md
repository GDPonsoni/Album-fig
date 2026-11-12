# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Projeto em português (código, mensagens, commits). Commits seguem Conventional Commits: `tipo(escopo): descrição`. O README.md tem as regras do jogo e o modelo de dados; leia antes de mexer em packs/trocas.

## Comandos

Dev precisa de Node 22 e Python 3.14. Postgres vem de pacote npm (porta 5433), ou `docker compose up -d`.

```bash
cd dev && npm install && node pg.mjs                  # Postgres, deixar rodando
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed-demo                 # conteúdo fictício; contas admin@/ana@/bia@example.com, senha album12345
.venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev             # porta 5173; build = tsc --noEmit && vite build
```

Testes (backend, precisa do Postgres no ar; usa banco `album_test` e roda as migrations):

```bash
cd backend && .venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_packs.py -k nome_do_teste   # teste único
```

E2E em viewport de celular: build do front, backend com `APP_ENV=dev BASE_URL=http://localhost:8000`, depois `node dev/e2e.mjs` (precisa do `seed-demo`). Carga: `python ../dev/load.py 50 5 2`. Não há linter nem testes de frontend.

CLI admin: `python -m app.cli create-admin <email> <nome>`, `seed-demo`, `purge-campaign --confirm` (irreversível).

## Arquitetura

Monorepo: `backend/` (FastAPI + SQLAlchemy síncrono + Alembic + Postgres), `frontend/` (React 19 + Vite + TS, PWA, react-router), `dev/` (Postgres local, e2e, carga). Em produção uma imagem Docker só (`Dockerfile`): o backend serve `frontend/dist` na mesma origem (fallback SPA em `main.py`), roda `alembic upgrade head` na subida.

Backend em camadas: `app/api/*` (routers: auth, game, trades, admin, dev) são finos; a regra de negócio fica em `app/services/*` (packs, trades, album, codes, content, images, mail, core). `core.py` tem `cfg()` (parâmetros do jogo lidos da tabela `Config`, editáveis no admin e valendo na hora), `audit`, `enforce_rate` (rate limit em tabela `RateLimitEvent`, por IP/chave) e `now()`.

Pontos que exigem ler vários arquivos:

- **CSRF:** `main.py` exige header `X-Requested-With: album` em todo método não seguro de `/api/*`; sem CORS. Frontend (`api.ts`) já envia.
- **Estado em memória / processo único:** o `Hub` de WebSocket das trocas (`api/trades.py`) e o `_sweeper` (varre trocas expiradas a cada 3 s, em `main.py`) vivem em memória. Nunca usar `--workers` > 1. Atrás de proxy, `--proxy-headers` é obrigatório (rate limit por IP).
- **Mídia:** imagens viram WebP ≤150 KB (`services/images.py`), com sufixo aleatório no nome. Armazenamento em `backend/media/` ou, com `R2_*`, num bucket R2 privado; sempre servidas por `/media/{path}` só para usuário logado.
- **E-mail:** dev usa caixa em memória (`/api/dev/mailbox`); prod usa SMTP ou API do Brevo (`BREVO_API_KEY`).
- **`APP_ENV`:** `prod` desliga docs, `/api/dev/*` (mailbox e atalhos que dão pacotes/códigos) e liga HSTS. Em prod `seed-demo` não cria admin. Testes usam `APP_ENV=test` (definido em `tests/conftest.py`, que também fixa `DATABASE_URL`).
- **Schema:** mudanças via migrations em `backend/migrations/versions/` (numeradas `000N_`); `0002` carrega dados iniciais (raridades, parâmetros, tipos de pacote).
- Frontend: páginas em `frontend/src/pages/`, `api.ts` (cliente), `auth.tsx` (contexto), `fx.ts` (efeitos), `public/sw.js` (service worker).

`.claude/skills/` contém só skills genéricas do Neon, sem relação com o código do app.
