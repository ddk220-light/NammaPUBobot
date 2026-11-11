import { env, exports } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { interactions } from '../src/discord/interactions';
import {
  harness,
  stub,
  settings,
  fill,
  act,
  person,
  guild,
  game,
  channel,
  current,
} from './harness';
import type { Match } from '../src/domain/play';

harness();
const config = () => {
  const s = settings();
  s.queues[0].modules.gold = true;
  s.queues[0].modules.predictions = true;
  s.queues[0].checkIn.enabled = false;
  return s;
};
const captain = (m: Match, side = 0) => Number(BigInt(m.captains[side]) - 100000000000000100n);
const side = (m: Match, n: number) =>
  m.teams[0].some((p) => p.id === person(n).id) ? 'alpha' : 'beta';
const organizer = person(99).id;
const steamId = '76561198000000001';

/** A finished, rated, settled match with bets from players 0, 8 and 9. */
async function settledMatch() {
  const m = await fill(config());
  await act('bet', 0, { matchId: m.id, side: side(m, 0), stake: 50 });
  await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
  await act('bet', 9, { matchId: m.id, side: 'beta', stake: 50 });
  await act('bet_close', captain(m), { matchId: m.id });
  await act('loss', captain(m, 1), { matchId: m.id });
  return m;
}
async function rows() {
  return runInDurableObject(stub(), (_i, state) => {
    const out: Record<string, unknown[]> = {};
    for (const { name } of state.storage.sql
      .exec<{ name: string }>(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '\\_cf\\_%' ESCAPE '\\'",
      )
      .toArray())
      out[name] = state.storage.sql.exec(`SELECT * FROM "${name}"`).toArray();
    // Key-value storage lives in a protected table; read it through its own API.
    out.kv = [...state.storage.kv.list()];
    return out;
  });
}
const sql = (query: string, ...args: SqlStorageValue[]) =>
  runInDurableObject(stub(), (_i, state) => state.storage.sql.exec(query, ...args).toArray());

async function command(name: string, sub: string, n: number, permissions = '0') {
  const raw = JSON.stringify({
    id: String(100000000000900000n + BigInt(Math.floor(Math.random() * 1e6))),
    type: 2,
    application_id: '100000000000000001',
    token: 'synthetic-interaction-token',
    guild_id: guild,
    channel_id: channel,
    member: { permissions, user: { id: person(n).id, username: person(n).name } },
    data: { name, options: [{ name: sub, type: 1, options: [] }] },
  });
  const timestamp = String(Math.floor(Date.now() / 1000));
  const key = await crypto.subtle.importKey(
    'pkcs8',
    Uint8Array.from(
      '302e020100300506032b6570042204209d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'
        .match(/../g)!
        .map((h) => parseInt(h, 16)),
    ),
    'Ed25519',
    false,
    ['sign'],
  );
  const sig = new Uint8Array(
    await crypto.subtle.sign('Ed25519', key, new TextEncoder().encode(timestamp + raw)),
  );
  const jobs: Promise<unknown>[] = [];
  const response = await interactions(
    new Request('https://setup.example/interactions', {
      method: 'POST',
      body: raw,
      headers: {
        'X-Signature-Timestamp': timestamp,
        'X-Signature-Ed25519': Array.from(sig, (b) => b.toString(16).padStart(2, '0')).join(''),
      },
    }),
    env,
    { waitUntil: (job: Promise<unknown>) => jobs.push(job) } as unknown as ExecutionContext,
  );
  await Promise.all(jobs);
  return ((await response.json()) as { data: { content: string; flags: number } }).data;
}

describe('hidden players on economy leaderboards', () => {
  const hide = (n: number) =>
    sql(
      'INSERT INTO player_meta(game_id,user_id,hidden) VALUES(?,?,1) ON CONFLICT(game_id,user_id) DO UPDATE SET hidden=1',
      game,
      person(n).id,
    );
  it.each(['leaderboard', 'predictions'])('leaves hidden players off the %s view', async (view) => {
    await settledMatch();
    const before = await stub().goldView(guild, channel, person(1).id, view);
    expect(before.ok && before.content).toContain(`<@${person(8).id}>`);
    await hide(8);
    const after = await stub().goldView(guild, channel, person(1).id, view);
    expect(after.ok && after.content).not.toContain(person(8).id);
    expect(after.ok && after.content).toContain(`<@${person(9).id}>`);
  });
});

