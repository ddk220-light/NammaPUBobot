import type { Match } from '../domain/play';
import type { Player } from '../domain/rating';
import type { ActionRow, Button, StringSelect } from './cards';

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
  if (player || captain || admin) content += '\nReplacing from the queue rebalances both teams.';
  return { content, components };
}
