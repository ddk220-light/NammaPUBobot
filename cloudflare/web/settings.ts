import { connectorSettings, type ConnectorDiagnostics } from './connectors';
import type { DiscordSetup, GameState } from '../src/domain/setup';
import { capabilities } from '../src/domain/setup';
import { defaultRatingSettings } from '../src/domain/rating';
import { defaultRecords } from '../src/domain/records';
import { defaultMaps, aoe2Maps } from '../src/domain/maps';
import { gameCapabilities, selectableCatalog } from '../src/games/catalog';
import type { Queue } from '../src/domain/config';
import { SettingsDraft } from './draft';
import { esc } from './ui';

export const categories = {
  basics: 'Basics',
  access: 'Access',
  preparation: 'Match Preparation',
  maps: 'Maps',
  engagement: 'Engagement',
  voice: 'Voice / Rally',
};
export type Category = keyof typeof categories;
export interface EditorSetup extends DiscordSetup {
  gameState: GameState[];
}

type Choice = { id: string; name: string; disabled?: boolean };
const choices = (values: string[] | Record<string, string>): Choice[] =>
  Array.isArray(values)
    ? values.map((id) => ({ id, name: id }))
    : Object.entries(values).map(([id, name]) => ({ id, name }));
let root = '';
function row(name: string, label: string, input: string, help: string, fallback?: string) {
  help ||= `Configure ${label.toLowerCase()} for your community.`;
  return `<div class="setting-row" data-setting data-search="${esc(`${label} ${help}`.toLowerCase())}"><div class="setting-copy"><div class="setting-title"><label for="${root}-${name}" class="setting-label">${esc(label)}</label>${help ? `<button type="button" class="icon-button" data-help="${esc(help)}" data-help-title="${esc(label)}" aria-label="More about ${esc(label)}">ⓘ</button>` : ''}${fallback !== undefined ? `<button type="button" class="icon-button" data-reset="${esc(name)}" data-default="${esc(fallback)}" aria-label="Reset ${esc(label)}">↺</button>` : ''}</div>${help ? `<p class="form-help">${esc(help)}</p>` : ''}</div><div class="setting-control">${input}</div></div>`;
}

