import {
  envArg,
  envCredentials,
  envConfig,
  usableOrigin,
  verifyApplication,
  discordRequest,
} from './env.mjs';

const name = envArg();
const credentials = await envCredentials(name);
await verifyApplication(name, credentials);
const origin = (await envConfig(name)).vars.APP_ORIGIN;
if (!usableOrigin(origin))
  throw new Error(
    `Set the real HTTPS ${name} APP_ORIGIN before connecting the interaction endpoint.`,
  );
const app = await discordRequest('/applications/@me', credentials.DISCORD_BOT_TOKEN, {
  method: 'PATCH',
  body: JSON.stringify({ interactions_endpoint_url: `${origin}/interactions` }),
});
if (app.interactions_endpoint_url !== `${origin}/interactions`)
  throw new Error('Discord did not retain the endpoint.');
console.log(
  `PASS: Discord validated and saved the signed ${name} interaction endpoint ${origin}/interactions.`,
);
