import {
  stagingCredentials,
  stagingConfig,
  discordRequest,
  verifyStagingApplication,
} from './env.mjs';

const config = await stagingConfig();
const origin = config.vars.APP_ORIGIN;
if (!origin || new URL(origin).protocol !== 'https:')
  throw new Error('Configure the staging HTTPS origin.');
const health = await fetch(`${origin}/health`, { signal: AbortSignal.timeout(10000) });
const healthBody = await health.json();
if (!health.ok || healthBody.environment !== 'staging')
  throw new Error('The staging health check failed.');
console.log(`PASS: staging health at ${origin}`);
const bootstrap = await fetch(`${origin}/api/bootstrap`, { signal: AbortSignal.timeout(10000) });
const setup = await bootstrap.json();
console.log(
  `Discord sign-in: ${setup.oauthReady ? 'configured' : 'missing credentials'}. Interaction handler: ${setup.discordReady ? 'configured' : 'missing credentials'}.`,
);
if (process.argv.includes('--discord')) {
  const credentials = await stagingCredentials(),
    application = await verifyStagingApplication(credentials);
  const expectedRedirect = `${origin}/auth/callback`;
  if (!application.redirect_uris?.includes(expectedRedirect))
    throw new Error(`Add OAuth redirect ${expectedRedirect} in the Discord developer portal.`);
  if (application.interactions_endpoint_url !== `${origin}/interactions`)
    throw new Error(`Set Discord Interactions Endpoint URL to ${origin}/interactions.`);
  const guild = process.env.STAGING_GUILD_ID;
  if (!/^\d{17,20}$/.test(guild ?? ''))
    throw new Error('Set STAGING_GUILD_ID to verify the test server.');
  const channels = await discordRequest(`/guilds/${guild}/channels`, credentials.DISCORD_BOT_TOKEN);
  const target = process.env.STAGING_CHANNEL_NAME ?? 'test-bot';
  const match = channels.filter((c) => c.name === target && c.type === 0);
  if (match.length !== 1)
    throw new Error(`Expected exactly one text channel named ${target}; found ${match.length}.`);
  console.log(
    `PASS: separate staging application, OAuth redirect and interaction endpoint. Test channel #${target}: ${match[0].id}.`,
  );
}
