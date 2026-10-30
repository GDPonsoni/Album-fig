# Álbum da Calourada

Álbum de figurinhas digital da calourada, web e mobile-first. Cada organização estudantil tem suas páginas no álbum, e os calouros completam a coleção abrindo pacotes, resgatando códigos espalhados pelo campus e trocando repetidas ao vivo.

## O que dá para fazer

- **Álbum:** páginas por organização e mosaicos que se revelam peça a peça. O verso da figurinha só aparece depois de colar.
- **Pacotes:** gaveta de 3 espaços abastecida por um timer contínuo, com sorteio feito no servidor.
- **Códigos:** cada um vale um pacote extra e só pode ser usado uma vez.
- **Trocas:** em tempo real, por QR ou link, com aceite dos dois lados.
- **Admin:** orgs, figurinhas, raridades, tipos de pacote, códigos, parâmetros e métricas.

**Stack:** React + Vite + TypeScript (PWA), FastAPI + SQLAlchemy + Alembic, PostgreSQL, WebSocket nas trocas.

## Rodar em desenvolvimento

Precisa de Node 22 e Python 3.14. O Postgres vem de um pacote npm, sem Docker nem root (quem preferir: `docker compose up -d`).

```bash
# 1. Banco (deixe rodando; porta 5433)
cd dev && npm install && node pg.mjs

# 2. Backend (porta 8000)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed-demo      # conteúdo fictício; imprime contas e códigos
.venv/bin/uvicorn app.main:app --reload --port 8000

# 3. Front (porta 5173; --host expõe na rede para testar no celular)
cd frontend && npm install && npm run dev
```

Abra http://localhost:5173. Contas de teste: `admin@`, `ana@` e `bia@example.com`, senha `album12345`.

Sem SMTP, os e-mails de confirmação saem no log do backend e em http://localhost:5173/api/dev/mailbox.

Para testar no celular, defina `BASE_URL=http://<ip-da-máquina>:5173` em `backend/.env`; é a base dos links de e-mail. O QR usa o endereço aberto no navegador.

## Testes

```bash
cd backend && .venv/bin/python -m pytest      # usa o banco album_test; precisa do Postgres no ar

# Ponta a ponta em viewport de celular, com o Chrome instalado:
cd frontend && npm run build
cd backend && APP_ENV=dev BASE_URL=http://localhost:8000 .venv/bin/uvicorn app.main:app --port 8000
node dev/e2e.mjs                               # precisa do seed-demo aplicado

# Carga, com o backend no ar:
cd backend && .venv/bin/python ../dev/load.py 50 5 2
```

## Produção

Configure `backend/.env` a partir de `.env.example` com `APP_ENV=prod`, `BASE_URL` https e SMTP. Depois:

```bash
cd frontend && npm run build                  # o backend entrega frontend/dist na mesma origem
cd backend && .venv/bin/alembic upgrade head
.venv/bin/python -m app.cli create-admin voce@exemplo.com Admin
.venv/bin/uvicorn app.main:app --port 8000 --proxy-headers
```

- **`APP_ENV=prod` é obrigatório.** Fora de produção ficam ligados a caixa de e-mails em `/api/dev/mailbox`, que expõe links de confirmação e de troca de senha, e os atalhos temporários que dão pacotes e códigos.
- As contas e senhas de teste só existem com `seed-demo`, que é recusado em produção.
- **Use um processo só (sem `--workers`).** Os avisos em tempo real da troca ficam em memória. Com mais processos, o aviso só chega por leitura periódica (5 s) até o `Hub` passar a usar LISTEN/NOTIFY.
- `--proxy-headers` é necessário atrás de proxy; sem ele, o limite de tentativas por IP trata todo mundo como um único IP.
- Faça backup de `backend/media/`, onde ficam as imagens.
- Fim da campanha (LGPD, irreversível): `python -m app.cli purge-campaign --confirm`.

## Conteúdo

Crie orgs, figurinhas e mosaicos em `/admin`. Em **Orgs → Mosaico**, a imagem é cortada nas peças e cada peça vira uma figurinha. Imagens enviadas viram WebP de até 150 KB. O layout da página fica em `slot_layout` (x, y, w, h em %), e apagar uma figurinha a remove de todos os inventários.

Pesos, quantidades, tetos, tempos e limites ficam na aba **Parâmetros** e nos tipos de pacote, e valem na hora.

## Commits

Seguimos [Conventional Commits](https://www.conventionalcommits.org/pt-br/): `tipo(escopo): descrição`, com tipos como `feat`, `fix`, `docs`, `test`, `refactor` e `chore`.

## Regras do jogo

- O timer da gaveta é um ciclo único por jogador e nunca para. Abrir um pacote não mexe nele, e o ciclo que termina com a gaveta cheia não entrega nada.
- O tempo fora do app conta, até encher a gaveta. Pacotes de código ficam fora dela.
- Em dia de premium, o primeiro pacote entregue no dia é o premium.
- Repetida é tudo além de 1 exemplar, colado ou não; esse exemplar nunca pode ser trocado.
- Cada lado precisa oferecer pelo menos 1 figurinha para aceitar uma troca.
- O sorteio evita repetir figurinha no mesmo pacote enquanto houver opção.

## Modelo de dados

Campos que não são óbvios pelo nome:

- `user.next_pack_at`: fim do ciclo atual do timer da gaveta.
- `user.last_daily_grant_on`: dia da última entrega, usado no premium e nos pacotes de boas-vindas.
- `rarity.rank`: ordena raridades para a garantia mínima.
- `pack_type.slug`: referência estável usada nos parâmetros.
- `trade.creator_accepted_version` e `partner_accepted_version`: o aceite é de cada lado e vale para uma versão da oferta.
