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
await shot(ana, '02b-pacotes');
await openPack(ana);
step('pacote recebido e aberto');
await ana.getByRole('link', { name: 'Colar no álbum' }).click();
const tile = ana.getByRole('button', { name: /^Colar / }).first();
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
  await ana.getByRole('button', { name: 'Fechar' }).waitFor();
  await ana.waitForTimeout(800);
  await shot(ana, '09-verso');
  await ana.getByRole('button', { name: 'Fechar' }).click();
  step('verso visível depois de colar');
}

// Colar o resto, inclusive peça de mosaico se houver
for (let i = 0; i < 12; i += 1) {
  const next = ana.getByRole('button', { name: /^Colar / }).first();
  if (!(await next.count())) break;
  await next.click();
  await ana.waitForTimeout(900);
}

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

await browser.close();
if (problems.length) {
  console.error('\nProblemas:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nTudo certo.');
