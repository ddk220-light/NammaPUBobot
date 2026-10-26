import { describe, it, expect } from 'vitest';
import { env } from 'cloudflare:workers';
import { harness, settings, person, action, game, queue } from './harness';
harness();
describe('M10 bounded local load rehearsal', () => {
  it('keeps 1000 community stores independent through concurrent queue and prediction work', async () => {
    const timings: number[] = [];
    const count = 1000,
      batch = 20;
    for (let offset = 0; offset < count; offset += batch)
      await Promise.all(
        Array.from({ length: Math.min(batch, count - offset) }, async (_, n) => {
          const guild = String(706320010369000000n + BigInt(offset + n)),
            stub = env.COMMUNITIES.getByName(guild),
            s = settings();
          s.queues[0].checkIn.enabled = false;
          s.queues[0].modules.gold = true;
          s.queues[0].modules.predictions = true;
          const start = performance.now();
          expect((await stub.save(guild, 0, s, person(99).id, 'save')).ok).toBe(true);
          await Promise.all([0, 1, 2, 3].map((n) => stub.play(guild, action('add', n))));
          const match = (await stub.records(guild)).matches[0];
          expect(match.roster).toHaveLength(4);
          await stub.play(guild, action('bet', 8, { matchId: match.id, side: 'alpha', stake: 50 }));
          const record = await stub.records(guild);
          expect(record.economy.wallets.find((w) => w.user_id === person(8).id)?.balance).toBe(450);
          expect(record.economy.books).toHaveLength(1);
          timings.push(performance.now() - start);
        }),
      );
    timings.sort((a, b) => a - b);
    console.log(
      JSON.stringify({
        rehearsal: 'local-mocked-Discord',
        communities: count,
        parallelism: batch,
        actionsPerCommunity: 5,
        journeyP50Ms: timings[Math.floor(count * 0.5)],
        journeyP95Ms: timings[Math.floor(count * 0.95)],
        disclaimer:
          'Local isolation/workload evidence; not a production latency or billing benchmark.',
      }),
    );
  }, 120000);
});
