// Roteiro de ponta a ponta dos critérios de aceite, em viewport de celular.
// Pré-requisitos: Postgres (dev/pg.mjs), seed-demo e backend em :8000 servindo o front compilado.
// Uso: node dev/e2e.mjs [pasta-de-screenshots]
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, devices } from 'playwright-core';

const BASE = process.env.E2E_BASE ?? 'http://localhost:8000';
const shots = process.argv[2] ?? join(dirname(fileURLToPath(import.meta.url)), '.e2e');
mkdirSync(shots, { recursive: true });
const backend = join(dirname(fileURLToPath(import.meta.url)), '..', 'backend');
const stamp = Date.now().toString(36);
const problems = [];

const browser = await chromium.launch({ channel: 'chrome', headless: true });

async function phone() {
  const context = await browser.newContext({ ...devices['Pixel 5'], locale: 'pt-BR' });
  const page = await context.newPage();
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/status of 4\d\d/.test(m.text())) problems.push(`console: ${m.text()}`);
  });
  return page;
}
const shot = (page, name) => page.screenshot({ path: join(shots, `${name}.png`) });
const step = (text) => console.log(`✓ ${text}`);

async function signup(page, nickname) {
  const email = `${nickname.toLowerCase()}@example.com`;
  await page.goto(`${BASE}/cadastro`);
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Apelido').fill(nickname);
  await page.getByLabel('Senha').fill('senha-e2e-123');
  await shot(page, '01-cadastro');
  await page.getByRole('button', { name: 'Criar conta' }).click();
  await page.getByText('enviamos um link').waitFor();
  const mails = await (await page.request.get(`${BASE}/api/dev/mailbox`)).json();
  const mail = mails.reverse().find((m) => m.to === email);
  const token = mail.body.match(/token=([\w-]+)/)[1];
  await page.goto(`${BASE}/entrar`);
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha').fill('senha-e2e-123');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.getByRole('navigation', { name: 'Principal' }).waitFor();
  await page.goto(`${BASE}/confirmar?token=${token}`);
  await page.getByText('E-mail confirmado').waitFor();
  await page.getByRole('link', { name: 'Ir para o álbum' }).click();
  await page.getByRole('navigation', { name: 'Principal' }).waitFor();
}

async function openPack(page) {
  await page.getByRole('link', { name: /Pacotes/ }).click();
  await page.getByRole('button', { name: /^Abrir Pacote/ }).first().click();
  await page.getByRole('button', { name: 'Rasgar pacote' }).waitFor();
  await shot(page, '03-pacote-fechado');
  await page.getByRole('button', { name: 'Rasgar pacote' }).click();
  await page.getByRole('button', { name: 'Próxima' }).waitFor();
  await page.waitForTimeout(500);
  await shot(page, '04-revelacao');
  while (await page.getByRole('button', { name: 'Próxima' }).isVisible()) {
    await page.getByRole('button', { name: 'Próxima' }).click();
    await page.waitForTimeout(60);
  }
  await page.getByRole('link', { name: 'Colar no álbum' }).waitFor();
  await shot(page, '05-resumo');
}

// ---- Critério 1: cadastro, confirmação, pacote, colar, progresso
const ana = await phone();
await signup(ana, `Ana${stamp}`);
step('cadastro e confirmação por e-mail');
await shot(ana, '02-capa');
await ana.getByRole('link', { name: /Pacotes/ }).click();
await ana.getByRole('button', { name: /^Abrir Pacote/ }).first().waitFor();
await ana.getByRole('heading', { name: '2 pacotes prontos' }).waitFor();
await ana.getByRole('timer').waitFor();
await shot(ana, '02b-pacotes');
await openPack(ana);
step('pacote recebido e aberto');
await ana.getByRole('link', { name: 'Colar no álbum' }).click();
const tile = ana.locator('.tray-tile').first();
await tile.waitFor();
await shot(ana, '06-bandeja');
await tile.click();
await ana.waitForTimeout(250);
await shot(ana, '07-voando');
await ana.locator('.album-bar-text small').filter({ hasText: /1\/|1 de/ }).first().waitFor({ timeout: 5000 });
await ana.waitForTimeout(300);
await shot(ana, '08-colada');
step('figurinha colada e progresso atualizado');

