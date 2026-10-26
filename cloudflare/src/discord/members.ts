import { snowflake } from '../domain/config';
import { PlayError, personSchema } from '../domain/play';
import type { Env } from '../env';
import { discord } from './client';

export function memberIds(input: string): string[] {
  const parts = input
    .trim()
    .split(/[\s,]+/)
    .filter(Boolean);
  const ids = parts.map((p) => /^<@!?(\d{17,20})>$/.exec(p)?.[1] ?? p);
  if (!ids.length || ids.length > 8 || ids.some((id) => !snowflake.safeParse(id).success))
    throw new PlayError('Enter 1–8 server members per team using mentions or Discord IDs.');
  if (new Set(ids).size !== ids.length)
    throw new PlayError('A team cannot contain duplicate players.');
  return ids;
}
export async function resolveMember(env: Env, guild: string, id: string) {
  snowflake.parse(guild);
  snowflake.parse(id);
  const member = (await discord(`/guilds/${guild}/members/${id}`, env.DISCORD_BOT_TOKEN!)) as {
    nick?: string | null;
    roles: string[];
    user: { id: string; username: string; global_name?: string | null; bot?: boolean };
  };
  if (member.user?.id !== id || member.user.bot)
    throw new PlayError('Choose a human member of this server.');
  return personSchema.parse({
    id,
    name: (member.nick ?? member.user.global_name ?? member.user.username).slice(0, 80),
    roles: member.roles,
  });
}
