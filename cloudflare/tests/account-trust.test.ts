import { env } from 'cloudflare:workers';
import { abortAllDurableObjects, runInDurableObject } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import {
  harness,
  setup as saveSetup,
  settings,
  current,
  stub,
  guild,
  game,
  person,
  act,
} from './harness';
import type { RecordAction } from '../src/domain/records';
import { playerSummary } from '../src/domain/records';
import type { GameAccountProof } from '../src/auth/game-accounts';
import { observeGamePlayers, selectionStats } from '../src/storage/game-records';
import { runtimeAdapter } from '../src/games/runtime';
import { providerJson } from '../src/games/transport';
import type { CatalogId } from '../src/games/types';
import { gameCapabilities } from '../src/games/catalog';

harness();
// Retired games enter these compatibility tests as existing persisted settings.
async function setup(c = settings()) {
  if (gameCapabilities(c.games[0].catalogId)?.lifecycle !== 'legacy') return saveSetup(c);
  const initial = structuredClone(c);
  initial.games[0].catalogId = null;
  const saved = await saveSetup(initial);
  await runInDurableObject(stub(), (_i, state) => {
    for (const g of c.games)
      state.storage.sql.exec('UPDATE games SET config=? WHERE id=?', JSON.stringify(g), g.id);
  });
  return saved;
}
async function fill(c = settings()) {
  await setup(c);
  for (let n = 0; n < 4; n++) await act('add', n);
  return current();
}
const steamId = '76561198000000001';
const config = (catalogId: CatalogId = 'northgard') => {
  const s = settings();
  s.games[0].catalogId = catalogId;
  return s;
};
const link = (rest: Partial<RecordAction> = {}): RecordAction => ({
  id: crypto.randomUUID(),
  kind: 'link',
  gameId: game,
  userId: person(0).id,
  provider: 'steam',
  externalId: steamId,
  url: `https://steamcommunity.com/profiles/${steamId}`,
  ...rest,
});
const proof = (rest: Partial<GameAccountProof> = {}): GameAccountProof => ({
  communityId: guild,
  gameId: game,
  catalogId: 'northgard',
  discordUserId: person(0).id,
  provider: 'steam',
  externalId: steamId,
  url: `https://steamcommunity.com/profiles/${steamId}`,
  namespace: 'steam',
  edition: 'Steam',
  verifiedBy: 'steam-openid',
  verifiedAt: Date.now(),
  ...rest,
});
const stats = (n = 0) => stub().statistics(guild, game, person(n).id, 0, true, person(n).id);
async function noParticipation() {
  await runInDurableObject(stub(), (_i, s) => {
    for (const table of [
      'profiles',
      'matches',
      'participants',
      'rating_changes',
      'wallets',
      'gold_ledger',
    ])
      expect(s.storage.sql.exec(`SELECT * FROM ${table}`).toArray()).toEqual([]);
  });
}
function companionProfile(platformId?: string, status = 200) {
  const previous = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = new URL(new Request(input, init).url);
    if (url.hostname === 'data.aoe2companion.com')
      return status === 200
        ? Response.json({
            profileId: 123,
            name: 'Fixture player',
            ...(platformId ? { steamId: platformId } : {}),
          })
        : new Response(null, { status });
    return previous(input, init);
  });
}