describe('/profile hide and unhide', () => {
  it('lets a player hide and show themselves for the channel queue’s game', async () => {
    await settledMatch();
    const hidden = await command('profile', 'hide', 1);
    expect(hidden.flags & 64).toBe(64);
    expect(hidden.content).toContain('You are now hidden in Any RTS');
    expect(hidden.content).toContain('gold or predictions leaderboards');
    const stats = await stub().statistics(guild, game);
    expect(stats.leaderboard.map((p) => p.id)).not.toContain(person(1).id);
    const gold = await stub().goldView(guild, channel, person(2).id, 'leaderboard');
    expect(gold.ok && gold.content).not.toContain(person(1).id);
    // Other members see the player as hidden in shared match history.
    const other = await stub().statistics(guild, game, person(2).id, 0, false, person(2).id);
    expect(other.player!.recent[0].teams.flat().map((p) => p.name)).toContain('Hidden player');
    const shown = await command('profile', 'unhide', 1);
    expect(shown.content).toContain('You are visible again in Any RTS');
    expect((await stub().statistics(guild, game)).leaderboard.map((p) => p.id)).toContain(
      person(1).id,
    );
  });
  it('allows hiding before a first match but never hiding someone else', async () => {
    await stub().save(guild, 0, config(), organizer, 'save');
    expect((await command('profile', 'hide', 7)).content).toContain('You are now hidden');
    expect(await sql('SELECT hidden FROM player_meta WHERE user_id=?', person(7).id)).toEqual([
      { hidden: 1 },
    ]);
    const other = await stub().recordAction(
      guild,
      { id: crypto.randomUUID(), kind: 'hide', gameId: game, userId: person(6).id },
      person(7).id,
      false,
    );
    expect(other.ok).toBe(false);
  });
});

describe('deleting a player’s data', () => {
  it('refuses while the player is in an active match', async () => {
    const m = await fill(config());
    const reply = await stub().deletePlayer(guild, person(0).id, organizer);
    expect(reply).toMatchObject({ ok: false, status: 409 });
    expect(!reply.ok && reply.content).toContain('active match');
    expect((await current()).id).toBe(m.id);
  });
  it('removes every row naming the player and anonymizes shared matches', async () => {
    const m = await settledMatch();
    const p0 = person(0).id;
    await act('add', 0);
    const queued = await stub().deletePlayer(guild, p0, organizer);
    expect(!queued.ok && queued.content).toContain('waiting in a queue');
    await act('remove', 0);
    await stub().recordAction(
      guild,
      {
        id: crypto.randomUUID(),
        kind: 'link',
        gameId: game,
        userId: p0,
        provider: 'steam',
        externalId: steamId,
      },
      p0,
      false,
    );
    await act('noadd', 99, { admin: true, target: person(0), seconds: 600, reason: 'test' });
    await runInDurableObject(stub(), (_i, state) => {
      const s = state.storage.sql;
      s.exec('INSERT INTO civ_pick_history VALUES(?,?,?,?,?,?)', m.id, 1, p0, channel, 'Franks', 1);
      s.exec(
        'INSERT INTO played_selections VALUES(?,?,?,?,?,?,?,?,?,?)',
        m.id,
        game,
        p0,
        'steam',
        steamId,
        'Franks',
        0,
        'player',
        'test',
        1,
      );
      s.exec('INSERT INTO readiness VALUES(?,?,?,?,?)', m.queue.id, game, p0, 1, 2);
      s.exec(
        'INSERT INTO civ_rounds VALUES(?,?)',
        m.id,
        JSON.stringify({ roster: [{ id: p0, side: 0 }], picks: { [p0]: 1 }, initiator: p0 }),
      );
    });
    const others = await sql(
      'SELECT user_id,config FROM profiles WHERE user_id!=? ORDER BY user_id',
      p0,
    );
    const wallets = await sql('SELECT * FROM wallets WHERE user_id!=? ORDER BY user_id', p0);
    expect(JSON.stringify(await rows())).toContain(p0);
    const finished = (await stub().records(guild)).matches.find((x) => x.id === m.id) as Match;

    const reply = await stub().deletePlayer(guild, p0, organizer);
    expect(reply.ok).toBe(true);

    const after = await rows();
    for (const [table, list] of Object.entries(after)) {
      expect(JSON.stringify(list), table).not.toContain(p0);
      expect(JSON.stringify(list), table).not.toContain(steamId);
      expect(JSON.stringify(list), table).not.toContain('Player 0');
    }
    expect(
      await sql('SELECT user_id,config FROM profiles WHERE user_id!=? ORDER BY user_id', p0),
    ).toEqual(others);
    expect(await sql('SELECT * FROM wallets ORDER BY user_id')).toEqual(wallets);
    expect(
      await sql(
        'SELECT w.user_id FROM wallets w LEFT JOIN gold_ledger l USING(game_id,user_id) GROUP BY w.game_id,w.user_id HAVING w.balance != COALESCE(SUM(l.amount),0)',
      ),
    ).toEqual([]);
    const saved = (await stub().records(guild)).matches.find((x) => x.id === m.id) as Match;
    expect(saved.state).toBe('finished');
    expect(saved.result).toBe(finished.result);
    expect(saved.result).not.toBeNull();
    const placeholder = saved.roster.find((p) => p.name === 'Deleted player')!;
    expect(placeholder.id).toMatch(/^deleted:/);
    expect(saved.teams.flat().filter((p) => p.id === placeholder.id)).toHaveLength(1);
    expect(saved.changes.map((c) => c.userId)).toContain(placeholder.id);
    const stats = await stub().statistics(guild, game, person(1).id, 0, true);
    expect(stats.player!.recent[0].teams.flat()).toContainEqual({
      id: null,
      name: 'Deleted player',
    });
    expect(stats.leaderboard).toHaveLength(3);
    const audit = after.record_audit.filter(
      (r) => (r as { kind: string }).kind === 'player_data_deleted',
    );
    expect(audit).toEqual([
      expect.objectContaining({ actor: organizer, user_id: null, details: '{}' }),
    ]);
    expect(await stub().deletePlayer(guild, p0, organizer)).toMatchObject({ ok: false });
  });
});

