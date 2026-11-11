import { spawn } from 'node:child_process';
import { envArg, envCredentials, verifyApplication } from './env.mjs';

const name = envArg();
const credentials = await envCredentials(name);
await verifyApplication(name, credentials);
await new Promise((resolve, reject) => {
  const child = spawn('node_modules/.bin/wrangler', ['secret', 'bulk', '--env', name], {
    stdio: ['pipe', 'inherit', 'inherit'],
    env: {
      ...process.env,
      WRANGLER_LOG_PATH: process.env.WRANGLER_LOG_PATH ?? '/private/tmp/community-wrangler.log',
    },
  });
  child.stdin.end(JSON.stringify(credentials));
  child.on('error', reject);
  child.on('exit', (code) =>
    code === 0 ? resolve() : reject(new Error(`Secret installation failed (${code}).`)),
  );
});
console.log(`${name} secrets installed. No secret values were printed.`);
