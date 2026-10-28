import { PlayError, personSchema, type Action } from '../domain/play';
import type { Env } from '../env';
import { discord, DiscordFailure } from './client';
import { resolveMember } from './members';

function shuffled<T>(items: T[]): T[] {
  const result = [...items];
  for (let i = result.length - 1; i > 0; i--) {
    const bound = i + 1,
      ceiling = Math.floor(4294967296 / bound) * bound;
    let value: number;
    do {
      value = crypto.getRandomValues(new Uint32Array(1))[0];
    } while (value >= ceiling);
    const j = value % bound;
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

export async function testMembers(
  env: Env,
  guild: string,
  channel: string,
  count: number,
  known: string[],
  eligible: (member: Action['actor']) => boolean = () => true,
): Promise<Action['actor'][]> {
  const token = env.DISCORD_BOT_TOKEN;
  if (!token) throw new PlayError('Discord bot access is unavailable.');
  try {
    const members = await discord<
      {
        nick?: string;
        roles: string[];
        user?: { id: string; bot?: boolean; global_name?: string; username: string };
      }[]
    >(`/guilds/${guild}/members?limit=1000`, token);
    const humans = new Map(
      members
        .filter((m) => m.user && !m.user.bot)
        .map((m) => [
          m.user!.id,
          personSchema.parse({
            id: m.user!.id,
            name: (m.nick ?? m.user!.global_name ?? m.user!.username).slice(0, 80),
            roles: m.roles,
          }),
        ]),
    );
    const available = [...humans.values()].filter(eligible);
    if (available.length >= count) return shuffled(available).slice(0, count);
    known = [...known, ...humans.keys()];
  } catch (error) {
    if (!(error instanceof DiscordFailure) || ![401, 403].includes(error.status)) throw error;
  }
  const messages = await discord<{ author?: { id: string; bot?: boolean } }[]>(
    `/channels/${channel}/messages?limit=100`,
    token,
  );
  const candidates = shuffled([
    ...new Set([
      ...known,
      ...messages.filter((m) => m.author && !m.author.bot).map((m) => m.author!.id),
    ]),
  ]);
  const selected: Action['actor'][] = [];
  for (const id of candidates.slice(0, 32)) {
    try {
      const member = await resolveMember(env, guild, id);
      if (eligible(member)) selected.push(member);
    } catch (error) {
      if (
        !(error instanceof PlayError) &&
        !(error instanceof DiscordFailure && error.status === 404)
      )
        throw error;
    }
    if (selected.length === count) return selected;
  }
  throw new PlayError(
    `Only ${selected.length} eligible human members could be verified. Use /test populate count:${Math.max(2, selected.length)} after more people have used this channel. Nothing was added.`,
  );
}