describe('account declarations and verified ownership in SQLite', () => {
  it('links before the first match with no rating, participation or economy writes', async () => {
    await setup(config());
    expect(await stub().linkGameProfile(guild, link(), person(0).id, false)).toMatchObject({
      ok: true,
    });
    const player = (await stats()).player!;
    expect(player).toMatchObject({
      rated: false,
      source: 'unrated',
      matchCount: 0,
      rank: null,
      eligible: false,
    });
    expect(player.profiles[0]).toMatchObject({
      provider: 'steam',
      external_id: steamId,
      trust: 'declared',
      namespace: 'steam',
    });
    expect(playerSummary(player)).toContain('Unrated');
    await noParticipation();
  });
  it('supports network-qualified original Generals declarations without inventing a profile URL', async () => {
    await setup(config('generals'));
    expect(
      await stub().linkGameProfile(
        guild,
        link({ externalId: 'gameranger:12345', url: undefined }),
        person(0).id,
        false,
      ),
    ).toMatchObject({ ok: true });
    expect((await stats()).player?.profiles[0]).toMatchObject({
      provider: 'generals-network',
      external_id: 'gameranger:12345',
      trust: 'declared',
      url: '',
    });
    await noParticipation();
  });
  it('keeps public existence and provider outages as declarations', async () => {
    await setup(config('aoe2'));
    companionProfile(steamId);
    expect(
      (await stub().linkGameProfile(guild, link({ externalId: '123' }), person(0).id, false)).ok,
    ).toBe(true);
    expect((await stats()).player?.profiles[0].trust).toBe('declared');
    await noParticipation();
  });
  it('keeps manual onboarding available during a provider outage', async () => {
    await setup(config('aoe2'));
    companionProfile(undefined, 503);
    const result = await stub().linkGameProfile(
      guild,
      link({ externalId: '123' }),
      person(0).id,
      false,
    );
    expect(result).toMatchObject({ ok: true, content: expect.stringContaining('unavailable') });
    expect((await stats()).player?.profiles[0].trust).toBe('declared');
  });
  it('rejects fabricated trust in ordinary actions and resolves duplicate accounts only by explicit reassignment', async () => {
    await setup(config());
    expect(
      (
        await stub().recordAction(
          guild,
          { ...link(), trust: 'ownership_verified' } as never,
          person(0).id,
          false,
        )
      ).ok,
    ).toBe(false);
    expect((await stats()).player).toBeNull();
    expect((await stub().linkVerifiedGameAccount(guild, proof())).ok).toBe(true);
    expect(
      (await stub().linkVerifiedGameAccount(guild, proof({ discordUserId: person(1).id }))).ok,
    ).toBe(false);
    expect(
      (
        await stub().recordAction(
          guild,
          link({ kind: 'reassign', userId: person(1).id }),
          person(99).id,
          true,
        )
      ).ok,
    ).toBe(false);
    expect(
      (
        await stub().recordAction(
          guild,
          link({
            kind: 'reassign',
            userId: person(1).id,
            reason: 'Organizer checked both players',
          }),
          person(99).id,
          true,
        )
      ).ok,
    ).toBe(true);
    const assigned = (await stats(1)).player?.profiles[0];
    expect(assigned).toMatchObject({ trust: 'organizer_assigned' });
    expect(assigned).not.toHaveProperty('verifiedAt');
    expect((await stats()).player).toBeNull();
    await noParticipation();
  });
  it('persists the authenticated account proof across restart and removes trust on unlink', async () => {
    await setup(config());
    expect((await stub().linkVerifiedGameAccount(guild, proof())).ok).toBe(true);
    await abortAllDurableObjects();
    expect((await stats()).player?.profiles[0]).toMatchObject({
      trust: 'ownership_verified',
      verifiedBy: 'steam-openid',
      platformId: steamId,
    });
    expect((await stub().recordAction(guild, link(), person(0).id, false)).content).toContain(
      'retained',
    );
    expect(
      (
        await stub().recordAction(
          guild,
          link({ kind: 'unlink', url: undefined }),
          person(0).id,
          false,
        )
      ).ok,
    ).toBe(true);
    expect((await stats()).player).toBeNull();
    await runInDurableObject(stub(), (_i, s) =>
      expect(s.storage.sql.exec('SELECT * FROM profile_claims').toArray()).toEqual([]),
    );
    await noParticipation();
  });
  it('requires a provider-proven Steam/native crosswalk and downgrades it when Steam is disconnected', async () => {
    await setup(config('aoe2'));
    companionProfile(steamId);
    expect(
      await providerJson({ url: 'https://data.aoe2companion.com/api/profiles/123' }),
    ).toMatchObject({ status: 'ok' });
    expect(await runtimeAdapter(env, 'aoe2')!.profile('123')).toMatchObject({
      status: 'ok',
      value: { platformId: steamId },
    });
    await stub().recordAction(
      guild,
      link({
        provider: 'aoe2',
        externalId: '123',
        url: 'https://www.aoe2companion.com/profile/123',
      }),
      person(0).id,
      false,
    );
    expect((await stub().linkVerifiedGameAccount(guild, proof({ catalogId: 'aoe2' }))).ok).toBe(
      true,
    );
    expect((await stats()).player?.profiles).toHaveLength(2);
    expect((await stats()).player?.profiles).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ provider: 'aoe2', trust: 'ownership_verified' }),
        expect.objectContaining({ provider: 'steam', trust: 'ownership_verified' }),
      ]),
    );
    await stub().recordAction(guild, link({ kind: 'unlink', url: undefined }), person(0).id, false);
    const native = (await stats()).player?.profiles[0];
    expect(native).toMatchObject({ provider: 'aoe2', trust: 'declared' });
    expect(native).not.toHaveProperty('verifiedAt');
  });
  it('does not upgrade a native declaration that belongs to another Steam account', async () => {
    await setup(config('aoe2'));
    companionProfile('76561198000000002');
    await stub().recordAction(
      guild,
      link({
        provider: 'aoe2',
        externalId: '123',
        url: 'https://www.aoe2companion.com/profile/123',
      }),
      person(0).id,
      false,
    );
    const result = await stub().linkVerifiedGameAccount(guild, proof({ catalogId: 'aoe2' }));
    expect(result.content).toContain('native game account could not be established');
    expect((await stats()).player?.profiles.find((p) => p.provider === 'aoe2')?.trust).toBe(
      'declared',
    );
    expect((await stats()).player?.profiles.find((p) => p.provider === 'steam')?.trust).toBe(
      'ownership_verified',
    );
  });
  it('rejects expired, wrong-game, and cross-community proof without side effects', async () => {
    await setup(config());
    for (const invalid of [
      proof({ verifiedAt: Date.now() - 3600000 }),
      proof({ catalogId: 'aoe2' }),
      proof({ communityId: '100000000000009999' }),
      proof({ verifiedBy: 'bar-oauth' }),
    ])
      expect((await stub().linkVerifiedGameAccount(guild, invalid)).ok).toBe(false);
    expect((await stats()).player).toBeNull();
    await noParticipation();
  });
  it('honors independent lookup and ownership switches without blocking declarations', async () => {
    const c = config('aoe2');
    c.games[0].connector = { enabled: true, capabilities: { accountLookup: false } };
    await setup(c);
    companionProfile(steamId);
    await stub().recordAction(
      guild,
      link({
        provider: 'aoe2',
        externalId: '123',
        url: 'https://www.aoe2companion.com/profile/123',
      }),
      person(0).id,
      false,
    );
    const before = vi.mocked(fetch).mock.calls.length;
    expect((await stub().linkVerifiedGameAccount(guild, proof({ catalogId: 'aoe2' }))).ok).toBe(
      true,
    );
    expect(vi.mocked(fetch).mock.calls).toHaveLength(before);
    expect((await stats()).player?.profiles.find((p) => p.provider === 'aoe2')?.trust).toBe(
      'declared',
    );
    const saved = await stub().read(guild);
    saved.settings.games[0].connector!.capabilities!.ownershipVerification = false;
    await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save');
    expect((await stub().linkVerifiedGameAccount(guild, proof({ catalogId: 'aoe2' }))).ok).toBe(
      false,
    );
    expect(
      (await stub().linkGameProfile(guild, link({ externalId: '124' }), person(0).id, false)).ok,
    ).toBe(true);
  });
  it('refuses a completed callback after its integration credentials are removed, while permitting manual SC2 linking', async () => {
    await setup(config('sc2'));
    expect(
      (
        await stub().linkVerifiedGameAccount(
          guild,
          proof({
            catalogId: 'sc2',
            provider: 'battle-net',
            verifiedBy: 'battlenet-oauth',
            externalId: '1:1:123',
            namespace: 'sc2-character',
            platformId: '999',
          }),
        )
      ).ok,
    ).toBe(false);
    expect(
      await stub().linkGameProfile(guild, link({ externalId: '1:1:123' }), person(0).id, false),
    ).toMatchObject({ ok: true });
    expect((await stats()).player?.profiles[0]).toMatchObject({
      provider: 'battle-net',
      trust: 'declared',
      external_id: '1:1:123',
    });
    await noParticipation();
  });
  it('keeps unsupported lobby capabilities visible as unsupported, regardless of the enabled switch', async () => {
    await setup(config());
    expect((await stub().connectorStatus(guild)).games[0].capabilities).toMatchObject({
      lobbyDiscovery: 'unsupported',
      lobbyObservation: 'unsupported',
      ownershipVerification: 'available',
    });
  });
  it('rechecks ownership settings after an interrupted native lookup', async () => {
    const c = config('aoe2');
    c.games[0].connector = { enabled: true };
    await setup(c);
    await stub().recordAction(
      guild,
      link({
        provider: 'aoe2',
        externalId: '123',
        url: 'https://www.aoe2companion.com/profile/123',
      }),
      person(0).id,
      false,
    );
    const previous = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (new URL(new Request(input, init).url).hostname === 'data.aoe2companion.com') {
        // Simulate a settings write while the callback is awaiting the provider.
        await runInDurableObject(stub(), (_i, s) => {
          const row = s.storage.sql
            .exec<{ config: string }>('SELECT config FROM games WHERE id=?', game)
            .toArray()[0];
          const changed = JSON.parse(row.config);
          changed.connector.capabilities = { ownershipVerification: false };
          s.storage.sql.exec('UPDATE games SET config=? WHERE id=?', JSON.stringify(changed), game);
        });
        return Response.json({ profileId: 123, name: 'Fixture', steamId });
      }
      return previous(input, init);
    });
    expect((await stub().linkVerifiedGameAccount(guild, proof({ catalogId: 'aoe2' }))).ok).toBe(
      false,
    );
    expect((await stats()).player?.profiles).toHaveLength(1);
    expect((await stats()).player?.profiles[0].trust).toBe('declared');
  });
  it('backs up ownership metadata, accepts schema7 without inventing verified trust, and requires schema8 claims', async () => {
    await setup(config());
    await stub().linkVerifiedGameAccount(guild, proof());
    const backup = await stub().operationalBackup(guild);
    expect(backup.tables.schema_version[0].version).toBe(8);
    expect(backup.tables.profile_claims[0].trust).toBe('ownership_verified');
    const restored = env.COMMUNITIES.getByName('recovery:claims:' + guild);
    expect((await restored.operationalRestore(guild, backup as never)).restored).toBe(true);
    expect(
      (await restored.statistics(guild, game, person(0).id, 0, true, person(0).id)).player
        ?.profiles[0].trust,
    ).toBe('ownership_verified');
    const legacy = structuredClone(backup);
    legacy.tables.schema_version[0].version = 7;
    delete legacy.tables.profile_claims;
    legacy.tables.profile_refs[0].provenance = 'player';
    const old = env.COMMUNITIES.getByName('recovery:legacyclaims:' + guild);
    expect((await old.operationalRestore(guild, legacy as never)).restored).toBe(true);
    expect(
      (await old.statistics(guild, game, person(0).id, 0, true, person(0).id)).player?.profiles[0]
        .trust,
    ).toBe('declared');
    const missing = structuredClone(backup);
    delete missing.tables.profile_claims;
    expect(
      (
        await env.COMMUNITIES.getByName('recovery:missingclaims:' + guild).operationalRestore(
          guild,
          missing as never,
        )
      ).restored,
    ).toBe(false);
  });
  it('never converts a name suggestion into a link or a played selection', async () => {
    const m = await fill(config('aoe2'));
    await runInDurableObject(stub(), (_i, s) => {
      const suggestions = observeGamePlayers(s.storage.sql, m, 'aoe2', {
        externalId: '9001',
        name: 'fixture',
        phase: 'started',
        observedAt: Date.now(),
        startedAt: Date.now() - 1,
        finishedAt: null,
        capacity: 4,
        map: null,
        source: 'fixture',
        players: m.teams.flatMap((t, side) =>
          t.map((p) => ({
            profileId: p.id,
            name: p.name,
            team: String(side + 1),
            selection: 'Franks',
          })),
        ),
      });
      expect(suggestions).toHaveLength(4);
      expect(s.storage.sql.exec('SELECT * FROM profile_refs').toArray()).toEqual([]);
      expect(s.storage.sql.exec('SELECT * FROM played_selections').toArray()).toEqual([]);
    });
  });
  it('preserves historical inferred links as untrusted suggestions until explicit confirmation', async () => {
    const c = config('aoe2');
    c.queues[0].checkIn.enabled = false;
    const m = await fill(c);
    const user = person(0).id;
    const side = m.teams[0].some((p) => p.id === user) ? 0 : 1;
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec(
        'INSERT INTO profile_refs VALUES(?,?,?,?,?,?,?)',
        game,
        'aoe2',
        '123',
        user,
        'https://www.aoe2companion.com/profile/123',
        'inferred:name:1.00:legacy',
        Date.now(),
      );
      s.storage.sql.exec(
        'INSERT INTO played_selections VALUES(?,?,?,?,?,?,?,?,?,?)',
        m.id,
        game,
        user,
        'aoe2',
        '123',
        'Franks',
        side,
        'inferred:name:1.00:legacy',
        'legacy',
        Date.now(),
      );
    });
    await act('loss', Number(BigInt(m.captains[0]) - 100000000000000100n), { matchId: m.id });
    expect((await stats()).player?.profiles[0].trust).toBe('suggested');
    expect((await stats()).player?.civilizations).toEqual([]);
    await stub().recordAction(
      guild,
      link({
        provider: 'aoe2',
        externalId: '123',
        url: 'https://www.aoe2companion.com/profile/123',
      }),
      user,
      false,
    );
    expect((await stats()).player?.profiles[0].trust).toBe('declared');
    expect((await stats()).player?.civilizations).toMatchObject([
      { selection: 'Franks', games: 1, inferred: 0 },
    ]);
  });
  it('preserves legacy Mythology links with an unknown edition until explicit Retold redeclaration', async () => {
    const c = config('aom');
    c.games[0].connector = { enabled: true };
    c.queues[0].checkIn.enabled = false;
    let m = await fill(c);
    await runInDurableObject(stub(), (_i, s) => {
      s.storage.sql.exec(
        'INSERT INTO profile_refs VALUES(?,?,?,?,?,?,?)',
        game,
        'aom',
        '123',
        person(0).id,
        'https://example.com/legacy-mythology/123',
        'player',
        Date.now(),
      );
    });
    await abortAllDurableObjects();
    expect((await stats()).player?.profiles[0]).toMatchObject({
      provider: 'aom',
      external_id: '123',
      edition: 'unspecified',
      namespace: 'aom',
      trust: 'declared',
    });
    await act('lobby_attach', 99, { admin: true, matchId: m.id, reference: '9001' });
    m = await current();
    expect(m.lobby?.profiles.find((p) => p.userId === person(0).id)?.profileId).toBeNull();
    await act('lobby_confirm', 99, { admin: true, matchId: m.id, reference: m.lobby!.id });
    m = await current();
    await runInDurableObject(stub(), (_i, s) => {
      observeGamePlayers(s.storage.sql, m, 'aom', {
        externalId: '9001',
        name: 'Retold fixture',
        phase: 'started',
        observedAt: Date.now(),
        startedAt: Date.now() - 1,
        finishedAt: null,
        capacity: 4,
        map: null,
        source: 'fixture',
        players: [{ profileId: '123', name: person(0).name, team: '1', selection: 'Zeus' }],
      });
      expect(s.storage.sql.exec('SELECT * FROM played_selections').toArray()).toEqual([]);
    });
    const previous = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) =>
      new URL(new Request(input, init).url).hostname === 'athens-live-api.worldsedgelink.com'
        ? new Response(null, { status: 503 })
        : previous(input, init),
    );
    expect(
      (
        await stub().linkGameProfile(
          guild,
          link({ provider: 'aom', externalId: '123', url: undefined }),
          person(0).id,
          false,
        )
      ).ok,
    ).toBe(true);
    expect((await stats()).player?.profiles[0]).toMatchObject({
      edition: gameCapabilities('aom')!.manifest.edition,
      namespace: gameCapabilities('aom')!.manifest.accountNamespace,
      trust: 'declared',
    });
    await act('lobby_attach', 99, { admin: true, matchId: m.id, reference: '9001' });
    m = await current();
    expect(m.lobby?.profiles.find((p) => p.userId === person(0).id)?.profileId).toBe('123');
  });
  it('retains the known legacy AoE2 Companion edition and namespace', async () => {
    await setup(config('aoe2'));
    await runInDurableObject(stub(), (_i, s) =>
      s.storage.sql.exec(
        'INSERT INTO profile_refs VALUES(?,?,?,?,?,?,?)',
        game,
        'aoe2',
        '123',
        person(0).id,
        'https://www.aoe2companion.com/profile/123',
        'player',
        Date.now(),
      ),
    );
    await abortAllDurableObjects();
    expect((await stats()).player?.profiles[0]).toMatchObject({
      edition: gameCapabilities('aoe2')!.manifest.edition,
      namespace: 'aoe2:profile',
      trust: 'declared',
    });
  });
  it('counts captain-confirmed manual factions without provider profiles and excludes substitutions', async () => {
    const c = config();
    c.queues[0].checkIn.enabled = false;
    const m = await fill(c);
    await act('loss', Number(BigInt(m.captains[0]) - 100000000000000100n), { matchId: m.id });
    await runInDurableObject(stub(), (_i, s) => {
      const saved = JSON.parse(
        s.storage.sql
          .exec<{ config: string }>('SELECT config FROM matches WHERE id=?', m.id)
          .toArray()[0].config,
      );
      saved.lobby = {
        confirmedBy: m.captains[0],
        manualFacts: {
          players: [
            {
              userId: person(0).id,
              side: m.teams[0].some((p) => p.id === person(0).id) ? 0 : 1,
              selection: 'Wolf',
            },
          ],
        },
      };
      s.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(saved), m.id);
      expect(selectionStats(s.storage.sql, game, person(0).id)).toMatchObject([
        { selection: 'Wolf', games: 1 },
      ]);
      saved.teams = saved.teams.map((team: { id: string }[]) =>
        team.filter((p) => p.id !== person(0).id),
      );
      s.storage.sql.exec('UPDATE matches SET config=? WHERE id=?', JSON.stringify(saved), m.id);
      expect(selectionStats(s.storage.sql, game, person(0).id)).toEqual([]);
      expect(s.storage.sql.exec('SELECT * FROM profile_refs').toArray()).toEqual([]);
    });
  });
});
