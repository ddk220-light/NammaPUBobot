import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { parse } from 'jsonc-parser';

// A positive allowlist prevents credentials, local storage and community backups
// from entering the source distribution, even if more evidence files are added.
const pkg = JSON.parse(await readFile('package.json', 'utf8'));
const name = `community-games-${pkg.version}`;
const temp = await mkdtemp(join(tmpdir(), 'community-source-release-'));
const root = join(temp, name);
const hashes = {};
await mkdir(root);
async function copy(path) {
  let data = await readFile(path);
  if (path === 'wrangler.jsonc') {
    const config = parse(data.toString());
    config.env.staging.name = 'community-games-example-staging';
    config.env.staging.vars.APP_ORIGIN = 'https://community-games.example';
    data = Buffer.from(JSON.stringify(config, null, 2) + '\n');
  }
  if (path === 'README.md')
    data = Buffer.from(
      data
        .toString()
        .replace(
          / Historical M1–M4 acceptance notes are in \[the pilot archive\]\(docs\/m1-m4-pilot-history\.md\); those describe earlier UI behavior, not this release\./,
          '',
        ),
    );
  await mkdir(join(root, path, '..'), { recursive: true });
  await writeFile(join(root, path), data);
  hashes[path] = createHash('sha256').update(data).digest('hex');
}
async function directory(path) {
  for (const item of (await readdir(path, { withFileTypes: true })).sort((a, b) =>
    a.name.localeCompare(b.name),
  )) {
    if (['__pycache__', 'node_modules', 'dist', '.DS_Store'].includes(item.name)) continue;
    if (item.name.startsWith('._')) continue;
    if (item.name.startsWith('.dev.vars') || item.name.startsWith('.env')) continue;
    const file = `${path}/${item.name}`;
    if (item.isDirectory()) await directory(file);
    else if (item.isFile() && !/^scripts\/make-.*fixtures\.py$/.test(file)) await copy(file);
    else if (item.isSymbolicLink()) throw Error(`Release cannot include symlink ${file}`);
  }
}
try {
  for (const file of [
    'package.json',
    'package-lock.json',
    'README.md',
    'LICENSE',
    '.gitignore',
    '.prettierrc.json',
    '.dev.vars.example',
    'tsconfig.json',
    'vite.config.ts',
    'vitest.config.ts',
    'wrangler.jsonc',
    'docs/operator-guide.md',
    '.github/workflows/verify.yml',
    'ops/migration-budget.example.json',
  ])
    await copy(file);
  for (const dir of ['src', 'web', 'scripts', 'tests']) await directory(dir);
  const manifest = { version: pkg.version, generatedAt: new Date().toISOString(), files: hashes };
  await writeFile(join(root, 'release-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  await mkdir('ops/releases', { recursive: true });
  const archive = `ops/releases/${name}.tar.gz`;
  const result = spawnSync('tar', ['-czf', archive, '-C', temp, name], {
    stdio: 'inherit',
    env: { ...process.env, COPYFILE_DISABLE: '1' },
  });
  if (result.status !== 0) throw Error('Source archive creation failed.');
  const sha256 = createHash('sha256')
    .update(await readFile(archive))
    .digest('hex');
  await writeFile(`${archive}.sha256`, `${sha256}  ${name}.tar.gz\n`);
  console.log(JSON.stringify({ archive, sha256, files: Object.keys(hashes).length }));
} finally {
  await rm(temp, { recursive: true, force: true });
}