const fieldDefaults: Record<string, string> = {
  enabled: 'Off for an advanced queue; on in the first-queue guide',
  size: '8 players',
  autoStart: 'On',
  isDefault: 'Off',
  keepQueueVisible: 'On',
  checkIn: 'On',
  teamMethod: 'Captain-based balance',
  requiredRole: 'No role restriction',
  blockedRole: 'No role restriction',
  voicePreview: 'Off',
  rallyEnabled: 'Off',
  taxEnabled: 'Off',
  visibility: defaultRecords.visibility,
  minimumMatches: String(defaultRecords.minimumMatches),
  recentDays: String(defaultRecords.recentDays),
  weeklyRating: String(defaultRecords.inactivity.weeklyRating),
  weeklyDeviation: String(defaultRecords.inactivity.weeklyDeviation),
  decay: defaultRecords.inactivity.enabled ? 'On' : 'Off',
  decorateNickname: 'Off',
  lobbyConnector: 'Off',
  autoLobby: 'Off',
  ratingEngine: 'AoE2-style Elo for a new game; later queues inherit the game’s engine',
  winStreakBoost: defaultRatingSettings.winStreakBoost ? 'On' : 'Off',
  lossStreakBoost: defaultRatingSettings.lossStreakBoost ? 'On' : 'Off',
  mapMode: defaultMaps().mode,
  mapCount: String(defaultMaps().count),
  mapShortlist: String(defaultMaps().shortlist),
  mapVoting: defaultMaps().voting,
  mapTie: defaultMaps().tie,
  mapNoVotes: defaultMaps().noVotes,
  mapSeconds: String(defaultMaps().seconds),
  mapRecent: String(defaultMaps().recentAvoid),
  mapCheckIn: defaultMaps().countsAsCheckIn ? 'On' : 'Off',
};
export function settingHelp(row: HTMLElement) {
  const input = row.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
    '[name]',
  )!;
  const fallback =
    row.querySelector<HTMLElement>('[data-default]')?.dataset.default ??
    fieldDefaults[input.name] ??
    (input.name.startsWith('module-') ? 'Off' : 'Choose a value; no universal default');
  const number = input instanceof HTMLInputElement && input.type === 'number';
  const allowed =
    input instanceof HTMLSelectElement
      ? [...input.options]
          .filter((o) => !o.disabled)
          .map((o) => o.textContent)
          .join(', ')
      : input instanceof HTMLInputElement && input.type === 'checkbox'
        ? input.disabled
          ? 'Unavailable for this game or deployment'
          : 'On or off'
        : number
          ? `${input instanceof HTMLInputElement && input.step === 'any' ? 'Numbers, including decimals' : 'Whole numbers'}${input.min ? ` from ${input.min}` : ''}${input.max ? ` to ${input.max}` : ''}. Server constraints also apply.`
          : `${input.required ? 'Required' : 'Optional'} text${input.maxLength > 0 ? `, up to ${input.maxLength} characters` : ''}.`;
  const scope = row.closest<HTMLFormElement>('form')?.dataset.config?.startsWith('queue:')
    ? 'This queue after saving; existing matches keep their captured settings.'
    : 'This game across all its queues after saving. Stored results are not recalculated.';
  return `<p>${esc(row.querySelector<HTMLElement>('[data-help]')?.dataset.help ?? '')}</p><dl class="help-facts"><div><dt>Default</dt><dd>${esc(fallback)}</dd></div><div><dt>Allowed</dt><dd>${esc(allowed)}</dd></div><div><dt>Applies to</dt><dd>${esc(scope)}</dd></div></dl>`;
}
export function settingReadouts(root: ParentNode) {
  root
    .querySelectorAll<HTMLInputElement>('form[data-config] input[type="number"]')
    .forEach((input) => {
      const durations = [
        'seconds',
        'reuseSeconds',
        'waitingSeconds',
        'matchSeconds',
        'mapSeconds',
        'rallyCooldown',
      ];
      if (!durations.includes(input.name) && !['size', 'taxMinuteUtc'].includes(input.name)) return;
      const row = input.closest('.setting-control');
      if (!row) return;
      let output = row.querySelector<HTMLElement>('[data-readout]');
      if (!output) {
        output = document.createElement('p');
        output.className = 'field-readout';
        output.dataset.readout = '';
        row.append(output);
      }
      const n = Number(input.value);
      output.textContent =
        !input.value || !Number.isSafeInteger(n) || n < 0
          ? 'Enter a valid whole number.'
          : input.name === 'size'
            ? n > 16
              ? 'Up to 16 players can play in one match.'
              : n < 2
                ? 'At least 2 players required.'
                : `${Math.ceil(n / 2)} vs ${Math.floor(n / 2)} · automatic starts use an even roster`
            : input.name === 'taxMinuteUtc'
              ? n <= 1439
                ? `Thursday ${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')} UTC`
                : 'Choose 0–1439 minutes after midnight UTC.'
              : input.name === 'matchSeconds' && (n === 0 || n >= 43200)
                ? 'Effective maximum: 12 hours'
                : n === 0
                  ? input.name === 'waitingSeconds'
                    ? 'No waiting expiry'
                    : input.name === 'reuseSeconds'
                      ? 'Ready reuse off'
                      : 'Check the allowed range'
                  : [
                      Math.floor(n / 3600) ? `${Math.floor(n / 3600)} h` : '',
                      Math.floor((n % 3600) / 60) ? `${Math.floor((n % 3600) / 60)} min` : '',
                      n % 60 ? `${n % 60} s` : '',
                    ]
                      .filter(Boolean)
                      .join(' ');
    });
}
function field(
  name: string,
  label: string,
  current: string | number,
  help = '',
  options: {
    type?: string;
    min?: number;
    max?: number;
    step?: number | 'any';
    fallback?: string | number;
    path?: string;
    required?: boolean;
  } = {},
) {
  const id = `${root}-${name}`;
  const attributes = `name="${name}" id="${id}" aria-label="${esc(label)}" data-issue-path="${root}.${options.path ?? name}" ${options.required === false ? '' : 'required'} ${options.min !== undefined ? `min="${options.min}"` : ''} ${options.max !== undefined ? `${typeof current === 'number' ? 'max' : 'maxlength'}="${options.max}"` : ''}`;
  const input =
    options.type === 'textarea'
      ? `<textarea ${attributes} rows="5">${esc(current)}</textarea>`
      : `<input ${attributes} type="${options.type ?? (typeof current === 'number' ? 'number' : 'text')}" value="${esc(current)}" ${typeof current === 'number' ? `step="${options.step ?? 1}"` : ''} />`;
  return row(
    name,
    label,
    input,
    help,
    options.fallback === undefined ? undefined : String(options.fallback),
  );
}
function toggle(name: string, label: string, current: boolean, help = '', unavailable = false) {
  return row(
    name,
    label,
    `<label class="switch-control"><input type="checkbox" id="${root}-${name}" name="${name}" aria-label="${esc(label)}" ${current ? 'checked' : ''} ${unavailable && !current ? 'disabled data-unavailable="true"' : ''} /><span aria-hidden="true" class="switch-track"></span><span class="switch-text">${current ? 'On' : 'Off'}</span></label>`,
    help,
  );
}
function select(
  name: string,
  label: string,
  current: string,
  items: Choice[],
  help = '',
  locked = false,
) {
  const available = [...items];
  if (current && !available.some((v) => v.id === current))
    available.push({ id: current, name: `Unavailable selection · ${current}` });
  return row(
    name,
    label,
    `<select id="${root}-${name}" name="${name}" aria-label="${esc(label)}" data-issue-path="${root}.${name}" ${locked ? 'disabled data-unavailable="true"' : ''}>${available.map((v) => `<option value="${esc(v.id)}" ${v.id === current ? 'selected' : ''} ${v.disabled && v.id !== current ? 'disabled' : ''}>${esc(v.name)}</option>`).join('')}</select>`,
    help,
  );
}
function multi(name: string, label: string, selected: string[], items: Choice[], help: string) {
  const available = [
    ...items,
    ...selected
      .filter((id) => !items.some((v) => v.id === id))
      .map((id) => ({ id, name: `Unavailable · ${id}` })),
  ];
  return `<fieldset class="choice-list"><legend>${esc(label)}</legend><p class="form-help">${esc(help)}</p>${available.map((v) => `<label class="choice"><input type="checkbox" name="${name}" value="${esc(v.id)}" ${selected.includes(v.id) ? 'checked' : ''} /><span>${esc(v.name)}</span></label>`).join('') || '<p class="muted">No available choices. Check Discord access and refresh.</p>'}</fieldset>`;
}
function section(id: Category, body: string) {
  return `<section data-category-panel="${id}" class="settings-category"><h3>${categories[id]}</h3>${body}</section>`;
}

