# UniAlbum

Plataforma de álbuns de figurinhas sazonais para universidades, web e mobile-first. Cada álbum tem título, capa, início e fim (cerca de 3 meses) e pertence a uma universidade; passado o fim, ele fica **imutável** e consultável na conta de quem jogou. Dentro de um álbum, cada organização estudantil tem seu capítulo, e os jogadores completam a coleção abrindo pacotes, resgatando códigos (por QR ou texto) e trocando repetidas ao vivo.

O primeiro álbum é o "Calourada". Por enquanto há **uma universidade só**: toda conta nova entra nela, sem tela de escolha.

## O que dá para fazer

- **Álbum:** páginas por organização e mosaicos que se revelam peça a peça. O verso da figurinha só aparece depois de colar.
- **Pacotes:** gaveta de 3 espaços abastecida por um timer contínuo, com sorteio feito no servidor.
- **Códigos:** cada um vale um pacote extra e só pode ser usado uma vez.
- **Trocas:** em tempo real, por QR ou link, com aceite dos dois lados.
- **Meus álbuns:** o álbum em andamento (com timer até o fim e progresso) e os encerrados, com colados/total, recebidas, repetidas e trocas, abertos em modo somente leitura.
- **Admin:** álbuns (criar, agendar, duplicar), capítulos, figurinhas, raridades, tipos de pacote, códigos com QR, parâmetros por álbum e métricas, com um estimador que mostra se o álbum ficou generoso ou apertado demais.
- **Editores por capítulo:** quem recebe o papel monta o próprio capítulo (layouts, textos, imagens com recorte, prévia ao vivo) e envia para revisão. **Só o superadmin, com verificação em duas etapas, aprova e publica.**

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

# Front: build (tsc + vite) e testes unitários (decodificação de QR)
cd frontend && npm run build && npm test

# Ponta a ponta em viewport de celular, com o Chrome instalado:
cd backend && APP_ENV=dev BASE_URL=http://localhost:8000 .venv/bin/python -m uvicorn app.main:app --port 8000
node dev/e2e.mjs                               # precisa do seed-demo aplicado
node dev/e2e-editor.mjs                        # editor, revisão com 2FA, publicação e revogação

