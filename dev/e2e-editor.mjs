// Fluxo do editor de ponta a ponta: papel, edição com autosave, envio, aprovação com 2FA, publicação e o jogador vendo.
// Pré-requisitos: os mesmos do e2e.mjs (seed-demo, backend em :8000 com o front compilado). Uso: node dev/e2e-editor.mjs
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, devices } from 'playwright-core';

const BASE = process.env.E2E_BASE ?? 'http://localhost:8000';
const here = dirname(fileURLToPath(import.meta.url));
const shots = join(here, '.e2e');
mkdirSync(shots, { recursive: true });
const backend = join(here, '..', 'backend');
const py = (code) => execFileSync(join(backend, '.venv/bin/python'), ['-c', code], { cwd: backend }).toString().trim();
const problems = [];
const step = (t) => console.log(`✓ ${t}`);

// Estado inicial: Ana vira editora do primeiro capítulo; o admin ganha 2FA ativo. Devolve o segredo.
const secret = py(`
import pyotp
from sqlalchemy import select, delete
from app.db import SessionLocal
from app.models import AlbumChapter, RoleGrant, User, ChapterRevision
from app.services.core import now
with SessionLocal() as db:
    chapter = db.scalar(select(AlbumChapter).order_by(AlbumChapter.id).limit(1))
    ana = db.scalar(select(User).where(User.email == 'ana@example.com'))
    admin = db.scalar(select(User).where(User.email == 'admin@example.com'))
    db.execute(delete(RoleGrant).where(RoleGrant.user_id == ana.id))
    db.execute(delete(ChapterRevision).where(ChapterRevision.chapter_id == chapter.id))
    db.add(RoleGrant(user_id=ana.id, chapter_id=chapter.id, role='editor'))
    admin.totp_secret = pyotp.random_base32(); admin.totp_enabled_at = now(); admin.totp_last_step = None
    db.commit()
    print(admin.totp_secret)
`);
// Passo seguinte: o código atual pode já ter sido gasto na ativação, e o servidor não aceita o mesmo passo duas vezes.
const totp = () => py(`import pyotp, time; print(pyotp.TOTP('${secret}').at(time.time() + 30))`);

const browser = await chromium.launch({ channel: 'chrome', headless: true });
async function open(desktop = false) {
  const context = await browser.newContext(desktop ? { viewport: { width: 1280, height: 900 } } : { ...devices['Pixel 5'], locale: 'pt-BR' });
  const page = await context.newPage();
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !/status of 4\d\d/.test(m.text())) problems.push(`console: ${m.text()}`); });
  return page;
}
const shot = (page, name) => page.screenshot({ path: join(shots, `${name}.png`) });
async function login(page, email) {
  await page.goto(`${BASE}/entrar`);
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha').fill('album12345');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.getByRole('navigation', { name: 'Principal' }).waitFor().catch(() => {});
}

// ---- Editora, no celular
const ana = await open();
await login(ana, 'ana@example.com');
await ana.goto(`${BASE}/conta`);
await ana.getByRole('link', { name: 'Editar meu capítulo' }).click();
await ana.getByText('Álbum em andamento: dá para mudar textos').waitFor();
if (await ana.getByRole('button', { name: 'Nova figurinha' }).isEnabled()) problems.push('criar figurinha deveria estar travado com o álbum em andamento');
await shot(ana, '30-editor');
step('editora entra pelo papel; estrutura travada com o álbum em andamento');

await ana.locator('.editor-sticker').first().click();
const nome = `Editada ${Date.now().toString(36)}`;
await ana.getByLabel('Nome', { exact: true }).fill(nome);
await ana.getByLabel('Texto do verso').fill('Verso escrito pela editora.');
await ana.getByLabel('Título da página').fill('Página reescrita');
await ana.getByText(/Salvo às/).waitFor({ timeout: 8000 });
step('autosave grava o rascunho sozinho');

