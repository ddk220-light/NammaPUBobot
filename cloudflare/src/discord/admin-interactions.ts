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
import { deferPublicComponentReply, finishInteractionReply } from './interaction-replies';

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
  const legacyPrivate = i.type === 3 && ((i.message?.flags ?? 0) & 64) !== 0;
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
    if (i.message?.author?.id !== env.DISCORD_APPLICATION_ID)
      return fail('Use a current /admin panel from this bot.');
    const parts = i.data?.custom_id?.split(':') ?? [];
    if (parts[0] !== 'adm' || parts[1] !== (legacyPrivate ? owner : 'public'))
      return fail('Use a current public /admin panel, or open /admin again.');
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
  const work = async () => {
    try {
      const stub = env.COMMUNITIES.getByName(i.guild_id!);
      const result = await stub.adminPanel(i.guild_id!, parsed.data);
      if (!result.ok) return result;
      if (!['view', 'match'].includes(parsed.data.op) && !result.confirmation)
        ctx.waitUntil(stub.flush());
      const card = adminPanelCard(
        result.view as AdminView,
        parsed.data,
        `${env.APP_ORIGIN}/app/?community=${i.guild_id}`,
        result.confirmation,
        legacyPrivate,
      );
      // Authorized clicks update this shared control panel. Queue/match cards use the outbox.
      await finishInteractionReply(env, i.token!, card, false);
      return { ok: true as const, content: '' };
    } catch {
      return {
        ok: false as const,
        content: 'The admin panel could not be updated. Open /admin to check the current state.',
      };
    }
  };
  // An unauthorized/stale click must never overwrite the shared public panel.
  if (i.type === 3 && !legacyPrivate) return deferPublicComponentReply(env, ctx, i.token, work);
  ctx.waitUntil(
    (async () => {
      const result = await work();
      if (!result.ok)
        await finishInteractionReply(
          env,
          i.token!,
          { ...result, embeds: [], components: [] },
          false,
        );
    })(),
  );
  return Response.json(i.type === 3 ? { type: 6 } : { type: 5 });
}
