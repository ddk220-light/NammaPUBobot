import { commands } from '../src/discord/commands.mjs';
import { stagingCredentials, discordRequest, verifyStagingApplication } from './env.mjs';

// Command registration uses the bot token, not the website's OAuth client secret.
const credentials = await stagingCredentials({ requireClientSecret: false });
await verifyStagingApplication(credentials);
const guildId = process.env.STAGING_GUILD_ID;
if (!/^\d{17,20}$/.test(guildId ?? ''))
  throw new Error(
    'Set STAGING_GUILD_ID explicitly. No global or production commands will be changed.',
  );
// Only this staging application's guild command namespace is replaced.
const result = await discordRequest(
  `/applications/${credentials.DISCORD_APPLICATION_ID}/guilds/${guildId}/commands`,
  credentials.DISCORD_BOT_TOKEN,
  { method: 'PUT', body: JSON.stringify(commands) },
);
console.log(
  `Registered ${result.length} command(s) for staging application ${credentials.DISCORD_APPLICATION_ID} in server ${guildId}.`,
);
