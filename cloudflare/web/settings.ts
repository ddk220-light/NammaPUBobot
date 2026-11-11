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
import { durationText, valueLabels } from './labels';

export { durationText, valueLabels };

export const categories = {
  basics: 'Basics',
  access: 'Access',
  preparation: 'Match preparation',
  maps: 'Maps',
  engagement: 'Extras',
  voice: 'Voice and rally',
};
export type Category = keyof typeof categories;
export interface EditorSetup extends DiscordSetup {
  gameState: GameState[];
}

type Choice = { id: string; name: string; disabled?: boolean };
const choices = (values: Record<string, string>): Choice[] =>
  Object.entries(values).map(([id, name]) => ({ id, name }));
const labelOf = (kind: keyof typeof valueLabels, id: string) =>
  (valueLabels[kind] as Record<string, string>)[id] ?? id;
const betaNote =
  'Beta: it works, but it is new and may still change; turning it off keeps saved data.';
let root = '';
function row(
  name: string,
  label: string,
  input: string,
  help: string,
  fallback?: string,
  beta = false,
) {
  return `<div class="setting-row" data-setting data-search="${esc(`${label} ${help}`.toLowerCase())}"><div class="setting-copy"><div class="setting-title"><label for="${root}-${name}" class="setting-label">${esc(label)}${beta ? ' <span class="beta-tag">Beta</span>' : ''}</label>${help ? `<button type="button" class="icon-button" data-help="${esc(help)}" data-help-title="${esc(label)}" aria-label="More about ${esc(label)}">ⓘ</button>` : ''}${fallback !== undefined ? `<button type="button" class="icon-button" data-reset="${esc(name)}" data-default="${esc(fallback)}" aria-label="Reset ${esc(label)}">↺</button>` : ''}</div>${help ? `<p class="form-help">${esc(help)}</p>` : ''}</div><div class="setting-control">${input}</div></div>`;
}
/** Rarely needed settings stay in the form (so saving keeps them) but start collapsed. */
const advanced = (body: string) =>
  `<details class="advanced-settings" data-advanced><summary>Advanced</summary>${body}</details>`;
/** Not offered to new communities yet; rendered hidden so saved values survive a save. */
const releaseHidden = (body: string) => `<div hidden data-release-hidden>${body}</div>`;

