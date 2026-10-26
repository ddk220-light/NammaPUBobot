import { readFile } from 'node:fs/promises';
const [origin, guild, game, file, mode = 'preview'] = process.argv.slice(2);
if (
  !origin ||
  new URL(origin).protocol !== 'https:' ||
  !/^\d{17,20}$/.test(guild ?? '') ||
  !game ||
  !file ||
  !['preview', 'apply'].includes(mode)
)
  throw Error(
    'Usage: node scripts/import-economy.mjs HTTPS_ORIGIN GUILD GAME_UUID BUNDLE.json preview|apply',
  );
const token = process.env.OPS_TOKEN;
if (!token) throw Error('Set OPS_TOKEN in the environment.');
const bundle = JSON.parse(await readFile(file, 'utf8'));
const response = await fetch(`${origin}/ops/communities/${guild}/economy-import`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ gameId: game, bundle, apply: mode === 'apply' }),
  signal: AbortSignal.timeout(60000),
});
const result = await response.json();
if (!response.ok || !result.ok)
  throw Error(result.error ?? `Economy import HTTP ${response.status}`);
console.log(
  JSON.stringify(
    { mode, alreadyImported: result.alreadyImported ?? false, counts: result.counts },
    null,
    2,
  ),
);
