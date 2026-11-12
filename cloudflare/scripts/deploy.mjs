#!/usr/bin/env node
/** Deploy, then register that environment's global commands when its Discord credentials exist. */
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { envArg, envCredentials } from './env.mjs';

const argv = process.argv.slice(2),
  name = envArg(argv);
const unknown = argv.filter(
  (a, i) =>
    !['--env', '--confirm-production'].includes(a) &&
    argv[i - 1] !== '--env' &&
    !a.startsWith('--env='),
);
if (unknown.length) throw Error(`Unknown argument ${unknown[0]}.`);
const run = (script, args) =>
  spawnSync(process.execPath, [fileURLToPath(new URL(script, import.meta.url)), ...args], {
    stdio: 'inherit',
    env: process.env,
  }).status ?? 1;

const deployed = run('build-metadata.mjs', [
  'deploy',
  '--env',
  name,
  ...(argv.includes('--confirm-production') ? ['--confirm-production'] : []),
]);
if (deployed !== 0) process.exit(deployed);
try {
  await envCredentials(name, { requireClientSecret: false });
} catch (error) {
  // CI deploys with Cloudflare credentials only; registration is then an operator step.
  console.log(`Deployed. Skipped ${name} command registration: ${error.message}`);
  process.exit(0);
}
process.exitCode = run('register-commands.mjs', ['--env', name]);
if (process.exitCode)
  console.error(
    `Deployed, but ${name} command registration failed; rerun npm run register:${name}.`,
  );
