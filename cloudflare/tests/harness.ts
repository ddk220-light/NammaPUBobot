import { env } from 'cloudflare:workers';
import { reset, runInDurableObject } from 'cloudflare:test';
import { afterEach, beforeEach, vi } from 'vitest';
import { emptyModules, type Settings } from '../src/domain/config';
import type { Action, Match } from '../src/domain/play';
export const guild = '706320010368516198',
  channel = '706320010368516200',
  game = '4daebc0a-55e8-4d56-a17d-d94913644910',
  queue = '81da571b-6a4a-4e5d-8e32-f4050df35a2d';
export const person = (n: number) => ({
  id: String(100000000000000100n + BigInt(n)),
  name: `Player ${n}`,
  roles: [],
});
export const stub = (g = guild) => env.COMMUNITIES.getByName(g);
let sequence = 100000000000100000n;
export const action = (kind: Action['kind'], n = 0, extra: Partial<Action> = {}): Action => ({
  id: String(sequence++),
  actor: person(n),
  kind,
  channelId: channel,
  admin: false,
  ...extra,
});
export const act = (kind: Action['kind'], n = 0, extra: Partial<Action> = {}, g = guild) =>
  stub(g).play(g, action(kind, n, extra));
export const settings = (): Settings => ({
  games: [{ id: game, name: 'Any RTS', catalogId: null }],
  queues: [
    {
      id: queue,
      name: 'Pickup',
      channelId: channel,
      gameId: game,
      size: 4,
      enabled: true,
      checkIn: { enabled: true, seconds: 60 },
      teamMethod: 'balanced',
      ratingEngine: 'aoe2',
      modules: { ...emptyModules },
    },
  ],
});
export async function setup(config = settings()) {
  return stub().save(guild, 0, config, person(99).id, 'save');
}
export const current = async () => (await stub().records(guild)).matches[0] as Match;
export async function fill(config = settings()) {
  await setup(config);
  for (let n = 0; n < 4; n++) await act('add', n);
  return current();
}
export async function flush() {
  await runInDurableObject(stub(), (_i, s) => {
    s.storage.sql.exec("UPDATE outbox SET due=0 WHERE status='pending'");
  });
  await stub().flush();
}
export function harness() {
  beforeEach(() => {
    sequence = 100000000000100000n;
    let message = 100000000001000000n;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const r = new Request(input, init),
        u = new URL(r.url);
      const member = /\/members\/(\d+)$/.exec(u.pathname);
      if (member) return Response.json({ roles: [], user: { id: member[1], username: 'Member' } });
      if (r.method === 'DELETE') return new Response(null, { status: 204 });
      if (r.method === 'GET' && u.pathname.endsWith('/messages')) return Response.json([]);
      return Response.json({ id: String(message++) });
    });
  });
  afterEach(async () => {
    await reset();
    vi.restoreAllMocks();
  });
}
