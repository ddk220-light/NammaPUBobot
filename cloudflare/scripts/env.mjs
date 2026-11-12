import { readFile } from 'node:fs/promises';
import { parse } from 'jsonc-parser';

export const ENVIRONMENTS = ['staging', 'production'];
// The deploy refuses this origin; production has no URL until the operator chooses a domain.
export const PLACEHOLDER_ORIGIN = 'https://production-origin.invalid';

/** Reads `--env <name>` (or `--env=<name>`); every operator script names its target explicitly. */
export function envArg(argv = process.argv.slice(2), fallback) {
  const i = argv.findIndex((a) => a === '--env' || a.startsWith('--env='));
  const name = i < 0 ? fallback : argv[i].includes('=') ? argv[i].slice(6) : argv[i + 1];
  if (!ENVIRONMENTS.includes(name)) throw new Error(`Choose --env ${ENVIRONMENTS.join(' or ')}.`);
  return name;
}

export async function envConfig(name) {
  const errors = [],
    source = await readFile(new URL('../wrangler.jsonc', import.meta.url), 'utf8'),
    config = parse(source, errors, { allowTrailingComma: true });
  if (errors.length || !config?.env?.[name])
    throw new Error(`Invalid ${name} Wrangler configuration.`);
  return config.env[name];
}

export function usableOrigin(origin) {
  return !!origin && origin !== PLACEHOLDER_ORIGIN && new URL(origin).protocol === 'https:';
}

export async function envCredentials(name, { requireClientSecret = true } = {}) {
  // Native env-file parsing handles quoted secrets without shell interpolation.
  try {
    process.loadEnvFile(new URL(`../.dev.vars.${name}`, import.meta.url));
  } catch (e) {
    if (e.code !== 'ENOENT') throw e;
  }
  const names = [
    'DISCORD_APPLICATION_ID',
    'DISCORD_PUBLIC_KEY',
    ...(requireClientSecret ? ['DISCORD_CLIENT_SECRET'] : []),
    'DISCORD_BOT_TOKEN',
  ];
  const missing = names.filter((n) => !process.env[n]);
  if (missing.length)
    throw new Error(`Missing ${name} settings: ${missing.join(', ')}. See .dev.vars.example.`);
  if (!/^\d{17,20}$/.test(process.env.DISCORD_APPLICATION_ID))
    throw new Error('Invalid application ID.');
  if (!/^[a-fA-F0-9]{64}$/.test(process.env.DISCORD_PUBLIC_KEY))
    throw new Error('Invalid Discord public key.');
  const config = await envConfig(name);
  if (config.vars.APP_ENV !== name) throw new Error(`Expected isolated ${name} configuration.`);
  return Object.fromEntries(names.map((n) => [n, process.env[n]]));
}
export async function discordRequest(path, token, init = {}) {
  const response = await fetch(`https://discord.com/api/v10${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bot ${token}` },
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok)
    throw new Error(
      `Discord returned HTTP ${response.status}; check the credentials and bot permissions.`,
    );
  return response.json();
}

/** Staging must be a separate "Staging" application; production must never be one. */
export function checkApplicationName(name, appName) {
  const staging = /\bstaging\b/i.test(appName ?? '');
  if (name === 'staging' && !staging)
    throw new Error(
      'Use a separate application whose name includes Staging. Production applications are rejected.',
    );
  if (name === 'production' && staging)
    throw new Error('This is a Staging application; production needs its own application.');
}

export async function verifyApplication(name, credentials) {
  const app = await discordRequest('/oauth2/applications/@me', credentials.DISCORD_BOT_TOKEN);
  if (
    app.id !== credentials.DISCORD_APPLICATION_ID ||
    app.verify_key !== credentials.DISCORD_PUBLIC_KEY
  )
    throw new Error('Token, application ID and public key do not describe the same application.');
  checkApplicationName(name, app.name);
  return app;
}
