import { env, exports } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import { describe, it, expect, vi } from 'vitest';
import { EconomyStore } from '../src/storage/economy-store';
import {
  harness,
  stub,
  settings,
  fill,
  current,
  act,
  person,
  guild,
  game,
  action,
} from './harness';
harness();
describe('M10 operator recovery boundaries', () => {
  it('imports complete gold history once without another seed and rejects altered source/scope', async () => {
    const s = settings();
    await stub().save(guild, 0, s, person(99).id, 'save');
    const bundle = {
      sourceId: 'legacy-copy',
      wallets: [
        { userId: person(8).id, balance: 550, seededAt: 1000 },
        { userId: person(9).id, balance: 450, seededAt: 1000 },
      ],
      ledger: [
        { id: '1', userId: person(8).id, amount: 500, kind: 'seed', at: 1000 },
        { id: '2', userId: person(8).id, amount: -50, kind: 'bet', at: 2000 },
        { id: '3', userId: person(8).id, amount: 100, kind: 'payout', at: 3000 },
        { id: '4', userId: person(9).id, amount: 500, kind: 'seed', at: 1000 },
        { id: '5', userId: person(9).id, amount: -50, kind: 'bet', at: 2000 },
      ],
      markets: [
        {
          id: '1',
          matchSourceId: 'legacy-match',
          status: 'settled' as const,
          winner: 'alpha' as const,
          openedAt: 2000,
          closedAt: 3000,
          bets: [
            { userId: person(8).id, side: 'alpha' as const, stake: 50, payout: 100 },
            { userId: person(9).id, side: 'beta' as const, stake: 50, payout: 0 },
          ],
        },
      ],
    };
    const invalid = structuredClone(bundle);
    invalid.markets[0].bets[0].payout = 99;
    expect(await stub().economyImport(guild, game, invalid, 'invalid', false)).toMatchObject({
      ok: false,
    });
    expect(await stub().economyImport(guild, game, bundle, 'digest', false)).toMatchObject({
      ok: true,
    });
    expect(await stub().economyImport(guild, game, bundle, 'digest', true)).toMatchObject({
      ok: true,
      alreadyImported: false,
    });
    expect(await stub().economyImport(guild, game, bundle, 'digest', true)).toMatchObject({
      ok: true,
      alreadyImported: true,
    });
    expect(await stub().economyImport(guild, game, bundle, 'different', true)).toMatchObject({
      ok: false,
    });
    expect((await stub().records(guild)).economy.importedPredictions).toHaveLength(1);
    await runInDurableObject(stub(), (_i, s) => {
      const e = new EconomyStore(s.storage.sql);
      e.seed(game, person(8).id);
      expect(e.balance(game, person(8).id)).toBe(550);
    });
  });
  it('backs up all community modules and restores an isolated read-only copy', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    s.queues[0].modules.gold = true;
    s.queues[0].modules.predictions = true;
    const m = await fill(s);
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    const backup = await stub().operationalBackup(guild);
    const recovered = env.COMMUNITIES.getByName('recovery:test-copy:' + guild);
    expect(await recovered.operationalRestore(guild, backup as any)).toMatchObject({
      readOnly: true,
      discordDelivery: false,
    });
    const before = await stub().records(guild),
      after = await recovered.records(guild);
    expect(after.matches).toEqual(before.matches);
    expect(after.economy).toEqual(before.economy);
    expect(after.waiting).toEqual(before.waiting);
    expect((await recovered.operationalBackup(guild)).sourceApplicationId).toBe(
      backup.sourceApplicationId,
    );
    expect((await recovered.play(guild, action('add', 9))).ok).toBe(false);
    vi.mocked(fetch).mockClear();
    await recovered.flush();
    expect(vi.mocked(fetch).mock.calls).toEqual([]);
    await runInDurableObject(recovered, async (_i, state) =>
      expect(await state.storage.getAlarm()).toBeNull(),
    );
  });
  it('rejects overwriting a populated object, foreign identity and unsafe table names', async () => {
    await fill();
    const backup = await stub().operationalBackup(guild);
    expect(await stub().operationalRestore(guild, backup as any)).toMatchObject({
      restored: false,
      error: expect.stringContaining('empty'),
    });
    const copy = env.COMMUNITIES.getByName('recovery:invalid:' + guild);
    expect(
      await copy.operationalRestore(guild, { ...backup, communityId: '706320010368516199' } as any),
    ).toMatchObject({ restored: false, error: expect.stringContaining('identity') });
    expect(
      await copy.operationalRestore(guild, {
        ...backup,
        tables: { ...backup.tables, 'games; DELETE FROM wallets': [] },
      } as any),
    ).toMatchObject({ restored: false, error: expect.stringContaining('Unsupported') });
    await runInDurableObject(copy, (_i, state) =>
      expect(state.storage.sql.exec('SELECT * FROM community').toArray()).toEqual([]),
    );
    const partial = structuredClone(backup);
    delete partial.tables.bets;
    expect(await copy.operationalRestore(guild, partial as any)).toMatchObject({
      restored: false,
      error: expect.stringContaining('Incomplete backup manifest'),
    });
  });
  it('exposes no public operational backup or connector write access', async () => {
    for (const path of [`/ops/communities/${guild}/backup`, `/ops/communities/${guild}/inspect`])
      expect((await exports.default.fetch('https://setup.example' + path)).status).toBe(403);
    expect(
      (
        await exports.default.fetch(
          new Request('https://setup.example/connectors/game-ended', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
          }),
        )
      ).status,
    ).toBe(403);
  });
  it('deduplicates trusted game-end prompts and never converts detection into a result', async () => {
    const s = settings();
    s.queues[0].checkIn.enabled = false;
    const m = await fill(s);
    expect(
      await stub().connectorEnded(guild, m.queue.channelId, m.id, 'test-end', m.version),
    ).toMatchObject({ accepted: true });
    expect(
      await stub().connectorEnded(guild, m.queue.channelId, m.id, 'test-end-again', m.version),
    ).toMatchObject({ accepted: false });
    expect((await current()).state).toBe('playing');
    expect((await stub().records(guild)).changes).toEqual([]);
    await act('recover_abort', 99, { matchId: m.id, admin: true });
    expect(
      await stub().connectorEnded(guild, m.queue.channelId, m.id, 'late-end', m.version),
    ).toMatchObject({ accepted: false });
  });
});
