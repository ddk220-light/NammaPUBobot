#!/usr/bin/env node
import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Native Node fetch needs explicit proxy opt-in in managed environments. Keep
// this in a subprocess because Node reads the setting at startup.
if (
  (process.env.HTTPS_PROXY || process.env.HTTP_PROXY) &&
  process.allowedNodeEnvironmentFlags.has('--use-env-proxy') &&
  !process.execArgv.includes('--use-env-proxy')
) {
  const child = spawnSync(
    process.execPath,
    ['--use-env-proxy', fileURLToPath(import.meta.url), ...process.argv.slice(2)],
    { stdio: 'inherit', env: process.env },
  );
  process.exitCode = child.status ?? 1;
} else {
  try {
    const result = await build({
      entryPoints: [fileURLToPath(new URL('provider-check.ts', import.meta.url))],
      bundle: true,
      write: false,
      platform: 'node',
      format: 'esm',
      target: 'node22',
      logLevel: 'silent',
    });
    const { main } = await import(
      `data:text/javascript;base64,${Buffer.from(result.outputFiles[0].contents).toString('base64')}`
    );
    process.exitCode = await main(process.argv.slice(2), process.env);
  } catch {
    // Build/operation errors may contain provider URLs. Never echo them to CI.
    process.stderr.write(
      'Provider check could not run. Check the local build and command arguments; no provider diagnostics were retained.\n',
    );
    process.exitCode = 1;
  }
}
