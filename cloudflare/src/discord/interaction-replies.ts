import { PlayError, type Action, type Reply } from '../domain/play';
import type { Env } from '../env';

// Every action must choose a feedback surface. Public means its queue, match,
// agreement or picker card already communicates the successful result.
const publicUpdates: Record<Action['kind'] | 'rally', boolean> = {
  add: true,
  remove: true,
  queue: true,
  start: true,
  clear: true,
  split: true,
  ready: true,
  loss: true,
  draw: true,
  abort: true,
  recover_abort: true,
  subme: true,
  subfor: true,
  subauto: true,
  force_sub: true,
  put: true,
  back: true,
  admin_report: true,
  manual_create: true,
  undo: true,
  recover: true,
  map_vote: true,
  map_pick: true,
  map_redo: true,
  civ_start: true,
  lobby_attach: true,
  lobby_unlink: true,
  bet_close: true,
  rally: true,
  // Queries, hidden picks, personal stakes and non-public administrative changes.
  teams: false,
  rank: false,
  gold: false,
  lobby_status: false,
  civ_claim: false,
  bet: false,
  bet_cancel: false,
  seed: false,
  noadd: false,
  forgive: false,
};
export const publicUpdate = (kind: Action['kind'] | 'rally') => publicUpdates[kind];
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Interaction tokens are used only in memory. Never log the webhook URL/token or
// put these requests in the public-card outbox. Retry the response, never the action.
export async function finishInteractionReply(
  env: Env,
  token: string,
  reply: Reply,
  quiet: boolean,
) {
  const remove = quiet && reply.ok !== false;
  const method = remove ? 'DELETE' : 'PATCH';
  let delay = 100;
  for (let attempt = 0; attempt < 3; attempt++) {
    // waitUntil can start before Discord has received the HTTP acknowledgement.
    await pause(delay);
    try {
      const response = await fetch(
        `https://discord.com/api/v10/webhooks/${env.DISCORD_APPLICATION_ID}/${token}/messages/@original`,
        {
          method,
          headers: { 'Content-Type': 'application/json' },
          body: remove
            ? undefined
            : JSON.stringify({
                content: reply.content.slice(0, 1900),
                allowed_mentions: { parse: [] },
              }),
          signal: AbortSignal.timeout(5000),
        },
      );
      if (response.ok || (remove && response.status === 404 && attempt === 2)) return;
      if (response.status === 429) {
        const body = (await response.json().catch(() => null)) as { retry_after?: number } | null;
        const retry = Number(body?.retry_after ?? response.headers.get('Retry-After') ?? 1);
        delay = Number.isFinite(retry) ? Math.max(250, retry * 1000) : 1000;
        // Respect long Retry-After values without keeping a Worker alive indefinitely.
        if (delay > 5000) break;
        continue;
      }
      if ([400, 401, 403].includes(response.status)) break;
    } catch {
      // A response can be lost after Discord applied it; both operations are safe to retry.
    }
    delay = 250 * (attempt + 1);
  }
  console.warn(JSON.stringify({ event: 'interaction_reply_failed', operation: method }));
}

export function deferInteractionReply(
  env: Env,
  ctx: Pick<ExecutionContext, 'waitUntil'>,
  token: string,
  quiet: boolean,
  work: () => Promise<Reply>,
) {
  ctx.waitUntil(
    (async () => {
      let reply: Reply;
      try {
        reply = await work();
      } catch (error) {
        reply = {
          ok: false,
          content:
            error instanceof PlayError
              ? error.message
              : 'This action couldn’t be confirmed. Check the latest card before trying again.',
        };
      }
      await finishInteractionReply(env, token, reply, quiet);
    })(),
  );
  // Slash commands cannot use the silent component acknowledgement (type 6).
  // Delete this temporary private thinking response after successful public updates.
  return Response.json({ type: 5, data: { flags: 64 } });
}

/** Acknowledge a public button without creating a private success receipt. */
export function deferPublicComponentReply(
  env: Env,
  ctx: Pick<ExecutionContext, 'waitUntil'>,
  token: string,
  work: () => Promise<Reply>,
) {
  ctx.waitUntil(
    (async () => {
      let reply: Reply;
      try {
        reply = await work();
      } catch (error) {
        reply = {
          ok: false,
          content:
            error instanceof PlayError
              ? error.message
              : 'The invitation could not be confirmed. Check the channel before trying again.',
        };
      }
      if (reply.ok !== false) return;
      // After a type-6 acknowledgement, @original is the PUBLIC queue card.
      // Errors must be new ephemeral followups, never edits/deletes of that card.
      let delay = 100;
      for (let attempt = 0; attempt < 3; attempt++) {
        await pause(delay);
        try {
          const response = await fetch(
            `https://discord.com/api/v10/webhooks/${env.DISCORD_APPLICATION_ID}/${token}`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                content: reply.content.slice(0, 1900),
                flags: 64,
                allowed_mentions: { parse: [] },
              }),
              signal: AbortSignal.timeout(5000),
            },
          );
          if (response.ok) return;
          if (response.status === 429) {
            const body = (await response.json().catch(() => null)) as {
              retry_after?: number;
            } | null;
            delay = Number(body?.retry_after ?? response.headers.get('Retry-After') ?? 1) * 1000;
            if (Number.isFinite(delay) && delay <= 5000) {
              delay = Math.max(250, delay);
              continue;
            }
          }
        } catch {
          /* Do not duplicate an ambiguously delivered private error. */
        }
        break;
      }
      console.warn(
        JSON.stringify({ event: 'interaction_reply_failed', operation: 'private_followup' }),
      );
    })(),
  );
  return Response.json({ type: 6 });
}
