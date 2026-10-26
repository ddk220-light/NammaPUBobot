import { z } from 'zod';
import { snowflake, type Queue } from './config';
import { defaultRatingSettings, type Player } from './rating';
import type { MapRound } from './maps';
import { PlayError } from './errors';
export { PlayError } from './errors';

export interface Match {
  id: string;
  queue: Queue;
  gameName: string;
  roster: Player[];
  teams: [Player[], Player[]];
  captains: [string, string];
  ready: string[];
  state: 'check_in' | 'playing' | 'finished' | 'aborted' | 'check_in_failed' | 'undone';
  result: 'alpha' | 'beta' | 'draw' | null;
  agreements: Record<string, 'draw' | 'abort'>;
  createdAt: number;
  deadline: number | null;
  closedAt: number | null;
  version: number;
  changes: { userId: string; before: number; after: number }[];
  subRequests?: string[];
  replacementRounds?: number;
  playingAt?: number;
  generation?: number;
  note?: string;
  ratedPlayers?: Player[];
  behaviorVersion?: number;
  expiresAt?: number;
  proposal?: { id: string; requester: string; kind: 'draw' | 'abort'; opposing: string };
  storiesBefore?: string[];
  storiesAfter?: string[];
  storyFacts?: {
    kind: 'rivalry' | 'duo' | 'lineup';
    players: string[];
    side: 0 | 1;
    wins: number;
    games: number;
  }[];
  mapRound?: MapRound;
}
export const personSchema = z
  .object({
    id: snowflake,
    name: z.string().min(1).max(80),
    roles: z.array(snowflake).max(250).optional(),
  })
  .strict();
export const actionSchema = z
  .object({
    id: z.union([snowflake, z.uuid()]),
    actor: personSchema,
    channelId: snowflake,
    admin: z.boolean(),
    kind: z.enum([
      'add',
      'remove',
      'start',
      'queue',
      'teams',
      'rank',
      'seed',
      'ready',
      'loss',
      'draw',
      'abort',
      'recover_abort',
      'subme',
      'subfor',
      'subauto',
      'force_sub',
      'put',
      'back',
      'clear',
      'split',
      'admin_report',
      'manual_create',
      'undo',
      'recover',
      'noadd',
      'forgive',
      'bet',
      'bet_cancel',
      'bet_close',
      'gold',
      'map_vote',
      'map_pick',
      'map_redo',
    ]),
    queue: z.string().max(100).optional(),
    target: personSchema.optional(),
    replacement: personSchema.optional(),
    teams: z
      .tuple([z.array(personSchema).min(1).max(8), z.array(personSchema).min(1).max(8)])
      .optional(),
    result: z.enum(['alpha', 'beta', 'draw', 'abort']).optional(),
    side: z.enum(['alpha', 'beta']).optional(),
    groupSize: z.number().int().min(2).max(16).optional(),
    sortByRating: z.boolean().optional(),
    seconds: z.number().int().min(1).max(1209600).optional(),
    reason: z.string().trim().max(200).optional(),
    proposalId: z.uuid().optional(),
    stake: z.number().int().min(10).max(9007199254740991).optional(),
    bookId: z.uuid().optional(),
    roundId: z.uuid().optional(),
    mapChoices: z.array(z.string().max(60)).max(25).optional(),
    matchId: z.uuid().optional(),
    version: z.number().int().nonnegative().optional(),
    skipCheckIn: z.boolean().optional(),
    subRequest: z.boolean().optional(),
    rating: z.number().int().min(0).max(10000).optional(),
    deviation: z.number().int().min(1).max(3000).optional(),
  })
  .strict();
export type Action = z.infer<typeof actionSchema>;
export interface Reply {
  content: string;
  ok?: boolean;
  link?: { label: string; url: string };
}
export const policy = (queue: Queue) => queue.ratingSettings ?? defaultRatingSettings;
export function playable(queue: Queue) {
  if (queue.ratingEngine === 'trueskill' && policy(queue).deviation < 2)
    throw new PlayError('TrueSkill needs an initial deviation of at least 2.');
  if (queue.size > 16)
    throw new PlayError(
      'Supports up to 16 players per match. Reduce the queue size before starting.',
    );
  if (queue.checkIn.enabled && queue.checkIn.seconds > 3600)
    throw new PlayError('Set check-in to 3600 seconds or less before starting.');
}
function combinations<T>(items: T[], count: number): T[][] {
  const result: T[][] = [];
  const visit = (from: number, chosen: T[]) => {
    if (chosen.length === count) {
      result.push(chosen.slice());
      return;
    }
    for (let i = from; i <= items.length - count + chosen.length; i++) {
      chosen.push(items[i]);
      visit(i + 1, chosen);
      chosen.pop();
    }
  };
  visit(0, []);
  return result;
}
export function balance(players: Player[], method: Queue['teamMethod']): [Player[], Player[]] {
  if (players.length < 2 || players.length > 16 || players.length % 2)
    throw new PlayError('Select an even roster of 2–16 players.');
  const half = players.length / 2,
    total = players.reduce((s, p) => s + p.rating, 0);
  const sum = (t: Player[]) => t.reduce((s, p) => s + p.rating, 0);
  const diff = (t: Player[]) => Math.abs(2 * sum(t) - total);
  if (method === 'random') {
    const shuffled = players.slice();
    for (let i = shuffled.length - 1; i > 0; i--) {
      // Rejection sampling avoids modulo bias for a genuinely random allocation.
      const bound = i + 1,
        ceiling = Math.floor(4294967296 / bound) * bound;
      let value: number;
      do {
        value = crypto.getRandomValues(new Uint32Array(1))[0];
      } while (value >= ceiling);
      const j = value % bound;
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    const sort = (t: Player[]) => t.sort((a, b) => b.rating - a.rating);
    return [sort(shuffled.slice(0, half)), sort(shuffled.slice(half))];
  }
  const regular = combinations(players, half).reduce((best, t) =>
    diff(t) < diff(best) ? t : best,
  );
  let alpha = regular;
  if (method === 'captain') {
    const sorted = players.slice().sort((a, b) => b.rating - a.rating);
    const [strong, weak, ...rest] = sorted;
    const candidates = combinations(rest, half - 1).map((t) => [strong, ...t]);
    const balanced = candidates.reduce((best, t) => (diff(t) < diff(best) ? t : best));
    const favorScore = (t: Player[]) => {
      const d = total - 2 * sum(t);
      return d >= 0 ? d : Math.abs(d) + 1e6;
    };
    const favored = candidates.reduce((best, t) => (favorScore(t) < favorScore(best) ? t : best));
    alpha =
      diff(favored) - diff(regular) <= 50
        ? favored
        : diff(balanced) - diff(regular) <= 50
          ? balanced
          : regular;
    void weak;
  }
  const sort = (t: Player[]) => t.slice().sort((a, b) => b.rating - a.rating);
  const ids = new Set(alpha.map((p) => p.id));
  return [sort(alpha), sort(players.filter((p) => !ids.has(p.id)))];
}
