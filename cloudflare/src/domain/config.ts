import { z } from 'zod';
import { ratingSettingsSchema } from './rating';
import { mapSettingsSchema } from './maps';

export const snowflake = z.string().regex(/^\d{17,20}$/, 'Enter a Discord ID.');
const id = z.uuid();
const name = z.string().trim().min(1).max(80);
export { gameCatalog as catalog } from '../games/catalog';
import { gameCapabilities } from '../games/catalog';

export const gameSchema = z
  .object({
    id,
    name,
    connector: z
      .object({
        enabled: z.boolean(),
        autoAttach: z.object({ lobbyName: name }).strict().optional(),
      })
      .strict()
      .optional(),
    catalogId: z.enum(['aoe2', 'aoe3', 'aoe4', 'aom', 'generals']).nullable(),
    records: z
      .object({
        visibility: z.enum(['public', 'members', 'admins']),
        minimumMatches: z.number().int().min(0).max(10000),
        recentDays: z.number().int().min(0).max(3650),
        ranks: z
          .array(
            z
              .object({
                threshold: z.number().int().min(0).max(10000),
                label: z.string().trim().min(1).max(30),
                roleId: snowflake.nullable(),
              })
              .strict(),
          )
          .max(30),
        decorateNickname: z.boolean(),
        inactivity: z
          .object({
            enabled: z.boolean(),
            weeklyRating: z.number().int().min(0).max(100),
            weeklyDeviation: z.number().int().min(0).max(500),
          })
          .strict(),
      })
      .strict()
      .optional(),
  })
  .strict();

export const modulesSchema = z
  .object({
    gold: z.boolean(),
    predictions: z.boolean(),
    quiz: z.boolean(),
    civs: z.boolean(),
    maps: z.boolean(),
    stories: z.boolean(),
  })
  .strict()
  .refine((m) => !m.predictions || m.gold, 'Predictions require gold.');
export const emptyModules = {
  gold: false,
  predictions: false,
  quiz: false,
  civs: false,
  maps: false,
  stories: false,
};

export const queueSchema = z
  .object({
    id,
    gameId: id,
    channelId: snowflake,
    name: z.string().trim().min(1).max(40),
    size: z.number().int().min(2).max(Number.MAX_SAFE_INTEGER),
    enabled: z.boolean(),
    checkIn: z
      .object({
        enabled: z.boolean(),
        seconds: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
        reuseSeconds: z.number().int().min(0).max(900).optional(),
      })
      .strict(),
    teamMethod: z.enum(['captain', 'balanced', 'random']),
    autoStart: z.boolean().optional(),
    aliases: z.array(z.string().trim().min(1).max(40)).max(10).optional(),
    isDefault: z.boolean().optional(),
    voicePreview: z.boolean().optional(),
    keepQueueVisible: z.boolean().optional(),
    economy: z
      .object({ taxEnabled: z.boolean(), taxMinuteUtc: z.number().int().min(0).max(1439) })
      .strict()
      .optional(),
    maps: mapSettingsSchema.optional(),
    rally: z
      .object({
        enabled: z.boolean(),
        channels: z.array(snowflake).max(20),
        roles: z.array(snowflake).max(20),
        cooldownSeconds: z.number().int().min(60).max(3600),
        mentionLimit: z.number().int().min(1).max(8),
      })
      .strict()
      .optional(),
    controls: z
      .object({
        waitingSeconds: z.number().int().min(0).max(1209600),
        matchSeconds: z.number().int().min(0).max(1209600),
        requiredRole: snowflake.nullable(),
        blockedRole: snowflake.nullable(),
      })
      .strict()
      .optional(),
    ratingEngine: z.enum(['flat', 'glicko2', 'trueskill', 'aoe2']),
    ratingSettings: ratingSettingsSchema.optional(),
    modules: modulesSchema,
  })
  .strict();