const fieldDefaults: Record<string, string> = {
  enabled: 'Off for a queue added here; on in the setup guide',
  size: '8 players',
  autoStart: 'On',
  earlyStart: valueLabels.earlyStart.moderators,
  isDefault: 'Off',
  keepQueueVisible: 'On',
  checkIn: 'On',
  teamMethod: valueLabels.teamMethod.captain,
  requiredRole: 'No role restriction',
  blockedRole: 'No role restriction',
  voicePreview: 'Off',
  rallyEnabled: 'Off',
  taxEnabled: 'Off',
  visibility: labelOf('visibility', defaultRecords.visibility),
  minimumMatches: String(defaultRecords.minimumMatches),
  recentDays: `${defaultRecords.recentDays} days`,
  weeklyRating: String(defaultRecords.inactivity.weeklyRating),
  weeklyDeviation: String(defaultRecords.inactivity.weeklyDeviation),
  decay: defaultRecords.inactivity.enabled ? 'On' : 'Off',
  decorateNickname: 'Off',
  lobbyConnector: 'Off',
  autoLobby: 'Off',
  ratingEngine: `${valueLabels.ratingEngine.aoe2} for a new game; later queues use the game’s system`,
  winStreakBoost: defaultRatingSettings.winStreakBoost ? 'On' : 'Off',
  lossStreakBoost: defaultRatingSettings.lossStreakBoost ? 'On' : 'Off',
  mapMode: labelOf('mapMode', defaultMaps().mode),
  mapCount: String(defaultMaps().count),
  mapShortlist: String(defaultMaps().shortlist),
  mapVoting: labelOf('mapVoting', defaultMaps().voting),
  mapTie: labelOf('mapTie', defaultMaps().tie),
  mapNoVotes: labelOf('mapNoVotes', defaultMaps().noVotes),
  mapSeconds: `${defaultMaps().seconds} seconds`,
  mapRecent: String(defaultMaps().recentAvoid),
  mapCheckIn: defaultMaps().countsAsCheckIn ? 'On' : 'Off',
  waitingSeconds: 'No expiry',
  matchSeconds: '12 hours',
};
export function settingHelp(row: HTMLElement) {
  const input = row.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
    '[name]',
  )!;
  const fallback =
    row.querySelector<HTMLElement>('[data-default]')?.dataset.defaultLabel ??
    row.querySelector<HTMLElement>('[data-default]')?.dataset.default ??
    fieldDefaults[input.name] ??
    (input.name.startsWith('module-') ? 'Off' : 'No fixed default');
  const number = input instanceof HTMLInputElement && input.type === 'number';
  const span = row.querySelector<HTMLElement>('[data-duration]');
  const allowed = span
    ? `${span.dataset.min === '0' ? 'From 0' : `From ${durationText(Number(span.dataset.min))}`} to ${durationText(Number(span.dataset.max))}.`
    : input instanceof HTMLSelectElement
      ? [...input.options]
          .filter((o) => !o.disabled)
          .map((o) => o.textContent)
          .join(', ')
      : input instanceof HTMLInputElement && input.type === 'checkbox'
        ? input.disabled
          ? 'Not available for this game'
          : 'On or off'
        : number
          ? `${input instanceof HTMLInputElement && input.step === 'any' ? 'Numbers, including decimals' : 'Whole numbers'}${input.min ? ` from ${input.min}` : ''}${input.max ? ` to ${input.max}` : ''}.`
          : `${input.required ? 'Required' : 'Optional'} text${input.maxLength > 0 ? `, up to ${input.maxLength} characters` : ''}.`;
  const scope = row.closest<HTMLFormElement>('form')?.dataset.config?.startsWith('queue:')
    ? 'This queue after saving; matches already running keep their settings.'
    : 'This game across all its queues after saving. Past results are not recalculated.';
  return `<p>${esc(row.querySelector<HTMLElement>('[data-help]')?.dataset.help ?? '')}</p><dl class="help-facts"><div><dt>Default</dt><dd>${esc(fallback)}</dd></div><div><dt>Allowed</dt><dd>${esc(allowed)}</dd></div><div><dt>Applies to</dt><dd>${esc(scope)}</dd></div></dl>`;
}
const units = { 60: 'minutes', 3600: 'hours', 86400: 'days' } as const;
const bestUnit = (seconds: number) =>
  seconds && seconds % 86400 === 0 ? 86400 : seconds && seconds % 3600 === 0 ? 3600 : 60;
