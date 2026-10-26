import { runInDurableObject, abortAllDurableObjects, runDurableObjectAlarm } from 'cloudflare:test';
import { describe, it, expect } from 'vitest';
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
import { EconomyStore } from '../src/storage/economy-store';
import { stakeOptions, taxAmount, WEEK, nextThursday } from '../src/domain/economy';
import { predictionPanel } from '../src/discord/prediction-panel';
harness();
const config = () => {
  const s = settings();
  s.queues[0].modules.gold = true;
  s.queues[0].modules.predictions = true;
  s.queues[0].checkIn.enabled = false;
  return s;
};
const wallet = async (n: number) =>
  (await stub().records(guild)).economy.wallets.find((w) => w.user_id === person(n).id)?.balance;
const captain = (m: Awaited<ReturnType<typeof current>>, side = 0) =>
  Number(BigInt(m.captains[side]) - 100000000000000100n);
describe('M5 gold predictions', () => {
  it('keeps prediction controls when teams or a recovered card are refreshed', async () => {
    const m = await fill(config());
    await act('teams', 0, { matchId: m.id });
    await act('recover', 99, { matchId: m.id, admin: true });
    await runInDurableObject(stub(), (_i, state) => {
      const payload = state.storage.sql
        .exec<{ payload: string }>('SELECT payload FROM outbox WHERE key=?', `match:${m.id}`)
        .toArray()[0].payload;
      expect(
        JSON.parse(payload).components.flatMap((r: any) => r.components.map((b: any) => b.label)),
      ).toContain('Place bet');
    });
  });
  it('refunds a prediction pool when check-in fails', async () => {
    const s = config();
    s.queues[0].checkIn.enabled = true;
    const m = await fill(s);
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await runInDurableObject(stub(), (_i, state) => {
      state.storage.sql.exec("UPDATE deadlines SET due=0 WHERE kind='check_in'");
      const changed = { ...m, deadline: 0 };
      state.storage.sql.exec(
        'UPDATE matches SET config=? WHERE id=?',
        JSON.stringify(changed),
        m.id,
      );
    });
    await runDurableObjectAlarm(stub());
    expect(await wallet(8)).toBe(500);
    expect((await stub().records(guild)).economy.books[0].status).toBe('refunded');
  });
  it('returns saved stake receipts after a close, and can cancel after the feature is disabled', async () => {
    const s = config(),
      m = await fill(s);
    const prediction = await stub().prediction(guild, m.queue.channelId, person(8).id, m.id);
    if (!prediction.ok) throw Error(prediction.content);
    const book = prediction.book!;
    const placed = action('bet', 8, { matchId: m.id, bookId: book.id, side: 'alpha', stake: 50 });
    const receipt = await stub().play(guild, placed);
    const saved = await stub().read(guild);
    saved.settings.queues[0].modules.predictions = false;
    await stub().save(guild, saved.revision, saved.settings, person(99).id, 'save');
    expect(
      (await stub().prediction(guild, m.queue.channelId, person(8).id, m.id, false, true)).ok,
    ).toBe(true);
    await act('bet_cancel', 8, { matchId: m.id, bookId: book.id });
    await act('bet_close', captain(m), { matchId: m.id });
    expect(await stub().predictionReceipt(guild, placed)).toEqual(receipt);
    expect(await wallet(8)).toBe(500);
  });
  it.each([
    [9, []],
    [10, [10]],
    [100, [20, 50, 100]],
    [500, [50, 125, 250]],
    [1000, [100, 250, 500]],
    [1200, [150, 375, 750]],
  ] as const)('preserves the existing balance-derived stakes for %s', (balance, options) =>
    expect(stakeOptions(balance)).toEqual(options),
  );
  it('optional gold stays off and does not create wallets', async () => {
    await fill();
    expect((await act('gold', 8)).ok).toBe(false);
    expect((await stub().records(guild)).economy.wallets).toEqual([]);
  });
  it('seeds once, adds distinct bets, and repeats an action without charging twice', async () => {
    const m = await fill(config());
    const a = action('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await stub().play(guild, a);
    await stub().play(guild, a);
    expect(await wallet(8)).toBe(450);
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    expect(await wallet(8)).toBe(400);
    expect(
      (await stub().records(guild)).economy.ledger.filter(
        (l) => l.user_id === person(8).id && l.kind === 'seed',
      ),
    ).toHaveLength(1);
  });
  it('locks sides, validates current amounts, and refunds a cancelled stake once', async () => {
    const m = await fill(config());
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    expect((await act('bet', 8, { matchId: m.id, side: 'beta', stake: 50 })).ok).toBe(false);
    expect((await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 51 })).ok).toBe(false);
    await act('bet_cancel', 8, { matchId: m.id });
    await act('bet_cancel', 8, { matchId: m.id });
    expect(await wallet(8)).toBe(500);
  });
  it('automatically selects a player side and only asks spectators to choose', async () => {
    const m = await fill(config()),
      state = await stub().prediction(guild, m.queue.channelId, person(0).id, m.id);
    expect(state.ok).toBe(true);
    if (!state.ok) throw Error();
    expect(predictionPanel(m, state.book, person(0).id, state.balance).content).toContain('Back ');
    expect(predictionPanel(m, state.book, person(8).id, 500).content).toContain('Who will win');
    const own = m.teams[0].some((p) => p.id === person(0).id) ? 'alpha' : 'beta';
    expect(
      (await act('bet', 0, { matchId: m.id, side: own === 'alpha' ? 'beta' : 'alpha', stake: 50 }))
        .ok,
    ).toBe(false);
  });
  it('settles a closed two-sided pot and survives restart without another payout', async () => {
    const m = await fill(config());
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await act('bet', 9, { matchId: m.id, side: 'beta', stake: 50 });
    await act('bet_close', captain(m), { matchId: m.id });
    await act('loss', captain(m, 1), { matchId: m.id });
    expect(await wallet(8)).toBe(550);
    expect(await wallet(9)).toBe(450);
    await abortAllDurableObjects();
    await act('loss', captain(m, 1), { matchId: m.id });
    expect(await wallet(8)).toBe(550);
    expect((await stub().records(guild)).economy.books[0]).toMatchObject({
      status: 'settled',
      result: 'alpha',
    });
  });
  it('reporting while open refunds instead of settling while ratings complete', async () => {
    const m = await fill(config());
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await act('bet', 9, { matchId: m.id, side: 'beta', stake: 50 });
    await act('loss', captain(m, 1), { matchId: m.id });
    expect(await wallet(8)).toBe(500);
    expect(await wallet(9)).toBe(500);
    expect((await stub().records(guild)).changes).toHaveLength(4);
    expect((await stub().records(guild)).economy.books[0]).toMatchObject({
      status: 'refunded',
      reason: 'reported_while_open',
    });
  });
  it('only captains/admins close and closure races cannot lose a stake', async () => {
    const m = await fill(config());
    expect((await act('bet_close', 8, { matchId: m.id })).ok).toBe(false);
    await act('bet', 9, { matchId: m.id, side: 'beta', stake: 50 });
    await Promise.all([
      act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 }),
      act('bet_close', captain(m), { matchId: m.id }),
    ]);
    await act('loss', captain(m, 1), { matchId: m.id });
    const w = await wallet(8);
    expect([500, 550]).toContain(w ?? 500);
    await runInDurableObject(stub(), (_i, s) => {
      for (const row of s.storage.sql.exec<{ game_id: string; user_id: string; balance: number }>(
        'SELECT * FROM wallets',
      )) {
        const sum = s.storage.sql
          .exec<{ total: number }>(
            'SELECT SUM(amount) AS total FROM gold_ledger WHERE game_id=? AND user_id=?',
            row.game_id,
            row.user_id,
          )
          .toArray()[0].total;
        expect(sum).toBe(row.balance);
      }
    });
  });
  it('keeps paid settlements final after winnings are spent and the result is corrected', async () => {
    const m = await fill(config());
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await act('bet', 9, { matchId: m.id, side: 'beta', stake: 50 });
    await act('bet_close', captain(m), { matchId: m.id });
    await act('loss', captain(m, 1), { matchId: m.id });
    for (let n = 4; n < 8; n++) await act('add', n);
    const next = await current();
    await act('bet', 8, { matchId: next.id, side: 'alpha', stake: 100 });
    expect(await wallet(8)).toBe(450);
    await act('undo', 99, { matchId: m.id, admin: true });
    await act('admin_report', 99, { matchId: m.id, result: 'beta', admin: true });
    expect(await wallet(8)).toBe(450);
    expect(
      (await stub().records(guild)).economy.books.find((b) => b.match_id === m.id),
    ).toMatchObject({ status: 'settled', result: 'alpha' });
  });
  it('an open roster change voids/refunds/reopens; a closed book never silently reopens', async () => {
    const m = await fill(config());
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await act('add', 4);
    await act('subauto', 0, { matchId: m.id });
    expect(await wallet(8)).toBe(500);
    const updated = await current();
    const first = (await stub().records(guild)).economy.books[0];
    expect(first.round).toBe(2);
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await act('bet', 9, { matchId: m.id, side: 'beta', stake: 50 });
    await act('bet_close', captain(updated), { matchId: m.id });
    await act('add', 5);
    await act('subauto', 1, { matchId: m.id });
    expect((await stub().records(guild)).economy.books[0].status).toBe('refunded');
    expect(await wallet(8)).toBe(500);
  });
  it('tax protects 500, grants seven-day grace, and applies once per missed eligible Thursday', async () => {
    const s = config();
    s.queues[0].economy = { taxEnabled: true, taxMinuteUtc: 0 };
    const m = await fill(s);
    await runInDurableObject(stub(), (_i, state) =>
      state.storage.transactionSync(() => {
        const e = new EconomyStore(state.storage.sql),
          cutoff = Date.now() - 1000;
        for (const [n, balance] of [
          [8, 520],
          [9, 1000],
          [10, 500],
        ]) {
          e.seed(game, person(n).id, cutoff - 2 * WEEK);
          e.change(
            game,
            person(n).id,
            balance - 500,
            'fixture',
            `fixture:${n}`,
            null,
            cutoff - 2 * WEEK,
          );
        }
        state.storage.sql.exec(
          'UPDATE books SET opened_at=?,closed_at=NULL WHERE match_id=?',
          cutoff - 100000,
          m.id,
        );
        state.storage.sql.exec(
          'UPDATE economy_clocks SET activated_at=?,due=?',
          cutoff - 2 * WEEK,
          cutoff,
        );
        e.tax(cutoff);
        e.tax(cutoff);
      }),
    );
    expect(await wallet(8)).toBe(500);
    expect(await wallet(9)).toBe(900);
    expect(await wallet(10)).toBe(500);
    expect(taxAmount(1000, Date.now(), Date.now() + 1000, false)).toBe(0);
  });
  it('a cancelled bet does not qualify for tax; a bet retained to close does', async () => {
    const m = await fill(config());
    await act('bet', 8, { matchId: m.id, side: 'alpha', stake: 50 });
    await act('bet_cancel', 8, { matchId: m.id });
    await act('bet', 9, { matchId: m.id, side: 'beta', stake: 50 });
    await act('bet_close', captain(m), { matchId: m.id });
    await runInDurableObject(stub(), (_i, s) =>
      expect(
        s.storage.sql.exec<{ user_id: string }>('SELECT user_id FROM bet_participation').toArray(),
      ).toEqual([{ user_id: person(9).id }]),
    );
  });
  it('Thursday schedule is future and deterministic at the boundary', () => {
    const date = Date.UTC(2026, 9, 15);
    expect(nextThursday(date - 1)).toBe(date);
    expect(nextThursday(date)).toBe(date + WEEK);
  });
});
