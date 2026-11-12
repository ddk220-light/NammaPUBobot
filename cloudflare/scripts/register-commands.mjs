import { commandsFor } from '../src/discord/commands.mjs';
import { ENVIRONMENTS, envCredentials, discordRequest, verifyApplication } from './env.mjs';

const usage =
  'Usage: node scripts/register-commands.mjs --env staging|production [--guild ID] [--clear-guild ID] [--print]';
const snowflake = (value, flag) => {
  if (!/^\d{17,20}$/.test(value ?? '')) throw new Error(`${flag} needs a Discord server ID.`);
  return value;
};

export function parseRegisterArgs(argv) {
  const options = { env: null, guild: null, clearGuild: null, print: false };
  for (let i = 0; i < argv.length; i++) {
    const [flag, inline] = argv[i].split(/=(.*)/s);
    const value = () => (inline !== undefined ? inline : argv[++i]);
    if (flag === '--env') options.env = value();
    else if (flag === '--guild') options.guild = snowflake(value(), '--guild');
    else if (flag === '--clear-guild') options.clearGuild = snowflake(value(), '--clear-guild');
    else if (flag === '--print' && inline === undefined) options.print = true;
    else throw new Error(`Unknown argument ${argv[i]}. ${usage}`);
  }
  if (!ENVIRONMENTS.includes(options.env)) throw new Error(usage);
  if (options.guild && options.guild === options.clearGuild)
    throw new Error('--guild and --clear-guild name the same server.');
  return options;
}

/**
 * Every Discord write this run performs, in order. The body is exactly commandsFor(env).
 * @param {{ env: string, applicationId: string, guild?: string | null, clearGuild?: string | null }} target
 */
export function registrationPlan({ env, applicationId, guild = null, clearGuild = null }) {
  const base = `/applications/${applicationId}`;
  return [
    {
      scope: guild ? `server ${guild}` : 'global',
      path: guild ? `${base}/guilds/${guild}/commands` : `${base}/commands`,
      body: commandsFor(env),
    },
    // Cleared after the new list is live, so the server is never left without commands.
    ...(clearGuild
      ? [
          {
            scope: `server ${clearGuild} (cleared)`,
            path: `${base}/guilds/${clearGuild}/commands`,
            body: [],
          },
        ]
      : []),
  ];
}

/** Exactly what --print writes: the serialized body a registration sends. */
export const printedBody = (env) => `${JSON.stringify(commandsFor(env))}\n`;

export async function main(argv = process.argv.slice(2)) {
  const options = parseRegisterArgs(argv);
  if (options.print) {
    process.stdout.write(printedBody(options.env));
    return;
  }
  // Command registration uses the bot token, not the website's OAuth client secret.
  const credentials = await envCredentials(options.env, { requireClientSecret: false });
  await verifyApplication(options.env, credentials);
  for (const write of registrationPlan({
    ...options,
    applicationId: credentials.DISCORD_APPLICATION_ID,
  })) {
    const result = await discordRequest(write.path, credentials.DISCORD_BOT_TOKEN, {
      method: 'PUT',
      body: JSON.stringify(write.body),
    });
    console.log(
      `${options.env}: ${result.length} command(s) for application ${credentials.DISCORD_APPLICATION_ID}, ${write.scope}.`,
    );
  }
}

if (process.argv[1]?.endsWith('register-commands.mjs')) await main();
