import { z } from 'zod';
import { PlayError } from './errors';
export const aoe2Maps = [
  'Arabia',
  'Arena',
  'Black Forest',
  'Nomad',
  'Acropolis',
  'Land Nomad',
  'Four Lakes',
  'Hideout',
  'Gold Rush',
  'Golden Pit',
  'Islands',
  'Team Islands',
  'Oasis',
  'MegaRandom',
  'African Clearing',
];
export const mapSettingsSchema = z
  .object({
    mode: z.enum(['direct', 'random', 'vote']),
    pool: z.array(z.string().trim().min(1).max(60)).min(1).max(100),
    shortlist: z.number().int().min(1).max(25),
    count: z.number().int().min(1).max(5),
    voting: z.enum(['plurality', 'approval']),
    tie: z.enum(['random', 'alphabetical']),
    noVotes: z.enum(['random', 'cancel']),
    seconds: z.number().int().min(10).max(3600),
    countsAsCheckIn: z.boolean(),
    recentAvoid: z.number().int().min(0).max(20),
  })
  .strict()
  .superRefine((s, c) => {
    if (new Set(s.pool.map((v) => v.toLowerCase())).size !== s.pool.length)
      c.addIssue({ code: 'custom', message: 'Map names must be unique.', path: ['pool'] });
    if (s.count > s.pool.length || s.count > s.shortlist)
      c.addIssue({
        code: 'custom',
        message: 'Provide enough pool and shortlist maps for the selected count.',
        path: ['count'],
      });
    if (s.countsAsCheckIn && s.mode !== 'vote')
      c.addIssue({
        code: 'custom',
        message: 'Only participant voting can count as check-in.',
        path: ['countsAsCheckIn'],
      });
  });
export type MapSettings = z.infer<typeof mapSettingsSchema>;
export const defaultMaps = (pool = aoe2Maps): MapSettings => ({
  mode: 'vote',
  pool: [...pool],
  shortlist: Math.min(5, pool.length),
  count: 1,
  voting: 'plurality',
  tie: 'random',
  noVotes: 'random',
  seconds: 60,
  countsAsCheckIn: false,
  recentAvoid: 0,
});
export type MapRound = {
  id: string;
  settings: MapSettings;
  choices: string[];
  ballots: Record<string, string[]>;
  selected: string[];
  state: 'open' | 'resolved';
  closesAt: number;
  note?: string;
};
function shuffle<T>(items: T[]) {
  const a = items.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const ceiling = Math.floor(4294967296 / (i + 1)) * (i + 1);
    let value: number;
    do {
      value = crypto.getRandomValues(new Uint32Array(1))[0];
    } while (value >= ceiling);
    const j = value % (i + 1);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}
export function createMapRound(settings: MapSettings, recent: string[], now: number): MapRound {
  const parsed = mapSettingsSchema.parse(settings),
    avoided = new Set(recent.map((v) => v.toLowerCase()));
  let pool = parsed.pool.filter((v) => !avoided.has(v.toLowerCase()));
  let note: string | undefined;
  if (pool.length < parsed.count) {
    pool = parsed.pool;
    note = 'Recent-map avoidance reset because too few maps remain.';
  }
  const choices = shuffle(pool).slice(
    0,
    Math.max(parsed.count, Math.min(parsed.shortlist, pool.length)),
  );
  const round: MapRound = {
    id: crypto.randomUUID(),
    settings: parsed,
    choices,
    ballots: {},
    selected: [],
    state: 'open',
    closesAt: now + parsed.seconds * 1000,
    note,
  };
  if (parsed.mode === 'random') {
    round.selected = choices.slice(0, parsed.count);
    round.state = 'resolved';
  }
  return round;
}
export function voteMap(round: MapRound, user: string, choices: string[]) {
  if (round.state !== 'open' || round.settings.mode !== 'vote')
    throw new PlayError('Map voting is closed.');
  if (
    !choices.length ||
    new Set(choices).size !== choices.length ||
    choices.some((c) => !round.choices.includes(c)) ||
    choices.length > (round.settings.voting === 'plurality' ? 1 : round.choices.length)
  )
    throw new PlayError('Choose valid maps from this round.');
  round.ballots[user] = choices.slice();
}
export function resolveMap(round: MapRound, direct?: string[]) {
  if (round.state === 'resolved') return;
  if (direct) {
    if (
      direct.length !== round.settings.count ||
      new Set(direct).size !== direct.length ||
      direct.some((v) => !round.choices.includes(v))
    )
      throw new PlayError(`Choose ${round.settings.count} maps.`);
    round.selected = direct;
  } else {
    if (!Object.keys(round.ballots).length && round.settings.noVotes === 'cancel')
      throw new PlayError('No map votes were cast.');
    const score = (map: string) =>
      Object.values(round.ballots).filter((b) => b.includes(map)).length;
    round.selected = round.choices
      .slice()
      .sort(
        (a, b) =>
          score(b) - score(a) ||
          (round.settings.tie === 'alphabetical'
            ? a.localeCompare(b)
            : round.choices.indexOf(a) - round.choices.indexOf(b)),
      )
      .slice(0, round.settings.count);
  }
  round.state = 'resolved';
}
