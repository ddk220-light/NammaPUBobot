import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname } from 'node:path';
const [mode, origin, guild, file, copy] = process.argv.slice(2);
if (
  !['backup', 'restore', 'inspect', 'checkpoint'].includes(mode) ||
  !origin ||
  !/^\d{17,20}$/.test(guild ?? '') ||
  !file
)
  throw Error(
    'Usage: node scripts/operational-backup.mjs backup|restore|inspect|checkpoint HTTPS_ORIGIN GUILD FILE [RECOVERY_COPY]',
  );
if (new URL(origin).protocol !== 'https:') throw Error('Operational endpoints require HTTPS.');
const token = process.env.OPS_TOKEN;
if (!token) throw Error('Set OPS_TOKEN in the environment; never put it in command arguments.');
const base = `${origin}/ops/communities/${guild}`;
const query = (params = {}) => {
  const search = new URLSearchParams({ ...params, ...(copy ? { copy } : {}) }).toString();
  return search ? `?${search}` : '';
};
const headers = { Authorization: `Bearer ${token}` };
async function call(path, init = {}, attempts = 3) {
  for (let attempt = 1; ; attempt++) {
    const response = await fetch(base + path, {
      method: 'GET',
      headers,
      signal: AbortSignal.timeout(60000),
      ...init,
    }).catch((error) => ({ ok: false, status: 0, error }));
    if (response.ok) return response.json();
    if (attempt >= attempts || init.method === 'POST' || [400, 403, 404].includes(response.status))
      throw Error(
        `Operational ${mode} returned HTTP ${response.status}. Inspect the request ID; no backup was declared successful.`,
      );
    await new Promise((r) => setTimeout(r, 1000 * attempt));
  }
}
let result, counts;
if (mode === 'restore') {
  const text = await readFile(file, 'utf8'),
    manifest = JSON.parse(await readFile(file + '.manifest.json', 'utf8'));
  if (createHash('sha256').update(text).digest('hex') !== manifest.sha256)
    throw Error('Backup checksum mismatch.');
  if (JSON.parse(text).communityId !== guild) throw Error('Backup community mismatch.');
  result = await call(`/restore${query()}`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: text,
  });
  if (!result.readOnly || result.discordDelivery !== false)
    throw Error('Recovery isolation was not confirmed.');
  console.log('PASS: isolated read-only community restored; Discord delivery remains disabled.');
  process.exit(0);
} else if (mode === 'backup') {
  // Paged: every table is read by rowid cursor, so no table is too large to back up.
  const manifest = await call(`/export${query()}`);
  const tables = {};
  counts = {};
  for (const { name, rows } of manifest.tables) {
    tables[name] = [];
    for (let after = 0; after !== null;) {
      const page = await call(`/export/${name}${query({ after: String(after) })}`);
      tables[name].push(...page.rows);
      after = page.next;
    }
    counts[name] = { manifest: rows, saved: tables[name].length };
    if (rows !== tables[name].length)
      console.warn(
        `NOTE: ${name} changed during the backup (${rows} rows at start, ${tables[name].length} saved). Pages are not one transaction; repeat during a quiet period for an exact snapshot.`,
      );
  }
  const { format, communityId, sourceApplicationId, createdAt } = manifest;
  result = { format, communityId, sourceApplicationId, createdAt, tables };
} else result = await call(`/${mode}${query()}`);
await mkdir(dirname(file), { recursive: true });
const text = JSON.stringify(result, null, 2) + '\n';
await writeFile(file, text, { mode: 0o600 });
await writeFile(
  file + '.manifest.json',
  JSON.stringify(
    {
      mode,
      origin,
      guild,
      copy: copy ?? null,
      createdAt: new Date().toISOString(),
      bytes: Buffer.byteLength(text),
      sha256: createHash('sha256').update(text).digest('hex'),
      ...(counts ? { tables: counts } : {}),
    },
    null,
    2,
  ) + '\n',
  { mode: 0o600 },
);
console.log(`PASS: ${mode} saved with checksum manifest (${Buffer.byteLength(text)} bytes).`);