export const settingsSchema = z
  .object({
    games: z.array(gameSchema).max(100),
    queues: z.array(queueSchema).max(200),
  })
  .strict()
  .superRefine((s, ctx) => {
    const games = new Set<string>(),
      queues = new Set<string>(),
      labels = new Set<string>(),
      catalogs = new Set<string>(),
      defaults = new Set<string>();
    for (const g of s.games) {
      if (g.connector?.enabled && !gameCapabilities(g.catalogId)?.lobby)
        ctx.addIssue({
          code: 'custom',
          message: 'No lobby connector is available for this game; use manual play.',
        });
      const taxMinutes = new Set(
        s.queues
          .filter((q) => q.gameId === g.id && q.modules.gold && q.economy?.taxEnabled)
          .map((q) => q.economy!.taxMinuteUtc),
      );
      if (taxMinutes.size > 1)
        ctx.addIssue({
          code: 'custom',
          message: 'Queues for one game must use the same Thursday tax time.',
        });
      if (games.has(g.id)) ctx.addIssue({ code: 'custom', message: 'Duplicate game identity.' });
      if (g.catalogId && catalogs.has(g.catalogId))
        ctx.addIssue({ code: 'custom', message: 'That catalog game is already configured.' });
      games.add(g.id);
      if (
        g.records &&
        new Set(g.records.ranks.map((r) => r.threshold)).size !== g.records.ranks.length
      )
        ctx.addIssue({ code: 'custom', message: 'Rank thresholds must be unique.' });
      if (g.catalogId) catalogs.add(g.catalogId);
    }
    if (s.games.filter((g) => g.records?.decorateNickname).length > 1)
      ctx.addIssue({
        code: 'custom',
        message: 'Nickname decoration can belong to only one game in each community.',
      });
    for (const q of s.queues) {
      if (q.modules.civs && s.games.find((g) => g.id === q.gameId)?.catalogId !== 'aoe2')
        ctx.addIssue({
          code: 'custom',
          message: 'Civilization picking is available for AoE2 queues only.',
        });
      if (q.modules.maps && !q.maps && s.games.find((g) => g.id === q.gameId)?.catalogId !== 'aoe2')
        ctx.addIssue({
          code: 'custom',
          message: 'Add a map pool before enabling map selection for this game.',
        });
      if (q.isDefault && defaults.has(q.channelId))
        ctx.addIssue({ code: 'custom', message: 'Choose one default queue per channel.' });
      if (q.isDefault) defaults.add(q.channelId);
      if (q.ratingEngine === 'trueskill' && q.ratingSettings && q.ratingSettings.deviation < 2)
        ctx.addIssue({
          code: 'custom',
          message: 'TrueSkill requires initial deviation of at least 2.',
        });
      if (q.ratingSettings && q.ratingSettings.minDeviation > q.ratingSettings.deviation)
        ctx.addIssue({
          code: 'custom',
          message: 'Minimum deviation cannot exceed initial deviation.',
        });
      const names = [q.name, ...(q.aliases ?? [])].map((n) => `${q.channelId}:${n.toLowerCase()}`);
      if (
        queues.has(q.id) ||
        names.some((n) => labels.has(n)) ||
        new Set(names).size !== names.length
      )
        ctx.addIssue({ code: 'custom', message: 'Duplicate queue identity or channel name.' });
      if (!games.has(q.gameId))
        ctx.addIssue({ code: 'custom', message: 'A queue refers to an unknown game.' });
      queues.add(q.id);
      names.forEach((n) => labels.add(n));
    }
  });

export const saveSchema = z
  .object({
    expectedRevision: z.number().int().nonnegative(),
    settings: settingsSchema,
  })
  .strict();
export type Settings = z.infer<typeof settingsSchema>;
export type Queue = z.infer<typeof queueSchema>;
export interface Snapshot {
  communityId: string;
  revision: number;
  settings: Settings;
}

/** Upgrade older saves without making a paused default silently choose another queue. */
export function normalizeDefaults(settings: Settings): Settings {
  const result = structuredClone(settings);
  for (const channel of new Set(result.queues.map((q) => q.channelId))) {
    const queues = result.queues.filter((q) => q.channelId === channel);
    const defaults = queues.filter((q) => q.isDefault);
    if (defaults.length > 1) throw new Error('Choose one default queue per channel.');
    const selected = defaults[0] ?? queues[0];
    for (const q of queues) q.isDefault = q.id === selected.id;
  }
  return result;
}
