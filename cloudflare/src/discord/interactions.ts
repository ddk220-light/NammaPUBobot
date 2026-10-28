import type { Env } from '../env';
import { canManage } from './client';
import { actionSchema, PlayError, type Action, type Match, type Reply } from '../domain/play';
import { playerSummary, recordActionSchema, recordPolicy } from '../domain/records';
import { snowflake } from '../domain/config';
import { memberIds, resolveMember } from './members';
import { matchCard, type Card, type ActionRow } from './cards';
import { substitutionPanel } from './player-panels';
import { queuePicker, type QueueChoice } from './queue-picker';
import { predictionPanel } from './prediction-panel';
import { adminCommandRoute } from './commands.mjs';
import { adminInteraction } from './admin-interactions';
import { testInteraction } from './test-interactions';
import {
  deferInteractionReply,
  deferPublicComponentReply,
  finishInteractionReply,
  publicUpdate,
} from './interaction-replies';

export async function verifyRequest(
  request: Request,
  keyHex: string,
  raw: string,
): Promise<boolean> {
  const sig = request.headers.get('X-Signature-Ed25519') ?? '';
  const timestamp = request.headers.get('X-Signature-Timestamp') ?? '';
  if (
    !/^[a-fA-F0-9]{64}$/.test(keyHex) ||
    !/^[a-fA-F0-9]{128}$/.test(sig) ||
    !/^\d{10}$/.test(timestamp)
  )
    return false;
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) return false;
  const bytes = (s: string) => Uint8Array.from(s.match(/../g)!.map((h) => parseInt(h, 16)));
  try {
    const key = await crypto.subtle.importKey('raw', bytes(keyHex), 'Ed25519', false, ['verify']);
    return await crypto.subtle.verify(
      'Ed25519',
      key,
      bytes(sig),
      new TextEncoder().encode(timestamp + raw),
    );
  } catch {
    return false;
  }
}
export interface Interaction {
  id?: string;
  type: number;
  application_id: string;
  guild_id?: string;
  channel_id?: string;
  token?: string;
  message?: { flags?: number; author?: { id: string } };
  member?: {
    permissions: string;
    nick?: string;
    roles?: string[];
    user?: { id: string; username?: string; global_name?: string; bot?: boolean };
  };
  data?: {
    name?: string;
    custom_id?: string;
    values?: string[];
    options?: Option[];
    resolved?: {
      users?: Record<string, { id: string; username: string; global_name?: string; bot?: boolean }>;
      members?: Record<string, { nick?: string; roles?: string[] }>;
    };
  };
}
interface Option {
  name: string;
  type?: number;
  value?: unknown;
  options?: Option[];
}
const ephemeral = (
  content: string,
  extra: { components?: ActionRow[]; embeds?: Card['embeds'] } = {},
) =>
  Response.json({
    type: 4,
    data: { ...extra, content, flags: 64, allowed_mentions: { parse: [] } },
  });