// Verso só depois de colada
const filled = ana.locator('.album-panel [data-slot]:not([disabled])').first();
if (await filled.count()) {
  await filled.click();
  const virar = ana.getByRole('button', { name: 'Virar' });
  await virar.waitFor();
  if (await ana.locator('.flip.flipped').count()) throw new Error('figurinha virou sozinha');
  await virar.click();
  await ana.locator('.flip.flipped').waitFor();
  await ana.waitForTimeout(600);
  await shot(ana, '09-verso');
  await ana.getByRole('button', { name: 'Ver frente' }).click();
  await ana.locator('.flip:not(.flipped)').waitFor();
  await ana.getByRole('button', { name: 'Fechar' }).click();
  step('verso visível depois de colar');
}

// Colar em sequência rápida: a animação não espera a rede e não cola em dobro
for (let i = 0; i < 2; i += 1) {
  const next = ana.locator('.tray-tile').first();
  if (!(await next.count())) break;
  await next.dblclick();
  await ana.waitForTimeout(900);
}
const coladas = async (page) => parseInt((await (await page.request.get(`${BASE}/api/album`)).json()).filled, 10);
if ((await coladas(ana)) !== 3) problems.push(`toque duplo colou errado: ${await coladas(ana)} coladas, esperado 3`);
step('colar em sequência, sem colagem em dobro');

// Colar tudo: o resto da bandeja de uma vez
const faltam = (await (await ana.request.get(`${BASE}/api/album`)).json()).tray.length;
await ana.getByRole('button', { name: 'Colar tudo' }).click();
await ana.waitForTimeout(200);
await shot(ana, '09b-colar-tudo');
await ana.getByText('Bandeja vazia').waitFor({ timeout: 8000 });
if ((await coladas(ana)) !== 3 + faltam) problems.push('colar tudo não colou a bandeja inteira');
await shot(ana, '09c-tudo-colado');
step('colar tudo esvazia a bandeja');

// O pacote aberto continua visível na tela de pacotes, com os selos
await ana.getByRole('link', { name: /Pacotes/ }).click();
const historico = ana.getByRole('region', { name: 'Últimos pacotes abertos' });
await historico.waitFor();
if ((await historico.locator('.history-pack').count()) !== 1) problems.push('histórico deveria ter 1 pacote');
if (!(await historico.locator('.seal-new, .seal-dup').count())) problems.push('histórico sem selos Nova/Repetida');
await openPack(ana);
await ana.getByRole('button', { name: 'Fechar' }).click();
await historico.locator('.history-pack').nth(1).waitFor();
await shot(ana, '09d-historico');
step('pacotes abertos ficam no histórico, sem recarregar');
await ana.getByRole('link', { name: 'Álbum' }).click();
await ana.getByRole('button', { name: 'Colar tudo' }).click();
await ana.getByText('Bandeja vazia').waitFor({ timeout: 8000 });

