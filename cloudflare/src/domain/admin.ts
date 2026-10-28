import { z } from 'zod';
import { snowflake, type Queue } from './config';
import { personSchema, type Match } from './play';

export const adminRequestSchema = z
  .object({
    id: snowflake,
    actor: personSchema,
    manager: z.boolean(),
    channelId: snowflake,
    queue: z.string().max(100).optional(),
    matchId: z.uuid().optional(),
    op: z.enum([
      'view',
      'match',
      'start',
      'clear',
      'queue',
      'alpha_loss',
      'beta_loss',
      'draw',
      'cancel',
      'recover',
      'close_betting',
    ]),
    version: z.number().int().nonnegative().optional(),
    confirm: z.boolean().optional(),
  })
  .strict();
export type AdminRequest = z.infer<typeof adminRequestSchema>;
export type AdminView = {
  queue: Queue;
  queues: { id: string; name: string }[];
  waiting: number;
  held: boolean;
  queueVersion: number;
  latest: Match | null;
  recent: Match[];
  active: Match[];
  selected: Match | null;
};
export const adminAllowed = (q: Queue, request: AdminRequest) =>
  request.manager || (q.adminRoles ?? []).some((id) => request.actor.roles?.includes(id));
export const queueAdminOps = new Set(['view', 'start', 'clear', 'queue']);
export const confirmedAdminOps = new Set([
  'clear',
  'alpha_loss',
  'beta_loss',
  'draw',
  'cancel',
  'close_betting',
]);
