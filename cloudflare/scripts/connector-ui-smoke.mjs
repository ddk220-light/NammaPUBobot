import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
// The shared browser suite owns the current responsive fixtures and starts Vite.
const result = spawnSync(
  process.execPath,
  [
    fileURLToPath(new URL('../node_modules/@playwright/test/cli.js', import.meta.url)),
    'test',
    'e2e/connector-integration.spec.ts',
    ...process.argv.slice(2),
  ],
  { cwd: fileURLToPath(new URL('../', import.meta.url)), stdio: 'inherit', env: process.env },
);
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
