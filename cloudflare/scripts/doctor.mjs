import { commandsFor } from '../src/discord/commands.mjs';
import {
  envArg,
  envCredentials,
  envConfig,
  usableOrigin,
  discordRequest,
  verifyApplication,
} from './env.mjs';

const name = envArg(process.argv.slice(2), 'staging');
const config = await envConfig(name);
const origin = config.vars.APP_ORIGIN;
if (!usableOrigin(origin)) throw new Error(`Configure the ${name} HTTPS origin.`);
const health = await fetch(`${origin}/health`, { signal: AbortSignal.timeout(10000) });
const healthBody = await health.json();
if (!health.ok || healthBody.environment !== name)
  throw new Error(`The ${name} health check failed.`);
console.log(`PASS: ${name} health at ${origin} (version ${healthBody.version}).`);
const bootstrap = await fetch(`${origin}/api/bootstrap`, { signal: AbortSignal.timeout(10000) });
const setup = await bootstrap.json();
console.log(
  `Discord sign-in: ${setup.oauthReady ? 'configured' : 'missing credentials'}. Interaction handler: ${setup.discordReady ? 'configured' : 'missing credentials'}.`,
);
if (process.argv.includes('--discord')) {
  const credentials = await envCredentials(name),
    application = await verifyApplication(name, credentials);
  const expectedRedirect = `${origin}/auth/callback`;
  if (!application.redirect_uris?.includes(expectedRedirect))
    throw new Error(`Add OAuth redirect ${expectedRedirect} in the Discord developer portal.`);
  if (application.interactions_endpoint_url !== `${origin}/interactions`)
    throw new Error(`Set Discord Interactions Endpoint URL to ${origin}/interactions.`);
  const registered = await discordRequest(
    `/applications/${application.id}/commands`,
    credentials.DISCORD_BOT_TOKEN,
  );
  const missing = commandsFor(name)
    .map((c) => c.name)
    .filter((c) => !registered.some((r) => r.name === c));
  if (missing.length || registered.length !== commandsFor(name).length)
    throw new Error(
      `Global commands differ from this revision (missing: ${missing.join(', ') || 'none'}). Run npm run register:${name}.`,
    );
  const prefix = name.toUpperCase();
  const guild = process.env[`${prefix}_GUILD_ID`];
  if (name === 'staging' && !/^\d{17,20}$/.test(guild ?? ''))
    throw new Error('Set STAGING_GUILD_ID to verify the test server.');
  if (guild) {
    const channels = await discordRequest(
      `/guilds/${guild}/channels`,
      credentials.DISCORD_BOT_TOKEN,
    );
    const target = process.env[`${prefix}_CHANNEL_NAME`] ?? 'test-bot';
    const match = channels.filter((c) => c.name === target && c.type === 0);
    if (match.length !== 1)
      throw new Error(`Expected exactly one text channel named ${target}; found ${match.length}.`);
    console.log(`Test channel #${target}: ${match[0].id}.`);
  }
  console.log(
    `PASS: separate ${name} application, OAuth redirect, interaction endpoint and ${registered.length} global commands.`,
  );
}
