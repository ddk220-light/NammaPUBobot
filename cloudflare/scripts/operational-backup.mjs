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
const url = `${origin}/ops/communities/${guild}/${mode}${copy ? `?copy=${encodeURIComponent(copy)}` : ''}`;
const headers = { Authorization: `Bearer ${token}` };
let body;
if (mode === 'restore') {
  const text = await readFile(file, 'utf8'),
    manifest = JSON.parse(await readFile(file + '.manifest.json', 'utf8'));
  if (createHash('sha256').update(text).digest('hex') !== manifest.sha256)
    throw Error('Backup checksum mismatch.');
  if (JSON.parse(text).communityId !== guild) throw Error('Backup community mismatch.');
  body = text;
  headers['Content-Type'] = 'application/json';
}
const response = await fetch(url, {
  method: mode === 'restore' ? 'POST' : 'GET',
  headers,
  body,
  signal: AbortSignal.timeout(60000),
});
if (!response.ok)
  throw Error(
    `Operational ${mode} returned HTTP ${response.status}. Inspect the request ID; no backup was declared successful.`,
  );
const result = await response.json();
if (mode === 'restore') {
  if (!result.readOnly || result.discordDelivery !== false)
    throw Error('Recovery isolation was not confirmed.');
  console.log('PASS: isolated read-only community restored; Discord delivery remains disabled.');
} else {
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
      },
      null,
      2,
    ) + '\n',
    { mode: 0o600 },
  );
  console.log(`PASS: ${mode} saved with checksum manifest (${Buffer.byteLength(text)} bytes).`);
}
