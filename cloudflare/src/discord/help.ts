import type { Env } from '../env';
import type { Queue } from '../domain/config';
import { identity } from '../identity';
import { canManage } from './client';
import type { Interaction } from './interactions';

/** The /help text: player basics, organizer pointers, or the unfinished-setup notice. */
export function helpText(
  queues: Pick<Queue, 'name' | 'checkIn'>[],
  organizer: boolean,
  // Bot moderator roles run the queue from Discord; /manage and the console need Manage Server.
  manager = organizer,
) {
  const lines = [`**Playing with ${identity.publicName}**`];
  if (!queues.length)
    lines.push(
      manager
        ? 'This channel has no queue yet. Finish setup on the website with the button below.'
        : 'This channel has no queue yet. Ask an organizer to finish setup on the website.',
    );
  else
    lines.push(
      '**Join:** press **Join** on the queue card, or use `/add`. **Leave** or `/remove` takes you out.',
      queues.some((q) => q.checkIn.enabled)
        ? '**Check-in:** when the queue fills, press **Check in** on the match card before time runs out, or you drop out of the match.'
        : '**Check-in:** matches here start as soon as the queue fills.',
      '**Results:** after the game, a captain presses **Report result** on the match card. No button? Use `/report`.',
      '**Rating:** `/rank` shows yours; `/leaderboard` shows the top players.',
      '**Game account:** `/profile link` connects your in-game profile.',
      '**Privacy:** `/profile hide` takes you off leaderboards and stats; `/profile unhide` undoes it.',
      ...(queues.length > 1
        ? [
            `This channel has ${queues.length} queues (${queues.map((q) => q.name).join(', ')}); add \`queue:\` to choose one.`,
          ]
        : []),
    );
  if (manager)
    lines.push(
      '**Organizers:** `/admin` posts the queue control panel. `/manage` has every organizer tool.',
      'Games, queues and roles are set up in the web console.',
    );
  else if (organizer) lines.push('**Moderators:** `/admin` posts the queue control panel.');
  return lines.join('\n');
}

export async function helpInteraction(i: Interaction, env: Env) {
  const reply = (content: string, url?: string) =>
    Response.json({
      type: 4,
      data: {
        content,
        flags: 64,
        allowed_mentions: { parse: [] },
        ...(url
          ? {
              components: [
                { type: 1, components: [{ type: 2, style: 5, label: 'Open web console', url }] },
              ],
            }
          : {}),
      },
    });
  if (!i.guild_id || !i.channel_id || !i.member || !/^\d+$/.test(i.member.permissions))
    return reply('Use /help in a server channel.');
  const settings = (await env.COMMUNITIES.getByName(i.guild_id).read(i.guild_id)).settings;
  const queues = settings.queues.filter((q) => q.channelId === i.channel_id);
  const roles = i.member.roles ?? [];
  // Same authority as the /admin panel: server managers, or this channel's bot moderator roles.
  const manager = canManage(i.member);
  const organizer =
    manager || queues.some((q) => (q.adminRoles ?? []).some((id) => roles.includes(id)));
  return reply(
    helpText(queues, organizer, manager),
    manager ? `${env.APP_ORIGIN}/app/?community=${i.guild_id}` : undefined,
  );
}
