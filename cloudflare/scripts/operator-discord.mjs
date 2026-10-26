import { readFile } from 'node:fs/promises';
import { parseEnv } from 'node:util';
import { commands } from '../src/discord/commands.mjs';
import { discordRequest } from './env.mjs';
const [mode, configFile, credentialsFile, guild] = process.argv.slice(2);
if (!['verify', 'connect', 'register'].includes(mode) || !configFile || !credentialsFile)
  throw Error(
    'Usage: node scripts/operator-discord.mjs verify|connect|register CONFIG PRIVATE_ENV [GUILD_ID|global]',
  );
const config = JSON.parse(await readFile(configFile, 'utf8'));
const credentials = parseEnv(await readFile(credentialsFile, 'utf8'));
const origin = new URL(config.vars.APP_ORIGIN);
if (origin.protocol !== 'https:' || origin.origin !== config.vars.APP_ORIGIN || config.env)
  throw Error('Use a standalone HTTPS operator configuration.');
const token = credentials.DISCORD_BOT_TOKEN;
const app = await discordRequest('/oauth2/applications/@me', token);
if (
  app.id !== credentials.DISCORD_APPLICATION_ID ||
  app.verify_key !== credentials.DISCORD_PUBLIC_KEY
)
  throw Error('Bot, application ID and public key do not match.');
if (!app.redirect_uris?.includes(`${origin.origin}/auth/callback`))
  throw Error('Register this installation’s OAuth callback first.');
if (mode === 'connect') {
  const saved = await discordRequest('/applications/@me', token, {
    method: 'PATCH',
    body: JSON.stringify({ interactions_endpoint_url: `${origin.origin}/interactions` }),
  });
  if (saved.interactions_endpoint_url !== `${origin.origin}/interactions`)
    throw Error('Signed endpoint validation failed.');
  console.log('PASS: this application’s signed interaction endpoint validated.');
} else if (mode === 'register') {
  if (guild !== 'global' && !/^\d{17,20}$/.test(guild ?? ''))
    throw Error('Specify the exact test guild ID, or explicitly choose global.');
  if (app.interactions_endpoint_url !== `${origin.origin}/interactions`)
    throw Error('Connect the signed endpoint before registering commands.');
  const result = await discordRequest(
    `/applications/${app.id}/${guild === 'global' ? '' : `guilds/${guild}/`}commands`,
    token,
    { method: 'PUT', body: JSON.stringify(commands) },
  );
  console.log(`PASS: ${result.length} commands registered for this app in ${guild}.`);
} else {
  if (app.interactions_endpoint_url !== `${origin.origin}/interactions`)
    throw Error('Interaction endpoint differs from this installation.');
  const response = await fetch(`${origin.origin}/health`, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw Error('Worker health unavailable.');
  console.log('PASS: same application, public key, OAuth redirect, endpoint and Worker health.');
}
