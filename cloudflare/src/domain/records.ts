import { z } from 'zod';
import { snowflake, type Settings } from './config';
import type { Player } from './rating';

export const defaultRecords = {
  visibility: 'members' as const,
  minimumMatches: 1,
  recentDays: 30,
  ranks: [0, 500, 1000, 1200, 1400, 1600, 1800, 2000, 2500].map((threshold, i) => ({
    threshold,
    label: ['F', 'D', 'C', 'B', 'A', '★', '★★', '★★★', '$'][i],
    roleId: null as string | null,
  })),
  decorateNickname: false,
  inactivity: { enabled: false, weeklyRating: 10, weeklyDeviation: 15 },
};
export const recordPolicy = (settings: Settings, game: string) =>
  settings.games.find((g) => g.id === game)?.records ?? defaultRecords;
export const rankLabel = (settings: Settings, game: string, rating: number) =>
  recordPolicy(settings, game)
    .ranks.slice()
    .sort((a, b) => b.threshold - a.threshold)
    .find((r) => r.threshold <= rating) ?? null;
export const profileSchema = z
  .object({
    id: snowflake,
    name: z.string().min(1).max(80),
    rating: z.number().int().min(0).max(10000),
    deviation: z.number().int().min(0).max(3000),
    wins: z.number().int().min(0).max(1000000),
    losses: z.number().int().min(0).max(1000000),
    draws: z.number().int().min(0).max(1000000),
    streak: z.number().int().min(-1000000).max(1000000),
  })
  .strict();
const time = z.number().int().min(946684800000).max(4102444800000);
export const importSchema = z
  .object({
    version: z.literal(1),
    source: z.enum(['manual', 'csv', 'pubobot', 'nammapubobot']),
    sourceId: z.string().trim().min(1).max(100),
    mode: z.enum(['seed', 'history']),
    mappings: z
      .array(
        z
          .object({
            ladder: z.string().min(1).max(100),
            gameId: z.uuid(),
            engine: z.enum(['aoe2', 'flat', 'glicko2', 'trueskill']).optional(),
            queues: z.record(z.string().max(100), z.uuid()),
          })
          .strict(),
      )
      .min(1)
      .max(20),
    players: z
      .array(
        z
          .object({
            ladder: z.string().min(1).max(100),
            player: profileSchema,
            hidden: z.boolean().optional(),
            lastRankedAt: time.nullable().optional(),
          })
          .strict(),
      )
      .max(500),
    matches: z
      .array(
        z
          .object({
            sourceId: z.string().min(1).max(100),
            ladder: z.string().max(100),
            queue: z.string().max(100),
            at: time,
            endedAt: time,
            result: z.enum(['alpha', 'beta', 'draw', 'abort']),
            teams: z.tuple([z.array(snowflake).min(1).max(8), z.array(snowflake).min(1).max(8)]),
          })
          .strict(),
      )
      .max(500),
    changes: z
      .array(
        z
          .object({
            sourceId: z.string().min(1).max(100),
            ladder: z.string().max(100),
            userId: snowflake,
            matchId: z.string().max(100).nullable(),
            at: time,
            before: z.number().int().min(0).max(10000),
            after: z.number().int().min(0).max(10000),
            deviationBefore: z.number().int().min(0).max(3000),
            deviationAfter: z.number().int().min(0).max(3000),
            reason: z.string().max(200),
          })
          .strict(),
      )
      .max(8000),
    unsupported: z.array(z.string().max(100)).max(30),
  })
  .strict();
export type ImportBundle = z.infer<typeof importSchema>;
export const recordActionSchema = z
  .object({
    id: z.union([z.uuid(), snowflake]),
    kind: z.enum(['hide', 'unhide', 'link', 'unlink', 'reassign', 'sync']),
    gameId: z.uuid(),
    userId: snowflake,
    provider: z
      .string()
      .trim()
      .regex(/^[a-z0-9_-]{1,40}$/)
      .optional(),
    externalId: z.string().trim().min(1).max(100).optional(),
    url: z
      .url()
      .refine((s) => s.startsWith('https://'), 'Use an HTTPS profile URL.')
      .max(400)
      .optional(),
    reason: z.string().trim().min(1).max(200).optional(),
  })
  .strict();
export type RecordAction = z.infer<typeof recordActionSchema>;
export interface RatingHistory {
  id: string;
  before: number;
  after: number;
  changed_at: number;
  match_id: string | null;
  reason: string | null;
  source: string | null;
  deviation_before: number | null;
  deviation_after: number | null;
}
export interface ProfileReference {
  provider: string;
  external_id: string;
  url: string;
  provenance: string;
  edition?: string;
  namespace?: string;
  trust?: 'declared' | 'organizer_assigned' | 'ownership_verified' | 'suggested';
  verifiedBy?: string;
  verifiedAt?: number;
  platformId?: string;
}
export interface PlayerStatistics extends Player {
  /** False for linked accounts that have never entered this game's rating system. */
  rated?: boolean;
  hidden: boolean;
  lastRankedAt: number | null;
  source: string;
  rank: string | null;
  peak: number;
  matchCount: number;
  winRate: number | null;
  eligible: boolean;
  history: RatingHistory[];
  profiles: ProfileReference[];
  civilizations: {
    selection: string;
    games: number;
    wins: number;
    losses: number;
    draws: number;
    inferred: number;
  }[];
  recent: {
    id: string;
    at: number;
    state: string;
    result: string | null;
    queue: string;
    note: string | null;
    teams: { id: string | null; name: string }[][];
  }[];
  relationships: {
    userId: string;
    name: string;
    teammateMatches: number;
    opponentMatches: number;
    winsTogether: number;
    winsAgainst: number;
  }[];
}
export interface Statistics {
  gameId: string;
  gameName: string;
  visibility: 'public' | 'members' | 'admins';
  policy: { minimumMatches: number; recentDays: number };
  page: number;
  total: number;
  pages: number;
  leaderboard: (Player & { lastRankedAt: number | null; rank: string | null })[];
  player: PlayerStatistics | null;
}
export const playerSummary = (p: Player & { rated?: boolean }) =>
  `${p.name}: ${p.rated === false ? 'Unrated' : `${p.rating} ±${p.deviation}`} · ${p.wins}W/${p.losses}L/${p.draws}D`;
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object')
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`)
      .join(',')}}`;
  return JSON.stringify(value);
}
export async function digest(value: unknown) {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(value))),
    ),
  )
    .map((n) => n.toString(16).padStart(2, '0'))
    .join('');
}
