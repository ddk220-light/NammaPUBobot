import { lobbySummary, lobbySearchSummary } from '../domain/lobby';
import type { Queue } from '../domain/config';
import { proposalSeconds, type Match, type Proposal, type ProposalOutcome } from '../domain/play';
import { identity } from '../identity';

const mention = (id: string) => `<@${id}>`;
const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? '' : 's'}`;
/** "2 hours", "1 minute 30 seconds": the two largest nonzero units. */
export function duration(seconds: number) {
  const parts = [
    [Math.floor(seconds / 86400), 'day'],
    [Math.floor((seconds % 86400) / 3600), 'hour'],
    [Math.floor((seconds % 3600) / 60), 'minute'],
    [Math.floor(seconds % 60), 'second'],
  ] as const;
  const shown = parts.filter(([n]) => n > 0).slice(0, 2);
  return shown.length ? shown.map(([n, unit]) => plural(n, unit)).join(' ') : '0 seconds';
}
const side = (s: 'alpha' | 'beta' | 0 | 1) => (s === 'alpha' || s === 0 ? 'Alpha' : 'Beta');
const relative = (at: number) => `<t:${Math.floor(at / 1000)}:R>`;
/** Test and test-populated matches never notify their members. */
export const quietMatch = (m: Match) => !!m.silent || !!m.test;
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
  allowed_mentions: { parse: string[]; users?: string[]; roles?: string[] };
}
export function queueCard(queue: Queue, players: { id: string }[], held = false): Card {
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
            ? `Waiting places expire after ${duration(queue.controls.waitingSeconds)}. Join again to renew yours.`
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
  if (['check_in', 'playing'].includes(match.state))
    text += `\n\nCaptains: ${mention(match.captains[0])} (Alpha) and ${mention(match.captains[1])} (Beta). After the game, either captain presses Report result.`;
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
      style: 1,
      label: 'Report result',
      custom_id: `m:${match.id}:${match.version}:report`,
    });
    buttons.push({
      type: 2,
      style: 2,
      label: 'Sub me',
      custom_id: `m:${match.id}:${match.version}:subauto`,
    });
  } else if (match.state === 'finished') {
    title += match.test
      ? ' · completed'
      : ` · ${match.result === 'draw' ? 'draw' : `${match.result === 'alpha' ? 'Alpha' : 'Beta'} wins`}`;
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
  if (match.test) {
    title = `TEST · ${title}`;
    text = `**Test match · ${match.id}**\nResults and ratings are not recorded.\n\n${text}`;
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
      description: lobbySummary(match.lobby).slice(0, 4000),
      color: 0x648c78,
      footer: { text: 'Provider observations and declared facts · results reported by captains' },
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
        footer: { text: identity.publicName },
      },
      ...extra,
    ],
    components,
    allowed_mentions: { parse: [] },
  };
}

const proposalTitle = {
  draw: 'Draw requested',
  abort: 'Cancellation requested',
  win: 'Result reported',
};
const outcomes: Record<ProposalOutcome, (p: Proposal) => string> = {
  accepted: (p) =>
    p.kind === 'win'
      ? `${mention(p.opposing)} confirmed that ${side(p.side!)} won.`
      : `${mention(p.opposing)} agreed. ${p.kind === 'draw' ? 'The draw is recorded.' : 'The match is cancelled.'}`,
  auto: (p) =>
    `Nobody disputed within ${duration(proposalSeconds)}, so ${side(p.side!)}’s win was recorded.`,
  declined: (p) =>
    p.kind === 'win'
      ? `${mention(p.opposing)} disputed this result. An organizer can record the result from /admin or the website.`
      : `${mention(p.opposing)} declined. The match continues; report the result when it ends.`,
  expired: () =>
    `No answer within ${duration(proposalSeconds)}, so this request expired. The match continues.`,
  replaced: () => 'A newer request replaced this one. Answer that one instead.',
  ended: () => 'This request closed because the match changed. Check the match card.',
};
function proposalText(p: Proposal) {
  if (p.kind === 'win')
    return `${mention(p.requester)} says ${side(p.side!)} won. ${mention(p.opposing)}, confirm or dispute.${p.expiresAt ? ` This confirms automatically ${relative(p.expiresAt)}.` : ''}`;
  return `${mention(p.requester)} requested ${p.kind === 'draw' ? 'a draw' : 'to cancel this match'}.\n${mention(p.opposing)}, do you agree?${p.expiresAt ? `\nThis request expires ${relative(p.expiresAt)}.` : ''}`;
}
/** A captain request card, or (with an outcome) the same card showing how it ended. */
export function agreementCard(
  match: Match,
  proposal: Proposal = match.proposal!,
  outcome?: ProposalOutcome,
): Card {
  const p = proposal,
    prefix = `a:${match.id}:${match.version}:${p.id}`,
    notify = !outcome && !quietMatch(match);
  const buttons: Button[] =
    p.kind === 'win'
      ? [
          { type: 2, style: 3, label: 'Confirm', custom_id: `${prefix}:win` },
          { type: 2, style: 4, label: 'Dispute', custom_id: `${prefix}:decline` },
        ]
      : [
          {
            type: 2,
            style: 3,
            label: p.kind === 'draw' ? 'Agree to draw' : 'Agree to cancel',
            custom_id: `${prefix}:${p.kind}`,
          },
          { type: 2, style: 2, label: 'Decline', custom_id: `${prefix}:decline` },
        ];
  return {
    content: notify ? mention(p.opposing) : '',
    embeds: [
      {
        title: `${match.queue.name} · ${proposalTitle[p.kind]}${outcome ? ' · closed' : ''}`,
        description: outcome
          ? `${proposalText({ ...p, expiresAt: undefined })}\n\n**${outcomes[outcome](p)}**`
          : proposalText(p),
        color: 0x648c78,
        footer: { text: identity.publicName },
      },
    ],
    components: outcome ? [] : [{ type: 1, components: buttons }],
    allowed_mentions: notify ? { parse: [], users: [p.opposing] } : { parse: [] },
  };
}
const notice = (content: string, users: string[] = [], roles: string[] = []): Card => ({
  content,
  embeds: [],
  components: [],
  allowed_mentions: {
    parse: [],
    ...(users.length ? { users } : {}),
    ...(roles.length ? { roles } : {}),
  },
});
/** A short channel message that a result was recorded, withdrawn or corrected. Never pings. */
export function resultNotice(match: Match, withdrawn = false): Card {
  const name = `**${match.queue.name}**`;
  if (withdrawn)
    return notice(
      `${name}: the result was withdrawn and ratings were restored. An organizer can record the corrected result.`,
    );
  if (match.state === 'aborted')
    return notice(
      `${name}: ${match.note === 'Match timed out without a result.' ? 'the match timed out without a result' : 'match cancelled'}. No ratings changed.`,
    );
  const result = match.result === 'draw' ? 'draw' : `${side(match.result!)} won`;
  return notice(
    `${name}: ${match.generation ? `corrected result, ${result}` : result.replace(/^./, (c) => c.toUpperCase())}. Ratings are updated on the match card.`,
  );
}
export function substituteNotice(match: Match, incoming: string, outgoing: string): Card {
  const team = match.teams[0].some((p) => p.id === incoming) ? 'Alpha' : 'Beta';
  return notice(
    `${mention(incoming)}, you’re in for ${mention(outgoing)} on ${team}.${match.state === 'check_in' ? ' Check in on the match card.' : ''}`,
    quietMatch(match) ? [] : [incoming],
  );
}
export function disputeNotice(match: Match, roles: string[]): Card {
  return notice(
    `${roles.map((id) => `<@&${id}>`).join(' ')} A result on **${match.queue.name}** is disputed. Record it from /admin or the website.`,
    [],
    quietMatch(match) ? [] : roles,
  );
}