function actionReply(
  i: Interaction,
  env: Env,
  ctx: Pick<ExecutionContext, 'waitUntil'> | undefined,
  kind: Action['kind'],
  reply: Reply,
) {
  if (i.type === 3 && publicUpdate(kind) && reply.ok !== false) {
    // Retire an owned private choice/confirmation panel after applying it. Never
    // delete a public card through this interaction's webhook.
    if (
      ctx &&
      i.token &&
      ((i.message?.flags ?? 0) & 64) !== 0 &&
      i.message?.author?.id === env.DISCORD_APPLICATION_ID
    )
      ctx.waitUntil(finishInteractionReply(env, i.token, reply, true));
    return Response.json({ type: 6 });
  }
  return ephemeral(
    reply.content,
    reply.link
      ? {
          components: [
            {
              type: 1,
              components: [{ type: 2, style: 5, label: reply.link.label, url: reply.link.url }],
            },
          ],
        }
      : {},
  );
}
export async function interactions(
  request: Request,
  env: Env,
  ctx?: Pick<ExecutionContext, 'waitUntil'>,
) {
  if (!env.DISCORD_PUBLIC_KEY || !env.DISCORD_APPLICATION_ID)
    return new Response('Discord setup is incomplete.', { status: 503 });
  const reader = request.body?.getReader();
  if (!reader) return new Response('Invalid signature.', { status: 401 });
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > 65536) {
      await reader.cancel();
      return new Response('Interaction is too large.', { status: 413 });
    }
    chunks.push(value);
  }
  const body = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const raw = new TextDecoder().decode(body);
  if (!(await verifyRequest(request, env.DISCORD_PUBLIC_KEY, raw)))
    return new Response('Invalid signature.', { status: 401 });
  let interaction: Interaction;
  try {
    interaction = JSON.parse(raw);
  } catch {
    return new Response('Invalid JSON.', { status: 400 });
  }
  if (!interaction || typeof interaction !== 'object' || typeof interaction.type !== 'number')
    return new Response('Invalid interaction.', { status: 400 });
  if (interaction.application_id !== env.DISCORD_APPLICATION_ID)
    return new Response('Wrong application.', { status: 401 });
  if (interaction.type === 1) return Response.json({ type: 1 });
  if (interaction.type === 2 && interaction.data?.name === 'test')
    return testInteraction(interaction, env, ctx);
  if (
    (interaction.type === 2 &&
      interaction.data?.name === 'admin' &&
      !interaction.data.options?.some((o) => o.type === 2)) ||
    (interaction.type === 3 && interaction.data?.custom_id?.startsWith('adm:'))
  )
    return adminInteraction(interaction, env, ctx);
  // Accept old registered /admin subcommands during command propagation.
  if (interaction.type === 2 && ['admin', 'manage'].includes(interaction.data?.name ?? '')) {
    if (
      !interaction.guild_id ||
      !interaction.channel_id ||
      !interaction.member ||
      !/^\d+$/.test(interaction.member.permissions) ||
      !canManage(interaction.member)
    )
      return ephemeral('Manage Server permission is required for administrator commands.');
    const group = interaction.data?.options?.[0];
    const action = group?.options?.[0];
    const route = group && action && adminCommandRoute(group.name, action.name);
    if (
      !route ||
      interaction.data?.options?.length !== 1 ||
      group?.type !== 2 ||
      group.options?.length !== 1 ||
      action?.type !== 1
    )
      return ephemeral('Choose an action under /manage setup, queue, match or player.');
    const args = action.options ?? [];
    if (
      route.required.some(
        (name: string) => !args.some((o) => o.name === name && o.value !== undefined),
      ) ||
      new Set(args.map((o) => o.name)).size !== args.length ||
      args.some(
        (o) => !route.arguments.includes(o.name) || o.options || o.type === 1 || o.type === 2,
      )
    )
      return ephemeral('Choose the required options for this administrator action.');
    // Dispatch to the existing handlers only after verifying the signed caller's
    // permission. Resolved members and the interaction ID remain unchanged.
    interaction = {
      ...interaction,
      data: {
        ...interaction.data,
        name: route.command,
        options: route.subcommand ? [{ name: route.subcommand, options: args }] : args,
      },
    };
  }
  if ((interaction.type === 2 && interaction.data?.name !== 'setup') || interaction.type === 3)
    return playInteraction(interaction, env, ctx);
  if (interaction.type !== 2 || interaction.data?.name !== 'setup')
    return ephemeral('This command is not available in this release.');
  if (!interaction.guild_id || !interaction.channel_id || !interaction.member)
    return ephemeral('Use setup in a server channel.');
  if (!/^\d+$/.test(interaction.member.permissions) || !canManage(interaction.member))
    return ephemeral('Manage Server permission is required to configure this community.');
  const command = interaction.data.options?.[0]?.name;
  if (command === 'web')
    return ephemeral(
      `Configure this community: ${env.APP_ORIGIN}/?community=${interaction.guild_id}`,
    );
  if (command !== 'status') return ephemeral('Use /manage setup status or /manage setup web.');
  const snapshot = await env.COMMUNITIES.getByName(interaction.guild_id).read(interaction.guild_id);
  const here = snapshot.settings.queues.filter((q) => q.channelId === interaction.channel_id);
  return ephemeral(
    `**Community setup**\n${snapshot.settings.games.length} game(s), ${snapshot.settings.queues.length} queue(s), settings revision ${snapshot.revision}.\n${here.length ? `${here.length} queue(s) configured in this channel.` : 'This channel has no configured queue yet.'}\nOpen setup: ${env.APP_ORIGIN}/?community=${interaction.guild_id}`,
  );
}