// Swipe entre páginas, nos dois sentidos, a partir de uma página conhecida
async function swipe(page, fromRight) {
  const box = await page.locator('.album-stage').boundingBox();
  const [start, end] = fromRight ? [box.x + box.width - 30, box.x + 30] : [box.x + 30, box.x + box.width - 30];
  await page.mouse.move(start, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(end, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(450);
  return page.locator('.album-bar-text').innerText();
}
await ana.goto(BASE);
await ana.getByRole('button', { name: /Tubarões da Engenharia/ }).click();
await ana.locator('.album-bar-text').getByText('Página 1').waitFor();
if (!(await swipe(ana, true)).includes('Página 2')) problems.push('swipe para a esquerda não avançou');
if (!(await swipe(ana, false)).includes('Página 1')) problems.push('swipe para a direita não voltou');
if (!(await swipe(ana, false)).includes('Capa')) problems.push('swipe não voltou para a capa');
if (new URL(ana.url()).search) problems.push('capa ficou com página na URL');
await shot(ana, '10-swipe');
step('swipe nos dois sentidos, até a capa');

// Voltar para a capa: pelo botão e pelo voltar do celular, de qualquer página
await ana.getByRole('button', { name: /Tubarões da Engenharia/ }).click();
await ana.getByRole('button', { name: 'Capa' }).click();
await ana.getByRole('button', { name: /Lobos da Arquitetura/ }).waitFor();
await ana.getByRole('button', { name: /Corujas da Medicina/ }).click();
await ana.locator('.album-bar-text').getByText('Corujas da Medicina').waitFor();
await shot(ana, '10b-dentro-da-org');
await ana.getByRole('button', { name: 'Próxima página' }).click();
await ana.waitForTimeout(350);
await ana.goBack();
await ana.getByRole('button', { name: /Lobos da Arquitetura/ }).waitFor();
if (new URL(ana.url()).pathname !== '/') problems.push('voltar do celular saiu do álbum');
await ana.goForward();
await ana.locator('.album-bar-text').getByText('Corujas da Medicina').waitFor();
await ana.reload();
await ana.locator('.album-bar-text').getByText('Corujas da Medicina').waitFor();
await ana.getByRole('button', { name: 'Capa' }).click();
await ana.getByRole('button', { name: /Lobos da Arquitetura/ }).waitFor();
step('capa pelo botão e pelo voltar; recarregar mantém a página');

// ---- Mosaico: vai até a última página da primeira org
await ana.goto(BASE);
for (let i = 0; i < 26; i += 1) {
  const label = await ana.locator('.album-bar-text small').innerText();
  if (label.startsWith('Mosaico')) break;
  await ana.getByRole('button', { name: 'Próxima página' }).click();
  await ana.waitForTimeout(320);
}
await ana.waitForTimeout(300);
await shot(ana, '11-mosaico');
step('página de mosaico renderizada');

// ---- Critério 2: troca via link
const nickBia = `Bia${stamp}`;
const bia = await phone();
await signup(bia, nickBia);
step('segunda conta pronta');
execFileSync(join(backend, '.venv/bin/python'), ['-c', `
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User, Sticker
from app.services.packs import add_to_inventory
with SessionLocal() as db:
    stickers = db.scalars(select(Sticker).order_by(Sticker.number).limit(4)).all()
    ana = db.scalar(select(User).where(User.nickname == 'Ana${stamp}'))
    bia = db.scalar(select(User).where(User.nickname == '${nickBia}'))
    add_to_inventory(db, ana.id, stickers[0].id, 3)
    add_to_inventory(db, bia.id, stickers[3].id, 3)
    db.commit()
`], { cwd: backend });

await ana.getByRole('link', { name: 'Trocar' }).click();
await ana.getByRole('button', { name: 'Trocar', exact: true }).click();
await ana.getByRole('button', { name: 'Compartilhar link' }).waitFor();
await ana.waitForTimeout(200);
await shot(ana, '12-qr');
const link = ana.url();
await bia.goto(link);
await bia.getByRole('button', { name: 'Entrar na troca' }).click();
await ana.getByText(`Trocando com`).waitFor();
step('entrada pelo link propaga para quem criou (tempo real)');

await ana.locator('.dupes button').first().click();
await bia.locator('.offer-col').nth(1).locator('li').first().waitFor();
await bia.locator('.dupes button').first().click();
await ana.locator('.offer-col').nth(1).locator('li').first().waitFor();
step('ofertas aparecem em tempo real');
await ana.waitForTimeout(150);
await shot(ana, '13-troca');
await ana.getByRole('button', { name: 'Aceitar troca' }).click();
await bia.getByText('Aceitou').waitFor();
await bia.getByRole('button', { name: 'Aceitar troca' }).click();
await ana.getByText('Troca feita!').waitFor();
await bia.getByText('Troca feita!').waitFor();
await shot(ana, '14-troca-feita');
step('troca concluída nos dois aparelhos');

// Mosaico com peças coladas, revelando a imagem
execFileSync(join(backend, '.venv/bin/python'), ['-c', `
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User, Sticker
from app.services.packs import add_to_inventory
from app.models import UserSticker
with SessionLocal() as db:
    ana = db.scalar(select(User).where(User.nickname == 'Ana${stamp}'))
    for s in db.scalars(select(Sticker).where(Sticker.is_mosaic_piece, Sticker.mosaic_index < 3)):
        add_to_inventory(db, ana.id, s.id, 1)
        db.flush()
        db.get(UserSticker, (ana.id, s.id)).pasted = True
    db.commit()
`], { cwd: backend });
await ana.goto(BASE);
for (let i = 0; i < 26; i += 1) {
  const label = await ana.locator('.album-bar-text small').innerText();
  if (label.startsWith('Mosaico')) break;
  await ana.getByRole('button', { name: 'Próxima página' }).click();
  await ana.waitForTimeout(320);
}
await ana.locator('.mosaic-piece.filled').first().waitFor();
await ana.waitForTimeout(300);
await shot(ana, '14b-mosaico-revelado');
step('mosaico revela as peças coladas');

// Link usado não vale mais para um terceiro
const eve = await phone();
await signup(eve, `Eve${stamp}`);
await eve.goto(link);
await eve.getByText('não é mais válido').waitFor();
step('link usado é recusado');

// ---- Resgate de código, com erro genérico
await eve.goto(`${BASE}/pacotes`);
await eve.getByLabel('Código').fill('AAAA-BBBB-CCCC-DDDD');
await eve.getByRole('button', { name: 'Resgatar' }).click();
await eve.getByText('Código inválido').waitFor();
await shot(eve, '15-codigo');
step('código inválido recebe erro genérico');

// ---- Link de QR de código: abrir não consome, o botão resgata
const { code: qrCode } = await (await eve.request.post(`${BASE}/api/dev/codes`, { data: { slug: 'comum' }, headers: { 'X-Requested-With': 'album' } })).json();
await eve.goto(`${BASE}/r/${qrCode}`);
await eve.getByRole('heading', { name: 'Resgatar este código?' }).waitFor();
await eve.reload();
await eve.getByRole('heading', { name: 'Resgatar este código?' }).waitFor();
await shot(eve, '15c-link-do-codigo');
await eve.getByRole('button', { name: 'Resgatar' }).click();
await eve.getByRole('heading', { name: 'Código resgatado!' }).waitFor();
await eve.goto(`${BASE}/r/${qrCode}`);
await eve.getByText('Código inválido').waitFor();
step('link do QR mostra a confirmação e só o botão resgata');

// Deslogado, o link do código sobrevive ao login
const { code: qrCode2 } = await (await eve.request.post(`${BASE}/api/dev/codes`, { data: { slug: 'comum' }, headers: { 'X-Requested-With': 'album' } })).json();
const visitante = await phone();
await visitante.goto(`${BASE}/r/${qrCode2}`);
await visitante.getByLabel('E-mail').fill('ana@example.com');
await visitante.getByLabel('Senha').fill('album12345');
await visitante.getByRole('button', { name: 'Entrar' }).click();
await visitante.getByRole('heading', { name: 'Resgatar este código?' }).waitFor();
step('destino do QR preservado no login');

// Ler QR sem câmera: mensagem clara, sem travar
await visitante.goto(`${BASE}/trocar`);
await visitante.getByRole('button', { name: 'Ler QR' }).click();
await visitante.getByRole('dialog', { name: 'Ler QR de troca' }).getByRole('alert').waitFor();
await shot(visitante, '15d-sem-camera');
await visitante.getByRole('button', { name: 'Fechar' }).click();
step('ler QR sem câmera mostra aviso');

await eve.goto(`${BASE}/pacotes`);

// ---- Gaveta e atalhos de teste
const prontos = () => eve.locator('.ready-head h2').innerText();
const antes = parseInt(await prontos(), 10) || 0;
await eve.getByRole('button', { name: '+ Pacote premium' }).click();
await eve.getByRole('heading', { name: `${antes + 1} ` }).waitFor();
await eve.getByRole('button', { name: 'Gerar código comum' }).click();
await eve.getByText('preenchido no campo').waitFor();
await eve.getByRole('button', { name: 'Resgatar' }).click();
await eve.getByText('Código resgatado!').waitFor();
await eve.waitForTimeout(700);
await shot(eve, '15a-codigo-premiado');
await eve.getByRole('button', { name: 'Guardar para depois' }).click();
await eve.getByRole('heading', { name: `${antes + 2} ` }).waitFor();
await shot(eve, '15b-gaveta-extras');
step('atalhos de teste geram pacote e código');

// ---- Admin
const admin = await phone();
await admin.goto(`${BASE}/entrar`);
await admin.getByLabel('E-mail').fill('admin@example.com');
await admin.getByLabel('Senha').fill('album12345');
await admin.getByRole('button', { name: 'Entrar' }).click();
await admin.getByRole('navigation', { name: 'Principal' }).waitFor();
await admin.goto(`${BASE}/admin`);
await admin.getByText('Pacotes abertos').waitFor();
await shot(admin, '16-admin');
await admin.getByRole('button', { name: 'Parâmetros' }).click();
await admin.getByText('daily.stock_cap').waitFor();
await admin.getByRole('button', { name: 'Figurinhas' }).click();
await admin.getByText(/Figurinhas \(\d+\)/).waitFor();
await admin.getByRole('button', { name: 'Editar' }).first().click();
await admin.waitForTimeout(100);
await shot(admin, '17-admin-figurinha');
step('painel admin navega');
await admin.getByRole('button', { name: 'Códigos' }).click();
await admin.getByRole('button', { name: /Imprimir disponíveis com QR/ }).click();
await admin.locator('.code-cards img').first().waitFor();
await shot(admin, '18-admin-codigos-qr');
step('folha de códigos com QR');

// ---- Admin de álbuns: lista, estimador ao vivo e sobreposição recusada com mensagem clara
await admin.goto(`${BASE}/admin/albums`);
await admin.getByText('Em andamento').first().waitFor();
await admin.getByRole('button', { name: 'Abrir' }).first().click();
await admin.getByRole('heading', { name: /Capítulos \(5\)/ }).waitFor();
await admin.locator('.estimate-table').waitFor();
const antesPct = await admin.locator('.estimate-table tbody tr').first().locator('td').last().innerText();
await admin.getByLabel('Minutos entre pacotes da gaveta').fill('20000');
await admin.waitForFunction((old) => document.querySelector('.estimate-table tbody tr td:last-child')?.textContent !== old, antesPct);
const depoisPct = await admin.locator('.estimate-table tbody tr').first().locator('td').last().innerText();
if (parseFloat(depoisPct) >= parseFloat(antesPct)) problems.push(`estimador não reagiu: ${antesPct} -> ${depoisPct}`);
await admin.locator('.estimate-alert').first().waitFor();
await shot(admin, '19-admin-albuns-estimativa');
await admin.getByRole('button', { name: 'Desfazer' }).click();
step('estimador recalcula ao mudar um parâmetro');

await admin.getByRole('button', { name: 'Novo álbum' }).click();
await admin.getByLabel('Título').fill('Sobreposto E2E');
await admin.getByLabel('Pronto para ir ao ar').check();
await admin.getByRole('button', { name: 'Salvar', exact: true }).click();
await admin.getByText('Já existe um álbum pronto nesse período').waitFor();
await shot(admin, '20-admin-sobreposicao');
await admin.getByLabel('Pronto para ir ao ar').uncheck();
await admin.getByLabel('Título').fill(`Rascunho ${stamp}`);
await admin.getByRole('button', { name: 'Salvar', exact: true }).click();
await admin.getByText(`Rascunho ${stamp}`).waitFor();
admin.once('dialog', (dialog) => dialog.accept());
await admin.locator('li', { hasText: `Rascunho ${stamp}` }).getByRole('button', { name: 'Apagar' }).click();
await admin.getByText(`Rascunho ${stamp}`).waitFor({ state: 'detached' });
step('álbum sobreposto é recusado; rascunho é criado e apagado');

// ---- Meus álbuns: em andamento com timer; depois do fim, só leitura com o resumo
await ana.goto(`${BASE}/albuns`);
await ana.getByRole('region', { name: 'Álbum em andamento' }).getByRole('timer').waitFor();
await ana.getByText('Termina em').waitFor();
await shot(ana, '21-meus-albuns');
step('meus álbuns mostra o álbum em andamento e o timer');

const album = (script) => execFileSync(join(backend, '.venv/bin/python'), ['-c', `
from datetime import timedelta
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Album
from app.services.core import now
with SessionLocal() as db:
    album = db.scalar(select(Album).where(Album.state == 'ready').order_by(Album.id).limit(1))
    ${script}
    db.commit()
`], { cwd: backend });
const fimOriginal = execFileSync(join(backend, '.venv/bin/python'), ['-c', `
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Album
with SessionLocal() as db:
    print(db.scalar(select(Album).where(Album.state == 'ready').order_by(Album.id).limit(1)).ends_at.isoformat())
`], { cwd: backend }).toString().trim();
try {
  album(`album.ends_at = now() - timedelta(seconds=5)`);
  await ana.goto(`${BASE}/albuns`);
  await ana.getByRole('heading', { name: 'Nenhum álbum em andamento' }).waitFor();
  await ana.getByRole('link', { name: 'Ver álbum' }).waitFor();
  await shot(ana, '22-album-encerrado-na-conta');
  await ana.getByRole('link', { name: 'Ver álbum' }).click();
  await ana.getByText('somente leitura').waitFor();
  await ana.getByRole('button', { name: /Tubarões da Engenharia/ }).click();
  await ana.locator('.album-panel [data-slot]:not([disabled])').first().waitFor();
  await shot(ana, '23-album-somente-leitura');
  if (await ana.locator('.tray-tile').count()) problems.push('álbum encerrado não deveria ter bandeja');
  await ana.goto(BASE);
  await ana.getByRole('heading', { name: 'Nenhum álbum em andamento' }).waitFor();
  await ana.getByRole('link', { name: /Pacotes/ }).click();
  await ana.getByText('Os pacotes voltam quando o próximo álbum começar').waitFor();
  const recusa = await ana.request.post(`${BASE}/api/album/paste-all`, { headers: { 'X-Requested-With': 'album' } });
  if (recusa.status() !== 409) problems.push(`colar em álbum encerrado deveria dar 409, deu ${recusa.status()}`);
  step('álbum encerrado: fica na conta, abre só para leitura e recusa escrita');
} finally {
  album(`from datetime import datetime; album.ends_at = datetime.fromisoformat('${fimOriginal}')`);
}

await browser.close();
if (problems.length) {
  console.error('\nProblemas:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nTudo certo.');
