#!/usr/bin/env node
/** Build/deploy only through this wrapper so /health identifies the source tree. */
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const app = fileURLToPath(new URL('../', import.meta.url));
const git = (...args) => execFileSync('git', args, { cwd: app, encoding: 'utf8' });
const root = git('rev-parse', '--show-toplevel').trim();
const revision = git('rev-parse', 'HEAD').trim();
if (!/^[0-9a-f]{40,64}$/.test(revision)) throw Error('Cannot determine source revision.');
const dirty = git('status', '--porcelain', '--untracked-files=normal').trim().length > 0;
const prefix = path.relative(root, app).split(path.sep).join('/');
const files = [
  ...new Set(
    git('ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '.')
      .split('\0')
      .filter(Boolean),
  ),
].sort();
const hash = createHash('sha256');
for (const name of files) {
  // File list is relative to cloudflare/, not the repository root. Include removed
  // tracked files in the digest so a dirty deletion also has a distinct identity.
  hash.update(`${prefix}/${name}\0`);
  try {
    hash.update(readFileSync(path.join(app, name)));
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    hash.update('<deleted>');
  }
  hash.update('\0');
}
const treeHash = hash.digest('hex');
const pkg = JSON.parse(readFileSync(path.join(app, 'package.json'), 'utf8'));
const metadata = {
  version: pkg.version,
  sourceRevision: revision,
  sourceDirty: dirty,
  sourceTreeHash: treeHash,
};
const args = process.argv.slice(2);
if (!args.length || args[0] === '--print') {
  process.stdout.write(`${JSON.stringify(metadata, null, 2)}\n`);
} else {
  if (!['dev', 'deploy'].includes(args[0])) throw Error('Expected dev, deploy, or --print.');
  const defines = [
    '--define',
    `__SOURCE_REVISION__:${JSON.stringify(revision)}`,
    '--define',
    `__SOURCE_DIRTY__:${JSON.stringify(dirty)}`,
    '--define',
    `__SOURCE_TREE_HASH__:${JSON.stringify(treeHash)}`,
  ];
  const result = spawnSync(
    process.execPath,
    [path.join(app, 'node_modules/wrangler/bin/wrangler.js'), ...args, ...defines],
    { cwd: app, stdio: 'inherit', env: process.env },
  );
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}
