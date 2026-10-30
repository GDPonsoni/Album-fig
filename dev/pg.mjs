// Postgres local sem Docker/root, só para desenvolvimento e testes. Porta 5433.
import EmbeddedPostgres from 'embedded-postgres';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dataDir = join(dirname(fileURLToPath(import.meta.url)), '.pgdata');
const firstRun = !existsSync(join(dataDir, 'PG_VERSION'));
const pg = new EmbeddedPostgres({
  databaseDir: dataDir, user: 'album', password: 'album', port: 5433, persistent: true,
  postgresFlags: ['-c', 'max_connections=200'],
});

if (firstRun) await pg.initialise();
await pg.start();
if (firstRun) {
  await pg.createDatabase('album');
  await pg.createDatabase('album_test');
}
console.log('Postgres pronto em localhost:5433 (bancos: album, album_test). Ctrl+C para parar.');

const stop = async () => { await pg.stop(); process.exit(0); };
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
