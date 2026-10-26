import { z } from 'zod';
import { snowflake } from './config';
export const economyImportSchema = z
  .object({
    sourceId: z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/),
    wallets: z
      .array(
        z
          .object({
            userId: snowflake,
            balance: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
            seededAt: z.number().int().nonnegative(),
          })
          .strict(),
      )
      .max(5000),
    ledger: z
      .array(
        z
          .object({
            id: z.string().min(1).max(100),
            userId: snowflake,
            amount: z.number().int().min(-Number.MAX_SAFE_INTEGER).max(Number.MAX_SAFE_INTEGER),
            kind: z.string().regex(/^[a-z_]{1,50}$/),
            at: z.number().int().nonnegative(),
          })
          .strict(),
      )
      .max(50000),
    markets: z
      .array(
        z
          .object({
            id: z.string().min(1).max(100),
            matchSourceId: z.string().max(100).nullable(),
            status: z.enum(['settled', 'refunded']),
            winner: z.enum(['alpha', 'beta']).nullable(),
            openedAt: z.number().int().nonnegative(),
            closedAt: z.number().int().nonnegative(),
            bets: z
              .array(
                z
                  .object({
                    userId: snowflake,
                    side: z.enum(['alpha', 'beta']),
                    stake: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
                    payout: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
                  })
                  .strict(),
              )
              .max(1000),
          })
          .strict(),
      )
      .max(5000)
      .optional(),
  })
  .strict();
export type EconomyImport = z.infer<typeof economyImportSchema>;