export function queueEditor(
  draft: SettingsDraft,
  setup: EditorSetup,
  queueId: string,
  overview = '',
) {
  const s = draft.working;
  const queues = draft.materialize().queues;
  const q = queues.find((q) => q.id === queueId) ?? queues[0];
  let html = `<div class="page-heading queue-editor-heading"><div><p class="eyebrow">Organize your next game</p><h1>Queues</h1><p class="view-description">Choose where players gather and how their matches work.</p></div><button type="button" class="primary" data-edit="add-queue" ${!s.games.length || !setup.channels.length ? 'disabled data-unavailable="true"' : ''}>Add queue</button></div>${overview}`;
  if (!q)
    return (
      html +
      '<section class="panel empty-state"><h2>Your first queue starts here</h2><p>Add a game and verify Discord access in Setup, then create a queue.</p><button type="button" data-view="setup">Open setup</button></section>'
    );
  root = `queues.${queues.indexOf(q)}`;
  const game = s.games.find((g) => g.id === q.gameId);
  const channel = setup.channels.find((c) => c.id === q.channelId);
  const cachedChannel = setup.knownChannels?.find((c) => c.id === q.channelId);
  const channelContext = channel
    ? `#${channel.name}`
    : cachedChannel
      ? `#${cachedChannel.name} · Not verified`
      : 'Channel not verified';
  html = html.replace(
    'Choose where players gather and how their matches work.',
    `${esc(game?.name ?? 'Unavailable game')} · ${esc(channelContext)}`,
  );
  const maps = q.maps ?? defaultMaps(game?.catalogId === 'aoe2' ? aoe2Maps : []);
  if (!q.maps && !maps.pool.length) maps.shortlist = 1;
  const roles = [{ id: '', name: 'No role restriction' }, ...setup.moderatorRoles];
  html += `<h2 class="sr-only">Queue settings</h2><div class="editor-toolbar"><label>Queue<select data-queue-select>${s.queues.map((v) => `<option value="${v.id}" ${v.id === q.id ? 'selected' : ''}>${esc(v.name)}</option>`).join('')}</select></label><label>Search settings<input type="search" data-settings-search placeholder="Name, permission, map…" /></label></div><div class="queue-editor-layout"><nav class="category-strip" aria-label="Queue settings categories">${Object.entries(
    categories,
  )
    .map(
      ([key, name]) =>
        `<button type="button" data-category="${key}">${name}<span class="dirty-dot" hidden aria-label="Unsaved changes"></span></button>`,
    )
    .join(
      '',
    )}</nav><form data-config="queue:${q.id}" class="settings-editor" novalidate><p class="notice" data-search-empty hidden>No settings match your search.</p>`;
  html += section(
    'basics',
    field('name', 'Queue name', q.name, 'The name players choose in Discord.', { max: 40 }) +
      select(
        'gameId',
        'Game',
        q.gameId,
        s.games,
        'Ratings and gold are shared by queues for the same game.',
      ) +
      select(
        'channelId',
        'Discord channel',
        q.channelId,
        [
          ...setup.channels.map((c) => ({
            id: c.id,
            name: `#${c.name}${c.permissions.some((p) => !p.granted) ? ' — missing permissions' : ''}`,
            disabled: c.permissions.some((p) => !p.granted),
          })),
          ...(setup.knownChannels ?? [])
            .filter((c) => !setup.channels.some((v) => v.id === c.id))
            .map((c) => ({ id: c.id, name: `#${c.name} · Not verified`, disabled: true })),
        ],
        `Enabled queues need View Channel, Send Messages, Embed Links and Read Message History.${!channel && cachedChannel?.observedAt ? ` Last seen ${new Date(cachedChannel.observedAt).toLocaleString()}; cached names do not verify access.` : ''}`,
      ) +
      field(
        'size',
        'Players',
        q.size,
        'The match starts at capacity when automatic start is enabled.',
        { min: 2 },
      ) +
      toggle(
        'enabled',
        'Enable queue',
        q.enabled,
        'Save your changes to enable or pause joining in Discord.',
      ) +
      toggle(
        'autoStart',
        'Start automatically',
        q.autoStart ?? true,
        'Start preparation as soon as the queue reaches capacity.',
      ) +
      toggle(
        'isDefault',
        'Default queue',
        q.isDefault ?? false,
        'Choose at most one default queue in each channel.',
      ) +
      field(
        'aliases',
        'Queue aliases',
        q.aliases?.join(', ') ?? '',
        'Comma-separated alternative names.',
        { required: false },
      ) +
      toggle(
        'keepQueueVisible',
        'Keep queue card visible',
        q.keepQueueVisible ?? true,
        'Every 15 minutes, move the card if it is outside the channel’s last five messages. Player updates still refresh it.',
      ),
  );
  html += section(
    'access',
    select(
      'requiredRole',
      'Required role',
      q.controls?.requiredRole ?? '',
      roles,
      'Only members with this role may join, if selected.',
    ) +
      select(
        'blockedRole',
        'Blocked role',
        q.controls?.blockedRole ?? '',
        roles,
        'Members with this role cannot join, if selected.',
      ) +
      multi(
        'adminRoles',
        'Bot moderator roles',
        q.adminRoles ?? [],
        setup.moderatorRoles,
        'Additional roles can clear queues, cancel matches, close betting and report results in Discord. These roles do not grant website access. Server managers already have access.',
      ),
  );
  html += section(
    'preparation',
    select(
      'teamMethod',
      'Team formation',
      q.teamMethod,
      choices({
        captain: 'Captain-based balance',
        balanced: 'Automatic balance',
        random: 'Random teams',
      }),
    ) +
      toggle(
        'checkIn',
        'Require check-in',
        q.checkIn.enabled,
        'Ask each player to confirm before the match starts.',
      ) +
      field(
        'seconds',
        'Check-in time limit (seconds)',
        q.checkIn.seconds,
        'Applies to newly created matches.',
        { min: 1, fallback: 60, path: 'checkIn.seconds' },
      ) +
      field(
        'reuseSeconds',
        'Ready validity (seconds)',
        q.checkIn.reuseSeconds ?? 900,
        'Reuse a recent ready confirmation. Zero disables reuse.',
        { min: 0, max: 900, fallback: 900, path: 'checkIn.reuseSeconds' },
      ) +
      field(
        'waitingSeconds',
        'Waiting expiry (seconds)',
        q.controls?.waitingSeconds ?? 0,
        'Zero keeps players queued until they leave.',
        { min: 0, max: 1209600, fallback: 0, path: 'controls.waitingSeconds' },
      ) +
      field(
        'matchSeconds',
        'Match timeout (seconds)',
        q.controls?.matchSeconds ?? 43200,
        'Maximum match lifetime is 12 hours. Existing legacy values are preserved until changed.',
        { min: 0, max: 1209600, fallback: 43200, path: 'controls.matchSeconds' },
      ) +
      `<div class="shared-setting"><h4>Shared rating settings</h4><p>${esc(game?.name ?? 'This game')} uses ${esc(q.ratingEngine)} ratings. Changes apply to every queue for this game.</p><button type="button" data-game-link="${q.gameId}">Edit game ratings</button></div>`,
  );
  html += section(
    'maps',
    toggle(
      'module-maps',
      'Enable map selection',
      q.modules.maps,
      'Keep your map pool when this feature is turned off.',
    ) +
      field(
        'mapPool',
        'Map pool',
        maps.pool.join('\n'),
        'One map per line. Saving map options requires a pool, even when map selection is off. Custom games can leave maps unconfigured.',
        { type: 'textarea', required: false, path: 'maps.pool' },
      ) +
      select(
        'mapMode',
        'Selection method',
        maps.mode,
        choices({ vote: 'Player voting', random: 'Random selection', direct: 'Captain picks' }),
      ) +
      field(
        'mapCount',
        'Maps to play',
        maps.count,
        'Maps belong to one ranked match, not separately rated rounds.',
        { min: 1, max: 5, path: 'maps.count' },
      ) +
      field('mapShortlist', 'Shortlist size', maps.shortlist, '', {
        min: 1,
        max: 25,
        path: 'maps.shortlist',
      }) +
      select(
        'mapVoting',
        'Voting',
        maps.voting,
        choices({ plurality: 'One choice', approval: 'Approve multiple maps' }),
      ) +
      select('mapTie', 'Ties', maps.tie, choices(['random', 'alphabetical'])) +
      select('mapNoVotes', 'No votes', maps.noVotes, choices(['random', 'cancel'])) +
      field('mapSeconds', 'Selection time (seconds)', maps.seconds, '', {
        min: 10,
        max: 3600,
        path: 'maps.seconds',
      }) +
      field('mapRecent', 'Recent matches to avoid', maps.recentAvoid, '', {
        min: 0,
        max: 20,
        path: 'maps.recentAvoid',
      }) +
      toggle(
        'mapCheckIn',
        'Map vote also checks in',
        maps.countsAsCheckIn,
        'Requires player voting. Uses the map selection time limit.',
      ),
  );
  html += section(
    'engagement',
    Object.entries({
      gold: ['Gold', 'Give players a shared game wallet.'],
      predictions: ['Predictions', 'Requires gold. Let players predict match results.'],
      quiz: ['Quiz', 'Not available in Cloudflare yet.'],
      civs: ['Civilization picking', 'Available for Age of Empires II queues.'],
      stories: ['Match stories', 'Post match summaries and milestones.'],
    })
      .map(([key, [label, help]]) =>
        toggle(
          `module-${key}`,
          label,
          q.modules[key as keyof typeof q.modules],
          help,
          !capabilities[key as keyof typeof capabilities] ||
            (key === 'predictions' && !q.modules.gold) ||
            (key === 'civs' && game?.catalogId !== 'aoe2'),
        ),
      )
      .join('') +
      toggle(
        'taxEnabled',
        'Enable weekly gold tax for this queue',
        q.economy?.taxEnabled ?? false,
        'Thursday tax takes 10% for qualifying inactivity, never below 500 gold. Its UTC schedule is shared in Game settings.',
      ),
  );
  html += section(
    'voice',
    toggle(
      'voicePreview',
      'Refresh voice availability',
      q.voicePreview ?? false,
      'Keep eligible voice participants available for Ping using a shared connection that refreshes every five minutes. Queue cards show the waiting roster. No audio is processed.',
    ) +
      toggle(
        'rallyEnabled',
        'Enable rally',
        q.rally?.enabled ?? false,
        'Invite eligible voice participants. The shared connection stops an hour after the last request if voice availability refresh is off.',
      ) +
      multi(
        'rallyChannels',
        'Allowed voice channels',
        q.rally?.channels ?? [],
        setup.voiceChannels,
        'Leave empty to use the caller’s channel.',
      ) +
      multi(
        'rallyRoles',
        'Additional rally roles',
        q.rally?.roles ?? [],
        setup.moderatorRoles,
        'Roles allowed to request a rally.',
      ) +
      field('rallyCooldown', 'Cooldown (seconds)', q.rally?.cooldownSeconds ?? 300, '', {
        min: 60,
        max: 3600,
        fallback: 300,
        path: 'rally.cooldownSeconds',
      }) +
      field('rallyMentions', 'Maximum mentions', q.rally?.mentionLimit ?? 8, '', {
        min: 1,
        max: 8,
        fallback: 8,
        path: 'rally.mentionLimit',
      }),
  );
  return (
    html +
    `</form></div><div class="actions queue-delete"><button type="button" class="danger" data-edit="remove-queue" data-id="${q.id}">Remove queue</button></div>`
  );
}

