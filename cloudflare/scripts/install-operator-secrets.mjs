import { readFile } from 'node:fs/promises';
import { parseEnv } from 'node:util';
import { spawn } from 'node:child_process';
import { discordRequest } from './env.mjs';
const [configFile, credentialsFile] = process.argv.slice(2);
if (!configFile || !credentialsFile)
  throw Error('Usage: node scripts/install-operator-secrets.mjs WRANGLER_CONFIG PRIVATE_ENV_FILE');
const config = JSON.parse(await readFile(configFile, 'utf8'));
const supplied = parseEnv(await readFile(credentialsFile, 'utf8'));
const names = [
  'DISCORD_APPLICATION_ID',
  'DISCORD_PUBLIC_KEY',
  'DISCORD_CLIENT_SECRET',
  'DISCORD_BOT_TOKEN',
  'OPS_TOKEN',
  'CONNECTOR_TOKEN',
];
const credentials = Object.fromEntries(
  names.filter((n) => supplied[n]).map((n) => [n, supplied[n]]),
);
for (const name of [
  'DISCORD_APPLICATION_ID',
  'DISCORD_PUBLIC_KEY',
  'DISCORD_CLIENT_SECRET',
  'DISCORD_BOT_TOKEN',
  'OPS_TOKEN',
])
  if (!credentials[name]) throw Error(`Missing ${name}.`);
if (!/^[a-f0-9]{64}$/.test(credentials.OPS_TOKEN))
  throw Error('OPS_TOKEN must be a random 32-byte hexadecimal key.');
const app = await discordRequest('/oauth2/applications/@me', credentials.DISCORD_BOT_TOKEN);
if (
  app.id !== credentials.DISCORD_APPLICATION_ID ||
  app.verify_key !== credentials.DISCORD_PUBLIC_KEY
)
  throw Error('Discord credentials do not identify the same application.');
if (config.vars?.APP_ORIGIN === undefined || !config.name || config.env)
  throw Error('Use a standalone operator configuration.');
await new Promise((resolve, reject) => {
  const child = spawn('node_modules/.bin/wrangler', ['secret', 'bulk', '--config', configFile], {
    stdio: ['pipe', 'inherit', 'inherit'],
    env: { ...process.env, WRANGLER_LOG_PATH: '/private/tmp/community-operator-wrangler.log' },
  });
  child.stdin.end(JSON.stringify(credentials));
  child.on('error', reject);
  child.on('exit', (code) =>
    code === 0 ? resolve() : reject(Error(`Secret installation failed (${code}).`)),
  );
});
console.log(
  `Credentials installed for ${config.name}; values were not printed. Register the app's own OAuth redirect and signed interaction endpoint before enabling it.`,
);
