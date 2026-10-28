import { readFile } from 'node:fs/promises';
import { parse } from 'jsonc-parser';

export async function stagingConfig() {
  const errors = [],
    config = parse(await readFile('wrangler.jsonc', 'utf8'), errors, { allowTrailingComma: true });
  if (errors.length || !config?.env?.staging)
    throw new Error('Invalid staging Wrangler configuration.');
  return config.env.staging;
}

export async function stagingCredentials({ requireClientSecret = true } = {}) {
  // Native env-file parsing handles quoted secrets without shell interpolation.
  try {
    process.loadEnvFile('.dev.vars.staging');
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
    throw new Error(`Missing staging settings: ${missing.join(', ')}. See .dev.vars.example.`);
  if (!/^\d{17,20}$/.test(process.env.DISCORD_APPLICATION_ID))
    throw new Error('Invalid application ID.');
  if (!/^[a-fA-F0-9]{64}$/.test(process.env.DISCORD_PUBLIC_KEY))
    throw new Error('Invalid Discord public key.');
  const config = await stagingConfig();
  if (config.vars.APP_ENV !== 'staging')
    throw new Error('Expected isolated staging configuration.');
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
      `Discord returned HTTP ${response.status}; check staging credentials and bot permissions.`,
    );
  return response.json();
}

export async function verifyStagingApplication(credentials) {
  const app = await discordRequest('/oauth2/applications/@me', credentials.DISCORD_BOT_TOKEN);
  if (
    app.id !== credentials.DISCORD_APPLICATION_ID ||
    app.verify_key !== credentials.DISCORD_PUBLIC_KEY
  )
    throw new Error('Token, application ID and public key do not describe the same application.');
  if (!/\bstaging\b/i.test(app.name))
    throw new Error(
      'Use a separate application whose name includes Staging. Production applications are rejected.',
    );
  return app;
}
