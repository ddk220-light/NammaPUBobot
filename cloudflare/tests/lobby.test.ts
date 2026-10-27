import { env } from 'cloudflare:workers';
import { runInDurableObject, abortAllDurableObjects, runDurableObjectAlarm } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import { aoe2Adapter, parseAoE2Match } from '../src/games/aoe2';
import { gameCapabilities } from '../src/games/catalog';
import { gameAdapter } from '../src/games/registry';
import { settingsSchema } from '../src/domain/config';
import { validObservation, type LobbyEvent } from '../src/domain/lobby';
import type { Match } from '../src/domain/play';
import {
  harness,
  settings,
  setup,
  fill,
  current,
  stub,
  guild,
  game,
  channel,
  person,
  act,
  action,
} from './harness';

harness();
const profileId = (id: string) => String(BigInt(id) - 100000000000000000n);
async function linked(withProfiles = true, count = 4) {
  const s = settings();
  s.games[0].catalogId = 'aoe2';
  s.games[0].connector = { enabled: true };
  s.queues[0].checkIn.enabled = false;
  s.queues[0].modules.gold = s.queues[0].modules.predictions = true;
  s.queues[0].size = count;
  await setup(s);
  for (let n = 0; n < count; n++) await act('add', n);
  const m = await current();
  if (withProfiles)
    for (const p of m.roster)
      expect(
        await stub().recordAction(
          guild,
          {
            id: crypto.randomUUID(),
            kind: 'link',
            gameId: game,
            userId: p.id,
            provider: 'aoe2',
            externalId: profileId(p.id),
            url: `https://www.aoe2companion.com/profile/${profileId(p.id)}`,
          },
          p.id,
          false,
        ),
      ).toMatchObject({ ok: true });
  expect(
    await act('lobby_attach', 99, { admin: true, matchId: m.id, reference: '9001' }),
  ).toMatchObject({ ok: true });
  return current();
}
function payload(m: Match, finished = true) {
  return {
    matchId: Number(m.lobby!.externalId),
    name: 'Synthetic test game',
    started: new Date(m.createdAt).toISOString(),
    finished: finished ? new Date().toISOString() : null,
    teams: m.teams.map((team, side) => ({
      teamId: side + 1,
      players: team.map((p) => ({
        profileId: Number(profileId(p.id)),
        name: p.name,
        civName: 'Franks',
      })),
    })),
  };
}
function event(m: Match): LobbyEvent {
  return {
    schemaVersion: 1,
    communityId: guild,
    gameId: game,
    matchId: m.id,
    bindingId: m.lobby!.id,
    rosterVersion: m.lobby!.rosterVersion,
    adapterId: 'aoe2',
    eventId: crypto.randomUUID(),
    observation: parseAoE2Match(payload(m), m.lobby!.externalId),
  };
}
const due = () =>
  runInDurableObject(stub(), (_i, state) => {
    state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='lobby'");
  });
const jobs = () =>
  runInDurableObject(stub(), (_i, state) =>
    state.storage.sql.exec("SELECT * FROM deadlines WHERE kind='lobby'").toArray(),
  );
const prompts = () =>
  runInDurableObject(stub(), (_i, state) =>
    state.storage.sql.exec("SELECT * FROM outbox WHERE key LIKE 'end:%'").toArray(),
  );

