import type { Match } from './play';
// Data is an internal, versioned snapshot of the legacy candidate vocabulary.
export interface StoryData {
  ids: string[];
  n: number;
  team_idx: number;
  winner: string;
  loser: string;
  k: number;
  series: number;
  sweep: boolean;
  wins: number;
  games: number;
  one_way: boolean;
  won: boolean;
  p: string;
  q: string;
  wr: number;
  base: number;
  kind: string;
  each: number;
}
export interface StoryCandidate {
  type: string;
  score: number;
  players: string[];
  teams: number[];
  data: StoryData;
}
export type StoryContext = {
  version: 1;
  rosters: string[][];
  candidates: StoryCandidate[];
  seed: string;
};
const pairs = (ids: string[]) => ids.flatMap((a, i) => ids.slice(i + 1).map((b) => [a, b]));
const trios = (ids: string[]) =>
  [...ids]
    .sort()
    .flatMap((a, i, all) =>
      all.slice(i + 1).flatMap((b, j) => all.slice(i + j + 2).map((c) => [a, b, c])),
    );
function trailing<T>(xs: (T | null)[]): [number, T | null] {
  const last = xs.at(-1);
  if (last == null) return [0, null];
  let k = 0;
  for (let i = xs.length - 1; i >= 0 && xs[i] === last; i--) k++;
  return [k, last];
}
export function storyCandidates(m: Match, history: Match[]): StoryCandidate[] {
  const h = history
    .filter(
      (h) =>
        h.id !== m.id &&
        h.state === 'finished' &&
        h.queue.channelId === m.queue.channelId &&
        h.createdAt < m.createdAt &&
        (h.closedAt ?? h.createdAt) >= m.createdAt - 90 * 86400000,
    )
    .sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id));
  const sides = (h: Match, id: string) => h.teams.findIndex((t) => t.some((p) => p.id === id));
  const outcome = (h: Match, id: string) =>
    h.result === 'draw' ? null : sides(h, id) === (h.result === 'alpha' ? 0 : 1);
  const group = (ids: string[], draws = true) =>
    h
      .filter((x) => sides(x, ids[0]) >= 0 && ids.every((id) => sides(x, id) === sides(x, ids[0])))
      .map((x) => outcome(x, ids[0]))
      .filter((x) => draws || x !== null);
  const opposition = (a: string, b: string) =>
    h
      .filter((x) => sides(x, a) >= 0 && sides(x, b) >= 0 && sides(x, a) !== sides(x, b))
      .map((x) => (x.result === 'draw' ? null : outcome(x, a) ? a : b));
  const c: StoryCandidate[] = [];
  const add = (
    type: string,
    score: number,
    players: string[],
    teams: number[],
    data: Partial<StoryData>,
  ) =>
    c.push({
      type,
      score,
      players,
      teams,
      data: {
        ids: [],
        n: 0,
        team_idx: 0,
        winner: '',
        loser: '',
        k: 0,
        series: 0,
        sweep: false,
        wins: 0,
        games: 0,
        one_way: false,
        won: false,
        p: '',
        q: '',
        wr: 0,
        base: 0,
        kind: '',
        each: 0,
        ...data,
      },
    });
  for (const [side, team] of m.teams.entries()) {
    const ids = team.map((p) => p.id);
    if (ids.length >= 3) {
      const s = group(ids, false),
        wins = s.filter(Boolean).length,
        games = s.length;
      if (games >= 2)
        add(
          'lineup',
          9 * (10 + games) * (wins === 0 || wins === games ? 1.25 : 1),
          [...ids].sort(),
          [side],
          {
            ids: [...ids].sort(),
            wins,
            games,
            one_way: wins === 0 || wins === games,
            won: wins * 2 > games,
            team_idx: side,
          },
        );
    }
    for (const pair of pairs(ids)) {
      const s = group(pair),
        dec = s.filter((x) => x !== null),
        wins = dec.filter(Boolean).length;
      if (dec.length >= 5 && (wins === 0 || wins === dec.length))
        add('perfect', 6 * dec.length * (wins ? 1 : 1.1), pair, [side], {
          ids: pair,
          n: dec.length,
          won: !!wins,
          team_idx: side,
        });
      const [k, won] = trailing(s);
      if (k >= 4 && s.length >= 6)
        add(
          'mate',
          2.6 * (k - 2) * (1 + 0.4 * Math.log10(s.length / 6)) * (won ? 1 : 1.1),
          pair,
          [side],
          { ids: pair, k, series: s.length, won: !!won, team_idx: side },
        );
    }
    for (const trio of trios(ids)) {
      const s = group(trio, false),
        wins = s.filter(Boolean).length,
        games = s.length,
        share = Math.max(wins, games - wins) / games,
        won = wins * 2 > games;
      if (games >= 5 && share >= 0.75)
        add('trio', 7 * games * share * (won ? 1 : 1.1), trio, [side], {
          ids: trio,
          wins,
          games,
          won: !!won,
          team_idx: side,
        });
    }
    for (const p of ids) {
      const all = h.filter((x) => sides(x, p) >= 0).map((x) => outcome(x, p)),
        dec = all.filter((x) => x !== null),
        base = dec.filter(Boolean).length / dec.length;
      const [k, won] = trailing(all);
      if (k >= 5) add('form', 2.2 * (k - 3) * (won ? 1 : 1.1), [p], [side], { p, k, won: !!won });
      if (dec.length < 10) continue;
      const mates = ids
        .filter((q) => q !== p)
        .map((q) => {
          const s = group([p, q], false);
          return { q, games: s.length, wr: s.filter(Boolean).length / s.length };
        })
        .filter((x) => x.games >= 6);
      for (const kind of ['best', 'worst']) {
        const candidates = mates
            .filter((x) => (kind === 'best' ? x.wr - base >= 0.15 : base - x.wr >= 0.15))
            .sort((a, b) => (kind === 'best' ? b.wr - a.wr : a.wr - b.wr)),
          pick = candidates[0];
        if (pick)
          add(
            'mate_wr',
            4 *
              Math.abs(pick.wr - base) *
              Math.sqrt(Math.min(pick.games, 25)) *
              (kind === 'worst' ? 1.15 : 1) *
              ([0, 1].includes(pick.wr) ? 1.25 : 1),
            [p, pick.q],
            [side],
            { p, ...pick, base, kind },
          );
      }
    }
  }
  for (const a of m.teams[0].map((p) => p.id))
    for (const b of m.teams[1].map((p) => p.id)) {
      const s = opposition(a, b),
        [k, winner] = trailing(s),
        dec = s.filter((x) => x !== null),
        sweep = k === dec.length;
      if (k >= 4 && s.length >= 4)
        add('h2h', 3 * (k - 2) + (sweep ? 2 : 0), [a, b], [0, 1], {
          winner: winner!,
          loser: winner === a ? b : a,
          k,
          series: s.length,
          sweep,
        });
      const recent = dec.slice(-8),
        each = recent.filter((x) => x === a).length;
      if (recent.length >= 6 && each * 2 === recent.length)
        add('deadlock', 2.3 + 0.4 * recent.length, [a, b], [0, 1], {
          ids: [a, b],
          each,
          n: recent.length,
        });
    }
  return c;
}
export function seededRandom(seed: string) {
  let h = 2166136261;
  for (const c of seed) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return () => {
    h += 0x6d2b79f5;
    let t = Math.imul(h ^ (h >>> 15), h | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export function selectStories(candidates: StoryCandidate[], random = Math.random, limit = 4) {
  const ordered = candidates
      .map((c) => ({ c, r: random() }))
      .sort((a, b) => b.c.score - a.c.score || a.r - b.r)
      .map((x) => x.c),
    chosen: StoryCandidate[] = [];
  const hard: Record<string, number> = { lineup: 1, trio: 1, deadlock: 1 };
  const overlaps = (c: StoryCandidate) =>
    chosen.some(
      (x) =>
        x.players.every((p) => c.players.includes(p)) ||
        c.players.every((p) => x.players.includes(p)),
    );
  const eligible = (c: StoryCandidate, relax = false) =>
    chosen.filter((x) => x.type === c.type).length < (hard[c.type] ?? (relax ? Infinity : 2)) &&
    !c.players.some((p) => chosen.filter((x) => x.players.includes(p)).length >= 2) &&
    !overlaps(c);
  for (const c of ordered) if (chosen.length < limit && eligible(c)) chosen.push(c);
  const missing = [0, 1].find((t) => !chosen.some((c) => c.teams.includes(t)));
  if (chosen.length && missing !== undefined)
    for (const c of ordered)
      if (c.teams.includes(missing) && eligible(c)) {
        if (chosen.length === limit) {
          const removable = chosen
            .filter((x) => chosen.some((y) => y !== x && y.teams.includes(1 - missing)))
            .sort((a, b) => a.score - b.score)[0];
          if (!removable) break;
          chosen.splice(chosen.indexOf(removable), 1);
        }
        chosen.push(c);
        break;
      }
  for (const c of ordered)
    if (chosen.length < limit && !chosen.includes(c) && eligible(c, true)) chosen.push(c);
  return chosen.sort((a, b) => b.score - a.score).slice(0, limit);
}
