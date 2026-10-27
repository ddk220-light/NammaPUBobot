import { describe, expect, it, vi } from 'vitest';
import { deferInteractionReply, finishInteractionReply } from '../src/discord/interaction-replies';
import { PlayError, type Reply } from '../src/domain/play';
import type { Env } from '../src/env';
import { harness } from './harness';

harness();
const env = { DISCORD_APPLICATION_ID: '100000000000000001' } as Env;
const token = 'synthetic-private-token';
const original = `https://discord.com/api/v10/webhooks/${env.DISCORD_APPLICATION_ID}/${token}/messages/@original`;

describe('interaction response lifecycle', () => {
  it('acknowledges before slow work and deletes only the original response on public success', async () => {
    const jobs: Promise<unknown>[] = [];
    let finish!: (reply: Reply) => void;
    const work = vi.fn(
      () =>
        new Promise<Reply>((resolve) => {
          finish = resolve;
        }),
    );
    const response = deferInteractionReply(
      env,
      { waitUntil: (p) => jobs.push(p) },
      token,
      true,
      work,
    );
    expect(await response.json()).toEqual({ type: 5, data: { flags: 64 } });
    expect(fetch).not.toHaveBeenCalled();
    finish({ content: 'Joined the queue.' });
    await Promise.all(jobs);
    expect(work).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      original,
      expect.objectContaining({ method: 'DELETE', body: undefined }),
    );
  });

  it('keeps actionable errors private after a deferred public action', async () => {
    const jobs: Promise<unknown>[] = [];
    deferInteractionReply(env, { waitUntil: (p) => jobs.push(p) }, token, true, async () => ({
      ok: false,
      content: 'This queue is closed.',
    }));
    await Promise.all(jobs);
    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      original,
      expect.objectContaining({ method: 'PATCH' }),
    );
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual({
      content: 'This queue is closed.',
      allowed_mentions: { parse: [] },
    });
  });

  it.each([true, false])(
    'handles rejected background work without exposing internal errors (domain: %s)',
    async (domain) => {
      const jobs: Promise<unknown>[] = [];
      deferInteractionReply(env, { waitUntil: (p) => jobs.push(p) }, token, true, async () => {
        throw domain
          ? new PlayError('Only a captain can do that.')
          : new Error('secret-provider-detail');
      });
      await Promise.all(jobs);
      const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string);
      expect(body.content).toContain(domain ? 'Only a captain' : 'couldn’t be confirmed');
      expect(body.content).not.toContain('secret-provider-detail');
    },
  );

  it('preserves a private choice receipt even when its public card also changes', async () => {
    await finishInteractionReply(env, token, { content: 'Mongols locked in.' }, false);
    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      original,
      expect.objectContaining({ method: 'PATCH' }),
    );
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string).content).toBe(
      'Mongols locked in.',
    );
  });

  it('retries an acknowledgement race without replaying the saved action', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(null, { status: 404 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const jobs: Promise<unknown>[] = [],
      work = vi.fn(async () => ({ content: 'Saved' }));
    deferInteractionReply(env, { waitUntil: (p) => jobs.push(p) }, token, true, work);
    await Promise.all(jobs);
    expect(work).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.every(([url, init]) => url === original && init?.method === 'DELETE'),
    ).toBe(true);
  });

  it('respects a webhook rate limit before retrying cleanup', async () => {
    const times: number[] = [];
    vi.mocked(fetch).mockImplementation(async () => {
      times.push(Date.now());
      return times.length === 1
        ? Response.json({ retry_after: 0.3 }, { status: 429 })
        : new Response(null, { status: 204 });
    });
    await finishInteractionReply(env, token, { content: 'Saved' }, true);
    expect(times).toHaveLength(2);
    expect(times[1] - times[0]).toBeGreaterThanOrEqual(290);
  });

  it('bounds cleanup failures and never logs the interaction credential', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 503 }));
    await finishInteractionReply(env, token, { content: 'Saved' }, true);
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(warn).toHaveBeenCalledExactlyOnceWith(
      JSON.stringify({ event: 'interaction_reply_failed', operation: 'DELETE' }),
    );
    expect(JSON.stringify(warn.mock.calls)).not.toContain(token);
  });
});
