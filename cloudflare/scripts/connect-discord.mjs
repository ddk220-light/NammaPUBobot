import {
  stagingCredentials,
  stagingConfig,
  verifyStagingApplication,
  discordRequest,
} from './env.mjs';

const credentials = await stagingCredentials();
await verifyStagingApplication(credentials);
const config = await stagingConfig();
const origin = config.vars.APP_ORIGIN;
if (new URL(origin).protocol !== 'https:')
  throw new Error('The interaction endpoint must be HTTPS.');
const app = await discordRequest('/applications/@me', credentials.DISCORD_BOT_TOKEN, {
  method: 'PATCH',
  body: JSON.stringify({ interactions_endpoint_url: `${origin}/interactions` }),
});
if (app.interactions_endpoint_url !== `${origin}/interactions`)
  throw new Error('Discord did not retain the endpoint.');
console.log(
  `PASS: Discord validated and saved the signed staging interaction endpoint ${origin}/interactions.`,
);