// Imagem com recorte no cliente: PNG gerado no próprio navegador
const png = join(shots, 'foto-editor.png');
const dataUrl = await ana.evaluate(() => {
  const c = document.createElement('canvas');
  c.width = 300; c.height = 200;
  const x = c.getContext('2d');
  const g = x.createLinearGradient(0, 0, 300, 200);
  g.addColorStop(0, '#0f6e8c'); g.addColorStop(1, '#e0a526');
  x.fillStyle = g; x.fillRect(0, 0, 300, 200);
  x.fillStyle = '#fff'; x.font = '40px sans-serif'; x.fillText('FOTO', 90, 115);
  return c.toDataURL('image/png');
});
writeFileSync(png, Buffer.from(dataUrl.split(',')[1], 'base64'));
await ana.getByLabel(/^Imagem de /).setInputFiles(png);
await ana.getByRole('dialog', { name: /Recortar/ }).waitFor();
await shot(ana, '31-recorte');
await ana.getByRole('button', { name: 'Usar este recorte' }).click();
await ana.getByRole('dialog', { name: /Recortar/ }).waitFor({ state: 'detached' });
await ana.locator('.editor-form .image-field img').waitFor();
await ana.getByText(/Salvo às/).waitFor({ timeout: 8000 });
await shot(ana, '32-editor-com-imagem');
step('upload com recorte no cliente');

// Link inválido é recusado pelo servidor, com aviso
await ana.getByRole('button', { name: 'Adicionar link' }).click();
await ana.getByLabel('Nome do link 1').fill('Golpe');
await ana.getByLabel('Endereço do link 1').fill('javascript:alert(1)');
await ana.getByText(/Não salvou/).waitFor({ timeout: 8000 });
await ana.getByLabel('Endereço do link 1').fill('https://example.com/atletica');
await ana.screenshot({ path: join(shots, '32b-link.png') });
await ana.getByText(/Salvo às/).waitFor({ timeout: 8000 });
step('link não-https é recusado; o válido salva');

await ana.getByRole('button', { name: 'Enviar para revisão' }).click();
await ana.getByText('Enviado. Quem administra').waitFor();
if (await ana.getByLabel('Nome', { exact: true }).isEnabled()) problems.push('depois de enviar, o formulário deveria travar');
await shot(ana, '33-enviado');
step('envio trava o rascunho');

// ---- Superadmin, no desktop: aprova com 2FA, confere diff/prévia e publica
const admin = await open(true);
await login(admin, 'admin@example.com');
await admin.goto(`${BASE}/admin/revisoes`);
await admin.getByRole('button', { name: 'Revisar' }).first().click();
await admin.getByText('O que muda').waitFor();
await admin.getByText(nome).first().waitFor();
await admin.getByRole('figure', { name: 'Proposto' }).waitFor();
await shot(admin, '34-revisao');
await admin.getByRole('button', { name: 'Aprovar' }).click();
await admin.getByRole('dialog', { name: 'Código de duas etapas' }).waitFor(); // a sessão ainda não confirmou o segundo fator
await admin.getByRole('dialog', { name: 'Código de duas etapas' }).getByLabel('Código').fill(totp());
await admin.getByRole('button', { name: 'Confirmar' }).click();
await admin.getByText('Revisão aprovada').waitFor();
step('aprovar pede o código de duas etapas e segue depois de confirmado');
admin.once('dialog', (d) => d.accept());
await admin.getByRole('button', { name: 'Publicar' }).click();
await admin.getByText('Publicada.').waitFor();
await shot(admin, '35-publicada');
step('publicação aplicada');

// ---- O jogador vê o publicado
const eu = await open();
await login(eu, 'ana@example.com');
await eu.goto(BASE);
await eu.getByRole('button', { name: /Tubarões da Engenharia/ }).click();
await eu.locator('.album-bar-text').getByText('Página 1').waitFor();
await eu.getByRole('heading', { name: 'Página reescrita' }).first().waitFor();
await shot(eu, '36-jogador');
step('o jogador vê o título publicado');

// ---- Revogar vale na hora
await admin.goto(`${BASE}/admin/revisoes`);
admin.once('dialog', (d) => d.accept());
await admin.getByRole('button', { name: 'Revogar' }).first().click();
await admin.getByText('Sem editores').first().waitFor();
await ana.reload();
await ana.getByText('Capítulo não encontrado').waitFor();
step('revogar o papel bloqueia o editor na recarga seguinte');

await browser.close();
if (problems.length) { console.error('\nProblemas:\n' + problems.join('\n')); process.exit(1); }
console.log('\nTudo certo.');