# Carga, com o backend no ar:
cd backend && .venv/bin/python ../dev/load.py 50 5 2
```

## Álbuns, papéis e publicação

**Estado do álbum** sai das datas, com o relógio do servidor (não há cron): `rascunho` e `pronto` são gravados; agendado, ativo e encerrado são derivados. O banco impede dois álbuns prontos com períodos sobrepostos na mesma universidade. Ao passar do fim, toda escrita de jogo responde 409, as trocas abertas expiram e o resumo final de cada jogador é gravado uma vez (no primeiro acesso, pela varredura de 3 s ou por `close-albums`).

**Parâmetros por álbum:** o `Config` é o padrão global; `album_config` sobrescreve por álbum (gaveta, boas-vindas, premium, validade de troca). Os tipos de pacote são copiados, com pesos explícitos, para cada álbum novo. Mudar o peso global de uma raridade depois não altera álbuns existentes.

**Congelamento:** com o álbum em andamento texto, imagem e layout mudam e dá para **acrescentar figurinhas** pelo editor (entram no próximo número livre, com a raridade escolhida na publicação, e passam a sair nos pacotes); não se remove, desativa, renumera nem muda a raridade de figurinha existente, e o mosaico não é criado nem muda de grade. Álbum encerrado não muda nada. Figurinha com inventário ou histórico não pode ser apagada.

**Fluxo de publicação:**

1. O superadmin concede, por e-mail e por capítulo, o papel de **editor** (escreve) ou de **revisor** (aprova, rejeita, publica e desfaz as revisões daquele capítulo, sem ser superadmin; só vê a fila dele, não julga a revisão que ele mesmo escreveu e também precisa do 2FA). O papel é conferido no banco a cada requisição, então revogar vale na hora.
2. O editor escreve um **rascunho** (um por capítulo) e envia. Rascunho é um documento JSON validado: sem número, raridade ou URL de imagem; links só `https`; imagens só as enviadas por ele ao próprio capítulo (reencodadas em WebP, privadas até a publicação).
3. O superadmin vê a fila em `/admin/revisoes`, com diff, imagens lado a lado e prévia do publicado e do proposto, e **aprova** ou **rejeita** com nota (rejeitada volta a ser editável).
4. **Publicar** aplica a revisão aprovada numa transação, escolhendo número e raridade das figurinhas novas (padrão: próximo número livre; raridade sugerida pelo editor ou a mais comum). A API do editor nunca escreve nas tabelas de produção; esse é o único caminho.
5. **Rollback** republica o estado que havia antes, pelas mesmas regras. Tudo é auditado (`role.*`, `revision.*`, `asset.*`).

**2FA do superadmin (TOTP):** ativação na página **Conta** (QR, código de confirmação e 10 códigos de recuperação, guardados só como hash). Aprovar, publicar, desfazer e conceder papéis exigem o código confirmado nos últimos 15 minutos da sessão; um código não vale duas vezes e 5 erros bloqueiam por 15 minutos. Perdeu o celular e os códigos? `python -m app.cli reset-2fa <email>`.

**CLI:**

```bash
python -m app.cli create-album "Inverno" 2027-03-01 2027-06-01 [--ready] [--from-album ID]
python -m app.cli close-albums                 # fecha em lote os que já acabaram
python -m app.cli purge-campaign --album-id ID --confirm       # só o que foi jogado nesse álbum
python -m app.cli purge-campaign --all-accounts --confirm      # todas as contas de jogadores
python -m app.cli reset-2fa voce@exemplo.com
```

**Migrações:** `0004` (álbuns, sem perda de dados: a campanha existente vira o álbum "Calourada"), `0005` (figurinha com histórico não some), `0006` (remove colunas antigas), `0007` (papéis, revisões, 2FA), `0008` (papel de revisor). Todas sobem e descem; `downgrade` da `0004` recusa se houver mais de um álbum. Teste sempre numa cópia: rode `seed-demo`, `alembic upgrade head` e `alembic downgrade -1`.

## Produção

Configure `backend/.env` a partir de `.env.example` com `APP_ENV=prod`, `BASE_URL` https e SMTP. Depois:

```bash
cd frontend && npm run build                  # o backend entrega frontend/dist na mesma origem
cd backend && .venv/bin/alembic upgrade head
.venv/bin/python -m app.cli create-admin voce@exemplo.com Admin
.venv/bin/uvicorn app.main:app --port 8000 --proxy-headers
```

- **No Render:** crie um Web Service com runtime Docker. O `Dockerfile` compila o front e roda a migração na subida. As variáveis entram pelo painel, em **Add from .env**, e o health check é `/api/health`. Sem shell no plano gratuito, crie o admin rodando `create-admin` localmente com o `DATABASE_URL` da Neon.
- **`APP_ENV=prod` é obrigatório.** Fora de produção ficam ligados a caixa de e-mails em `/api/dev/mailbox`, que expõe links de confirmação e de troca de senha, e os atalhos temporários que dão pacotes e códigos.
- Em produção, `seed-demo` cria o conteúdo fictício sem a conta admin e com uma senha aleatória para Ana e Bia, impressa no terminal. A senha `album12345` só vale em dev.
- **Use um processo só (sem `--workers`).** Os avisos em tempo real da troca ficam em memória. Com mais processos, o aviso só chega por leitura periódica (5 s) até o `Hub` passar a usar LISTEN/NOTIFY.
- `--proxy-headers` é necessário atrás de proxy; sem ele, o limite de tentativas por IP trata todo mundo como um único IP.
- Em servidor sem disco persistente, guarde as imagens no Cloudflare R2 (`R2_*`), em um bucket privado: só usuários logados as recebem, pelo próprio servidor. Sem isso, elas ficam em `backend/media/`, que precisa de backup.
- Se o servidor bloquear SMTP, como o plano gratuito do Render, envie os e-mails pela API do Brevo (`BREVO_API_KEY`).
- Fim da campanha (LGPD, irreversível): `purge-campaign` com `--album-id` ou `--all-accounts` (ver acima).

## Conteúdo

Crie álbuns e capítulos em `/admin/albums` e figurinhas em `/admin`, sempre dentro de um álbum. Em **Álbuns → Capítulos → Gerar mosaico**, a imagem é cortada nas peças e cada peça vira uma figurinha. Imagens enviadas viram WebP de até 150 KB, guardadas em `orgs/<org>/logo-…`, `orgs/<org>/mosaico-…`, `orgs/<org>/pecas/peca-01-…` e `orgs/<org>/figurinhas/007-nome-…`. O sufixo aleatório impede adivinhar a URL de figurinhas que o jogador ainda não tem. O layout da página fica em `slot_layout` (x, y, w, h em %), e figurinha que alguém já tem não pode ser apagada.

Pesos, quantidades, tetos, tempos e limites ficam na aba **Parâmetros** e nos tipos de pacote, e valem na hora.

## Commits

Seguimos [Conventional Commits](https://www.conventionalcommits.org/pt-br/): `tipo(escopo): descrição`, com tipos como `feat`, `fix`, `docs`, `test`, `refactor` e `chore`.

## Regras do jogo

- O timer da gaveta só corre enquanto há espaço livre. Com a gaveta cheia ele para, e recomeça do zero quando um pacote é aberto.
- O tempo fora do app conta, até encher a gaveta. Pacotes de código ficam fora dela.
- Em dia de premium, o primeiro pacote entregue no dia é o premium.
- Repetida é tudo além de 1 exemplar, colado ou não; esse exemplar nunca pode ser trocado.
- Cada lado precisa oferecer pelo menos 1 figurinha para aceitar uma troca.
- O sorteio evita repetir figurinha no mesmo pacote enquanto houver opção.

## Modelo de dados

Campos que não são óbvios pelo nome:

- `album_participation.next_pack_at`: fim do ciclo atual do timer da gaveta, por jogador e por álbum (nulo com a gaveta cheia).
- `album_participation.last_daily_grant_on`: dia da última entrega, usado no premium e nos pacotes de boas-vindas.
- `album_participation.final_stats`: resumo gravado uma vez no fechamento do álbum.
- `album_chapter`: a org dentro de um álbum; guarda a ordem, o mosaico, o layout publicado e a revisão no ar.
- `sticker.content_key`: identidade estável da figurinha no conteúdo do editor; `(album_id, number)` é único.
- `chapter_revision.previous_content`: o que estava em produção antes da publicação, usado no rollback.
- `asset`: imagem de editor, `pending` (privada) até ser publicada.
- `rarity.rank`: ordena raridades para a garantia mínima.
- `pack_type.slug`: referência estável usada nos parâmetros.
- `trade.creator_accepted_version` e `partner_accepted_version`: o aceite é de cada lado e vale para uma versão da oferta.
