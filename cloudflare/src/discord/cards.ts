import { lobbySummary, lobbySearchSummary } from '../domain/lobby';
import type { Queue } from '../domain/config';
import type { Match } from '../domain/play';

const mention = (id: string) => `<@${id}>`;
export interface Button {
  type: 2;
  style: number;
  label: string;
  custom_id?: string;
  url?: string;
  disabled?: boolean;
}
export interface StringSelect {
  type: 3;
  custom_id: string;
  placeholder?: string;
  options: { label: string; value: string; description?: string }[];
  min_values?: number;
  max_values?: number;
  disabled?: boolean;
}
export interface ActionRow {
  type: 1;
  components: (Button | StringSelect)[];
}
export interface Card {
  content: string;
  embeds: { title: string; description: string; color: number; footer?: { text: string } }[];
  components: ActionRow[];
  allowed_mentions: { parse: string[]; users?: string[] };
}
export function queueCard(
  queue: Queue,
  players: { id: string }[],
  held = false,
  voice: string[] | null = null,
): Card {
  const roster = players
    .slice(0, 16)
    .map((p) => mention(p.id))
    .join(' · ');
  const more = players.length > 16 ? `\n+${players.length - 16} more waiting` : '';
  return {
    content: '',
    embeds: [
      {
        title: `${queue.name} · ${players.length}/${queue.size}`,
        color: 0xb9e77c,
        description: [
          !queue.enabled ? 'This queue is paused.' : '',
          held ? "Click Join when you're ready to start again." : '',
          players.length ? `${roster}${more}` : 'No one’s waiting yet.',
          queue.controls?.waitingSeconds
            ? `Waiting places expire after ${queue.controls.waitingSeconds} seconds. Join again to renew yours.`
            : '',
          queue.enabled && queue.voicePreview && voice?.length
            ? `> *${voice.slice(0, 5).map(mention).join(' · ')}*`
            : '',
        ]
          .filter(Boolean)
          .join('\n\n'),
      },
    ],
    components: [
      {
        type: 1,
        components: [
          {
            type: 2,
            style: 3,
            label: 'Join',
            custom_id: `q:${queue.id}:join`,
            disabled: !queue.enabled,
          },
          { type: 2, style: 2, label: 'Leave', custom_id: `q:${queue.id}:leave` },
          ...(queue.voicePreview
            ? [
                {
                  type: 2 as const,
                  style: 2,
                  label: 'Ping',
                  custom_id: `q:${queue.id}:ping`,
                  disabled: !queue.enabled,
                },
              ]
            : []),
        ],
      },
    ],
    allowed_mentions: { parse: [] },
  };
}
export function queuePingCard(queue: Queue, users: string[], needed: number): Card {
  return {
    content: `${users.map(mention).join(' ')} — join ${queue.name} soon if you're up for a game! ${needed} more player${needed === 1 ? '' : 's'} needed.`,
    embeds: [],
    components: [
      {
        type: 1,
        components: [{ type: 2, style: 3, label: 'Join', custom_id: `q:${queue.id}:join` }],
      },
    ],
    allowed_mentions: { parse: [], users },
  };
}
export function matchCard(match: Match): Card {
  const changes = new Map(match.changes.map((change) => [change.userId, change]));
  const team = (index: 0 | 1) =>
    match.teams[index]
      .map((p) => {
        const change = match.state === 'finished' ? changes.get(p.id) : undefined;
        const rating = change
          ? `${change.after} (${change.after - change.before >= 0 ? '+' : ''}${change.after - change.before})`
          : String(p.rating);
        const ready =
          (match.state === 'check_in' && match.queue.checkIn.enabled) ||
          match.state === 'check_in_failed'
            ? `${match.ready.includes(p.id) ? '✓' : '○'} `
            : '';
        return `${ready}${mention(p.id)} · ${rating}${p.id === match.captains[index] ? ' · captain' : ''}`;
      })
      .join('\n');
  let text = `**Alpha**\n${team(0)}\n\n**Beta**\n${team(1)}`;
  if (['check_in', 'playing'].includes(match.state) && match.subRequests?.length)
    text += `\n\n**Looking for a substitute:** ${match.subRequests.map(mention).join(' · ')}`;
  let title = `${match.queue.name} · ${match.gameName}`;
  const buttons: Button[] = [];
  const components: ActionRow[] = [];
  if (match.state === 'check_in') {
    const checkingIn = match.queue.checkIn.enabled && match.ready.length < match.roster.length;
    title += checkingIn ? ' · check in' : ' · map selection';
    const status = match.queue.checkIn.enabled
      ? `**${match.ready.length}/${match.roster.length} ready**${match.deadline ? ` · closes <t:${Math.floor(match.deadline / 1000)}:R>` : ''}\n`
      : '';
    text = `${status}${match.mapRound?.state === 'open' ? (match.mapRound.settings.countsAsCheckIn ? 'Choose your maps to keep your place.' : match.mapRound.settings.mode === 'direct' ? 'Captains, choose the maps below.' : 'Choose the maps below.') : 'Check in to keep your place.'}\n\n${text}`;
    if (
      match.queue.checkIn.enabled &&
      !match.mapRound?.settings.countsAsCheckIn &&
      !(match.mapRound?.state === 'open' && match.ready.length === match.roster.length)
    )
      buttons.push({
        type: 2,
        style: 3,
        label: 'Check in',
        custom_id: `m:${match.id}:${match.version}:ready`,
      });
    buttons.push({
      type: 2,
      style: 2,
      label: 'Back out',
      custom_id: `m:${match.id}:${match.version}:back`,
    });
  } else if (match.state === 'playing') {
    title += ' · awaiting result';
    buttons.push({
      type: 2,
      style: 2,
      label: 'Sub me',
      custom_id: `m:${match.id}:${match.version}:subauto`,
    });
  } else if (match.state === 'finished') {
    title += ` · ${match.result === 'draw' ? 'draw' : `${match.result === 'alpha' ? 'Alpha' : 'Beta'} wins`}`;
  } else if (match.state === 'check_in_failed') {
    title += ' · check-in ended';
    text += '\n\nNot everyone checked in. ✓ Ready players are back in the queue.';
  } else if (match.state === 'undone') {
    title += ' · result withdrawn';
    text += '\n\nThe result and its rating changes were reversed. Awaiting a corrected result.';
  } else {
    title += ' · cancelled';
    text +=
      match.note === 'Match timed out without a result.'
        ? '\n\nNo result was reported before the time limit. No ratings changed.'
        : '\n\nNo ratings changed.';
  }
  if (match.note && match.state === 'check_in') text += `\n\n${match.note}`;
  if (match.mapRound) {
    const round = match.mapRound;
    if (round.state === 'resolved') text += `\n\n**Maps:** ${round.selected.join(' · ')}`;
    else if (['check_in', 'playing'].includes(match.state)) {
      text += `\n\n**${round.settings.mode === 'direct' ? 'Pick' : 'Vote for'} ${round.settings.count} map${round.settings.count === 1 ? '' : 's'}**${round.settings.mode === 'direct' ? '' : ` · ${Object.keys(round.ballots).length}/${match.roster.length} voted`} · closes <t:${Math.floor(round.closesAt / 1000)}:R>${round.settings.countsAsCheckIn ? '\nYour vote counts as check-in.' : ''}`;
      components.push({
        type: 1,
        components: [
          {
            type: 3,
            custom_id: `mv:${match.id}:${round.id}`,
            placeholder:
              round.settings.mode === 'direct' ? 'Captain: select maps' : 'Choose your maps',
            min_values: round.settings.mode === 'direct' ? round.settings.count : 1,
            max_values:
              round.settings.mode === 'direct'
                ? round.settings.count
                : round.settings.voting === 'plurality'
                  ? 1
                  : round.choices.length,
            options: round.choices.map((name, index) => ({ label: name, value: String(index) })),
          },
        ],
      });
      if (round.note) text += `\n${round.note}`;
    }
  }
  const stories = ['check_in', 'playing'].includes(match.state)
    ? match.storiesBefore
    : match.state === 'finished'
      ? match.storiesAfter
      : [];
  const extra: Card['embeds'] = [];
  if (stories?.length)
    extra.push({
      title: match.state === 'finished' ? '⚔️ Final Tale of the Tape' : '⚔️ Tale of the Tape',
      description: stories.join('\n\n').slice(0, 4000),
      color: 0xe67e22,
      footer: { text: 'Last 90 days · just for fun' },
    });
  if (match.lobby)
    extra.push({
      title: `${match.gameName} lobby`,
      description: lobbySummary(match.lobby),
      color: 0x648c78,
      footer: { text: 'Public game evidence · results reported by players' },
    });
  else if (match.lobbySearch && match.state === 'playing')
    extra.push({
      title: `${match.gameName} lobby`,
      description: lobbySearchSummary(match.lobbySearch),
      color: 0x648c78,
      footer: { text: 'Results reported by captains' },
    });
  if (buttons.length) components.push({ type: 1, components: buttons });
  return {
    content: '',
    embeds: [
      {
        title,
        description: text,
        color: match.state === 'finished' ? 0xb9e77c : 0x648c78,
        footer: { text: 'Community Games' },
      },
      ...extra,
    ],
    components,
    allowed_mentions: { parse: [] },
  };
}

export function agreementCard(match: Match): Card {
  const proposal = match.proposal!;
  const draw = proposal.kind === 'draw';
  return {
    content: mention(proposal.opposing),
    embeds: [
      {
        title: `${match.queue.name} · ${draw ? 'Draw requested' : 'Cancellation requested'}`,
        description: `${mention(proposal.requester)} requested ${draw ? 'a draw' : 'to cancel this match'}.\n${mention(proposal.opposing)}, do you agree?`,
        color: 0x648c78,
        footer: { text: 'Community Games' },
      },
    ],
    components: [
      {
        type: 1,
        components: [
          {
            type: 2,
            style: 3,
            label: draw ? 'Agree to draw' : 'Agree to cancel',
            custom_id: `a:${match.id}:${match.version}:${proposal.id}:${proposal.kind}`,
          },
        ],
      },
    ],
    allowed_mentions: { parse: [], users: [proposal.opposing] },
  };
}