async function playInteraction(
  i: Interaction,
  env: Env,
  ctx?: Pick<ExecutionContext, 'waitUntil'>,
) {
  const member = i.member,
    actor = member?.user;
  if (
    !snowflake.safeParse(i.guild_id).success ||
    !snowflake.safeParse(i.channel_id).success ||
    !snowflake.safeParse(i.id).success ||
    !actor ||
    actor.bot ||
    !member ||
    !/^\d+$/.test(member.permissions)
  )
    return ephemeral('Use this command as a member of a server channel.');
  const action: Partial<Action> = {
    id: i.id,
    actor: {
      id: actor.id,
      name: (member.nick ?? actor.global_name ?? actor.username ?? actor.id).slice(0, 80),
      roles: member.roles ?? [],
    },
    channelId: i.channel_id,
    admin: canManage(member),
  };
  if (
    (i.type === 2 && i.data?.name === 'civpick') ||
    (i.type === 3 && i.data?.custom_id?.startsWith('civ:'))
  ) {
    if (!ctx || !i.token) return ephemeral('Use a current civilization command or button.');
    if (i.type === 3) {
      if (i.message?.author?.id !== env.DISCORD_APPLICATION_ID)
        return ephemeral('Use the bot’s civilization card.');
      const parts = /^civ:([a-f0-9-]{36}):(\d+):(\d+)$/.exec(i.data!.custom_id!);
      if (!parts) return ephemeral('Use the latest civilization card.');
      Object.assign(action, {
        kind: 'civ_claim',
        matchId: parts[1],
        generation: Number(parts[2]),
        choice: Number(parts[3]),
      });
    } else {
      const option = (n: string) => i.data?.options?.find((o) => o.name === n)?.value;
      Object.assign(action, {
        kind: 'civ_start',
        matchId: option('match'),
        minutes: option('minutes'),
        redo: option('redo'),
      });
    }
    const parsed = actionSchema.safeParse(action);
    if (!parsed.success) return ephemeral('Choose a valid match and duration (1–10 minutes).');
    return deferInteractionReply(env, ctx, i.token, publicUpdate(parsed.data.kind), async () => {
      const stub = env.COMMUNITIES.getByName(i.guild_id!);
      const reply = await stub.play(i.guild_id!, parsed.data);
      ctx.waitUntil(stub.flush());
      return reply;
    });
  }
  if (i.type === 2 && i.data?.name === 'lobby') {
    if (!ctx || !i.token) return ephemeral('Use a current lobby command.');
    const sub = i.data.options?.[0],
      options = sub?.options ?? [];
    const option = (name: string) => options.find((o) => o.name === name)?.value;
    if (!['attach', 'unlink', 'status', 'link'].includes(sub?.name ?? ''))
      return ephemeral('Choose attach, unlink, status or link.');
    return deferInteractionReply(
      env,
      ctx,
      i.token,
      ['attach', 'unlink'].includes(sub!.name),
      async () => {
        const stub = env.COMMUNITIES.getByName(i.guild_id!);
        if (sub!.name === 'link') {
          const s = await stub.read(i.guild_id!),
            qs = s.settings.queues.filter((q) => q.channelId === i.channel_id);
          const name = String(option('queue') ?? '').toLowerCase();
          const q = name
            ? qs.find(
                (q) =>
                  q.id === name ||
                  q.name.toLowerCase() === name ||
                  q.aliases?.some((a) => a.toLowerCase() === name),
              )
            : (qs.find((q) => q.isDefault) ?? (qs.length === 1 ? qs[0] : undefined));
          if (!q) throw new PlayError('Choose a queue in this channel.');
          const input = recordActionSchema.parse({
            id: i.id,
            kind: 'link',
            gameId: q.gameId,
            userId: option('user') ?? actor.id,
            externalId: option('profile_id'),
          });
          return stub.linkGameProfile(i.guild_id!, input, actor.id, canManage(member));
        }
        const input = actionSchema.parse({
          ...action,
          kind: `lobby_${sub!.name}`,
          ...(option('match') ? { matchId: option('match') } : {}),
          ...(option('reference') ? { reference: option('reference') } : {}),
        });
        const reply = await stub.play(i.guild_id!, input);
        ctx.waitUntil(stub.flush());
        return reply;
      },
    );
  }
  if (i.type === 2 && i.data?.name === 'rally') {
    if (!ctx || !i.token) return ephemeral('Use a current rally command.');
    const options = i.data.options ?? [],
      queue = options.find((o) => o.name === 'queue')?.value,
      channel = options.find((o) => o.name === 'voice_channel')?.value;
    if (
      (queue !== undefined && typeof queue !== 'string') ||
      (channel !== undefined && !snowflake.safeParse(channel).success)
    )
      return ephemeral('Choose valid queue and voice options.');
    action.kind = 'queue';
    action.queue = queue as string | undefined;
    const parsed = actionSchema.safeParse(action);
    if (!parsed.success) return ephemeral('Choose current queue options.');
    return deferInteractionReply(env, ctx, i.token, publicUpdate('rally'), async () => {
      const stub = env.COMMUNITIES.getByName(i.guild_id!);
      const reply = await stub.rally(i.guild_id!, parsed.data, channel as string | undefined);
      ctx.waitUntil(stub.flush());
      return reply;
    });
  }
  if (
    i.type === 2 &&
    (['rank', 'leaderboard', 'profile'].includes(i.data?.name ?? '') ||
      (i.data?.name === 'rating' &&
        ['hide', 'unhide', 'sync'].includes(i.data.options?.[0]?.name ?? '')))
  ) {
    try {
      const root = i.data!.name!,
        top = i.data!.options ?? [],
        options = ['profile', 'rating'].includes(root) ? (top[0]?.options ?? []) : top;
      const option = (key: string) => options.find((o) => o.name === key)?.value;
      const stub = env.COMMUNITIES.getByName(i.guild_id!),
        s = await stub.read(i.guild_id!);
      const qs = s.settings.queues.filter((q) => q.channelId === i.channel_id),
        name = String(option('queue') ?? '').toLowerCase();
      const q = name
        ? qs.find(
            (q) =>
              q.id === name ||
              q.name.toLowerCase() === name ||
              (q.aliases ?? []).some((a) => a.toLowerCase() === name),
          )
        : (qs.find((q) => q.isDefault) ?? (qs.length === 1 ? qs[0] : undefined));
      if (!q) return ephemeral('Choose a queue name for this channel.');
      const target = String(option('user') ?? actor.id);
      if (root === 'rating' || (root === 'profile' && top[0]?.name !== 'show')) {
        const kind = root === 'rating' ? top[0]?.name : top[0]?.name;
        const parsed = recordActionSchema.parse({
          id: i.id,
          kind,
          gameId: q.gameId,
          userId: target,
          ...(option('provider') ? { provider: option('provider') } : {}),
          ...(option('profile_id') ? { externalId: option('profile_id') } : {}),
          ...(option('url') ? { url: option('url') } : {}),
        });
        const result = await stub.recordAction(i.guild_id!, parsed, actor.id, canManage(member));
        ctx?.waitUntil(stub.flush());
        return ephemeral(result.content);
      }
      if (recordPolicy(s.settings, q.gameId).visibility === 'admins' && !canManage(member))
        return ephemeral('Only organizers can view this game’s statistics.');
      const page = Math.max(0, Math.min(9999, Number(option('page') ?? 1) - 1));
      if (!Number.isInteger(page)) return ephemeral('Choose a whole page number.');
      const stats = await stub.statistics(
        i.guild_id!,
        q.gameId,
        root === 'leaderboard' ? undefined : target,
        page,
        canManage(member),
        actor.id,
      );
      if (root === 'leaderboard')
        return ephemeral(
          `**${stats.gameName} leaderboard · page ${page + 1}/${Math.max(1, stats.pages)}**\n${stats.leaderboard.map((p, n) => `${page * 10 + n + 1}. ${playerSummary(p)} · ${p.rank ?? 'unranked'}`).join('\n') || 'No players meet the current thresholds.'}\nMinimum ${stats.policy.minimumMatches} match${stats.policy.minimumMatches === 1 ? '' : 'es'}${stats.policy.recentDays ? `; played within ${stats.policy.recentDays} days` : ''}.\nWeb statistics: ${env.APP_ORIGIN}/stats?community=${i.guild_id}&game=${q.gameId}`.slice(
            0,
            1900,
          ),
        );
      const p = stats.player;
      if (!p)
        return ephemeral(
          'No visible player record for this game yet. Play a match or ask an organizer to seed your rating.',
        );
      let content = `**${stats.gameName} · ${p.rank ?? 'Unranked'}**\n${playerSummary(p)}\n${p.matchCount} matches · ${p.winRate === null ? 'no win rate' : `${(p.winRate * 100).toFixed(1)}% wins`} · streak ${p.streak} · peak ${p.peak}`;
      if (option('detailed') || root === 'profile')
        content += `\nRecent: ${
          p.recent
            .slice(0, 5)
            .map(
              (m) =>
                `${m.state === 'finished' ? m.result : m.state} (${new Date(m.at).toISOString().slice(0, 10)})`,
            )
            .join(', ') || 'No match history'
        }\nRating changes: ${
          p.history
            .slice(0, 5)
            .map((c) => `${c.before} → ${c.after}`)
            .join(', ') || 'None'
        }\nProfile references (unverified): ${p.profiles.map((r) => `${r.provider}: ${r.external_id}${r.provenance.startsWith('inferred:') ? ' (inferred; /lobby link to confirm or /profile unlink to correct)' : ''}`).join(', ') || 'None'}\nSource: ${p.source}`;
      if (root === 'profile')
        content += `\nPlayed civilizations: ${
          p.civilizations
            ?.slice(0, 5)
            .map(
              (c) =>
                `${c.selection}: ${c.wins}/${c.games} wins (${((100 * c.wins) / c.games).toFixed(0)}%)${c.inferred ? ' · inferred link' : ''}`,
            )
            .join('; ') || 'None recorded yet'
        }`;
      content += `\nWeb statistics: ${env.APP_ORIGIN}/stats?community=${i.guild_id}&game=${q.gameId}&user=${target}`;
      return ephemeral(content.slice(0, 1900));
    } catch (error) {
      return ephemeral(
        error instanceof PlayError
          ? error.message
          : 'Invalid player-record options, or records could not be confirmed. Try /rank again.',
      );
    }
  }
  if (i.type === 3) {
    let predictionReply = ephemeral;
    try {
      const custom = i.data?.custom_id ?? '';
      const map = /^mv:([a-f0-9-]{36}):([a-f0-9-]{36})$/.exec(custom);
      if (map) {
        const stub = env.COMMUNITIES.getByName(i.guild_id!),
          current = await stub.playerMatch(i.guild_id!, i.channel_id!, actor.id, map[1]);
        if (!current.ok) return ephemeral(current.content);
        const m = current.match as Match,
          round = m.mapRound;
        if (
          !round ||
          round.id !== map[2] ||
          !i.data?.values?.length ||
          i.data.values.some((v) => !/^\d{1,2}$/.test(v) || !round.choices[Number(v)])
        )
          return ephemeral('Choose maps from the current round.');
        action.matchId = m.id;
        action.roundId = round.id;
        action.kind = round.settings.mode === 'direct' ? 'map_pick' : 'map_vote';
        action.mapChoices = i.data.values.map((v) => round.choices[Number(v)]);
        const reply = await stub.play(i.guild_id!, actionSchema.parse(action));
        ctx?.waitUntil(stub.flush());
        return actionReply(i, env, ctx, action.kind, reply);
      }
      const pick = /^b:([a-f0-9-]{36}):(pick|close)$/.exec(custom);
      const personal = /^(bp|bs|bc):([a-f0-9-]{36}):(?:(a|b):)?(\d{17,20})(?::(\d{1,16}))?$/.exec(
        custom,
      );
      if (pick || personal) {
        if (personal && personal[4] !== actor.id)
          return ephemeral('Open Place bet to choose your own stake.');
        if (
          personal &&
          ((i.message?.flags ?? 0) & 64) !== 0 &&
          i.message?.author?.id === env.DISCORD_APPLICATION_ID
        )
          predictionReply = (content, extra = {}) =>
            Response.json({
              type: 7,
              data: {
                ...extra,
                content,
                components: extra.components ?? [],
                allowed_mentions: { parse: [] },
              },
            });
        const stub = env.COMMUNITIES.getByName(i.guild_id!);
        if (pick?.[2] === 'close' || personal?.[1] === 'bs' || personal?.[1] === 'bc') {
          const receipt = await stub.predictionReceipt(i.guild_id!, {
            id: i.id!,
            actor: action.actor!,
            channelId: i.channel_id!,
            admin: action.admin!,
            kind: pick ? 'bet_close' : personal![1] === 'bc' ? 'bet_cancel' : 'bet',
            ...(pick ? { matchId: pick[1] } : { bookId: personal![2] }),
            ...(personal?.[1] === 'bs'
              ? {
                  side: personal[3] === 'a' ? ('alpha' as const) : ('beta' as const),
                  stake: Number(personal[5]),
                }
              : {}),
          });
          if (receipt)
            return pick?.[2] === 'close'
              ? actionReply(i, env, ctx, 'bet_close', receipt)
              : predictionReply(receipt.content);
        }
        const state = await stub.prediction(
          i.guild_id!,
          i.channel_id!,
          actor.id,
          pick?.[1] ?? personal![2],
          !!personal,
          !!pick || personal?.[1] === 'bc' || personal?.[1] === 'bp',
        );
        if (!state.ok) return predictionReply(state.content);
        if ((pick && pick[2] === 'pick') || personal?.[1] === 'bp') {
          const panel = predictionPanel(
            state.match as Match,
            state.book,
            actor.id,
            state.balance,
            personal?.[3] === 'a' ? 'alpha' : personal?.[3] === 'b' ? 'beta' : undefined,
            state.bettingEnabled,
            state.currentBet,
          );
          return predictionReply(panel.content, panel);
        }
        action.matchId = state.match.id;
        action.bookId = state.book?.id;
        action.kind = pick ? 'bet_close' : personal![1] === 'bc' ? 'bet_cancel' : 'bet';
        if (action.kind === 'bet') {
          action.side = personal![3] === 'a' ? 'alpha' : 'beta';
          action.stake = Number(personal![5]);
        }
      }
      if (pick || personal) {
        const parsed = actionSchema.parse(action),
          stub = env.COMMUNITIES.getByName(i.guild_id!);
        const reply = await stub.play(i.guild_id!, parsed);
        ctx?.waitUntil(stub.flush());
        if ((action.kind === 'bet' || action.kind === 'bet_cancel') && reply.ok !== false) {
          const next = await stub.prediction(
            i.guild_id!,
            i.channel_id!,
            actor.id,
            action.matchId,
            false,
            true,
          );
          if (next.ok) {
            const panel = predictionPanel(
              next.match as Match,
              next.book,
              actor.id,
              next.balance,
              action.side,
              next.bettingEnabled,
              next.currentBet,
            );
            return predictionReply(
              action.kind === 'bet_cancel' ? `${reply.content}\n${panel.content}` : reply.content,
              panel,
            );
          }
        }
        return action.kind === 'bet_close'
          ? actionReply(i, env, ctx, 'bet_close', reply)
          : predictionReply(reply.content);
      }
      const queue = /^q:([a-f0-9-]{36}):(join|leave|ping)(?::g\d+)?$/.exec(custom);
      if (queue?.[2] === 'ping') {
        if (!ctx || !i.token) return ephemeral('Use the Ping button on the current queue card.');
        const input = actionSchema.safeParse({ ...action, kind: 'queue', queue: queue[1] });
        if (!input.success) return ephemeral('Choose a current queue in this channel.');
        return deferPublicComponentReply(env, ctx, i.token, async () => {
          const stub = env.COMMUNITIES.getByName(i.guild_id!);
          const reply = await stub.pingVoice(i.guild_id!, input.data);
          if (reply.ok !== false) await stub.flush();
          return reply;
        });
      }
      const picker = /^(qs|qp):(add|remove|queue):(\d{1,2})$/.exec(custom);
      const parsed =
        /^m:([a-f0-9-]{36}):(\d+):(ready|loss|draw|abort|subme|subauto|back|subs|request|cancel|take|auto|rejoin|confirm_loss|confirm_back)$/.exec(
          custom,
        );
      const agreement = /^a:([a-f0-9-]{36}):(\d+):([a-f0-9-]{36}):(abort|draw)$/.exec(custom);
      if (agreement) {
        action.matchId = agreement[1];
        action.version = Number(agreement[2]);
        action.proposalId = agreement[3];
        action.kind = agreement[4] as 'abort' | 'draw';
      } else if (queue) {
        action.queue = queue[1];
        action.kind = queue[2] === 'join' ? 'add' : 'remove';
      } else if (picker) {
        const kind = picker[2] as QueueChoice;
        const stub = env.COMMUNITIES.getByName(i.guild_id!);
        const s = await stub.read(i.guild_id!);
        const qs = s.settings.queues.filter((q) => q.channelId === i.channel_id);
        if (picker[1] === 'qp') {
          const panel = queuePicker(qs, kind, Number(picker[3]));
          return ephemeral(panel.content, panel);
        }
        if (i.data?.values?.length !== 1 || !qs.some((q) => q.id === i.data!.values![0]))
          return ephemeral('That queue is no longer available here. Choose a current queue.');
        action.queue = i.data.values[0];
        action.kind = kind;
      } else if (parsed) {
        action.matchId = parsed[1];
        action.version = Number(parsed[2]);
        const kind = parsed[3];
        if (kind === 'abort') return ephemeral('Use `/report abort` to request a cancellation.');
        if (['take', 'auto', 'rejoin'].includes(kind)) {
          if (
            kind !== 'rejoin' &&
            (i.data?.values?.length !== 1 || !snowflake.safeParse(i.data.values[0]).success)
          )
            return ephemeral('Choose one player from the current match.');
          const receipt = await env.COMMUNITIES.getByName(i.guild_id!).playerReceipt(i.guild_id!, {
            id: i.id!,
            actor: action.actor!,
            channelId: action.channelId!,
            admin: action.admin!,
            kind: kind === 'rejoin' ? 'add' : kind === 'take' ? 'subfor' : 'subauto',
            matchId: action.matchId,
            version: action.version,
            ...(kind === 'rejoin' ? {} : { targetId: i.data!.values![0] }),
          });
          if (receipt)
            return actionReply(
              i,
              env,
              ctx,
              kind === 'rejoin' ? 'add' : kind === 'take' ? 'subfor' : 'subauto',
              receipt,
            );
        }
        if (['subs', 'take', 'auto', 'rejoin', 'back'].includes(kind)) {
          const current = await env.COMMUNITIES.getByName(i.guild_id!).playerMatch(
            i.guild_id!,
            i.channel_id!,
            actor.id,
            action.matchId,
            action.version,
          );
          if (!current.ok) return ephemeral(current.content);
          const m = current.match as Match;
          if (['subs', 'take', 'auto', 'rejoin'].includes(kind)) {
            return ephemeral('Use the current match or queue card.');
          }
          if (kind === 'subs') {
            const panel = substitutionPanel(m, actor.id, !!action.admin);
            if (current.url)
              panel.components.push({
                type: 1,
                components: [{ type: 2, style: 5, label: 'View match', url: current.url }],
              });
            return ephemeral(panel.content, panel);
          }
          if (kind === 'back') {
            if (!['check_in', 'playing'].includes(m.state))
              return ephemeral('This match is already closed.');
            if (
              kind === 'back' &&
              (!m.roster.some((p) => p.id === actor.id) || m.state !== 'check_in')
            )
              return ephemeral('Only a player checking in can leave this match.');
            const side = m.captains[0] === actor.id ? 'Alpha' : 'Beta';
            return ephemeral(
              'Leave this match? A queued substitute rebalances both teams. Without one, the match is cancelled and ready players return to the queue.',
              {
                components: [
                  {
                    type: 1,
                    components: [
                      {
                        type: 2,
                        style: 4,
                        label: 'Leave match',
                        custom_id: `m:${m.id}:${m.version}:confirm_${kind}`,
                      },
                    ],
                  },
                ],
              },
            );
          }
          if (kind === 'rejoin') {
            if (['check_in', 'playing'].includes(m.state))
              return ephemeral('This match is still active.');
            action.kind = 'add';
            action.queue = m.queue.id;
          } else {
            if (i.data?.values?.length !== 1 || !snowflake.safeParse(i.data.values[0]).success)
              return ephemeral('Choose one player from the current match.');
            const target = m.roster.find((p) => p.id === i.data!.values![0]);
            if (!target) return ephemeral('That player has left this match. Use its latest card.');
            action.target = { id: target.id, name: target.name };
            action.kind = kind === 'take' ? 'subfor' : 'subauto';
          }
        } else {
          action.kind =
            kind === 'request' || kind === 'cancel'
              ? 'subme'
              : kind === 'confirm_loss'
                ? 'loss'
                : kind === 'confirm_back'
                  ? 'back'
                  : (kind as Action['kind']);
          if (kind === 'request' || kind === 'cancel') action.subRequest = kind === 'request';
        }
      } else return ephemeral('This button is no longer available. Use the latest card.');
    } catch {
      return predictionReply(
        'This action couldn’t be confirmed. Check the latest card before trying again.',
      );
    }
  } else {
    const root = i.data?.name,
      top = i.data?.options ?? [],
      sub = top[0];
    let options = top;
    if (
      ['queue', 'report', 'rating', 'match', 'moderation', 'predictions', 'maps'].includes(
        root ?? '',
      )
    ) {
      options = sub?.options ?? [];
      action.kind =
        root === 'queue'
          ? sub?.name === 'show'
            ? 'queue'
            : sub?.name === 'start'
              ? 'start'
              : ['clear', 'split'].includes(sub?.name ?? '')
                ? (sub.name as Action['kind'])
                : undefined
          : root === 'rating' && sub?.name === 'seed'
            ? 'seed'
            : root === 'match'
              ? (
                  {
                    abort: 'recover_abort',
                    report: 'admin_report',
                    create: 'manual_create',
                    undo: 'undo',
                    substitute: 'force_sub',
                    put: 'put',
                    recover: 'recover',
                  } as Record<string, Action['kind']>
                )[sub?.name ?? '']
              : root === 'moderation' && ['noadd', 'forgive'].includes(sub?.name ?? '')
                ? (sub.name as Action['kind'])
                : root === 'gold'
                  ? 'gold'
                  : root === 'predictions'
                    ? 'bet_close'
                    : root === 'maps'
                      ? sub?.name === 'redo'
                        ? 'map_redo'
                        : 'teams'
                      : root === 'report' && ['loss', 'draw', 'abort'].includes(sub?.name ?? '')
                        ? (sub.name as Action['kind'])
                        : undefined;
    } else if (
      ['add', 'remove', 'teams', 'rank', 'subme', 'subfor', 'subauto', 'gold'].includes(root ?? '')
    )
      action.kind = root as Action['kind'];
    if (!action.kind) return ephemeral('This command is not available in this release.');
    const option = (name: string) => options.find((o) => o.name === name)?.value;
    if (option('queue') !== undefined) action.queue = option('queue') as string;
    if (root === 'gold') {
      const view = String(option('view') ?? 'balance');
      if (!['balance', 'history', 'leaderboard'].includes(view))
        return ephemeral('Choose a current gold view.');
      const reply = await env.COMMUNITIES.getByName(i.guild_id!).goldView(
        i.guild_id!,
        i.channel_id!,
        actor.id,
        view,
        action.queue,
        !!action.admin,
      );
      return ephemeral(reply.content.slice(0, 1900));
    }
    if (root === 'predictions' && sub?.name === 'leaderboard') {
      const reply = await env.COMMUNITIES.getByName(i.guild_id!).goldView(
        i.guild_id!,
        i.channel_id!,
        actor.id,
        'predictions',
        action.queue,
        !!action.admin,
      );
      return ephemeral(reply.content.slice(0, 1900));
    }
    if (option('match') !== undefined) action.matchId = option('match') as string;
    if (option('skip_check_in') !== undefined)
      action.skipCheckIn = option('skip_check_in') as boolean;
    if (option('rating') !== undefined) action.rating = option('rating') as number;
    if (option('deviation') !== undefined) action.deviation = option('deviation') as number;
    if (option('result') !== undefined) action.result = option('result') as Action['result'];
    if (option('side') !== undefined) action.side = option('side') as Action['side'];
    if (option('group_size') !== undefined) action.groupSize = option('group_size') as number;
    if (option('sort_by_rating') !== undefined)
      action.sortByRating = option('sort_by_rating') as boolean;
    if (option('seconds') !== undefined) action.seconds = option('seconds') as number;
    if (option('reason') !== undefined) action.reason = option('reason') as string;
    for (const [name, key] of [
      ['user', 'target'],
      ['replacement', 'replacement'],
    ] as const) {
      const id = option(name);
      if (id !== undefined) {
        if (typeof id !== 'string') return ephemeral('Choose a server member.');
        const user = i.data?.resolved?.users?.[id],
          resolvedMember = i.data?.resolved?.members?.[id];
        if (!user || !resolvedMember || user.bot)
          return ephemeral('Choose a human member of this server.');
        action[key] = {
          id,
          name: (resolvedMember.nick ?? user.global_name ?? user.username).slice(0, 80),
          roles: resolvedMember.roles ?? [],
        };
      }
    }
    if (action.kind === 'manual_create') {
      if (!action.admin) return ephemeral('Manage Server permission is required for this action.');
      let ids: [string[], string[]];
      try {
        ids = [memberIds(String(option('alpha') ?? '')), memberIds(String(option('beta') ?? ''))];
      } catch (e) {
        return ephemeral(e instanceof Error ? e.message : 'Choose valid server members.');
      }
      if (new Set(ids.flat()).size !== ids.flat().length)
        return ephemeral('Teams cannot contain duplicate players.');
      if (!ctx || !i.token)
        return ephemeral('This command needs a current Discord interaction. Try again.');
      // Member lookup happens after acknowledgement, then the public result card is the receipt.
      return deferInteractionReply(env, ctx, i.token, publicUpdate('manual_create'), async () => {
        action.teams = (await Promise.all(
          ids.map((team) => Promise.all(team.map((id) => resolveMember(env, i.guild_id!, id)))),
        )) as Action['teams'];
        const parsed = actionSchema.parse(action),
          stub = env.COMMUNITIES.getByName(i.guild_id!);
        const reply = await stub.play(i.guild_id!, parsed);
        ctx.waitUntil(stub.flush());
        return reply;
      });
    }
  }
  const parsed = actionSchema.safeParse(action);
  if (!parsed.success)
    return ephemeral(
      'Those options aren’t valid. Use a current card or choose the command options again.',
    );
  const stub = env.COMMUNITIES.getByName(i.guild_id!);
  try {
    if (action.kind === 'teams') {
      const current = await stub.playerMatch(i.guild_id!, i.channel_id!, actor.id, action.matchId);
      if (!current.ok) return ephemeral(current.content);
      const card = matchCard(current.match as Match);
      return ephemeral(card.content, card);
    }
    if (i.type === 2 && publicUpdate(parsed.data.kind)) {
      if (!ctx || !i.token) return ephemeral('Use a current Discord command.');
      return deferInteractionReply(env, ctx, i.token, true, async () => {
        const reply = await stub.play(i.guild_id!, parsed.data);
        ctx.waitUntil(stub.flush());
        return reply;
      });
    }
    const reply = await stub.play(i.guild_id!, parsed.data);
    ctx?.waitUntil(stub.flush());
    return actionReply(i, env, ctx, parsed.data.kind, reply);
  } catch {
    return ephemeral(
      'This action couldn’t be confirmed. Check the latest card before trying again.',
    );
  }
}
