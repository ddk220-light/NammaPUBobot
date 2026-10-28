import { canManage } from './client';
import type { Env } from '../env';
import type { Interaction } from './interactions';
import {
  adminRequestSchema,
  queueAdminOps,
  type AdminRequest,
  type AdminView,
} from '../domain/admin';
import { adminPanelCard } from './admin-panel';
import { finishInteractionReply } from './interaction-replies';

export function adminInteraction(
  i: Interaction,
  env: Env,
  ctx?: Pick<ExecutionContext, 'waitUntil'>,
) {
  const fail = (content: string) =>
    Response.json({ type: 4, data: { content, flags: 64, allowed_mentions: { parse: [] } } });
  if (
    !ctx ||
    !i.token ||
    !i.member?.user ||
    i.member.user.bot ||
    !/^\d+$/.test(i.member.permissions)
  )
    return fail('Use /admin in a server channel.');
  const owner = i.member.user.id;
  const input: Partial<AdminRequest> = {
    id: i.id,
    channelId: i.channel_id,
    manager: canManage(i.member),
    actor: {
      id: owner,
      name: (i.member.nick ?? i.member.user.global_name ?? i.member.user.username ?? owner).slice(
        0,
        80,
      ),
      roles: i.member.roles ?? [],
    },
    op: 'view',
  };
  if (!/^\d{17,20}$/.test(i.guild_id ?? '')) return fail('Use /admin in a server channel.');
  if (i.type === 2) {
    const options = i.data?.options ?? [];
    if (
      options.length > 1 ||
      options.some((o) => o.name !== 'queue' || typeof o.value !== 'string')
    )
      return fail('Use /admin with an optional queue name.');
    input.queue = options[0]?.value as string | undefined;
  } else {
    if (
      i.message?.author?.id !== env.DISCORD_APPLICATION_ID ||
      ((i.message?.flags ?? 0) & 64) === 0
    )
      return fail('Use your private /admin panel.');
    const parts = i.data?.custom_id?.split(':') ?? [];
    if (parts[0] !== 'adm' || parts[1] !== owner)
      return fail('This admin panel belongs to someone else. Open /admin yourself.');
    const op = parts[2];
    if (['selectq', 'selectm'].includes(op) && parts.length === 3 && i.data?.values?.length === 1) {
      input.op = op === 'selectq' ? 'view' : 'match';
      if (op === 'selectq') input.queue = i.data.values[0];
      else input.matchId = i.data.values[0];
    } else {
      if (
        ![5, 6].includes(parts.length) ||
        (parts.length === 6 && parts[5] !== 'yes') ||
        !/^\d+$/.test(parts[4] ?? '') ||
        i.data?.values?.length
      )
        return fail('Use a current admin panel button.');
      input.op = op as AdminRequest['op'];
      input.version = Number(parts[4]);
      input.confirm = parts[5] === 'yes';
      if (queueAdminOps.has(op)) input.queue = parts[3];
      else input.matchId = parts[3];
    }
  }
  const parsed = adminRequestSchema.safeParse(input);
  if (!parsed.success) return fail('Use a current admin panel command or button.');
  ctx.waitUntil(
    (async () => {
      try {
        const stub = env.COMMUNITIES.getByName(i.guild_id!);
        const result = await stub.adminPanel(i.guild_id!, parsed.data);
        if (!result.ok) {
          await finishInteractionReply(
            env,
            i.token!,
            { ...result, embeds: [], components: [] },
            false,
          );
          return;
        }
        if (!['view', 'match'].includes(parsed.data.op) && !result.confirmation)
          ctx.waitUntil(stub.flush());
        const card = adminPanelCard(
          result.view as AdminView,
          parsed.data,
          `${env.APP_ORIGIN}/?community=${i.guild_id}`,
          result.confirmation,
        );
        // Only the organizer's existing private panel is updated; public cards use the outbox.
        await finishInteractionReply(env, i.token!, card, false);
      } catch {
        await finishInteractionReply(
          env,
          i.token!,
          {
            ok: false,
            content:
              'The admin panel could not be updated. Open /admin to check the current state.',
            embeds: [],
            components: [],
          },
          false,
        );
      }
    })(),
  );
  return Response.json(i.type === 3 ? { type: 6 } : { type: 5, data: { flags: 64 } });
}
