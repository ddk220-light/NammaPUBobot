import { runInDurableObject, abortAllDurableObjects } from 'cloudflare:test';
import { describe, it, expect } from 'vitest';
import { harness, stub, settings, fill, current, act, person, guild } from './harness';
import { beforeStories, afterStories, streakLine } from '../src/domain/stories';
import type { Match } from '../src/domain/play';
harness();
describe('M7 game-agnostic stories', () => {
  it('does not announce one/two consecutive wins', async () => {
    const m = await fill();
    m.roster.forEach((p) => (p.streak = 2));
    expect(beforeStories(m, [])).toEqual([]);
  });
  it('shows each team leader and ties without selecting an audio winner', async () => {
    const m = await fill();
    m.teams.forEach((t) => t.forEach((p) => (p.streak = 3)));
    const lines = beforeStories(m, []);
    expect(lines).toHaveLength(1);
    expect(lines[0].match(/\(tied\)/g)).toHaveLength(2);
    expect(lines[0]).not.toContain('👑');
  });
  it.each([3, 4, 5, 6, 7, 8, 9, 10, 12])(
    'uses the preserved streak statement at %s',
    async (streak) => {
      const m = await fill(),
        p = { ...m.roster[0], streak };
      expect(streakLine(p)).toContain(`${streak} consecutive wins`);
      if (streak >= 10) expect(streakLine(p)).toContain('beyond GODLIKE');
    },
  );
  it('uses sufficient historical samples and saves rivalry payoff context', async () => {
    const m = await fill(),
      history: Array<Match> = Array.from({ length: 5 }, (_, n) => ({
        ...structuredClone(m),
        id: crypto.randomUUID(),
        state: 'finished',
        result: 'alpha',
        createdAt: m.createdAt - n - 1,
      }));
    const lines = beforeStories(m, history);
    expect(m.storyContext?.candidates.length).toBeGreaterThan(0);
    expect(
      lines.some((v) => /unbeaten|flawless|Perfect record|cursed|winless|0-5|5-0/.test(v)),
    ).toBe(true);
    m.state = 'finished';
    m.result = 'beta';
    expect(
      afterStories(m, m.roster).some((v) =>
        /drop one|run is over|CURSE BREAKS|on the board/.test(v),
      ),
    ).toBe(true);
  });
  it('stores optional stories once across restart and result retries', async () => {
    const s = settings();
    s.queues[0].modules.stories = true;
    s.queues[0].checkIn.enabled = false;
    const m = await fill(s);
    await act('loss', Number(BigInt(m.captains[1]) - 100000000000000100n), { matchId: m.id });
    const saved = await current();
    await abortAllDurableObjects();
    await act('loss', 0, { matchId: m.id });
    expect((await current()).storiesAfter).toEqual(saved.storiesAfter);
  });
});