export function gameEditors(
  draft: SettingsDraft,
  setup: EditorSetup,
  diagnostics: ConnectorDiagnostics | null = null,
) {
  const queues = draft.materialize().queues;
  return `<section class="panel"><div class="section-heading"><h2>Games</h2><span class="muted">Shared ratings, ranks and records</span></div>${draft.working.games
    .map((g, index) => {
      root = `games.${index}`;
      const p = g.records ?? defaultRecords;
      const q = queues.find((q) => q.gameId === g.id);
      const rating = q?.ratingSettings ?? defaultRatingSettings;
      const state = setup.gameState.find((state) => state.id === g.id);
      const locked = Boolean(state?.ratingEngineLocked);
      const engineChanged =
        locked && state?.ratingEngine && q && q.ratingEngine !== state.ratingEngine;
      const rankRoles = [
        { id: '', name: 'Label only — no Discord role' },
        ...setup.roles.map((r) => ({
          id: r.id,
          name: `${r.name}${r.reason ? ` — ${r.reason}` : ''}`,
          disabled: !r.rankEligible,
        })),
      ];
      return (
        `<details class="game-settings" id="game-settings-${g.id}"><summary><span class="game-tile-icon" aria-hidden="true">${esc(g.catalogId ? (g.catalogId === 'aoe2' ? 'II' : g.catalogId === 'aoe3' ? 'III' : g.catalogId === 'aoe4' ? 'IV' : g.name.slice(0, 2).toUpperCase()) : '◇')}</span><span class="game-tile-copy"><strong>${esc(g.name)}</strong><span class="record-meta">${queues.filter((q) => q.gameId === g.id).length} queues · Shared ratings, ranks and leaderboard</span></span><span class="game-tile-action">Game settings</span></summary><form data-config="game:${g.id}" novalidate>` +
        field('name', 'Game name', g.name, '', { max: 80 }) +
        connectorSettings(
          g,
          draft.saved.communityId,
          diagnostics,
          draft.saved.settings.games.some((saved) => saved.id === g.id),
        ) +
        select(
          'visibility',
          'Statistics visibility',
          p.visibility,
          choices({ public: 'Public', members: 'Server members', admins: 'Administrators' }),
        ) +
        toggle(
          'searchPublishing',
          'Publish this leaderboard to search engines',
          Boolean(p.searchPublishing),
          'Requires public statistics and a public community display name. Existing link-public pages stay unlisted until you enable this.',
        ) +
        field('minimumMatches', 'Minimum leaderboard matches', p.minimumMatches, '', {
          min: 0,
          max: 10000,
          path: 'records.minimumMatches',
        }) +
        field(
          'recentDays',
          'Leaderboard recency (days)',
          p.recentDays,
          'Zero disables the recency requirement.',
          { min: 0, max: 3650, path: 'records.recentDays' },
        ) +
        toggle(
          'decay',
          'Weekly inactivity decay',
          p.inactivity.enabled,
          'After 30 days without ranked play, decay runs Mondays in UTC and respects rank floors.',
        ) +
        field('weeklyRating', 'Weekly rating decay', p.inactivity.weeklyRating, '', {
          min: 0,
          max: 100,
          path: 'records.inactivity.weeklyRating',
        }) +
        field('weeklyDeviation', 'Weekly deviation increase', p.inactivity.weeklyDeviation, '', {
          min: 0,
          max: 500,
          path: 'records.inactivity.weeklyDeviation',
        }) +
        toggle(
          'decorateNickname',
          'Show rating in nicknames',
          p.decorateNickname,
          `${setup.access.manageNicknames ? 'Manage Nicknames is granted.' : 'Grant the bot Manage Nicknames to synchronize names.'} Only one game can decorate nicknames; Discord’s role hierarchy still applies.`,
        ) +
        `<fieldset class="rank-list"><legend>Rank thresholds</legend><p class="form-help">Use distinct thresholds. Players receive the highest rank they have reached. Cosmetic roles must sit below the bot’s highest role.</p><div data-rank-rows>${(draft.raw.has(
          `game:${g.id}`,
        )
          ? Object.keys(draft.raw.get(`game:${g.id}`)!)
              .filter((key) => /^rank-\d+-threshold$/.test(key))
              .map((key) => Number(key.split('-')[1]))
          : p.ranks.map((_, i) => i)
        )
          .map((i) => rankRow(i, p.ranks[i], rankRoles))
          .join('')}</div><button type="button" data-add-rank>Add rank</button></fieldset></form>` +
        (q
          ? `<form data-config="rating:${g.id}" novalidate><h3>Ratings shared by every queue</h3>${select('ratingEngine', 'Rating system', q.ratingEngine, choices({ aoe2: 'AoE2-style Elo', glicko2: 'Glicko2', trueskill: 'TrueSkill', flat: 'Flat' }), locked ? 'Locked because this game has player ratings.' : 'The engine becomes fixed after the first player rating is stored.', locked)}${engineChanged ? `<p class="notice error">Player ratings now use ${esc(state!.ratingEngine)}. Restore that rating system before saving. Your other edits will be kept.</p><button type="button" data-restore-engine="${g.id}">Use saved rating system</button>` : ''}${Object.entries(
              {
                initial: 'Starting rating',
                deviation: 'Initial deviation',
                minDeviation: 'Minimum deviation',
                scale: 'Overall scale (%)',
                winScale: 'Win scale (%)',
                lossScale: 'Loss scale (%)',
                drawBonus: 'Draw bonus (%)',
              },
            )
              .map(([key, label]) =>
                field(
                  key,
                  label,
                  rating[key as keyof typeof rating] as number,
                  ['scale', 'winScale', 'lossScale'].includes(key)
                    ? 'Zero uses the default 100% scale. Use the enable controls to pause features.'
                    : '',
                  {
                    min: key === 'drawBonus' ? -500 : key === 'deviation' ? 1 : 0,
                    max:
                      key === 'initial'
                        ? 10000
                        : ['deviation', 'minDeviation'].includes(key)
                          ? 3000
                          : key === 'scale'
                            ? 1000
                            : 500,
                    step: ['scale', 'winScale', 'lossScale', 'drawBonus'].includes(key) ? 'any' : 1,
                    fallback: defaultRatingSettings[key as keyof typeof rating] as number,
                  },
                ),
              )
              .join(
                '',
              )}${toggle('winStreakBoost', 'Win streak boost', rating.winStreakBoost)}${toggle('lossStreakBoost', 'Loss streak boost', rating.lossStreakBoost)}${field('taxMinuteUtc', 'Thursday tax time (UTC minutes)', q.economy?.taxMinuteUtc ?? 0, 'Minutes after midnight UTC: 0 is 00:00, 600 is 10:00. Changes apply to all queues; each keeps its tax enable switch.', { min: 0, max: 1439, fallback: 0 })}</form>`
          : '<p class="notice">Create the first queue to configure this game’s shared rating policy.</p>') +
        `<a class="button" href="/stats?community=${draft.saved.communityId}&game=${g.id}">Open game statistics</a></details>`
      );
    })
    .join(
      '',
    )}<form data-add-game class="add-game"><h3>Add a game</h3><label>Catalog game<select name="catalogId"><option value="">Custom game</option>${selectableCatalog
    .filter((g) => !draft.working.games.some((existing) => existing.catalogId === g.id))
    .map(
      (g) =>
        `<option value="${g.id}">${esc(g.name)}${g.lifecycle === 'conditional' ? ' · provider access required' : ''}</option>`,
    )
    .join(
      '',
    )}</select></label><label>Custom game name<input name="name" maxlength="80" placeholder="Your game’s name" /></label><button type="submit" class="primary">Add to changes</button></form></section>`;
}
export function rankRow(
  index: number,
  rank = { threshold: 0, label: '', roleId: null as string | null },
  roles: Choice[] = [],
) {
  return `<div class="rank-row" data-rank-row><label>Rating threshold<input type="number" name="rank-${index}-threshold" value="${rank.threshold}" min="0" max="10000" required /></label><label>Rank label<input name="rank-${index}-label" value="${esc(rank.label)}" maxlength="30" required /></label><label>Cosmetic Discord role<select name="rank-${index}-role">${[{ id: '', name: 'No Discord role' }, ...roles.filter((r) => r.id), ...(rank.roleId && !roles.some((r) => r.id === rank.roleId) ? [{ id: rank.roleId, name: `Unavailable · ${rank.roleId}` }] : [])].map((r) => `<option value="${r.id}" ${r.id === rank.roleId ? 'selected' : ''} ${r.disabled && r.id !== rank.roleId ? 'disabled' : ''}>${esc(r.name)}</option>`).join('')}</select></label><button type="button" data-remove-rank>Remove rank</button></div>`;
}
