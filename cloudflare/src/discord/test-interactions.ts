import type { Env } from '../env';
import type { Interaction } from './interactions';
import { canManage } from './client';
import { testRequestSchema } from '../domain/testing';
import { deferInteractionReply } from './interaction-replies';

export function testInteraction(
  i: Interaction,
  env: Env,
  ctx?: Pick<ExecutionContext, 'waitUntil'>,
) {
  const fail = () =>
    Response.json({
      type: 4,
      data: {
        content: 'Use a /test command in a configured server channel.',
        flags: 64,
        allowed_mentions: { parse: [] },
      },
    });
  if (
    !ctx ||
    !i.token ||
    !/^\d{17,20}$/.test(i.guild_id ?? '') ||
    !i.member?.user ||
    i.member.user.bot ||
    !/^\d+$/.test(i.member.permissions)
  )
    return fail();
  const sub = i.data?.options?.[0];
  if (i.data?.options?.length !== 1 || sub?.type !== 1) return fail();
  const args = sub.options ?? [];
  const allowed =
    sub.name === 'populate'
      ? ['queue', 'count']
      : sub.name === 'result'
        ? ['queue', 'result']
        : ['queue'];
  if (
    new Set(args.map((o) => o.name)).size !== args.length ||
    args.some((o) => !allowed.includes(o.name) || o.options)
  )
    return fail();
  const input = testRequestSchema.safeParse({
    ...Object.fromEntries(args.map((o) => [o.name, o.value])),
    id: i.id,
    op: sub.name,
    channelId: i.channel_id,
    manager: canManage(i.member),
    actor: {
      id: i.member.user.id,
      name: (
        i.member.nick ??
        i.member.user.global_name ??
        i.member.user.username ??
        i.member.user.id
      ).slice(0, 80),
      roles: i.member.roles ?? [],
    },
  });
  if (!input.success) return fail();
  // Queries/reset need a private receipt; mutations are already visible on TEST cards.
  return deferInteractionReply(
    env,
    ctx,
    i.token,
    !['status', 'reset'].includes(input.data.op),
    () => env.COMMUNITIES.getByName(i.guild_id!).testCommand(i.guild_id!, input.data),
  );
}
