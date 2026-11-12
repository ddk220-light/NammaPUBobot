import type { Match } from '../domain/play';
import type { Player } from '../domain/rating';
import { duration, type ActionRow, type Button, type StringSelect } from './cards';
import { adminResultRow } from './admin-panel';
import { proposalSeconds } from '../domain/play';

export interface PlayerPanel {
  content: string;
  components: ActionRow[];
}

/** A private view of the saved match. The command handler remains the authority. */
export function substitutionPanel(match: Match, actorId: string, admin = false): PlayerPanel {
  if (!['check_in', 'playing'].includes(match.state))
    return { content: 'This match has ended. Join a queue for the next game.', components: [] };

  const prefix = `m:${match.id}:${match.version}`,
    player = match.roster.find((p) => p.id === actorId),
    captain = match.captains.includes(actorId),
    requests = new Set(match.subRequests ?? []),
    requestedPlayers = match.roster.filter((p) => requests.has(p.id)),
    components: ActionRow[] = [];
  const teamName = (id: string) => (match.teams[0].some((p) => p.id === id) ? 'Alpha' : 'Beta');
  const button = (kind: string, label: string, style = 2): Button => ({
    type: 2,
    style,
    label,
    custom_id: `${prefix}:${kind}`,
  });
  const select = (kind: string, placeholder: string, players: Player[]): ActionRow => {
    const menu: StringSelect = {
      type: 3,
      custom_id: `${prefix}:${kind}`,
      placeholder,
      min_values: 1,
      max_values: 1,
      options: players.map((p) => ({
        label: p.name,
        value: p.id,
        description: `${teamName(p.id)}${match.captains.includes(p.id) ? ' · Captain' : ''}`,
      })),
    };
    return { type: 1, components: [menu] };
  };

  let content = `**Substitutions · ${match.queue.name}**`;
  if (player) {
    const requested = requests.has(actorId);
    content += `\nYou’re on **${teamName(actorId)}**. ${requested ? 'Your sub request is open.' : 'Need to leave? Ask for a sub.'}\nYou keep your place until someone replaces you.`;
    components.push({
      type: 1,
      components: [
        button(
          requested ? 'cancel' : 'request',
          requested ? 'Cancel request' : 'Request a sub',
          requested ? 2 : 1,
        ),
        button('subauto', 'Replace me from queue'),
      ],
    });
  } else if (requestedPlayers.length) {
    content += `\nChoose who to replace. You’ll join their team${match.state === 'check_in' ? ' and need to check in' : ''}.`;
    components.push(select('take', 'Take a player’s place', requestedPlayers));
  } else {
    content += '\nNo one needs a sub right now.';
  }

  if (captain || admin) {
    const targets = match.roster.filter((p) => p.id !== actorId);
    if (targets.length) {
      content += `\nYou can also replace ${player ? 'another' : 'a'} player from the queue.`;
      components.push(select('auto', 'Replace a player from queue', targets));
    }
  }
  if (player || captain || admin)
    content +=
      match.state === 'check_in'
        ? '\nReplacing from the queue rebalances both teams during check-in.'
        : '\nThe substitute takes the same place. Everyone else keeps their team.';
  return { content, components };
}

const plain = (value: string) => value.replace(/[\\`*_~|>\[\]<@#:]/g, '\\$&');
const teamOf = (match: Match, id: string) =>
  match.teams[0].some((p) => p.id === id) ? 'Alpha' : 'Beta';

/**
 * The private panel behind Report result. Captains report, organizers get the
 * /admin result controls (owned by the clicker), everyone else learns who reports.
 */
export function reportPanel(match: Match, actorId: string, organizer: boolean): PlayerPanel {
  if (!['check_in', 'playing'].includes(match.state))
    return { content: 'This match has ended. The match card shows the result.', components: [] };
  const prefix = `m:${match.id}:${match.version}`,
    side = match.captains.indexOf(actorId),
    components: ActionRow[] = [];
  const button = (kind: string, label: string, style: number): Button => ({
    type: 2,
    style,
    label,
    custom_id: `${prefix}:${kind}`,
  });
  let content = `**Report result · ${match.queue.name}**`;
  if (side >= 0 && match.state === 'playing') {
    content += `\nYou’re ${side ? 'Beta' : 'Alpha'}’s captain. **We lost** records the result now. **We won** asks <@${match.captains[side ? 0 : 1]}> to confirm; it confirms automatically after ${duration(proposalSeconds)} unless they dispute it. **Draw** and **Cancel match** need both captains to agree.`;
    components.push({
      type: 1,
      components: [
        button('confirm_loss', 'We lost', 4),
        button('claim', 'We won', 3),
        button('draw', 'Draw', 2),
        button('abort', 'Cancel match', 2),
      ],
    });
  }
  if (organizer) {
    content += `\nAs an organizer you can ${components.length ? 'also ' : ''}record the result directly. You’ll confirm before anything changes.`;
    components.push(adminResultRow(actorId, match));
  }
  if (!components.length)
    content +=
      match.state === 'playing'
        ? `\nAlpha’s captain is <@${match.captains[0]}> and Beta’s is <@${match.captains[1]}>. One of them reports the result, or an organizer can.`
        : '\nReport the result after check-in finishes.';
  if (match.proposal && components.length)
    content += '\nA captain request is open; answer it on its card.';
  return { content, components };
}

/** Confirm Sub me before leaving a running match. */
export function substitutePrompt(
  match: Match,
  actorId: string,
  substitute?: { id: string; name: string },
): PlayerPanel {
  if (!['check_in', 'playing'].includes(match.state))
    return { content: 'This match has ended. Join a queue for the next game.', components: [] };
  if (!match.roster.some((p) => p.id === actorId))
    return {
      content:
        'Only players in this match can use Sub me. Join the queue to be picked as a substitute.',
      components: [],
    };
  if (!substitute)
    return {
      content:
        'No eligible substitute is waiting. Ask someone to join the queue or take your place.',
      components: [],
    };
  return {
    content: `Leave this match? ${plain(substitute.name)} will take your place on ${teamOf(match, actorId)}.`,
    components: [
      {
        type: 1,
        components: [
          {
            type: 2,
            style: 4,
            label: 'Leave match',
            custom_id: `m:${match.id}:${match.version}:confirm_sub`,
          },
        ],
      },
    ],
  };
}
