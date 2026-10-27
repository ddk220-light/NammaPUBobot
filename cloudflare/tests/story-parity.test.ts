import { describe, it, expect } from 'vitest';
import fixtures from './fixtures/story-parity.json';
import { storyCandidates, selectStories, seededRandom } from '../src/domain/story-candidates';
import { teasePhrase, payoffPhrase } from '../src/domain/story-phrases';
import { storyFrame } from '../src/domain/story-framing';
import { beforeStories, afterStories } from '../src/domain/stories';
import type { Match } from '../src/domain/play';
import { settings } from './harness';
const p = (id: string) => ({
  id,
  name: id,
  rating: 1000,
  deviation: 100,
  wins: 0,
  losses: 0,
  draws: 0,
  streak: 0,
});
function match(teams: string[][]): Match {
  const ts = teams.map((t) => t.map(p)) as Match['teams'];
  return {
    id: 'current',
    queue: settings().queues[0],
    gameName: 'AoE2',
    roster: ts.flat(),
    teams: ts,
    captains: [ts[0][0].id, ts[1][0].id],
    ready: [],
    state: 'playing',
    result: null,
    agreements: {},
    createdAt: Date.now(),
    deadline: null,
    closedAt: null,
    version: 1,
    changes: [],
  };
}
describe('Railway golden banter parity', () => {
  it.each(fixtures)(
    '$name reproduces candidate thresholds, scores, every tease and both payoffs',
    (f) => {
      const m = match(f.teams),
        history = f.history.map((h, i) => ({
          ...match(h.teams),
          id: `history-${i}`,
          createdAt: m.createdAt - (f.history.length - i) * 1000,
          closedAt: m.createdAt - (f.history.length - i) * 1000 + 1,
          state: 'finished' as const,
          result:
            h.winner === null
              ? ('draw' as const)
              : h.winner === 0
                ? ('alpha' as const)
                : ('beta' as const),
        }));
      const actual = storyCandidates(m, history);
      expect(actual).toHaveLength(f.expected.length);
      const rosters = m.teams.map((t) => t.map((p) => p.id)),
        nick = Object.fromEntries(m.roster.map((p) => [p.id, `<@${p.id}>`])),
        first = (xs: string[]) => xs[0];
      for (const e of f.expected) {
        const c = actual.find(
          (c) =>
            c.type === e.type &&
            JSON.stringify([...c.players].sort()) === JSON.stringify(e.players) &&
            Object.entries(e.data).every(
              ([k, v]) => JSON.stringify(c.data[k as keyof typeof c.data]) === JSON.stringify(v),
            ),
        );
        expect(c, JSON.stringify(e)).toBeDefined();
        expect(c!.score).toBeCloseTo(e.score, 10);
        expect(teasePhrase(c!, nick, storyFrame(c!, rosters, nick, first), first)).toBe(e.tease);
        expect(
          payoffPhrase(c!, nick, storyFrame(c!, rosters, nick, first, true), first, true),
        ).toBe(e.won);
        expect(
          payoffPhrase(c!, nick, storyFrame(c!, rosters, nick, first, false), first, false),
        ).toBe(e.lost);
      }
      const selected = selectStories(actual, seededRandom('fixture'));
      expect(selected.length).toBeLessThanOrEqual(4);
      for (const id of m.roster.map((p) => p.id))
        expect(selected.filter((c) => c.players.includes(id)).length).toBeLessThanOrEqual(2);
      for (const type of ['lineup', 'trio', 'deadlock'])
        expect(selected.filter((c) => c.type === type).length).toBeLessThanOrEqual(1);
      beforeStories(m, history);
      m.state = 'finished';
      m.result = 'alpha';
      const payoff = afterStories(m, m.roster);
      expect(afterStories(JSON.parse(JSON.stringify(m)), m.roster)).toEqual(payoff);
      m.teams[0][0] = { ...m.teams[0][0], id: 'substitute' };
      expect(afterStories(m, m.roster)).toEqual([]);
    },
  );
  it('covers all eight types and excludes stale, other-channel and undone history', () => {
    expect(new Set(fixtures.flatMap((f) => f.expected.map((c) => c.type)))).toEqual(
      new Set(['lineup', 'perfect', 'mate_wr', 'h2h', 'mate', 'trio', 'deadlock', 'form']),
    );
    const m = match(fixtures[0].teams),
      h = Array.from({ length: 6 }, (_, i) => ({
        ...structuredClone(m),
        id: `h${i}`,
        state: 'finished' as const,
        result: 'alpha' as const,
        createdAt: m.createdAt - 10000,
        closedAt: m.createdAt - 1,
      }));
    expect(storyCandidates(m, h).length).toBeGreaterThan(0);
    expect(
      storyCandidates(
        m,
        h.map((x) => ({ ...x, closedAt: m.createdAt - 91 * 86400000 })),
      ),
    ).toEqual([]);
    expect(
      storyCandidates(
        m,
        h.map((x) => ({ ...x, queue: { ...x.queue, channelId: 'other' } })),
      ),
    ).toEqual([]);
    expect(
      storyCandidates(
        m,
        h.map((x) => ({ ...x, state: 'undone' })),
      ),
    ).toEqual([]);
  });
});
