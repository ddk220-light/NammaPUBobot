import type { Env } from '../env';
import { ACTIVITY_GRANULARITY_MS, type CommunityTouch } from './public-directory';

// Per-isolate throttle so routine interactions rarely reach the singleton directory at all.
const touched = new Map<string, number>();
export const forgetTouchCache = () => touched.clear();

/** Best-effort tenant index update. Never throws and never delays the caller's response. */
export function touchCommunity(
  env: Env,
  guild: string | undefined,
  touch: CommunityTouch,
  ctx?: Pick<ExecutionContext, 'waitUntil'>,
) {
  if (!env.PUBLIC_DIRECTORY || !guild || !/^\d{17,20}$/.test(guild)) return;
  const now = Date.now();
  if (touch.kind === 'activity') {
    if ((touched.get(guild) ?? 0) > now - ACTIVITY_GRANULARITY_MS) return;
    if (touched.size > 10000) touched.clear();
    touched.set(guild, now);
  }
  const work = Promise.resolve()
    .then(() => env.PUBLIC_DIRECTORY!.getByName('directory').touch(guild, touch, now))
    .catch(() => {
      touched.delete(guild);
    });
  if (ctx) ctx.waitUntil(work);
}
