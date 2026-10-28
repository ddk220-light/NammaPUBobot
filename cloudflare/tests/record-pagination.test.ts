import { runInDurableObject } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { harness, stub, setup, settings, person, guild, game } from './harness';
harness();
describe('organizer player pagination', () => {
  it('reaches every player across the fifty-record boundary with stable tie ordering', async () => {
    await setup();
    await runInDurableObject(stub(), (_i, state) => {
      for (let n = 0; n < 55; n++)
        state.storage.sql.exec(
          'INSERT INTO profiles VALUES(?,?,?)',
          game,
          person(n).id,
          JSON.stringify({
            ...person(n),
            rating: 1000,
            deviation: 200,
            wins: 0,
            losses: 0,
            draws: 0,
            streak: 0,
          }),
        );
    });
    const first = await stub().records(guild),
      second = await stub().records(guild, undefined, 1);
    expect(first.players).toHaveLength(50);
    expect(second.players).toHaveLength(5);
    expect(first.playersTotal).toBe(55);
    expect(second.playersTotal).toBe(55);
    expect(first.playersPage).toBe(0);
    expect(second.playersPage).toBe(1);
    expect(new Set([...first.players, ...second.players].map((p) => p.id)).size).toBe(55);
    expect((await stub().records(guild, undefined, 2)).players).toHaveLength(0);
    // Engine eligibility cannot be inferred from the currently loaded player page.
    expect((await stub().records(guild, undefined, 2)).gameState).toContainEqual({
      id: game,
      ratingEngineLocked: true,
      ratingEngine: 'aoe2',
    });
  });
  it('returns the persisted engine after removing the last queue and validates a replacement', async () => {
    const config = settings();
    config.queues[0].ratingEngine = 'glicko2';
    await setup(config);
    await runInDurableObject(stub(), (_i, state) => {
      state.storage.sql.exec('INSERT INTO rating_engines VALUES(?,?)', game, 'glicko2');
      state.storage.sql.exec(
        'INSERT INTO profiles VALUES(?,?,?)',
        game,
        person(0).id,
        JSON.stringify({
          ...person(0),
          rating: 1000,
          deviation: 200,
          wins: 0,
          losses: 0,
          draws: 0,
          streak: 0,
        }),
      );
    });
    const removed = { ...config, queues: [] };
    expect((await stub().save(guild, 1, removed, person(99).id, 'save')).ok).toBe(true);
    expect((await stub().records(guild)).gameState).toEqual([
      { id: game, ratingEngineLocked: true, ratingEngine: 'glicko2' },
    ]);
    config.queues[0].ratingEngine = 'aoe2';
    expect(await stub().save(guild, 2, config, person(99).id, 'save')).toMatchObject({
      ok: false,
      issues: [{ path: ['settings', 'queues', 0, 'ratingEngine'] }],
    });
    config.queues[0].ratingEngine = 'glicko2';
    expect((await stub().save(guild, 2, config, person(99).id, 'save')).ok).toBe(true);
  });
  it('counts and pages only the selected game', async () => {
    const config = settings(),
      other = crypto.randomUUID();
    config.games.push({ id: other, name: 'Other game', catalogId: null });
    await setup(config);
    await runInDurableObject(stub(), (_i, state) => {
      for (const [gameId, total] of [
        [game, 51],
        [other, 8],
      ] as const)
        for (let n = 0; n < total; n++)
          state.storage.sql.exec(
            'INSERT INTO profiles VALUES(?,?,?)',
            gameId,
            person(n).id,
            JSON.stringify({
              ...person(n),
              rating: 1000,
              deviation: 200,
              wins: 0,
              losses: 0,
              draws: 0,
              streak: 0,
            }),
          );
    });
    expect((await stub().records(guild, game, 1)).players).toHaveLength(1);
    const otherRecords = await stub().records(guild, other);
    expect(otherRecords.playersTotal).toBe(8);
    expect(otherRecords.players.every((p) => p.gameId === other)).toBe(true);
  });
});