describe('data deletion routes', () => {
  const base = 'https://setup.example',
    sessionId = 'c'.repeat(64),
    csrf = 'd'.repeat(64);
  let permissions = '32',
    owner = false;
  const headers = () => ({
    Cookie: `__Host-cg-session=${sessionId}`,
    Origin: base,
    'X-CSRF-Token': csrf,
    'Content-Type': 'application/json',
  });
  const post = (path: string, body: unknown) =>
    exports.default.fetch(`${base}/api/communities/${guild}/${path}`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(body),
    });
  beforeEach(async () => {
    permissions = '32';
    owner = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = new URL(new Request(input, init).url);
      if (url.pathname === '/api/v10/users/@me/guilds')
        return Response.json([{ id: guild, name: 'Friends', permissions, owner }]);
      return Response.json({ id: '100000000000999999' });
    });
    await env.AUTH.getByName(`session:${sessionId}`).saveSession({
      user: { id: organizer, username: 'organizer', avatar: null },
      accessToken: 'test-access-token',
      csrf,
      expiresAt: Date.now() + 60000,
    });
  });
  it('deletes a player for a Manage Server organizer', async () => {
    await settledMatch();
    const response = await post('player/delete', { userId: person(3).id });
    expect(response.status).toBe(200);
    expect(JSON.stringify(await rows())).not.toContain(person(3).id);
    const missing = await post('player/delete', { userId: person(3).id });
    expect(missing.status).toBe(409);
  });
  it('requires the owner or an Administrator and the exact server name to wipe a community', async () => {
    await settledMatch();
    await env.PUBLIC_DIRECTORY!.getByName('directory').register({
      guild,
      revision: 1,
      community: 'friends-1',
      games: [],
    });
    expect((await post('delete', { confirmName: 'Friends' })).status).toBe(403);
    permissions = '8';
    expect((await post('delete', { confirmName: 'friends' })).status).toBe(400);
    const ok = await post('delete', { confirmName: 'Friends' });
    expect(ok.status).toBe(200);
    const fresh = await stub().read(guild);
    expect(fresh).toMatchObject({ revision: 0, settings: { games: [], queues: [] } });
    expect((await stub().records(guild)).matches).toEqual([]);
    const left = await rows();
    for (const [table, list] of Object.entries(left))
      if (!['community', 'schema_version', 'record_state', 'site_metadata'].includes(table))
        expect(list, table).toEqual([]);
    expect(
      (await env.PUBLIC_DIRECTORY!.getByName('directory').entries()).some((e) => e.guild === guild),
    ).toBe(false);
    // The community can be set up again from scratch.
    expect((await stub().save(guild, 0, config(), organizer, 'save')).ok).toBe(true);
  });
  it('refuses to wipe a community with an active match', async () => {
    owner = true;
    permissions = '0';
    await fill(config());
    const response = await post('delete', { confirmName: 'Friends' });
    expect(response.status).toBe(409);
    expect((await stub().records(guild)).matches).toHaveLength(1);
  });
});
