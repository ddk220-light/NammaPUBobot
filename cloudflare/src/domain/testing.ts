import { z } from 'zod';
import { snowflake } from './config';
import { personSchema } from './play';

export const testRequestSchema = z
  .object({
    id: snowflake,
    actor: personSchema,
    manager: z.boolean(),
    channelId: snowflake,
    queue: z.string().max(100).optional(),
    op: z.enum(['populate', 'checkin']),
    count: z.number().int().min(2).max(16).optional(),
    matchId: z.uuid().optional(),
  })
  .strict();
export type TestRequest = z.infer<typeof testRequestSchema>;

/** Retained only to safely clean up sessions created by earlier staging builds. */
export interface RetiredTestSession {
  id: string;
  expiresAt: number;
  cleaning?: boolean;
  cleanupPrepared?: boolean;
}