const trimNumber = (n: number) => String(Math.round(n * 1000) / 1000);
/** Show a duration's stored seconds in the visible amount and unit (after render, mount or reset). */
export function syncDurations(root: ParentNode) {
  root.querySelectorAll<HTMLElement>('[data-duration]').forEach((box) => {
    const stored = box.querySelector<HTMLInputElement>('input[type="hidden"]')!;
    const amount = box.querySelector<HTMLInputElement>('[data-duration-amount]')!;
    const unit = box.querySelector<HTMLSelectElement>('[data-duration-unit]')!;
    if (stored.value.trim() === '' || !Number.isFinite(Number(stored.value))) {
      amount.value = stored.value;
      return;
    }
    const seconds = Number(stored.value);
    if (Math.round(Number(amount.value) * Number(unit.value)) === seconds && amount.value !== '')
      return;
    unit.value = String(bestUnit(seconds));
    amount.value = trimNumber(seconds / Number(unit.value));
  });
}
/** Write the visible amount and unit back as whole seconds, the stored and saved value. */
export function captureDuration(control: Element) {
  const box = control.closest<HTMLElement>('[data-duration]');
  if (!box) return null;
  const stored = box.querySelector<HTMLInputElement>('input[type="hidden"]')!;
  const amount = box.querySelector<HTMLInputElement>('[data-duration-amount]')!;
  const unit = box.querySelector<HTMLSelectElement>('[data-duration-unit]')!;
  const n = Number(amount.value);
  stored.value =
    amount.value.trim() === '' || !Number.isFinite(n)
      ? ''
      : String(Math.round(n * Number(unit.value)));
  return stored;
}
export function settingReadouts(root: ParentNode) {
  root
    .querySelectorAll<HTMLInputElement>(
      'form[data-config] input[type="number"][name], form[data-config] [data-duration] input[type="hidden"]',
    )
    .forEach((input) => {
      const durations = [
        'seconds',
        'reuseSeconds',
        'waitingSeconds',
        'matchSeconds',
        'mapSeconds',
        'rallyCooldown',
      ];
      if (!durations.includes(input.name) && input.name !== 'size') return;
      const row = input.closest('.setting-control');
      if (!row) return;
      let output = row.querySelector<HTMLElement>('[data-readout]');
      if (!output) {
        output = document.createElement('p');
        output.className = 'field-readout';
        output.dataset.readout = '';
        output.setAttribute('aria-live', 'polite');
        row.append(output);
      }
      const n = Number(input.value);
      output.textContent =
        !input.value || !Number.isSafeInteger(n) || n < 0
          ? input.type === 'hidden'
            ? 'Enter a number.'
            : 'Enter a whole number.'
          : input.name === 'size'
            ? n > 16
              ? 'Up to 16 players can play in one match.'
              : n < 2
                ? 'At least 2 players required.'
                : `${Math.ceil(n / 2)} vs ${Math.floor(n / 2)} · automatic starts use an even roster`
            : input.name === 'matchSeconds' && (n === 0 || n >= 43200)
              ? 'Matches end after 12 hours at most.'
              : n === 0
                ? input.name === 'waitingSeconds'
                  ? 'Players stay queued until they leave.'
                  : input.name === 'reuseSeconds'
                    ? 'Off: players always confirm again.'
                    : 'Check the allowed range.'
                : input.type === 'hidden'
                  ? `= ${durationText(n)}`
                  : n < 60
                    ? ''
                    : `= ${durationText(n)}`;
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
    placeholder?: string;
  } = {},
) {
  const id = `${root}-${name}`;
  const attributes = `name="${name}" id="${id}" aria-label="${esc(label)}" data-issue-path="${root}.${options.path ?? name}" ${options.required === false ? '' : 'required'} ${options.min !== undefined ? `min="${options.min}"` : ''} ${options.max !== undefined ? `${typeof current === 'number' ? 'max' : 'maxlength'}="${options.max}"` : ''} ${options.placeholder ? `placeholder="${esc(options.placeholder)}"` : ''}`;
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
/** A long duration: an amount and a unit on screen, whole seconds in the form. */
function duration(
  name: string,
  label: string,
  seconds: number,
  help: string,
  options: { min: number; max: number; fallback: number; path: string },
) {
  const id = `${root}-${name}`;
  const unit = bestUnit(seconds);
  return row(
    name,
    label,
    `<div class="duration-control" data-duration data-min="${options.min}" data-max="${options.max}"><input type="number" id="${id}" aria-label="${esc(label)}" data-duration-amount min="0" step="any" inputmode="decimal" value="${trimNumber(seconds / unit)}" required /><select aria-label="${esc(label)} unit" data-duration-unit>${Object.entries(
      units,
    )
      .map(
        ([value, text]) =>
          `<option value="${value}" ${Number(value) === unit ? 'selected' : ''}>${text}</option>`,
      )
      .join(
        '',
      )}</select><input type="hidden" name="${name}" value="${seconds}" data-issue-path="${root}.${options.path}" /></div>`,
    help,
    String(options.fallback),
  ).replace(
    'data-reset=',
    `data-default-label="${esc(durationText(options.fallback))}" data-reset=`,
  );
}
function toggle(
  name: string,
  label: string,
  current: boolean,
  help = '',
  unavailable = false,
  beta = false,
) {
  return row(
    name,
    label,
    `<label class="switch-control"><input type="checkbox" id="${root}-${name}" name="${name}" aria-label="${esc(label)}" ${current ? 'checked' : ''} ${unavailable && !current ? 'disabled data-unavailable="true"' : ''} /><span aria-hidden="true" class="switch-track"></span><span class="switch-text">${current ? 'On' : 'Off'}</span></label>`,
    help,
    undefined,
    beta,
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
    available.push({
      id: current,
      name:
        name === 'channelId'
          ? 'Saved channel · unavailable or not verified'
          : 'Saved choice · no longer available',
    });
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
      .map((id) => ({ id, name: 'No longer available in Discord' })),
  ];
  return `<fieldset class="choice-list"><legend>${esc(label)}</legend><p class="form-help">${esc(help)}</p>${available.map((v) => `<label class="choice"><input type="checkbox" name="${name}" value="${esc(v.id)}" ${selected.includes(v.id) ? 'checked' : ''} /><span>${esc(v.name)}</span></label>`).join('') || '<p class="muted">Nothing to choose from yet. Check Discord access and refresh.</p>'}</fieldset>`;
}
function section(id: Category, body: string, hidden = false) {
  return `<section data-category-panel="${id}" class="settings-category"${hidden ? ' hidden data-release-hidden' : ''}><h3>${categories[id]}</h3>${body}</section>`;
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
    () => `${esc(game?.name ?? 'Unavailable game')} · ${esc(channelContext)}`,
  );
  const maps = q.maps ?? defaultMaps(game?.catalogId === 'aoe2' ? aoe2Maps : []);
  if (!q.maps && !maps.pool.length) maps.shortlist = 1;
  const roles = [{ id: '', name: 'No role restriction' }, ...setup.moderatorRoles];
  // First release: voice, quiz and gold tax appear only where a community already turned them on.
  const saved = draft.saved.settings.queues.find((v) => v.id === q.id);
  const either = (on: (v: Queue | undefined) => boolean | undefined) => Boolean(on(q) || on(saved));
  const showVoice = either((v) => v?.voicePreview || v?.rally?.enabled);
  const showQuiz = either((v) => v?.modules.quiz);
  const showTax = either((v) => v?.economy?.taxEnabled);
  html += `<h2 class="sr-only">Queue settings</h2><div class="editor-toolbar"><label>Queue<select data-queue-select>${s.queues.map((v) => `<option value="${v.id}" ${v.id === q.id ? 'selected' : ''}>${esc(v.name)}</option>`).join('')}</select></label><label>Search settings<input type="search" data-settings-search placeholder="Name, role, map…" /></label></div><div class="queue-editor-layout"><nav class="category-strip" aria-label="Queue settings categories">${Object.entries(
    categories,
  )
    .filter(([key]) => key !== 'voice' || showVoice)
    .map(
      ([key, name]) =>
        `<button type="button" data-category="${key}">${name}<span class="dirty-dot" hidden aria-label="Unsaved changes"></span></button>`,
    )
    .join(
      '',
    )}</nav><form data-config="queue:${q.id}" class="settings-editor" novalidate><p class="notice" data-search-empty hidden>No settings match your search.</p>`;
  html += section(
    'basics',
    field('name', 'Queue name', q.name, 'The name players see in Discord. Up to 40 characters.', {
      max: 40,
    }) +
      select(
        'gameId',
        'Game',
        q.gameId,
        s.games,
        'Queues for the same game share ratings, gold and the leaderboard.',
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
        `Where the queue card is posted and players join. The bot needs View Channel, Send Messages, Embed Links and Read Message History there.${!channel && cachedChannel?.observedAt ? ` Last seen ${new Date(cachedChannel.observedAt).toLocaleString()}; check access again to confirm it.` : ''}`,
      ) +
      field(
        'size',
        'Players per match',
        q.size,
        'A match starts when this many players have joined. Default 8, from 2 to 16.',
        { min: 2 },
      ) +
      toggle(
        'enabled',
        'Enable queue',
        q.enabled,
        'When off, the queue is paused and players cannot join. Takes effect when you save.',
      ) +
      toggle(
        'autoStart',
        'Start automatically',
        q.autoStart ?? true,
        'Start the match as soon as the queue is full. Default on.',
      ) +
      advanced(
        toggle(
          'isDefault',
          'Default queue',
          q.isDefault ?? false,
          'When a channel has several queues, /add without a name joins this one. One per channel.',
        ) +
          field(
            'aliases',
            'Queue nicknames',
            q.aliases?.join(', ') ?? '',
            'Other names players can type for this queue, separated by commas.',
            { required: false },
          ) +
          toggle(
            'keepQueueVisible',
            'Keep queue card visible',
            q.keepQueueVisible ?? true,
            'Every 15 minutes, repost the card if it has scrolled out of the last five messages. Default on.',
          ),
      ),
  );
  html += section(
    'access',
    select(
      'requiredRole',
      'Required role',
      q.controls?.requiredRole ?? '',
      roles,
      'Only members with this role may join. Default: anyone can join.',
    ) +
      select(
        'blockedRole',
        'Blocked role',
        q.controls?.blockedRole ?? '',
        roles,
        'Members with this role cannot join. Default: nobody is blocked.',
      ) +
      multi(
        'adminRoles',
        'Bot moderator roles',
        q.adminRoles ?? [],
        setup.moderatorRoles,
        'These roles can clear the queue, cancel matches, close predictions and report results in Discord. They do not get access to this website. Server managers can always do this.',
      ) +
      select(
        'earlyStart',
        'Who can start a match before the queue is full',
        q.earlyStart ?? 'moderators',
        choices(valueLabels.earlyStart),
        'A full queue always starts by itself. This decides who may start one early, with fewer players and possibly uneven teams. Default: organizers and moderators.',
      ),
  );
  html += section(
    'preparation',
    select(
      'teamMethod',
      'How teams are made',
      q.teamMethod,
      choices(valueLabels.teamMethod),
      'Captain-based balance picks two captains and balances the rest by rating. Automatic balance splits everyone by rating. Random ignores ratings.',
    ) +
      toggle(
        'checkIn',
        'Require check-in',
        q.checkIn.enabled,
        'Each player confirms they are ready before the match starts. Default on.',
      ) +
      field(
        'seconds',
        'Check-in time (seconds)',
        q.checkIn.seconds,
        'How long players have to check in. Default 60 seconds.',
        { min: 1, fallback: 60, path: 'checkIn.seconds' },
      ) +
      duration(
        'waitingSeconds',
        'Remove waiting players after',
        q.controls?.waitingSeconds ?? 0,
        'Players who wait this long are taken out of the queue. 0 keeps them until they leave (the default). Up to 14 days.',
        { min: 0, max: 1209600, fallback: 0, path: 'controls.waitingSeconds' },
      ) +
      `<div class="shared-setting"><h4>Ratings</h4><p>${esc(game?.name ?? 'This game')} uses ${esc(labelOf('ratingEngine', q.ratingEngine))} ratings, shared by every queue for this game.</p><button type="button" data-game-link="${q.gameId}">Edit game ratings</button></div>` +
      advanced(
        field(
          'reuseSeconds',
          'Ready lasts for (seconds)',
          q.checkIn.reuseSeconds ?? 900,
          'A player who checked in recently is counted as ready again without confirming. 0 turns this off. Default 900 seconds (15 min), up to 900.',
          { min: 0, max: 900, fallback: 900, path: 'checkIn.reuseSeconds' },
        ) +
          duration(
            'matchSeconds',
            'End unreported matches after',
            q.controls?.matchSeconds ?? 43200,
            'A match with no result is closed after this long, without rating changes. Matches never run longer than 12 hours (the default).',
            { min: 0, max: 1209600, fallback: 43200, path: 'controls.matchSeconds' },
          ),
      ),
  );
  html += section(
    'maps',
    toggle(
      'module-maps',
      'Choose a map for each match',
      q.modules.maps,
      'Players vote, or a map is picked for them, before each match. Turning this off keeps your map pool.',
    ) +
      field(
        'mapPool',
        'Map pool',
        maps.pool.join('\n'),
        'One map per line. Saving map options needs at least one map, even when map choice is off. Custom games can leave this empty.',
        { type: 'textarea', required: false, path: 'maps.pool' },
      ) +
      select(
        'mapMode',
        'How the map is chosen',
        maps.mode,
        choices(valueLabels.mapMode),
        'Players vote on a shortlist, a map is picked at random, or the captains choose.',
      ) +
      field(
        'mapCount',
        'Maps per match',
        maps.count,
        'Games played on these maps count as one rated match. Default 1, up to 5.',
        { min: 1, max: 5, path: 'maps.count' },
      ) +
      field(
        'mapSeconds',
        'Map choice time (seconds)',
        maps.seconds,
        `How long players have to vote. Default ${defaultMaps().seconds} seconds, from 10 seconds to 1 hour.`,
        { min: 10, max: 3600, path: 'maps.seconds' },
      ) +
      advanced(
        field(
          'mapShortlist',
          'Maps on the ballot',
          maps.shortlist,
          `How many maps from the pool players choose between. Default ${defaultMaps().shortlist}, up to 25.`,
          { min: 1, max: 25, path: 'maps.shortlist' },
        ) +
          select(
            'mapVoting',
            'Votes per player',
            maps.voting,
            choices(valueLabels.mapVoting),
            'One choice each, or approve every map a player is happy with.',
          ) +
          select(
            'mapTie',
            'When maps tie',
            maps.tie,
            choices(valueLabels.mapTie),
            'Which map wins when two or more get the same number of votes.',
          ) +
          select(
            'mapNoVotes',
            'When nobody votes',
            maps.noVotes,
            choices(valueLabels.mapNoVotes),
            'What happens if time runs out with no votes.',
          ) +
          field(
            'mapRecent',
            'Skip maps from recent matches',
            maps.recentAvoid,
            'Leave out maps played in this many recent matches. 0 allows repeats (the default). Up to 20.',
            { min: 0, max: 20, path: 'maps.recentAvoid' },
          ) +
          toggle(
            'mapCheckIn',
            'Voting also checks players in',
            maps.countsAsCheckIn,
            'A map vote counts as checking in, using the map choice time. Needs player voting.',
          ),
      ),
  );
  const tax = toggle(
    'taxEnabled',
    'Weekly gold tax',
    q.economy?.taxEnabled ?? false,
    'Turned on earlier for this queue. Each Thursday, inactive players lose 10% of their gold above 500. Its time is set with the game’s ratings in Setup.',
  );
  const unavailable = (key: keyof typeof q.modules) =>
    !capabilities[key as keyof typeof capabilities] ||
    (key === 'predictions' && !q.modules.gold) ||
    (key === 'civs' && game?.catalogId !== 'aoe2');
  html += section(
    'engagement',
    toggle(
      'module-gold',
      'Gold',
      q.modules.gold,
      `Players earn play-money gold from matches to spend on predictions. It has no real value. Off by default. ${betaNote}`,
      unavailable('gold'),
      true,
    ) +
      toggle(
        'module-predictions',
        'Predictions',
        q.modules.predictions,
        `Players bet gold on who wins a match, until the game starts. Needs gold. Off by default. ${betaNote}`,
        unavailable('predictions'),
        true,
      ) +
      toggle(
        'module-civs',
        'Civilization picks',
        q.modules.civs,
        `Players pick civilizations before a match. Age of Empires II queues only. Off by default. ${betaNote}`,
        unavailable('civs'),
        true,
      ) +
      toggle(
        'module-stories',
        'Match stories',
        q.modules.stories,
        `Posts a short summary and milestones after each match. Off by default. ${betaNote}`,
        unavailable('stories'),
        true,
      ) +
      (showQuiz
        ? toggle(
            'module-quiz',
            'Quiz',
            q.modules.quiz,
            'Turned on earlier for this queue. The quiz is not offered right now; you can turn it off.',
            unavailable('quiz'),
          )
        : releaseHidden(toggle('module-quiz', 'Quiz', q.modules.quiz, '', true))) +
      (showTax ? tax : releaseHidden(tax)),
  );
  html += section(
    'voice',
    toggle(
      'voicePreview',
      'Show who is in voice',
      q.voicePreview ?? false,
      'Turned on earlier for this queue. Keeps a list of voice participants for Ping, refreshed every five minutes. No audio is processed.',
    ) +
      toggle(
        'rallyEnabled',
        'Rally',
        q.rally?.enabled ?? false,
        'Turned on earlier for this queue. Lets players invite people in voice channels to join.',
      ) +
      multi(
        'rallyChannels',
        'Voice channels for rally',
        q.rally?.channels ?? [],
        setup.voiceChannels,
        'Leave empty to use the channel of the person who starts the rally.',
      ) +
      multi(
        'rallyRoles',
        'Roles that can rally',
        q.rally?.roles ?? [],
        setup.moderatorRoles,
        'Roles allowed to start a rally, in addition to moderators.',
      ) +
      field(
        'rallyCooldown',
        'Time between rallies (seconds)',
        q.rally?.cooldownSeconds ?? 300,
        'Default 300 seconds (5 min), from 60 seconds to 1 hour.',
        { min: 60, max: 3600, fallback: 300, path: 'rally.cooldownSeconds' },
      ) +
      field(
        'rallyMentions',
        'People mentioned per rally',
        q.rally?.mentionLimit ?? 8,
        'Default 8, from 1 to 8.',
        { min: 1, max: 8, fallback: 8, path: 'rally.mentionLimit' },
      ),
    !showVoice,
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
        `<details class="game-settings" id="game-settings-${g.id}"><summary><span class="game-tile-icon" aria-hidden="true">${esc(g.catalogId ? (g.catalogId === 'aoe2' ? 'II' : g.catalogId === 'aoe3' ? 'III' : g.catalogId === 'aoe4' ? 'IV' : g.name.slice(0, 2).toUpperCase()) : '◇')}</span><span class="game-tile-copy"><strong>${esc(g.name)}</strong><span class="record-meta">${((n) => `${n} ${n === 1 ? 'queue' : 'queues'}`)(queues.filter((q) => q.gameId === g.id).length)} · Shared ratings, ranks and leaderboard</span></span><span class="game-tile-action">Game settings</span></summary><form data-config="game:${g.id}" novalidate>` +
        field(
          'name',
          'Game name',
          g.name,
          'Shown in Discord and on statistics pages. Up to 80 characters.',
          { max: 80 },
        ) +
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
          choices(valueLabels.visibility),
          'Who can open this game’s leaderboard and player pages. Default: server members.',
        ) +
        toggle(
          'searchPublishing',
          'Publish this leaderboard to search engines',
          Boolean(p.searchPublishing),
          'Needs public statistics and a public community name (below). Pages shared by link stay unlisted until you turn this on.',
        ) +
        field(
          'minimumMatches',
          'Matches before a player is listed',
          p.minimumMatches,
          `Players appear on the leaderboard after this many matches. Default ${defaultRecords.minimumMatches}.`,
          { min: 0, max: 10000, path: 'records.minimumMatches' },
        ) +
        field(
          'recentDays',
          'Hide players inactive for (days)',
          p.recentDays,
          `Players without a match in this many days leave the leaderboard until they play again. 0 keeps everyone listed. Default ${defaultRecords.recentDays}.`,
          { min: 0, max: 3650, path: 'records.recentDays' },
        ) +
        toggle(
          'decay',
          'Lower ratings of inactive players',
          p.inactivity.enabled,
          'After 30 days without a ranked match, ratings drop a little every Monday (UTC), never below a rank floor. Default off.',
        ) +
        toggle(
          'decorateNickname',
          'Show rating in nicknames',
          p.decorateNickname,
          `${setup.access.manageNicknames ? 'The bot has Manage Nicknames.' : 'The bot needs Manage Nicknames for this.'} Only one game can change nicknames, and Discord does not let the bot rename members above it.`,
        ) +
        advanced(
          field(
            'weeklyRating',
            'Weekly rating drop',
            p.inactivity.weeklyRating,
            `Points an inactive player loses each week. Default ${defaultRecords.inactivity.weeklyRating}, up to 100.`,
            { min: 0, max: 100, path: 'records.inactivity.weeklyRating' },
          ) +
            field(
              'weeklyDeviation',
              'Weekly uncertainty increase',
              p.inactivity.weeklyDeviation,
              `How much less certain an inactive player’s rating becomes each week, so it moves faster when they return. Default ${defaultRecords.inactivity.weeklyDeviation}, up to 500.`,
              { min: 0, max: 500, path: 'records.inactivity.weeklyDeviation' },
            ),
        ) +
        `<fieldset class="rank-list"><legend>Ranks</legend><p class="form-help">Players get the highest rank whose rating they have reached. Use different ratings for each rank. A Discord role for a rank must sit below the bot’s highest role.</p><div data-rank-rows>${(draft.raw.has(
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
          ? `<form data-config="rating:${g.id}" novalidate><h3>Ratings shared by every queue</h3>${select('ratingEngine', 'Rating system', q.ratingEngine, choices(valueLabels.ratingEngine), locked ? 'Locked because players already have ratings for this game.' : 'Fixed once the first player has a rating.', locked)}${engineChanged ? `<p class="notice error">Player ratings now use ${esc(labelOf('ratingEngine', state!.ratingEngine!))}. Switch back to it before saving; your other changes are kept.</p><button type="button" data-restore-engine="${g.id}">Use saved rating system</button>` : ''}${ratingField('initial', 'Starting rating', `Rating for a new player. Default ${defaultRatingSettings.initial}.`)}${advanced(
              (
                [
                  [
                    'deviation',
                    'Starting uncertainty',
                    'Higher values let a new player’s rating move faster at first.',
                  ],
                  [
                    'minDeviation',
                    'Lowest uncertainty',
                    'Keeps established ratings from becoming too slow to change.',
                  ],
                  ['scale', 'Rating change size (%)', 'Scales every rating change. 0 means 100%.'],
                  ['winScale', 'Win size (%)', 'Scales points gained for a win. 0 means 100%.'],
                  ['lossScale', 'Loss size (%)', 'Scales points lost for a loss. 0 means 100%.'],
                  ['drawBonus', 'Draw bonus (%)', 'Extra change for a draw. Default 0.'],
                ] as const
              )
                .map(([key, label, help]) => ratingField(key, label, help))
                .join('') +
                toggle(
                  'winStreakBoost',
                  'Bigger gains on a win streak',
                  rating.winStreakBoost,
                  'Players on a winning streak gain a little more. Default off.',
                ) +
                toggle(
                  'lossStreakBoost',
                  'Bigger losses on a losing streak',
                  rating.lossStreakBoost,
                  'Players on a losing streak lose a little more. Default off.',
                ),
            )}${(taxShown(draft, g.id) ? (v: string) => v : releaseHidden)(taxTime(q.economy?.taxMinuteUtc ?? 0))}</form>`
          : '<p class="notice">Add a queue for this game to set its ratings.</p>') +
        `<a class="button" href="/stats?community=${draft.saved.communityId}&game=${g.id}">Open game statistics</a></details>`
      );
      function ratingField(key: keyof typeof rating, label: string, help: string) {
        return field(key, label, rating[key] as number, help, {
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
          fallback: defaultRatingSettings[key] as number,
        });
      }
    })
    .join(
      '',
    )}<form data-add-game class="add-game"><h3>Add a game</h3><label>Game<select name="catalogId"><option value="">Custom game</option>${selectableCatalog
    .filter((g) => !draft.working.games.some((existing) => existing.catalogId === g.id))
    .map(
      (g) =>
        `<option value="${g.id}">${esc(g.name)}${g.lifecycle === 'conditional' ? ' · lobby tracking awaiting activation' : ''}</option>`,
    )
    .join(
      '',
    )}</select></label><label>Custom game name<input name="name" maxlength="80" placeholder="Your game’s name" /></label><button type="submit" class="primary">Add to changes</button></form></section>`;
}
const taxShown = (draft: SettingsDraft, gameId: string) =>
  [...draft.saved.settings.queues, ...draft.materialize().queues].some(
    (q) => q.gameId === gameId && q.economy?.taxEnabled,
  );
/** Tax time is stored as minutes after midnight UTC; organizers pick a clock time. */
function taxTime(minutes: number) {
  const clock = `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
  return row(
    'taxMinuteUtc',
    'Weekly gold tax time (UTC)',
    `<input type="time" id="${root}-taxMinuteUtc" aria-label="Weekly gold tax time (UTC)" data-clock-for="taxMinuteUtc" value="${clock}" required /><input type="hidden" name="taxMinuteUtc" value="${minutes}" data-issue-path="${root}.economy.taxMinuteUtc" />`,
    'The tax runs every Thursday at this time, in UTC. It applies to every queue of this game that has the tax on. Default 00:00.',
  );
}
/** Mirror a clock input into its hidden minutes-after-midnight field. */
export function captureClock(input: Element) {
  const name = (input as HTMLElement).dataset.clockFor;
  if (!name) return null;
  const stored = input.closest('form')!.querySelector<HTMLInputElement>(`[name="${name}"]`)!;
  const [h, m] = (input as HTMLInputElement).value.split(':').map(Number);
  stored.value = Number.isInteger(h) && Number.isInteger(m) ? String(h * 60 + m) : '';
  return stored;
}
export function syncClocks(root: ParentNode) {
  root.querySelectorAll<HTMLInputElement>('[data-clock-for]').forEach((clock) => {
    const n = Number(
      clock.closest('form')!.querySelector<HTMLInputElement>(`[name="${clock.dataset.clockFor}"]`)!
        .value,
    );
    if (Number.isInteger(n) && n >= 0 && n < 1440)
      clock.value = `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
  });
}
export function rankRow(
  index: number,
  rank = { threshold: 0, label: '', roleId: null as string | null },
  roles: Choice[] = [],
) {
  return `<div class="rank-row" data-rank-row><label>Rating threshold<input type="number" name="rank-${index}-threshold" value="${rank.threshold}" min="0" max="10000" required /></label><label>Rank label<input name="rank-${index}-label" value="${esc(rank.label)}" maxlength="30" required /></label><label>Discord role (optional)<select name="rank-${index}-role">${[{ id: '', name: 'No Discord role' }, ...roles.filter((r) => r.id), ...(rank.roleId && !roles.some((r) => r.id === rank.roleId) ? [{ id: rank.roleId, name: 'Unavailable role' }] : [])].map((r) => `<option value="${r.id}" ${r.id === rank.roleId ? 'selected' : ''} ${r.disabled && r.id !== rank.roleId ? 'disabled' : ''}>${esc(r.name)}</option>`).join('')}</select></label><button type="button" data-remove-rank>Remove rank</button></div>`;
}
