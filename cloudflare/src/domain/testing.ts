import { z } from 'zod';
import { snowflake, type Settings, type Queue } from './config';
import { personSchema } from './play';

export const testRequestSchema = z
  .object({
    id: snowflake,
    actor: personSchema,
    manager: z.boolean(),
    channelId: snowflake,
    queue: z.string().max(100).optional(),
    op: z.enum(['populate', 'start', 'checkin', 'maps', 'result', 'status', 'reset']),
    count: z.number().int().min(2).max(16).optional(),
    result: z.enum(['alpha', 'beta', 'draw', 'abort']).optional(),
  })
  .strict();
export type TestRequest = z.infer<typeof testRequestSchema>;
export type TestSession = {
  id: string;
  expiresAt: number;
  cleaning: boolean;
  cleanupPrepared?: boolean;
};
export const testLifetime = 60 * 60 * 1000;
export const testObjectName = (guild: string, queue: string) => `test:${guild}:${queue}`;

/** Only a copied configuration is changed. Never register this queue in the real community. */
export function testSettings(settings: Settings, source: Queue): Settings {
  const game = structuredClone(settings.games.find((g) => g.id === source.gameId)!);
  game.connector = { enabled: false };
  game.records = undefined;
  const queue = structuredClone(source);
  queue.autoStart = false;
  queue.voicePreview = false;
  queue.keepQueueVisible = false;
  queue.rally = undefined;
  queue.economy = undefined;
  queue.controls = undefined;
  queue.checkIn.reuseSeconds = 0;
  queue.modules = {
    ...queue.modules,
    gold: false,
    predictions: false,
    quiz: false,
    civs: false,
    stories: false,
  };
  return { games: [game], queues: [queue] };
}
