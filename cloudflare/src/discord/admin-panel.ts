import type { AdminRequest, AdminView } from '../domain/admin';
import type { Match } from '../domain/play';
import type { ActionRow, Button, Card } from './cards';

const text = (value: string) => value.replace(/[\\`*_~|>\[\]]/g, '\\$&');
const status = (m: Match) =>
  m.state === 'finished'
    ? m.result === 'draw'
      ? 'Draw'
      : m.result
        ? `${m.result === 'alpha' ? 'Alpha' : 'Beta'} won`
        : 'Result unavailable'
    : {
        check_in: 'Checking in',
        playing: 'Awaiting result',
        aborted: 'Cancelled',
        check_in_failed: 'Check-in failed',
        undone: 'Result undone',
      }[m.state];
const matchLine = (m: Match) =>
  `\`${m.id}\` · **${status(m)}** · <t:${Math.floor((m.closedAt ?? m.createdAt) / 1000)}:d>`;
const teams = (m: Match) =>
  m.teams
    .map(
      (team, side) =>
        `**${side ? 'Beta' : 'Alpha'}:** ${team.map((p) => `<@${p.id}>`).join(' · ')}`,
    )
    .join('\n');
const id = (owner: string, op: string, target: string, version = 0, confirm = false) =>
  `adm:${owner}:${op}:${target}:${version}${confirm ? ':yes' : ''}`;
const button = (
  owner: string,
  op: string,
  target: string,
  version: number,
  label: string,
  disabled = false,
  style = 2,
  confirm = false,
): Button => ({
  type: 2,
  style,
  label,
  custom_id: id(owner, op, target, version, confirm),
  disabled,
});

export function adminPanelCard(
  view: AdminView,
  request: AdminRequest,
  settingsUrl: string,
  confirmation?: string,
): Card {
  const q = view.queue,
    m = view.selected,
    owner = request.actor.id;
  const refresh = () => button(owner, m ? 'match' : 'view', m?.id ?? q.id, 0, 'Refresh panel');
  if (confirmation) {
    const labels: Record<string, string> = {
      clear: `Clear all ${view.waiting} waiting players from ${q.name}?`,
      alpha_loss: 'Record Alpha’s loss (Beta wins)?',
      beta_loss: 'Record Beta’s loss (Alpha wins)?',
      draw: 'Record a draw?',
      cancel: 'Cancel this match without changing ratings?',
      close_betting: 'Close betting for this match?',
    };
    const target = confirmation === 'clear' ? q.id : m!.id;
    const version = confirmation === 'clear' ? view.queueVersion : m!.version;
    return {
      content: '',
      allowed_mentions: { parse: [] },
      embeds: [
        {
          title: 'Confirm admin action',
          color: 0xe4a44a,
          description: `**${text(labels[confirmation])}**\n${confirmation === 'clear' ? 'Active matches are unaffected.' : `${matchLine(m!)}\n${teams(m!)}\n${['alpha_loss', 'beta_loss', 'draw'].includes(confirmation) ? 'This updates ratings, match history and applicable gold/betting settlements.' : confirmation === 'cancel' ? 'Open predictions are refunded.' : 'Players cannot place or cancel bets after closure.'}`}`,
        },
      ],
      components: [
        {
          type: 1,
          components: [
            button(owner, confirmation, target, version, 'Confirm', false, 4, true),
            { ...refresh(), label: 'Back' },
          ],
        },
      ],
    };
  }
  const components: ActionRow[] = [];
  if (view.queues.length > 1)
    components.push({
      type: 1,
      components: [
        {
          type: 3,
          custom_id: `adm:${owner}:selectq`,
          placeholder: 'Choose a queue',
          min_values: 1,
          max_values: 1,
          options: view.queues.slice(0, 25).map((q) => ({ label: q.name, value: q.id })),
        },
      ],
    });
  if (view.active.length > 1)
    components.push({
      type: 1,
      components: [
        {
          type: 3,
          custom_id: `adm:${owner}:selectm`,
          placeholder: 'Choose an ongoing match',
          min_values: 1,
          max_values: 1,
          options: view.active.map((m) => ({ label: `${m.id} · ${status(m)}`, value: m.id })),
        },
      ],
    });
  components.push({
    type: 1,
    components: [
      button(
        owner,
        'start',
        q.id,
        view.queueVersion,
        'Start queue',
        !q.enabled || view.waiting < q.size,
        3,
      ),
      button(owner, 'queue', q.id, view.queueVersion, 'Refresh queue card'),
      button(owner, 'clear', q.id, view.queueVersion, 'Clear queue', !view.waiting, 4),
      refresh(),
      ...(request.manager
        ? [{ type: 2 as const, style: 5, label: 'Settings', url: settingsUrl }]
        : []),
    ],
  });
  if (m && ['check_in', 'playing'].includes(m.state)) {
    components.push({
      type: 1,
      components: [
        button(owner, 'alpha_loss', m.id, m.version, 'Alpha lost', m.state !== 'playing', 4),
        button(owner, 'beta_loss', m.id, m.version, 'Beta lost', m.state !== 'playing', 4),
        button(owner, 'draw', m.id, m.version, 'Draw', m.state !== 'playing'),
        button(owner, 'cancel', m.id, m.version, 'Cancel match', false, 4),
      ],
    });
    components.push({
      type: 1,
      components: [
        button(owner, 'recover', m.id, m.version, 'Refresh match card'),
        ...(m.queue.modules.predictions
          ? [button(owner, 'close_betting', m.id, m.version, 'Close betting')]
          : []),
      ],
    });
  }
  return {
    content: '',
    allowed_mentions: { parse: [] },
    components,
    embeds: [
      {
        title: `${q.name} · Admin`,
        color: 0xb9e77c,
        description: [
          `**Queue:** ${view.waiting}/${q.size} waiting · ${!q.enabled ? 'Paused' : view.held ? 'Waiting for a fresh join' : 'Open'}`,
          `Automatic start: ${(q.autoStart ?? true) ? 'on' : 'off'} · Check-in: ${q.checkIn.enabled ? `${q.checkIn.seconds}s` : 'off'}`,
          `**Latest match**\n${view.latest ? matchLine(view.latest) : 'No matches yet.'}`,
          ...(view.queues.length > 25
            ? ['Use `/admin queue:name` to select queues beyond this menu.']
            : []),
        ].join('\n\n'),
      },
      ...(m
        ? [
            {
              title: 'Selected match',
              color: 0x729bd3,
              description: `${matchLine(m)}\n${teams(m)}`,
            },
          ]
        : []),
      {
        title: 'Last 5 completed matches',
        color: 0x729bd3,
        description: view.recent.map(matchLine).join('\n') || 'No completed matches yet.',
      },
    ],
  };
}
