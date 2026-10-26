import type { Env } from '../env';
export const DISCORD_API = 'https://discord.com/api/v10';
export const MANAGE_GUILD = 32n,
  ADMINISTRATOR = 8n;
export const REQUIRED_CHANNEL_PERMISSIONS = 1024n | 2048n | 16384n | 65536n;
export const PERMISSIONS = [
  { bit: 1024n, name: 'View Channel' },
  { bit: 2048n, name: 'Send Messages' },
  { bit: 16384n, name: 'Embed Links' },
  { bit: 65536n, name: 'Read Message History' },
];
export interface Guild {
  id: string;
  name: string;
  owner?: boolean;
  permissions: string;
  icon?: string | null;
}
export interface Channel {
  id: string;
  guild_id?: string;
  name: string;
  type: number;
  permission_overwrites?: Overwrite[];
}
interface Overwrite {
  id: string;
  type: number;
  allow: string;
  deny: string;
}
export interface Member {
  roles: string[];
}
export interface Role {
  id: string;
  permissions: string;
}
export class DiscordFailure extends Error {
  constructor(
    public status: number,
    public retryAfter?: string,
  ) {
    super('Discord is unavailable or access was denied.');
  }
}
export function canManage(g: Pick<Guild, 'owner' | 'permissions'>) {
  const bits = BigInt(g.permissions);
  return !!g.owner || !!(bits & (MANAGE_GUILD | ADMINISTRATOR));
}
export async function discord<T>(
  path: string,
  token: string,
  bearer = false,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Authorization', `${bearer ? 'Bearer' : 'Bot'} ${token}`);
  headers.set('User-Agent', 'DiscordBot (https://github.com/ddk220-light/NammaPUBobot, 0.1.0)');
  for (let attempt = 0; attempt < 3; attempt++) {
    const response = await fetch(`${DISCORD_API}${path}`, {
      ...init,
      headers,
      signal: AbortSignal.timeout(5000),
    });
    if (response.ok) return response.json() as Promise<T>;
    let retryAfter = response.headers.get('Retry-After') ?? undefined;
    if (response.status === 429) {
      const body = (await response.json().catch(() => null)) as { retry_after?: number } | null;
      if (typeof body?.retry_after === 'number') retryAfter = String(body.retry_after);
      const seconds = Number(retryAfter);
      // Admin HTTP requests can wait briefly without holding a Discord interaction.
      // Long/global limits return an explicit retry time, never an unbounded loop.
      if (Number.isFinite(seconds) && seconds >= 0 && seconds <= 2 && attempt < 2) {
        await new Promise((resolve) => setTimeout(resolve, Math.ceil(seconds * 1000) + 100));
        continue;
      }
    }
    throw new DiscordFailure(response.status, retryAfter);
  }
  throw new DiscordFailure(503);
}
export async function userGuilds(accessToken: string): Promise<Guild[]> {
  const out: Guild[] = [];
  let after = '';
  for (let page = 0; page < 10; page++) {
    const rows = await discord<Guild[]>(
      `/users/@me/guilds?limit=200${after ? `&after=${after}` : ''}`,
      accessToken,
      true,
    );
    out.push(...rows);
    if (rows.length < 200) return out;
    const next = rows.at(-1)!.id;
    if (next === after) throw new Error('Discord pagination did not advance.');
    after = next;
  }
  throw new Error('Too many communities to load safely.');
}
export function channelPermissions(
  guildId: string,
  botId: string,
  roles: Role[],
  member: Member,
  channel: Channel,
) {
  let bits = roles
    .filter((r) => r.id === guildId || member.roles.includes(r.id))
    .reduce((b, r) => b | BigInt(r.permissions), 0n);
  if (bits & ADMINISTRATOR) return PERMISSIONS.map((p) => ({ name: p.name, granted: true }));
  const overwrites = channel.permission_overwrites ?? [];
  const everyone = overwrites.find((o) => o.type === 0 && o.id === guildId);
  if (everyone) bits = (bits & ~BigInt(everyone.deny)) | BigInt(everyone.allow);
  let deny = 0n,
    allow = 0n;
  for (const o of overwrites.filter((o) => o.type === 0 && member.roles.includes(o.id))) {
    deny |= BigInt(o.deny);
    allow |= BigInt(o.allow);
  }
  bits = (bits & ~deny) | allow;
  const personal = overwrites.find((o) => o.type === 1 && o.id === botId);
  if (personal) bits = (bits & ~BigInt(personal.deny)) | BigInt(personal.allow);
  return PERMISSIONS.map((p) => ({ name: p.name, granted: !!(bits & p.bit) && !!(bits & 1024n) }));
}
export async function channels(env: Env, guildId: string) {
  if (!env.DISCORD_BOT_TOKEN || !env.DISCORD_APPLICATION_ID) throw new DiscordFailure(503);
  const token = env.DISCORD_BOT_TOKEN;
  const [list, roles, member] = await Promise.all([
    discord<Channel[]>(`/guilds/${guildId}/channels`, token),
    discord<Role[]>(`/guilds/${guildId}/roles`, token),
    discord<Member>(`/guilds/${guildId}/members/${env.DISCORD_APPLICATION_ID}`, token),
  ]);
  return list
    .filter((c) => c.type === 0 || c.type === 5)
    .map((c) => ({
      id: c.id,
      name: c.name,
      permissions: channelPermissions(guildId, env.DISCORD_APPLICATION_ID!, roles, member, c),
    }));
}