describe('game adapter contract', () => {
  it.each([
    '9001',
    'https://aoe.ms/lobby?gameId=9001',
    'https://www.aoe2companion.com/matches/9001',
    'aoe2de://0/9001',
  ])('parses %s without following user URLs', (value) => {
    expect(aoe2Adapter.parseLobbyReference(value)).toBe('9001');
    expect(aoe2Adapter.lobbyUrl('9001')).toBe('https://aoe.ms/lobby?gameId=9001');
  });
  it.each([
    '-1',
    '0',
    '1e9',
    'https://aoe.ms.evil.test/lobby?gameId=9001',
    'http://aoe.ms/lobby?gameId=9001',
    'https://user:pass@aoe.ms/lobby?gameId=9001',
    'https://aoe.ms:444/lobby?gameId=9001',
  ])('rejects %s', (value) => {
    expect(() => aoe2Adapter.parseLobbyReference(value)).toThrow();
  });
  it('advertises actual capabilities and keeps other games manual', () => {
    expect(gameCapabilities('aoe2')).toMatchObject({ lobby: true, occupancy: true });
    for (const id of ['generals', 'aoe4', 'aoe3', 'aom']) {
      expect(gameAdapter(id)).toBeUndefined();
      const s = settings();
      s.games[0].catalogId = id as 'aoe4';
      expect(settingsSchema.safeParse(s).success).toBe(true);
      s.games[0].connector = { enabled: true };
      expect(settingsSchema.safeParse(s).success).toBe(false);
    }
    expect(gameCapabilities('generals')?.selection).toBe('faction');
  });
  it('rejects duplicate/invalid players and inconsistent times', () => {
    const now = Date.now(),
      base = {
        matchId: 9001,
        started: new Date(now - 1000).toISOString(),
        finished: new Date(now).toISOString(),
        teams: [{ teamId: 1, players: [{ profileId: 100 }] }],
      };
    expect(parseAoE2Match(base, '9001', now).phase).toBe('finished');
    for (const input of [
      { ...base, matchId: 9002 },
      { ...base, started: null },
      { ...base, finished: new Date(now - 2000).toISOString() },
      { ...base, started: new Date(now + 1).toISOString() },
      { ...base, teams: [{ players: [{ profileId: 'bad' }] }] },
      { ...base, teams: [{ players: [{ profileId: 100 }, { profileId: 100 }] }] },
    ])
      expect(() => parseAoE2Match(input, '9001', now)).toThrow();
    expect(
      validObservation({ ...parseAoE2Match(base, '9001', now), observedAt: now - 400000 }, now),
    ).toBe(false);
  });
  it.each([404, 429, 500])('handles provider HTTP %s without a socket fallback', async (status) => {
    vi.mocked(fetch).mockImplementation(async () => new Response(null, { status }));
    expect(await aoe2Adapter.observe('9001', false)).toMatchObject({
      status: status === 404 ? 'not_found' : 'unavailable',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('honors a provider retry delay longer than the normal backoff ceiling', async () => {
    await linked();
    vi.mocked(fetch).mockImplementation(
      async () => new Response(null, { status: 429, headers: { 'Retry-After': '3600' } }),
    );
    await due();
    const before = Date.now();
    await stub().pollLobbies(guild);
    expect(Number((await jobs())[0].due) - before).toBeGreaterThanOrEqual(3600000);
  });
  it('bounds malformed, oversized and failed responses', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('bad json'));
    expect((await aoe2Adapter.observe('9001', false)).status).toBe('unavailable');
    vi.mocked(fetch).mockResolvedValue(new Response('x'.repeat(400001)));
    expect((await aoe2Adapter.observe('9001', false)).status).toBe('unavailable');
    vi.mocked(fetch).mockRejectedValue(new DOMException('timed out', 'TimeoutError'));
    expect((await aoe2Adapter.observe('9001', false)).status).toBe('unavailable');
  });
});

describe('durable lobby journey', () => {
  it('verifies all eight players before prompting in the standard game size', async () => {
    const m = await linked(true, 8);
    expect(m.roster).toHaveLength(8);
    expect(await stub().connectorEnded(guild, event(m))).toMatchObject({ accepted: true });
    expect((await current()).lobby).toMatchObject({ rosterVerified: true, prompted: true });
    expect(await prompts()).toHaveLength(1);
  });
  it('allows explicit attachment during check-in and never borrows a match from another community', async () => {
    const s = settings();
    s.games[0].catalogId = 'aoe2';
    s.games[0].connector = { enabled: true };
    const m = await fill(s);
    expect(
      await act('lobby_attach', 99, { admin: true, reference: '9001', matchId: m.id }),
    ).toMatchObject({ ok: true });
    const other = '706320010368516299';
    expect(
      await stub(other).play(
        other,
        action('lobby_attach', 99, { admin: true, reference: '9001', matchId: m.id }),
      ),
    ).toMatchObject({ ok: false });
  });

  it('deduplicates attachment and authorizes captains in the correct channel', async () => {
    const m = await linked();
    const retry = action('lobby_attach', 99, { admin: true, reference: '9001', matchId: m.id });
    await stub().play(guild, retry);
    await stub().play(guild, retry);
    expect((await current()).lobby!.id).toBe(m.lobby!.id);
    const other = m.roster.find((p) => !m.captains.includes(p.id))!;
    expect(
      await act('lobby_unlink', 99, { actor: { id: other.id, name: other.name }, matchId: m.id }),
    ).toMatchObject({ ok: false });
    expect(
      await act('lobby_unlink', 99, {
        admin: true,
        matchId: m.id,
        channelId: '706320010368516299',
      }),
    ).toMatchObject({ ok: false });
    expect(
      await act('lobby_unlink', 99, {
        actor: { id: m.captains[0], name: 'Captain' },
        matchId: m.id,
      }),
    ).toMatchObject({ ok: true });
    expect(await jobs()).toHaveLength(0);
  });
  it('prompts once for verified completion while closing bets and never awarding a result', async () => {
    const m = await linked();
    const e = event(m);
    expect(await stub().connectorEnded(guild, e)).toMatchObject({ accepted: true });
    expect(await stub().connectorEnded(guild, e)).toMatchObject({ accepted: false });
    await stub().connectorEnded(guild, { ...e, eventId: crypto.randomUUID() });
    expect(await prompts()).toHaveLength(1);
    expect(await jobs()).toHaveLength(0);
    expect((await current()).state).toBe('playing');
    expect((await current()).lobby).toMatchObject({ rosterVerified: true, prompted: true });
    const records = await stub().records(guild);
    expect(records.changes).toEqual([]);
    expect(records.economy.books[0].status).toBe('refunded');
    await act('recover_abort', 99, { matchId: m.id, admin: true });
    expect(await prompts()).toHaveLength(0);
    expect(await stub().connectorEnded(guild, event(m))).toMatchObject({ accepted: false });
  });
  it.each([
    'wrong_game',
    'wrong_community',
    'wrong_binding',
    'wrong_roster',
    'wrong_external',
    'wrong_adapter',
    'historical',
  ])('rejects %s evidence', async (kind) => {
    const m = await linked(),
      e = event(m);
    if (kind === 'wrong_game') e.gameId = crypto.randomUUID();
    if (kind === 'wrong_community') e.communityId = '706320010368516299';
    if (kind === 'wrong_binding') e.bindingId = crypto.randomUUID();
    if (kind === 'wrong_roster') e.rosterVersion++;
    if (kind === 'wrong_external') e.observation.externalId = '9002';
    if (kind === 'wrong_adapter') e.adapterId = 'aoe4';
    if (kind === 'historical') e.observation.startedAt = m.createdAt - 300000;
    expect(await stub().connectorEnded(guild, e)).toMatchObject({ accepted: false });
    expect(await prompts()).toHaveLength(0);
  });
  it.each(['missing', 'wrong_player', 'wrong_team'])(
    'keeps manual fallback for %s profile evidence',
    async (kind) => {
      const m = await linked(kind !== 'missing'),
        e = event(m);
      if (kind === 'missing') e.observation.players.forEach((p) => (p.name = 'Unknown guest'));
      if (kind === 'wrong_player') e.observation.players[0].profileId = '999999';
      if (kind === 'wrong_team') e.observation.players.forEach((p) => (p.team = '1'));
      await stub().connectorEnded(guild, e);
      expect((await current()).lobby!.rosterVerified).toBe(false);
      expect(await prompts()).toHaveLength(0);
    },
  );
  it('persists polling across restart and never infers start from 404/full status', async () => {
    const m = await linked();
    vi.mocked(fetch).mockImplementation(async () => new Response(null, { status: 404 }));
    await due();
    await stub().pollLobbies(guild);
    expect((await current()).lobby).toMatchObject({ state: 'unavailable', failures: 1 });
    expect((await current()).lobby!.observation).toBeUndefined();
    expect((await jobs())[0].due).toBeGreaterThan(Date.now());
    await abortAllDurableObjects();
    vi.mocked(fetch).mockImplementation(async () => Response.json(payload(m, false)));
    await due();
    await stub().pollLobbies(guild);
    expect((await current()).lobby!.observation!.phase).toBe('started');
    expect(await prompts()).toHaveLength(0);
    expect((await stub().records(guild)).economy.books[0].status).toBe('refunded');
    vi.mocked(fetch).mockImplementation(async () => Response.json(payload(m)));
    await due();
    await stub().pollLobbies(guild);
    expect(await prompts()).toHaveLength(1);
  });
  it.each(['cancel', 'relink', 'substitute', 'disable', 'profile'])(
    'discards an in-flight response after %s',
    async (change) => {
      const m = await linked();
      let changed = false;
      const original = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        if (!String(input).startsWith('https://data.aoe2companion.com/'))
          return original(input, init);
        if (!changed) {
          changed = true;
          if (change === 'cancel') await act('recover_abort', 99, { matchId: m.id, admin: true });
          if (change === 'relink')
            await act('lobby_attach', 99, { matchId: m.id, admin: true, reference: '9002' });
          if (change === 'substitute')
            await act('force_sub', 99, {
              matchId: m.id,
              admin: true,
              target: { id: m.roster[0].id, name: m.roster[0].name },
              replacement: person(20),
            });
          if (change === 'disable') {
            const s = await stub().read(guild);
            s.settings.games[0].connector!.enabled = false;
            await stub().save(guild, s.revision, s.settings, person(99).id, 'save');
          }
          if (change === 'profile')
            await stub().recordAction(
              guild,
              {
                id: crypto.randomUUID(),
                kind: 'unlink',
                gameId: game,
                userId: m.roster[0].id,
                provider: 'aoe2',
                externalId: profileId(m.roster[0].id),
              },
              m.roster[0].id,
              false,
            );
        }
        return Response.json(payload(m));
      });
      await due();
      await stub().pollLobbies(guild);
      expect(changed).toBe(true);
      expect(await prompts()).toHaveLength(0);
      expect((await current()).lobby!.observation).toBeUndefined();
    },
  );
  it('respects rate limits, bounds retries, and polls nothing when idle', async () => {
    await linked();
    vi.mocked(fetch).mockImplementation(async () => new Response(null, { status: 429 }));
    await due();
    const before = Date.now();
    await stub().pollLobbies(guild);
    expect(Number((await jobs())[0].due) - before).toBeGreaterThanOrEqual(120000);
    vi.mocked(fetch).mockClear();
    await stub().pollLobbies(guild);
    expect(fetch).not.toHaveBeenCalled();
    await act('lobby_unlink', 99, { admin: true });
    await stub().pollLobbies(guild);
    expect(fetch).not.toHaveBeenCalled();
  });
  it('uses the shared alarm without losing lobby or match lifetime deadlines', async () => {
    const m = await linked();
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      String(input).startsWith('https://data.aoe2companion.com/')
        ? Response.json(payload(m, false))
        : original(input, init),
    );
    await due();
    await runDurableObjectAlarm(stub());
    expect((await current()).lobby!.observation!.phase).toBe('started');
    const kinds = await runInDurableObject(stub(), (_i, s) =>
      s.storage.sql
        .exec<{ kind: string }>('SELECT kind FROM deadlines')
        .toArray()
        .map((r) => r.kind),
    );
    expect(kinds).toContain('lobby');
    expect(kinds).toContain('lifetime');
  });
  it('treats a full roster without start evidence as unknown and rejects time regression', async () => {
    const m = await linked();
    vi.mocked(fetch).mockImplementation(async () =>
      Response.json({ ...payload(m, false), started: null }),
    );
    await due();
    await stub().pollLobbies(guild);
    expect((await current()).lobby!.observation).toBeUndefined();
    const e = event(m);
    await stub().connectorEnded(guild, e);
    expect(
      await stub().connectorEnded(guild, {
        ...e,
        eventId: crypto.randomUUID(),
        observation: { ...e.observation, observedAt: e.observation.observedAt - 1 },
      }),
    ).toMatchObject({ accepted: false });
    expect(
      await stub().connectorEnded(guild, {
        ...e,
        eventId: crypto.randomUUID(),
        observation: { ...e.observation, startedAt: e.observation.startedAt! - 1 },
      }),
    ).toMatchObject({ accepted: false });
    expect(await prompts()).toHaveLength(1);
  });
  it('invalidates delivered evidence immediately when an organizer disables the connector', async () => {
    const m = await linked();
    await stub().connectorEnded(guild, event(m));
    const snapshot = await stub().read(guild);
    snapshot.settings.games[0].connector!.enabled = false;
    await stub().save(guild, snapshot.revision, snapshot.settings, person(99).id, 'save');
    expect((await current()).lobby!.state).toBe('invalidated');
    expect(await prompts()).toHaveLength(0);
    expect(await jobs()).toHaveLength(0);
  });
  it('rejects ambiguous profiles and requires an explicit fresh attachment after mapping edits', async () => {
    const m = await linked();
    await stub().recordAction(
      guild,
      {
        id: crypto.randomUUID(),
        kind: 'link',
        gameId: game,
        userId: person(0).id,
        provider: 'aoe2',
        externalId: '99999',
        url: 'https://www.aoe2companion.com/profile/99999',
      },
      person(0).id,
      false,
    );
    expect((await current()).lobby!.state).toBe('invalidated');
    expect(await act('lobby_attach', 99, { admin: true, reference: '9001' })).toMatchObject({
      ok: false,
    });
    await stub().recordAction(
      guild,
      {
        id: crypto.randomUUID(),
        kind: 'unlink',
        gameId: game,
        userId: person(0).id,
        provider: 'aoe2',
        externalId: '99999',
      },
      person(0).id,
      false,
    );
    expect(await act('lobby_attach', 99, { admin: true, reference: '9001' })).toMatchObject({
      ok: true,
    });
    expect((await current()).lobby!.id).not.toBe(m.lobby!.id);
    expect(await stub().connectorEnded(guild, event(m))).toMatchObject({ accepted: false });
  });
  it('stops polling when the maximum lifetime elapses', async () => {
    await linked();
    await runInDurableObject(stub(), (_i, s) => {
      const row = s.storage.sql
        .exec<{ id: string; config: string }>('SELECT id,config FROM matches')
        .toArray()[0];
      const m = JSON.parse(row.config);
      m.expiresAt = Date.now() - 1;
      s.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(m), row.id);
    });
    vi.mocked(fetch).mockClear();
    await due();
    await stub().pollLobbies(guild);
    expect(fetch).not.toHaveBeenCalled();
    expect(await jobs()).toHaveLength(0);
    expect((await current()).lobby!.state).toBe('stopped');
  });
  it('checks profile existence but preserves conflicts and labels self-declared ownership', async () => {
    await linked(false);
    vi.mocked(fetch).mockImplementation(async () =>
      Response.json({ profileId: 100, name: 'Public player' }),
    );
    const input = {
      id: crypto.randomUUID(),
      kind: 'link' as const,
      gameId: game,
      userId: person(0).id,
      externalId: '100',
    };
    const result = await stub().linkGameProfile(guild, input, person(0).id, false);
    expect(result.ok).toBe(true);
    expect(result.content).toContain('ownership is not verified');
    expect(
      await stub().linkGameProfile(
        guild,
        { ...input, id: crypto.randomUUID(), userId: person(1).id },
        person(1).id,
        false,
      ),
    ).toMatchObject({ ok: false });
    expect(
      await stub().linkGameProfile(
        guild,
        { ...input, id: crypto.randomUUID(), userId: person(1).id },
        person(0).id,
        false,
      ),
    ).toMatchObject({ ok: false });
  });
  it('retains binding evidence in backup and leaves restored polling disabled', async () => {
    const m = await linked();
    const backup = await stub().operationalBackup(guild);
    expect(backup.tables.schema_version[0].version).toBe(7);
    const copy = env.COMMUNITIES.getByName('lobby-recovery');
    expect(
      await copy.operationalRestore(guild, {
        ...backup,
        sourceApplicationId: backup.sourceApplicationId!,
      }),
    ).toMatchObject({ restored: true, readOnly: true });
    expect((await copy.records(guild)).matches[0].lobby!.id).toBe(m.lobby!.id);
    vi.mocked(fetch).mockClear();
    await copy.pollLobbies(guild);
    expect(fetch).not.toHaveBeenCalled();
    expect(await runInDurableObject(copy, (_i, s) => s.storage.getAlarm())).toBeNull();
  });
});
