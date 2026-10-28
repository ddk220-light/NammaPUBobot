import { SettingsDraft, fields, value } from './draft';
import {
  queueEditor,
  gameEditors,
  categories,
  rankRow,
  type Category,
  type EditorSetup,
} from './settings';
import { ApiError, openDialog, labelTables, installViewport } from './ui';
import { gameCatalog } from '../src/games/catalog';
import './style.css';
import './responsive.css';
import { onboarding, onboardingSubmit, applyImport, invalidateImport } from './onboarding';
import { showStats } from './stats';
import { type Settings, type Snapshot } from '../src/domain/config';
import { type Player } from '../src/domain/rating';
import type { Action, Match } from '../src/domain/play';
interface Records {
  gameState: { id: string; ratingEngineLocked: boolean }[];
  economy: {
    wallets: { game_id: string; user_id: string; balance: number }[];
    ledger: {
      kind: string;
      amount: number;
      user_id: string;
      balance_after: number;
      created_at: number;
    }[];
    books: { match_id: string; status: string; result: string | null; reason: string | null }[];
    taxRuns: { cutoff: number; holders: number; total: number }[];
  };
  onboarding: unknown;
  pendingReturns: { queue_id: string; user_id: string; name: string; status: string }[];
  matches: Match[];
  players: (Player & { gameId: string })[];
  playersTotal: number;
  playersPage: number;
  delivery: { key: string; status: string; error: string | null }[];
  waiting: { queueId: string; channelId: string; players: { id: string; name: string }[] }[];
  noadds: { user_id: string; until_ms: number; reason: string }[];
  audit: {
    id: string;
    action: string;
    actor_id: string;
    at: number;
    match_id: string | null;
    details: string;
  }[];
}

interface Bootstrap {
  environment: string;
  version: string;
  oauthReady: boolean;
  discordReady: boolean;
  inviteUrl: string | null;
  catalog: { id: string; name: string }[];
  user: { id: string; username: string } | null;
  csrf: string | null;
}
interface Channel {
  id: string;
  name: string;
  permissions: { name: string; granted: boolean }[];
}
interface Guild {
  id: string;
  name: string;
}
const app = document.querySelector<HTMLElement>('#app')!;
let bootstrap: Bootstrap,
  guilds: Guild[] = [],
  guildId = '',
  snapshot: Snapshot | null = null,
  channelList: Channel[] = [];
let busy = false;
let records: Records | null = null;
let draft: SettingsDraft | null = null;
let setup: EditorSetup = {
  channels: [],
  moderatorRoles: [],
  roles: [],
  voiceChannels: [],
  gameState: [],
  access: {
    status: 'unavailable',
    message: 'Checking Discord access…',
    manageRoles: false,
    manageNicknames: false,
  },
};
let selectedQueue = new URL(location.href).searchParams.get('queue') ?? '';
let category: Category = (new URL(location.href).searchParams.get('section') ??
  'basics') as Category;
let settingsSearch = '';
let conflict = false;
let loadSequence = 0;
let awaitingDiscordReturn = false;
const canLeave = () =>
  !draft?.dirty || confirm('Discard your unsaved settings and leave this community?');
const organizerViews = ['overview', 'queues', 'matches', 'players', 'setup'] as const;
type OrganizerView = (typeof organizerViews)[number];
const viewNames: Record<OrganizerView, string> = {
  overview: 'Overview',
  queues: 'Queues',
  matches: 'Matches',
  players: 'Players',
  setup: 'Setup',
};
const viewsByCommunity = new Map<string, OrganizerView>();
interface OrganizerFilters {
  playerGame: string;
  playerSearch: string;
  matchQueue: string;
  matchState: string;
}
const filtersByCommunity = new Map<string, OrganizerFilters>();
const requestedView = new URL(location.href).searchParams.get('view');
let initialViewUsed = false;
let refreshedAt: Date | null = null;
const escape = (s: unknown) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
const selected = (a: string, b: string) => (a === b ? ' selected' : '');
const isOrganizerView = (value: string | null): value is OrganizerView =>
  organizerViews.includes(value as OrganizerView);

function currentView(): OrganizerView {
  const saved = viewsByCommunity.get(guildId);
  if (saved) return saved;
  const view =
    !initialViewUsed && isOrganizerView(requestedView)
      ? requestedView
      : snapshot?.settings.queues.length
        ? 'overview'
        : 'setup';
  initialViewUsed = true;
  viewsByCommunity.set(guildId, view);
  return view;
}
function selectView(view: OrganizerView, focus = false, push = true) {
  viewsByCommunity.set(guildId, view);
  const url = new URL(location.href);
  url.searchParams.set('view', view);
  if (push && url.href !== location.href) history.pushState(null, '', url);
  app.querySelectorAll<HTMLButtonElement>('[role=tab][data-view]').forEach((button) => {
    const active = button.dataset.view === view;
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
    if (active && focus) button.focus();
  });
  app.querySelectorAll<HTMLElement>('[data-view-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
}
function playerName(id: string, gameId?: string) {
  return (
    records?.players.find((p) => p.id === id && (!gameId || p.gameId === gameId))?.name ??
    records?.matches.flatMap((m) => m.roster).find((p) => p.id === id)?.name ??
    records?.waiting.flatMap((w) => w.players).find((p) => p.id === id)?.name ??
    id
  );
}
function gameName(id: string) {
  return snapshot?.settings.games.find((g) => g.id === id)?.name ?? id;
}
function currentFilters(): OrganizerFilters {
  let filters = filtersByCommunity.get(guildId);
  if (!filters) {
    const game =
      snapshot?.settings.games.find(
        (g) =>
          records?.players.some((p) => p.gameId === g.id) ||
          records?.economy?.wallets.some((w) => w.game_id === g.id),
      ) ?? snapshot?.settings.games[0];
    filters = {
      playerGame: game?.id ?? '',
      playerSearch: '',
      matchQueue: '',
      matchState: '',
    };
    filtersByCommunity.set(guildId, filters);
  }
  if (filters.playerGame && !snapshot?.settings.games.some((g) => g.id === filters.playerGame))
    filters.playerGame = '';
  if (
    filters.matchQueue &&
    !snapshot?.settings.queues.some((q) => q.id === filters.matchQueue) &&
    !records?.matches.some((m) => m.queue.id === filters.matchQueue)
  )
    filters.matchQueue = '';
  return filters;
}
function applyFilters() {
  if (!snapshot) return;
  const filters = currentFilters();
  const search = filters.playerSearch.trim().toLocaleLowerCase();
  for (const [attribute, label] of [
    ['rating', 'player records'],
    ['wallet', 'balances'],
  ] as const) {
    const rows = app.querySelectorAll<HTMLTableRowElement>(`[data-${attribute}-row]`);
    let visible = 0;
    rows.forEach((row) => {
      row.hidden =
        Boolean(filters.playerGame && row.dataset.game !== filters.playerGame) ||
        Boolean(search && !row.dataset.playerSearch?.includes(search));
      if (!row.hidden) visible++;
    });
    const count = app.querySelector<HTMLElement>(`[data-filter-count="${attribute}"]`);
    const total = records?.playersTotal ?? rows.length;
    const morePlayers = attribute === 'rating' && rows.length < total;
    const countText =
      attribute === 'rating'
        ? `${visible} matching · ${rows.length} of ${total} player records loaded`
        : `${visible} of ${rows.length} ${label}`;
    if (count && count.textContent !== countText) count.textContent = countText;
    const empty = app.querySelector<HTMLElement>(`[data-filter-empty="${attribute}"]`);
    if (empty) {
      empty.hidden = visible > 0;
      empty.textContent = morePlayers
        ? 'No matching players in the loaded records. Load more players to continue searching.'
        : rows.length
          ? `No ${label} match these filters.`
          : attribute === 'rating'
            ? 'No player ratings yet. Complete a match or seed starting ratings below.'
            : 'No gold balances yet.';
    }
    const table = app.querySelector<HTMLElement>(`[data-filter-table="${attribute}"]`);
    if (table) table.hidden = visible === 0;
  }
  const more = app.querySelector<HTMLButtonElement>('[data-action="load-players"]');
  if (more)
    more.hidden =
      !records || records.players.length >= (records.playersTotal ?? records.players.length);
  const matches = app.querySelectorAll<HTMLDetailsElement>('[data-match-record]');
  let visible = 0;
  matches.forEach((match) => {
    match.hidden =
      Boolean(filters.matchQueue && match.dataset.queue !== filters.matchQueue) ||
      Boolean(filters.matchState && match.dataset.state !== filters.matchState);
    if (!match.hidden) visible++;
  });
  const count = app.querySelector<HTMLElement>('[data-filter-count="match"]');
  const countText = `${visible} of ${matches.length} recent matches`;
  if (count && count.textContent !== countText) count.textContent = countText;
  const empty = app.querySelector<HTMLElement>('[data-filter-empty="match"]');
  if (empty) {
    empty.hidden = visible > 0;
    empty.textContent = matches.length
      ? 'No recent matches match these filters.'
      : 'No matches yet. Players join the queue in Discord.';
  }
}
function updateFilter(input: HTMLInputElement | HTMLSelectElement) {
  if (!snapshot || !input.dataset.filter) return;
  const filters = currentFilters();
  switch (input.dataset.filter) {
    case 'player-game':
      filters.playerGame = input.value;
      break;
    case 'player-search':
      filters.playerSearch = input.value;
      break;
    case 'match-queue':
      filters.matchQueue = input.value;
      break;
    case 'match-state':
      filters.matchState = input.value;
      break;
    default:
      return;
  }
  applyFilters();
}
function playerRatingRows() {
  return records!.players
    .map(
      (p) =>
        `<tr data-rating-row data-game="${p.gameId}" data-player-search="${escape(p.name.toLocaleLowerCase())}"><td><a href="/stats?community=${guildId}&game=${p.gameId}&user=${p.id}">${escape(p.name)}</a></td><td>${escape(gameName(p.gameId))}</td><td>${p.rating}</td><td>${p.wins} / ${p.losses} / ${p.draws}</td></tr>`,
    )
    .join('');
}
async function loadMorePlayers() {
  if (!records || records.players.length >= (records.playersTotal ?? records.players.length))
    return;
  const previous = records;
  const community = guildId;
  const nextPage = (previous.playersPage ?? 0) + 1;
  const page = await api<Records>(communityUrl(`records?playersPage=${nextPage}`));
  if (community !== guildId || records !== previous) return;
  const players = new Map(
    [...previous.players, ...page.players].map((player) => [
      `${player.gameId}:${player.id}`,
      player,
    ]),
  );
  records.players = [...players.values()];
  records.playersTotal = page.playersTotal;
  records.playersPage = page.playersPage;
  const body = app.querySelector<HTMLElement>('[data-rating-body]');
  if (body) {
    body.innerHTML = playerRatingRows();
    labelTables(app);
  }
  applyFilters();
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  if (bootstrap?.csrf) headers.set('X-CSRF-Token', bootstrap.csrf);
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  const body = (await response.json().catch(() => ({}))) as {
    error?: string;
    requestId?: string;
    code?: string;
    issues?: { path: (string | number)[]; message: string }[];
  };
  if (!response.ok)
    throw new ApiError(
      response.status,
      body.code ?? 'request_failed',
      body.error ?? 'Request failed.',
      body.requestId ?? response.headers.get('X-Request-ID') ?? undefined,
      body.issues,
    );
  return body as T;
}
const communityUrl = (suffix: string) => `/api/communities/${guildId}/${suffix}`;
function notice(message: string, error = false) {
  const box = document.querySelector<HTMLElement>('#notice');
  if (box) {
    box.textContent = message;
    box.className = `notice${error ? ' error' : ''}`;
    box.setAttribute('role', error ? 'alert' : 'status');
    box.hidden = false;
    if (error) {
      box.tabIndex = -1;
      if (!app.querySelector('[aria-invalid="true"]')) box.focus();
    }
  }
}
async function run(action: () => Promise<void>) {
  if (busy) return;
  busy = true;
  const previousNotice = app.querySelector<HTMLElement>('#notice');
  if (previousNotice) {
    previousNotice.hidden = true;
    previousNotice.textContent = '';
  }
  app.setAttribute('aria-busy', 'true');
  const disabled = new Map<Element, boolean>();
  app
    .querySelectorAll<
      HTMLInputElement | HTMLSelectElement | HTMLButtonElement | HTMLTextAreaElement
    >('input, select, button, textarea')
    .forEach((b) => {
      disabled.set(b, b.disabled);
      b.disabled = true;
    });
  try {
    await action();
  } catch (error) {
    if (error instanceof ApiError && error.code === 'revision_conflict') conflict = true;
    if (error instanceof ApiError && error.issues.length) showIssues(error.issues);
    notice(error instanceof Error ? error.message : 'Try again.', true);
  } finally {
    busy = false;
    app.setAttribute('aria-busy', 'false');
    app
      .querySelectorAll<
        HTMLInputElement | HTMLSelectElement | HTMLButtonElement | HTMLTextAreaElement
      >('input, select, button, textarea')
      .forEach((b) => (b.disabled = b.dataset.unavailable === 'true' || disabled.get(b) === true));
    updateDirty();
    app.querySelector<HTMLElement>('[aria-invalid="true"]:not(:disabled)')?.focus();
  }
}
async function save(settings: Settings) {
  if (!snapshot) return;
  const community = guildId;
  const saved = await api<Snapshot>(communityUrl('config'), {
    method: 'PUT',
    body: JSON.stringify({ expectedRevision: snapshot.revision, settings }),
  });
  if (community !== guildId) return;
  snapshot = saved;
  draft = new SettingsDraft(saved);
  conflict = false;
  invalidateImport();
  render();
  notice('Settings saved. Discord delivery may still be pending.');
}
async function loadCommunity(id: string) {
  const sequence = ++loadSequence;
  guildId = id;
  snapshot = null;
  draft = null;
  records = null;
  channelList = [];
  invalidateImport();
  const url = new URL(location.href);
  url.searchParams.set('community', id);
  history.replaceState(null, '', url);
  render();
  const result = await api<EditorSetup & { snapshot: Snapshot; records: Records }>(
    communityUrl('setup'),
  );
  if (guildId !== id || sequence !== loadSequence) return;
  snapshot = result.snapshot;
  draft = new SettingsDraft(snapshot);
  setup = result;
  channelList = result.channels;
  records = result.records;
  refreshedAt = new Date();
  conflict = false;
  render();
}
async function refreshRecords() {
  const id = guildId;
  const result = await api<Records>(communityUrl('records'));
  if (id !== guildId) return;
  records = result;
  setup.gameState = result.gameState ?? setup.gameState;
  refreshedAt = new Date();
  render();
}
async function refreshSetup() {
  if (!snapshot || !draft) return loadCommunity(guildId);
  const id = guildId;
  const result = await api<EditorSetup & { snapshot: Snapshot; records: Records }>(
    communityUrl('setup'),
  );
  if (id !== guildId) return;
  setup = result;
  channelList = result.channels;
  records = result.records;
  refreshedAt = new Date();
  if (!draft.dirty) {
    snapshot = result.snapshot;
    draft = new SettingsDraft(snapshot);
    conflict = false;
  } else if (result.snapshot.revision !== snapshot.revision) conflict = true;
  render();
  notice(
    conflict
      ? 'Saved settings changed elsewhere. Your edits are preserved. Continue editing, or reload the latest settings to discard your changes.'
      : setup.access.message,
    conflict || setup.access.status !== 'ready',
  );
}
function showIssues(issues: { path: (string | number)[]; message: string }[]) {
  for (const issue of issues) {
    const path = issue.path.filter((p) => p !== 'settings');
    let input: HTMLElement | null = null;
    if (path[0] === 'queues' && typeof path[1] === 'number') {
      const q = draft?.working.queues[path[1]];
      selectedQueue = q?.id ?? selectedQueue;
      if (
        q &&
        (path[2] === 'ratingSettings' ||
          path[2] === 'ratingEngine' ||
          (path[2] === 'economy' && path[3] === 'taxMinuteUtc'))
      ) {
        selectView('setup');
        render();
        const name = String(path[3] ?? path[2]);
        input = app.querySelector<HTMLElement>(
          `[data-config="rating:${q.gameId}"] [name="${name}"]`,
        );
      } else {
        selectView('queues');
        render();
      }
    } else if (path[0] === 'games') {
      selectView('setup');
      render();
      const game = draft?.working.games[Number(path[1])];
      const form = app.querySelector<HTMLFormElement>(`[data-config="game:${game?.id}"]`);
      if (path[2] === 'records' && path[3] === 'ranks' && typeof path[4] === 'number') {
        const row = form?.querySelectorAll('[data-rank-row]')[path[4]];
        input =
          row?.querySelector<HTMLElement>(
            `[name$="-${path[5] === 'roleId' ? 'role' : path[5]}"]`,
          ) ?? null;
      } else if (form && typeof path.at(-1) === 'string')
        input = form.querySelector<HTMLElement>(`[name="${path.at(-1)}"]`);
    }
    input ??= app.querySelector<HTMLElement>(
      `[data-issue-path="${path.join('.').replaceAll('"', '')}"]`,
    );
    if (input) {
      input.setAttribute('aria-invalid', 'true');
      input.closest('details')?.setAttribute('open', '');
      const panel = input.closest<HTMLElement>('[data-category-panel]');
      if (panel) {
        category = panel.dataset.categoryPanel as Category;
        settingsSearch = '';
        applyCategories();
      }
      const id = `error-${path.join('-')}`;
      input.setAttribute('aria-describedby', id);
      input.insertAdjacentHTML(
        'afterend',
        `<p id="${escape(id)}" class="field-error" role="alert">${escape(issue.message)}</p>`,
      );
      input.focus();
      break;
    }
  }
}
function updateDirty() {
  const dirty = Boolean(draft?.dirty);
  document.body.classList.toggle('has-draft', dirty);
  const bar = app.querySelector<HTMLElement>('#savebar');
  if (bar) bar.hidden = !dirty;
  const saveButton = app.querySelector<HTMLButtonElement>('[data-edit="save"]');
  if (saveButton) saveButton.disabled = busy || !dirty;
  const reload = app.querySelector<HTMLElement>('[data-edit="reload-conflict"]');
  if (reload) reload.hidden = !conflict;
  app.querySelectorAll<HTMLFormElement>('form[data-config]').forEach((form) => {
    const changed = draft?.raw.has(form.dataset.config!);
    form.classList.toggle('is-dirty', Boolean(changed));
    const changedFields = draft!.changedFields(form.dataset.config!);
    form.querySelectorAll<HTMLElement>('[data-category-panel]').forEach((panel) => {
      const changed = [...panel.querySelectorAll<HTMLInputElement>('[name]')].some((el) =>
        changedFields.has(el.name),
      );
      const dot = app.querySelector<HTMLElement>(
        `[data-category="${panel.dataset.categoryPanel}"] .dirty-dot`,
      );
      if (dot) dot.hidden = !changed;
    });
  });
}
function applyCategories() {
  if (!(category in categories)) category = 'basics';
  const search = settingsSearch.trim().toLocaleLowerCase();
  let total = 0;
  app.querySelectorAll<HTMLElement>('[data-category-panel]').forEach((panel) => {
    let matches = 0;
    panel
      .querySelectorAll<HTMLElement>('[data-setting], .choice-list, .shared-setting')
      .forEach((row) => {
        row.hidden = Boolean(
          search &&
          !(row.dataset.search ?? row.textContent ?? '').toLocaleLowerCase().includes(search),
        );
        if (!row.hidden) matches++;
      });
    panel.hidden = search ? matches === 0 : panel.dataset.categoryPanel !== category;
    if (!panel.hidden) total += matches;
  });
  app
    .querySelectorAll<HTMLElement>('[data-category]')
    .forEach((tab) =>
      tab.setAttribute('aria-current', String(!search && tab.dataset.category === category)),
    );
  const empty = app.querySelector<HTMLElement>('[data-search-empty]');
  if (empty) empty.hidden = total > 0;
}
function matchRepairs(m: Match) {
  const active = ['playing', 'check_in'].includes(m.state);
  const options = [
    ['recover', 'Refresh / recover card'],
    ...(active
      ? [
          ['force_sub', 'Force replacement'],
          ['put', 'Place / move player'],
          ['recover_abort', 'Abort without ratings'],
          ['admin_report', 'Record result'],
        ]
      : []),
    ...(m.state === 'finished' ? [['undo', 'Undo result']] : []),
    ...(m.state === 'undone' ? [['admin_report', 'Record corrected result']] : []),
  ];
  return `<details class="rating-options"><summary>Administrator actions</summary><form data-kind="repair" data-id="${m.id}" class="repair-form">${['finished', 'undone'].includes(m.state) ? '<p class="muted">Undo reverses this result’s recorded rating changes and counts. Other matches remain unchanged. A corrected result uses current ratings; paid prediction settlements remain final.</p>' : '<p class="muted">Repair the saved match or record its result. You will review the action before applying it.</p>'}<div class="fields"><label>Match action<select name="kind">${options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select></label><label>Player ID<input name="target" placeholder="Outgoing player, or player to place" /></label><label>Replacement ID<input name="replacement" placeholder="New server member" /></label><label>Team / winner<select name="result"><option value="alpha">Alpha</option><option value="beta">Beta</option><option value="draw">Draw</option><option value="abort">Abort</option></select></label></div><button>Apply match action</button></form></details>`;
}
function queueChoice() {
  return `<label>Queue<select name="queueId">${snapshot!.settings.queues.map((q) => `<option value="${q.id}">${escape(q.name)} · #${escape(channelList.find((c) => c.id === q.channelId)?.name ?? q.channelId)}</option>`).join('')}</select></label>`;
}
function queueAdministration() {
  const queues = snapshot!.settings.queues;
  if (!queues.length) return '';
  return `<details class="rating-options"><summary>Queue controls and manual records</summary>${records!.waiting
    .filter((w) => queues.some((q) => q.id === w.queueId))
    .map((w) => {
      const q = queues.find((q) => q.id === w.queueId)!;
      return `<form data-kind="queue-control" data-id="${q.id}" class="repair-form"><h3>${escape(q.name)} · ${w.players.length} waiting</h3><p>${w.players.map((p) => escape(p.name)).join(' · ') || 'Queue is empty.'}</p><div class="fields"><label>Queue action<select name="kind"><option value="start">Start match</option><option value="split">Split into matches</option><option value="clear">Clear waiting queue</option></select></label><label>Group size<input name="groupSize" type="number" min="2" max="16" step="2" value="2" /></label><label class="toggle"><input name="skipCheckIn" type="checkbox" /> Skip check-in for this manual start</label><label class="toggle"><input name="sortByRating" type="checkbox" /> Group by rating before splitting</label></div><button>Apply queue action</button></form>`;
    })
    .join('')}
  <form data-kind="manual" class="repair-form"><h3>Record a game played outside the bot</h3><p class="muted">Enter player mentions or Discord IDs separated by spaces. This creates a ranked result and changes ratings.</p><div class="fields">${queueChoice()}<label>Alpha players<input name="alpha" required /></label><label>Beta players<input name="beta" required /></label><label>Winner<select name="result"><option value="alpha">Alpha</option><option value="beta">Beta</option><option value="draw">Draw</option></select></label></div><button>Record manual match</button></form></details>`;
}
function playerAdministration() {
  if (!snapshot!.settings.queues.length) return '';
  return `<details class="rating-options"><summary>Rating tools and restrictions</summary>
  <form data-kind="seed" class="repair-form"><h3>Seed a local rating</h3><div class="fields">${queueChoice()}<label>Player ID<input name="target" required /></label><label>Rating<input name="rating" type="number" min="0" max="10000" required /></label></div><button>Seed rating</button></form>
  <form data-kind="noadd" class="repair-form"><h3>Restrict queue access</h3><p class="muted">A restriction applies to this community’s queues.</p><div class="fields">${queueChoice()}<label>Player ID<input name="target" required /></label><label>Duration seconds<input name="seconds" type="number" min="1" max="1209600" value="60" required /></label><label>Reason<input name="reason" maxlength="200" /></label></div><button>Apply no-add</button></form>
  ${records!.noadds.map((b) => `<p>${escape(playerName(b.user_id))} · until ${escape(new Date(b.until_ms).toLocaleString())}${b.reason ? ` · ${escape(b.reason)}` : ''} <button data-action="forgive" data-id="${b.user_id}">Remove restriction</button></p>`).join('')}</details>`;
}
function diagnostics() {
  return `<details class="technical-details"><summary>Delivery diagnostics and audit</summary><h3>Discord deliveries</h3>${records!.delivery.map((d) => `<p>${escape(d.key)} · ${escape(d.status)}${d.error ? ` · ${escape(d.error)}` : ''}</p>`).join('') || '<p>No delivery tasks recorded.</p>'}<h3>Administrator activity</h3>${records!.audit.map((a) => `<p>${escape(new Date(a.at).toLocaleString())} · ${escape(a.action.replaceAll('_', ' '))} · ${escape(playerName(a.actor_id))}${a.match_id ? ` · match ${escape(a.match_id)}` : ''}</p>`).join('') || '<p>No administrator activity recorded.</p>'}</details>`;
}
async function repair(
  action: Omit<Action, 'actor' | 'admin' | 'target' | 'replacement' | 'teams'> & {
    target?: string;
    replacement?: string;
    teams?: [string, string];
  },
) {
  const result = await api<{ reply: { content: string }; records: Records }>(
    communityUrl('actions'),
    { method: 'POST', body: JSON.stringify(action) },
  );
  records = result.records;
  setup.gameState = records.gameState ?? setup.gameState;
  render();
  notice(result.reply.content);
}
async function submitRepair(form: HTMLFormElement, data: FormData) {
  const chosenKind = String(data.get('kind') ?? form.dataset.kind);
  const kind = (chosenKind === 'manual' ? 'manual_create' : chosenKind) as Action['kind'];
  const match =
    form.dataset.kind === 'repair' ? records?.matches.find((m) => m.id === form.dataset.id) : null;
  const queue = snapshot!.settings.queues.find(
    (q) =>
      q.id ===
      (form.dataset.kind === 'queue-control' ? form.dataset.id : String(data.get('queueId') ?? '')),
  );
  const action: Parameters<typeof repair>[0] = {
    id: (form.dataset.actionId ??= crypto.randomUUID()),
    kind,
    channelId: match?.queue.channelId ?? queue!.channelId,
  };
  if (match) {
    action.matchId = match.id;
    action.version = match.version;
  }
  if (queue) action.queue = queue.id;
  if (['force_sub', 'put', 'seed', 'noadd'].includes(kind))
    action.target = String(data.get('target') ?? '').trim();
  if (kind === 'force_sub') action.replacement = String(data.get('replacement') ?? '').trim();
  if (kind === 'put') action.side = String(data.get('result')) as Action['side'];
  if (['admin_report', 'manual_create'].includes(kind))
    action.result = String(data.get('result')) as Action['result'];
  if (kind === 'manual_create')
    action.teams = [String(data.get('alpha')), String(data.get('beta'))];
  if (kind === 'start') action.skipCheckIn = data.has('skipCheckIn');
  if (kind === 'split') {
    action.groupSize = Number(data.get('groupSize'));
    action.sortByRating = data.has('sortByRating');
  }
  if (kind === 'seed') action.rating = Number(data.get('rating'));
  if (kind === 'noadd') {
    action.seconds = Number(data.get('seconds'));
    action.reason = String(data.get('reason') ?? '');
  }
  const prompt =
    kind === 'undo'
      ? `Undo match ${match!.id}? Reverse its recorded rating changes and counts, keeping other results and the audit.`
      : ['admin_report', 'manual_create', 'seed'].includes(kind)
        ? `Apply ${kind === 'seed' ? `rating ${action.rating} to player ${action.target}` : `ranked result ${action.result} for ${match?.id ?? 'these teams'}`}? This changes local ratings and history.`
        : kind === 'clear'
          ? `Clear all waiting players from ${queue!.name}?`
          : kind === 'force_sub'
            ? `Replace player ${action.target} with ${action.replacement} in match ${match!.id}?`
            : kind === 'put'
              ? `Place player ${action.target} on ${action.side === 'alpha' ? 'Alpha' : 'Beta'} in match ${match!.id}?`
              : kind === 'recover_abort'
                ? `Abort match ${match!.id} without rating changes?`
                : kind === 'noadd'
                  ? `Prevent player ${action.target} from joining this community's queues for ${action.seconds} seconds?`
                  : kind === 'split'
                    ? `Split ${queue!.name} into ${action.groupSize}-player matches${action.sortByRating ? ', grouping by rating' : ''}? Leftovers stay queued.`
                    : kind === 'start'
                      ? `Start ${queue!.name}${action.skipCheckIn ? ' with check-in skipped for this match' : ' with its saved rules'}?`
                      : `Refresh the saved card for match ${match!.id}?`;
  const fingerprint = JSON.stringify(action);
  if (form.dataset.review !== fingerprint) {
    form.dataset.review = fingerprint;
    form.querySelector('[data-review]')?.remove();
    form.insertAdjacentHTML(
      'beforeend',
      `<div class="notice" data-review>${escape(prompt)} <button type="button" data-action="cancel-repair">Cancel</button></div>`,
    );
    const submit = form.querySelector<HTMLButtonElement>('button:not([type="button"])')!;
    submit.dataset.originalLabel ??= submit.textContent!;
    submit.textContent = 'Confirm action';
    return;
  }
  await repair(action);
}
function cancelRepair(form: HTMLFormElement) {
  delete form.dataset.review;
  delete form.dataset.actionId;
  form.querySelector('[data-review]')?.remove();
  const submit = form.querySelector<HTMLButtonElement>('button[data-original-label]');
  if (submit) {
    submit.textContent = submit.dataset.originalLabel!;
    delete submit.dataset.originalLabel;
  }
}
function recordSection() {
  if (!records) return '';
  const blocked = records.delivery.filter((w) => w.status === 'blocked');
  const returns = records.pendingReturns ?? [];
  const filters = currentFilters();
  const queues = new Map(
    [...snapshot!.settings.queues, ...records.matches.map((m) => m.queue)].map((q) => [q.id, q]),
  );
  const matchFilters = `<div class="record-filters"><label>Filter matches by queue<select data-filter="match-queue"><option value="">All queues</option>${[...queues.values()].map((q) => `<option value="${q.id}"${selected(q.id, filters.matchQueue)}>${escape(q.name)} · ${escape(gameName(q.gameId))}</option>`).join('')}</select></label><label>Match status<select data-filter="match-state"><option value="">All statuses</option>${Object.entries(
    {
      check_in: 'Checking in',
      playing: 'Awaiting result',
      finished: 'Finished',
      aborted: 'Cancelled',
      check_in_failed: 'Check-in incomplete',
      undone: 'Result withdrawn',
    },
  )
    .map(
      ([value, label]) =>
        `<option value="${value}"${selected(value, filters.matchState)}>${label}</option>`,
    )
    .join(
      '',
    )}</select></label></div><p class="record-meta" data-filter-count="match" role="status" aria-live="polite"></p>`;
  const stateName = (m: Match) =>
    m.state === 'finished'
      ? m.result === 'draw'
        ? 'Draw'
        : `${m.result === 'alpha' ? 'Alpha' : 'Beta'} wins`
      : {
          check_in: 'Checking in',
          playing: 'Awaiting result',
          aborted: 'Cancelled',
          check_in_failed: 'Check-in incomplete',
          undone: 'Result withdrawn',
        }[m.state];
  return `<section class="panel"><div class="section-heading"><h2>Matches</h2></div><p class="muted">Recent games and their recorded results.</p>${matchFilters}${blocked.length ? `<p class="notice error">${blocked.length} Discord card delivery task(s) need attention. Check channel permissions in Setup, then refresh the affected match under Administrator actions.</p>` : ''}
    ${returns.length ? `<details><summary>Players awaiting return after a timeout</summary><p class="muted">Overflow waits for a free place. Blocked returns need a fresh join after checking membership and roles.</p>${returns.map((r) => `<p>${escape(r.name)} · ${escape(snapshot?.settings.queues.find((q) => q.id === r.queue_id)?.name ?? r.queue_id)} · ${escape(r.status)}</p>`).join('')}</details>` : ''}
    <div class="record-list">${records.matches.map((m) => `<details class="match-record" data-match-record data-queue="${m.queue.id}" data-state="${m.state}"${['playing', 'check_in'].includes(m.state) ? ' open' : ''}><summary>${escape(m.queue.name)} · ${escape(m.gameName)} <span class="status-pill">${escape(stateName(m))}</span><span class="record-meta">${escape(new Date(m.createdAt).toLocaleString())}</span></summary><div class="record-teams">${m.teams.map((team, side) => `<div><strong>${side === 0 ? 'Alpha' : 'Beta'}</strong>${team.map((p) => `<p>${escape(p.name)} · ${p.rating}${m.captains[side] === p.id ? ' · captain' : ''}</p>`).join('')}</div>`).join('')}</div>${m.changes.length ? `<h3>Rating changes</h3><p>${m.changes.map((c) => `${escape(m.roster.find((p) => p.id === c.userId)?.name ?? c.userId)}: ${c.before} → ${c.after}`).join(' · ')}</p>` : ''}${m.note ? `<p>${escape(m.note)}</p>` : ''}${matchRepairs(m)}<details class="technical-details"><summary>Record identifiers</summary><p>Match ${escape(m.id)}</p>${m.roster.map((p) => `<p>${escape(p.name)} · ${escape(p.id)}</p>`).join('')}</details></details>`).join('')}<p class="empty-state" data-filter-empty="match" hidden></p></div>
    ${queueAdministration()}${diagnostics()}</section>`;
}
function playerSection() {
  if (!records) return '';
  const economy = records.economy;
  const filters = currentFilters();
  const playerFilters = `<div class="table-toolbar"><label>Filter players by game<select data-filter="player-game"><option value="">All games</option>${snapshot!.settings.games.map((g) => `<option value="${g.id}"${selected(g.id, filters.playerGame)}>${escape(g.name)}</option>`).join('')}</select></label><label>Find a player<input type="search" data-filter="player-search" value="${escape(filters.playerSearch)}" placeholder="Player name" /></label></div>`;
  const matchName = (id: string) => {
    const match = records!.matches.find((m) => m.id === id);
    return match
      ? `${match.queue.name} · ${new Date(match.createdAt).toLocaleString()}`
      : 'Earlier match';
  };
  return `<section class="panel"><div class="section-heading"><h2>Player ratings</h2></div><p class="muted">Ratings and gold are shared by queues for the same game. These filters apply to ratings and gold balances.</p>${playerFilters}<p class="record-meta" data-filter-count="rating" role="status" aria-live="polite"></p><div class="table-wrap" data-filter-table="rating"><table><thead><tr><th scope="col">Player</th><th scope="col">Game</th><th scope="col">Rating</th><th scope="col">W / L / D</th></tr></thead><tbody data-rating-body>${playerRatingRows()}</tbody></table></div><p class="empty-state" data-filter-empty="rating" hidden></p><div class="actions"><button type="button" data-action="load-players"${records.players.length < (records.playersTotal ?? records.players.length) ? '' : ' hidden'}>Load more players</button></div>${playerAdministration()}</section>
    ${economy && (economy.wallets.length || snapshot!.settings.queues.some((q) => q.modules.gold)) ? `<section class="panel"><h2>Gold and predictions</h2><p class="record-meta" data-filter-count="wallet" role="status" aria-live="polite"></p><div class="table-wrap" data-filter-table="wallet"><table><thead><tr><th scope="col">Player</th><th scope="col">Game</th><th scope="col">Gold</th></tr></thead><tbody>${economy.wallets.map((w) => `<tr data-wallet-row data-game="${w.game_id}" data-player-search="${escape(playerName(w.user_id, w.game_id).toLocaleLowerCase())}"><td>${escape(playerName(w.user_id, w.game_id))}</td><td>${escape(gameName(w.game_id))}</td><td>${w.balance}</td></tr>`).join('')}</tbody></table></div><p class="empty-state" data-filter-empty="wallet" hidden></p><details class="rating-options"><summary>Gold ledger and prediction history</summary><h3>Gold activity</h3>${economy.ledger.map((l) => `<p>${escape(playerName(l.user_id))} · ${escape(l.kind.replaceAll('_', ' '))} · ${l.amount >= 0 ? '+' : ''}${l.amount} · balance ${l.balance_after}</p>`).join('') || '<p>No gold activity recorded.</p>'}<h3>Prediction pools</h3>${economy.books.map((b) => `<details><summary>${escape(matchName(b.match_id))} · ${escape(b.status)}</summary><p>${escape(b.reason ?? b.result ?? 'Open')}${b.status === 'settled' ? ' · Paid settlement remains final after corrections.' : ''}</p><p class="record-meta">Match ${escape(b.match_id)}</p></details>`).join('') || '<p>No prediction pools recorded.</p>'}</details></section>` : ''}
    ${onboarding(snapshot!, records.onboarding)}`;
}
function setupSection() {
  const configured = new Set(snapshot!.settings.queues.map((q) => q.channelId));
  const missing = (channel: Channel) => channel.permissions.filter((p) => !p.granted);
  const configuredChannels = channelList
    .filter((c) => configured.has(c.id))
    .sort((a, b) => Number(Boolean(missing(b).length)) - Number(Boolean(missing(a).length)));
  const otherChannels = channelList
    .filter((c) => !configured.has(c.id))
    .sort((a, b) => Number(Boolean(missing(b).length)) - Number(Boolean(missing(a).length)));
  const channelRow = (c: Channel) =>
    `<div class="channel-row${missing(c).length ? ' channel-error' : ''}"><strong>#${escape(c.name)}</strong><span>${
      missing(c).length
        ? `Missing: ${missing(c)
            .map((p) => escape(p.name))
            .join(', ')}`
        : 'Required permissions granted'
    }</span></div>`;
  const inaccessible = [...configured].filter((id) => !channelList.some((c) => c.id === id));
  return `<div class="page-heading"><div><p class="eyebrow">Make it yours</p><h1>Community setup</h1><p class="view-description">Games, shared ratings and Discord permissions.</p></div></div>${setupJourney()}${gameEditors(draft!, setup)}
    <section class="panel"><div class="section-heading"><h2>Discord access</h2>${bootstrap.inviteUrl ? `<a class="button" data-discord-invite target="_blank" rel="noopener" href="${escape(bootstrap.inviteUrl)}&guild_id=${guildId}&disable_guild_select=true">Invite bot to server</a>` : ''}</div><p class="notice${setup.access.status === 'ready' ? '' : ' error'}">${escape(setup.access.message)}</p><button type="button" data-action="check-access">Check access again</button><p class="muted">Check the channels used by your queues. Optional rank roles need Manage Roles; nickname decoration needs Manage Nicknames.</p><div class="channel-list">${inaccessible.map((id) => `<p class="notice error">The bot cannot access configured channel ${escape(id)}. Check its Discord permissions or choose another channel in Queues.</p>`).join('')}${configuredChannels.map(channelRow).join('')}${!configured.size ? '<p class="empty-state">No queue channels selected yet.</p>' : ''}</div>
    ${otherChannels.length ? `<details class="rating-options"><summary>Other available channels (${otherChannels.length})</summary><div class="channel-list">${otherChannels.map(channelRow).join('')}</div></details>` : ''}${!channelList.length ? '<p class="notice error">No accessible text channels found. Check the bot’s channel permissions.</p>' : ''}<details class="technical-details"><summary>Configuration details</summary><p>Saved settings revision ${snapshot!.revision}</p><p>Community ${escape(guildId)}</p></details></section>`;
}
function setupJourney() {
  const saved = snapshot!.settings;
  const ready =
    setup.access.status === 'ready' &&
    saved.queues.some(
      (q) =>
        q.enabled &&
        setup.channels.some((c) => c.id === q.channelId && c.permissions.every((p) => p.granted)),
    );
  const pending =
    records?.delivery.filter((d) => d.status !== 'sent' && d.status !== 'done').length ?? 0;
  const steps = [true, setup.access.status === 'ready', saved.games.length > 0, ready];
  return `<section class="panel setup-journey"><div class="section-heading"><h2>${ready ? 'Ready to play' : 'Set up your first game'}</h2><span class="status-pill">${steps.filter(Boolean).length} / 4 complete</span></div><ol class="setup-steps">${['Choose community', 'Verify Discord access', 'Add a game', 'Save an enabled queue'].map((label, i) => `<li class="${steps[i] ? 'complete' : ''}"><span>${steps[i] ? '✓' : i + 1}</span>${label}</li>`).join('')}</ol><p class="form-help">Progress reflects saved settings and verified access.${pending ? ` ${pending} recent delivery task(s) are not complete; check delivery diagnostics.` : ''}</p><div class="actions">${!steps[1] && bootstrap.inviteUrl ? `<a class="button primary" data-discord-invite target="_blank" rel="noopener" href="${escape(bootstrap.inviteUrl)}&guild_id=${guildId}&disable_guild_select=true">Add to Discord</a><button type="button" data-action="check-access">I’m back — check access</button>` : ''}${steps[2] ? '<button type="button" data-view="queues">Configure a queue</button>' : ''}</div></section>`;
}
function overviewSection() {
  const saved = snapshot!.settings;
  const active =
    records?.matches.filter((m) => ['playing', 'check_in'].includes(m.state)).length ?? 0;
  const problems = records?.delivery.filter((d) => d.error) ?? [];
  return `<div class="page-heading"><div><p class="eyebrow">Your community, ready to play</p><h1>Overview</h1><p class="view-description">Organize games here. Join and play together in Discord.</p></div><button type="button" data-view="queues" class="primary">Manage queues</button></div>${setupJourney()}<div class="overview-grid"><section class="panel metric"><span>Games</span><strong>${saved.games.length}</strong></section><section class="panel metric"><span>Enabled queues</span><strong>${saved.queues.filter((q) => q.enabled).length}</strong></section><section class="panel metric"><span>Active in recent matches</span><strong>${active}</strong><small>From the latest ${records?.matches.length ?? 0} records</small></section></div><section class="panel"><h2>Delivery health</h2>${problems.length ? problems.map((p) => `<p class="notice error">${escape(p.error)}</p>`).join('') : '<p>No errors in the loaded delivery tasks.</p>'}<p class="form-help">Shows recent tasks, not an all-time health total.</p><button type="button" data-view="matches">Open delivery diagnostics</button></section>`;
}
function render() {
  // A render replaces the import inputs, so any previous review must expire with them.
  invalidateImport();
  const openGames = [...app.querySelectorAll<HTMLDetailsElement>('.game-settings[open]')].map(
    (el) => el.id,
  );
  if (!bootstrap.user) {
    document.body.classList.remove('organizer');
    app.innerHTML = `<section class="intro"><p class="eyebrow">A better game night</p><h1>Your community.<br>Your rules.</h1><p class="lead">Set up Discord queues, manage game-night rules and review results in one place.</p><div class="actions">${bootstrap.oauthReady ? `<a class="button primary" href="/auth/login?returnTo=${encodeURIComponent(location.pathname + location.search)}">Sign in with Discord</a>` : '<p class="notice">Discord sign-in is not configured yet.</p>'}${bootstrap.inviteUrl ? `<a class="button" href="${escape(bootstrap.inviteUrl)}">Add to Discord</a>` : ''}</div><div id="notice" hidden></div></section>`;
    return;
  }
  document.body.classList.add('organizer');
  const current = guilds.find((g) => g.id === guildId);
  const iconPaths = {
    overview: '<path d="m3 10 9-7 9 7v11h-6v-7H9v7H3z"/>',
    queues:
      '<path d="M9 5h12M9 12h12M9 19h12"/><circle cx="3" cy="5" r="1"/><circle cx="3" cy="12" r="1"/><circle cx="3" cy="19" r="1"/>',
    matches: '<path d="M4 21V3h15l-3 5 3 5H4"/>',
    players: '<circle cx="12" cy="7" r="4"/><path d="M4 21v-3a8 8 0 0 1 16 0v3z"/>',
    setup: '<path d="M4 3v7m0 4v7M12 3v3m0 4v11M20 3v11m0 4v3M1 10h6m2-4h6m2 8h6"/>',
  };
  const icons = Object.fromEntries(
    Object.entries(iconPaths).map(([key, paths]) => [
      key,
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`,
    ]),
  );
  app.innerHTML = `<div class="organizer-layout"><aside class="organizer-sidebar"><p class="eyebrow">Workspace</p><label class="desktop-community">Community<select id="community"><option value="">Choose a server</option>${guilds.map((g) => `<option value="${g.id}"${selected(g.id, guildId)}>${escape(g.name)}</option>`).join('')}</select></label><nav class="workspace-nav" role="tablist" aria-label="Organizer sections">${organizerViews.map((view) => `<button type="button" class="workspace-tab" id="tab-${view}" role="tab" data-view="${view}" aria-controls="view-${view}" aria-selected="false" tabindex="-1"><span aria-hidden="true" class="nav-icon">${icons[view]}</span><span>${viewNames[view]}</span></button>`).join('')}</nav><div class="sidebar-footer"><p>Community Games</p><span>Play together. Make it yours.</span></div></aside><div class="workspace-content"><div class="context-bar"><button type="button" class="mobile-menu icon-button" data-edit="drawer" aria-label="Choose community or queue">☰</button><span class="context-name">${escape(current?.name ?? 'Choose your community')}</span><button type="button" data-action="refresh-records" aria-label="Refresh records">Refresh</button><button type="button" data-action="logout" aria-label="Sign out ${escape(bootstrap.user.username)}">Sign out</button></div><div id="notice" class="notice" role="status" hidden></div><div class="workspace-scroll">
    ${!guilds.length ? '<section class="panel"><h1>No communities yet</h1><p>You need Manage Server permission to configure a community.</p></section>' : ''}
    ${guildId && !snapshot ? '<section class="panel"><h1>Checking your community…</h1><p>Loading saved settings and Discord permissions.</p><button data-action="reload">Retry</button></section>' : ''}
    ${snapshot && draft ? organizerViews.map((view) => `<section class="workspace-panel" role="tabpanel" id="view-${view}" data-view-panel="${view}" aria-labelledby="tab-${view}" hidden>${{ overview: overviewSection, queues: () => queueEditor(draft!, setup, selectedQueue), matches: recordSection, players: playerSection, setup: setupSection }[view]()}</section>`).join('') : ''}
    </div><div id="savebar" class="savebar" hidden><span><strong>Unsaved changes</strong><small>Applies across this community</small></span><button type="button" data-edit="reload-conflict" hidden>Reload latest</button><button type="button" data-edit="discard">Discard</button><button type="button" class="primary" data-edit="save">Save changes</button></div></div></div>`;
  if (snapshot && draft) {
    selectView(currentView(), false, false);
    draft.mount(app);
    applyCategories();
    applyFilters();
    labelTables(app);
    updateDirty();
    const search = app.querySelector<HTMLInputElement>('[data-settings-search]');
    if (search) search.value = settingsSearch;
    openGames.forEach((id) => document.getElementById(id)?.setAttribute('open', ''));
    const selectedGame = new URL(location.href).searchParams.get('game');
    if (selectedGame)
      document.getElementById(`game-settings-${selectedGame}`)?.setAttribute('open', '');
    if (busy)
      app
        .querySelectorAll<
          HTMLInputElement | HTMLButtonElement | HTMLSelectElement | HTMLTextAreaElement
        >('input,button,select,textarea')
        .forEach((el) => (el.disabled = true));
  }
}
app.addEventListener('submit', (event) => {
  event.preventDefault();
  const form = (event.target as Element).closest<HTMLFormElement>('form');
  if (!form || !snapshot || !draft) return;
  if (form.dataset.config) {
    draft.capture(form);
    updateDirty();
    return;
  }
  if (form.hasAttribute('data-add-game')) {
    const data = fields(form);
    const item = gameCatalog.find((g) => g.id === value(data, 'catalogId'));
    const name = item?.name ?? value(data, 'name').trim();
    if (!name) {
      notice('Enter a custom game name or choose a catalog game.', true);
      return;
    }
    draft.working.games.push({ id: crypto.randomUUID(), name, catalogId: item?.id ?? null });
    render();
    return;
  }
  const data = new FormData(form);
  void run(async () => {
    if (
      await onboardingSubmit(form, data, snapshot!, {
        api,
        url: communityUrl,
        reload: refreshRecords,
        notice,
      })
    ) {
      labelTables(app);
      return;
    }
    if (['repair', 'queue-control', 'manual', 'seed', 'noadd'].includes(form.dataset.kind ?? ''))
      await submitRepair(form, data);
  });
});
app.addEventListener('click', (event) => {
  if (handleEditorClick(event)) return;
  const tab = (event.target as Element).closest<HTMLButtonElement>('[data-view]');
  if (tab && !busy && isOrganizerView(tab.dataset.view ?? null)) {
    selectView(tab.dataset.view as OrganizerView);
    return;
  }
  const button = (event.target as Element).closest<HTMLElement>('[data-action]');
  if (!button) return;
  void run(async () => {
    switch (button.dataset.action) {
      case 'refresh-records':
        if (snapshot) await refreshRecords();
        else if (guildId) await loadCommunity(guildId);
        break;
      case 'load-players':
        await loadMorePlayers();
        break;
      case 'cancel-repair':
        cancelRepair(button.closest('form')!);
        break;
      case 'apply-import':
        await applyImport({
          api,
          url: communityUrl,
          reload: refreshRecords,
          notice,
        });
        break;
      case 'logout':
        if (!canLeave()) break;
        await api('/api/logout', { method: 'POST' });
        draft = snapshot ? new SettingsDraft(snapshot) : null;
        location.reload();
        break;
      case 'check-access':
        await refreshSetup();
        break;
      case 'reload':
        if (!canLeave()) break;
        if (guildId) await loadCommunity(guildId);
        else location.reload();
        break;
      case 'forgive': {
        const q = snapshot?.settings.queues[0];
        if (q)
          await repair({
            id: crypto.randomUUID(),
            kind: 'forgive',
            channelId: q.channelId,
            queue: q.id,
            target: button.dataset.id,
          });
        break;
      }
    }
  });
});
app.addEventListener('keydown', (event) => {
  const tab = (event.target as Element).closest<HTMLButtonElement>('[role=tab][data-view]');
  if (!tab || busy || !isOrganizerView(tab.dataset.view ?? null)) return;
  const position = organizerViews.indexOf(tab.dataset.view as OrganizerView);
  const next =
    event.key === 'ArrowRight'
      ? (position + 1) % organizerViews.length
      : event.key === 'ArrowLeft'
        ? (position + organizerViews.length - 1) % organizerViews.length
        : event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? organizerViews.length - 1
            : null;
  if (next === null) return;
  event.preventDefault();
  selectView(organizerViews[next], true);
});
app.addEventListener('change', (event) => {
  const input = event.target as HTMLInputElement;
  if (input.dataset.filter) updateFilter(input);
  const form = input.closest('form');
  if (form?.dataset.review) cancelRepair(form);
  if (input.id === 'community' && input.value) {
    if (canLeave()) void run(() => loadCommunity(input.value));
    else input.value = guildId;
  }
});
app.addEventListener('input', (event) => {
  const input = event.target as HTMLInputElement;
  if (input.dataset.filter === 'player-search') updateFilter(input);
});

function editRoute() {
  const url = new URL(location.href);
  url.searchParams.set('queue', selectedQueue);
  url.searchParams.set('section', category);
  if (url.href !== location.href) history.pushState(null, '', url);
}
function captureEdit(event: Event) {
  const input = event.target as HTMLInputElement;
  const form = input.closest<HTMLFormElement>('form[data-config]');
  if (form && draft) {
    draft.capture(form);
    input.removeAttribute('aria-invalid');
    input.parentElement?.querySelector('.field-error')?.remove();
    if (input.type === 'checkbox') {
      const text = input.closest('.switch-control')?.querySelector('.switch-text');
      if (text) text.textContent = input.checked ? 'On' : 'Off';
    }
    updateDirty();
    if (input.name === 'gameId' && event.type === 'change') render();
  }
  if (input.hasAttribute('data-settings-search')) {
    settingsSearch = input.value;
    applyCategories();
  }
  if (input.hasAttribute('data-queue-select')) {
    selectedQueue = input.value;
    settingsSearch = '';
    editRoute();
    render();
  }
  if (input.closest('[data-kind="import-preview"]')) invalidateImport();
}
app.addEventListener('input', captureEdit);
app.addEventListener('change', captureEdit);
function handleEditorClick(event: MouseEvent): boolean {
  const target = event.target as Element;
  if (target.closest('[data-discord-invite]')) awaitingDiscordReturn = true;
  const gameSummary = target.closest<HTMLElement>('.game-settings > summary');
  if (gameSummary) {
    const details = gameSummary.parentElement as HTMLDetailsElement;
    const url = new URL(location.href);
    if (details.open) url.searchParams.delete('game');
    else url.searchParams.set('game', details.id.replace('game-settings-', ''));
    if (url.href !== location.href) history.pushState(null, '', url);
  }
  if (target.closest('[data-discard-import]')) {
    invalidateImport();
    return true;
  }
  const help = target.closest<HTMLElement>('[data-help]');
  if (help) {
    openDialog(help.dataset.helpTitle!, `<p>${escape(help.dataset.help)}</p>`);
    return true;
  }
  const reset = target.closest<HTMLElement>('[data-reset]');
  if (reset && !busy) {
    const input = reset
      .closest('form')!
      .elements.namedItem(reset.dataset.reset!) as HTMLInputElement;
    input.value = reset.dataset.default!;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }
  const cat = target.closest<HTMLElement>('[data-category]');
  if (cat) {
    category = cat.dataset.category as Category;
    settingsSearch = '';
    const search = app.querySelector<HTMLInputElement>('[data-settings-search]');
    if (search) search.value = '';
    editRoute();
    applyCategories();
    return true;
  }
  const gameLink = target.closest<HTMLElement>('[data-game-link]');
  if (gameLink) {
    selectView('setup');
    const url = new URL(location.href);
    url.searchParams.set('game', gameLink.dataset.gameLink!);
    history.replaceState(null, '', url);
    const details = document.getElementById(
      `game-settings-${gameLink.dataset.gameLink}`,
    ) as HTMLDetailsElement;
    if (details) {
      details.open = true;
      details.scrollIntoView({ block: 'start' });
    }
    return true;
  }
  const rank = target.closest<HTMLElement>('[data-add-rank], [data-remove-rank]');
  if (rank && !busy && draft) {
    const form = rank.closest<HTMLFormElement>('form')!;
    if (rank.hasAttribute('data-remove-rank')) rank.closest('[data-rank-row]')!.remove();
    else {
      const indexes = [...form.querySelectorAll<HTMLInputElement>('[name$="-threshold"]')].map(
        (el) => Number(el.name.split('-')[1]),
      );
      form.querySelector('[data-rank-rows]')!.insertAdjacentHTML(
        'beforeend',
        rankRow(
          Math.max(-1, ...indexes) + 1,
          undefined,
          setup.roles.map((r) => ({
            id: r.id,
            name: r.name + (r.reason ? ` — ${r.reason}` : ''),
            disabled: !r.rankEligible,
          })),
        ),
      );
    }
    draft.capture(form);
    updateDirty();
    return true;
  }
  const action = target.closest<HTMLElement>('[data-edit]');
  if (!action) return false;
  if (busy) return true;
  if (action.dataset.edit === 'drawer') {
    const dialog = openDialog(
      'Your community',
      `<nav aria-label="Communities">${guilds.map((g) => `<button type="button" class="drawer-link" data-community="${g.id}" ${g.id === guildId ? 'aria-current="true"' : ''}>${escape(g.name)}</button>`).join('')}</nav><h3>Queues</h3>${(draft?.working.queues ?? []).map((q) => `<button type="button" class="drawer-link" data-drawer-queue="${q.id}">${escape(q.name)}</button>`).join('')}<button type="button" class="drawer-link" data-drawer-setup>Community setup</button>`,
      'drawer',
    );
    dialog.addEventListener('click', (e) => {
      const b = (e.target as Element).closest<HTMLElement>('button');
      if (!b) return;
      if (b.dataset.community && (b.dataset.community === guildId || canLeave())) {
        dialog.close();
        if (b.dataset.community !== guildId) void run(() => loadCommunity(b.dataset.community!));
      }
      if (b.dataset.drawerQueue) {
        selectedQueue = b.dataset.drawerQueue;
        dialog.close();
        selectView('queues');
        editRoute();
        render();
      }
      if (b.hasAttribute('data-drawer-setup')) {
        dialog.close();
        selectView('setup');
      }
    });
    return true;
  }
  if (!draft || !snapshot) return true;
  if (action.dataset.edit === 'add-queue') {
    selectedQueue = draft.addQueue(draft.working.games[0].id, setup.channels[0].id);
    category = 'basics';
    settingsSearch = '';
    editRoute();
    render();
  }
  if (
    action.dataset.edit === 'remove-queue' &&
    confirm('Remove this queue when you save changes? Its player and match records will remain.')
  ) {
    draft.removeQueue(action.dataset.id!);
    selectedQueue = '';
    render();
  }
  if (
    action.dataset.edit === 'discard' &&
    confirm('Discard all unsaved settings for this community?')
  ) {
    draft = new SettingsDraft(snapshot);
    conflict = false;
    render();
  }
  if (action.dataset.edit === 'reload-conflict' && canLeave())
    void run(() => loadCommunity(guildId));
  if (action.dataset.edit === 'save') {
    const result = draft.validate();
    if (!result.success) {
      showIssues(
        result.error.issues.map((i) => ({
          path: i.path as (string | number)[],
          message: i.message,
        })),
      );
      notice(result.error.issues.map((i) => i.message).join(' '), true);
    } else void run(() => save(result.data));
  }
  return true;
}
window.addEventListener('beforeunload', (event) => {
  if (draft?.dirty) {
    event.preventDefault();
    event.returnValue = '';
  }
});
window.addEventListener('popstate', () => {
  if (location.pathname === '/stats') return;
  const url = new URL(location.href);
  const id = url.searchParams.get('community');
  if (id && id !== guildId) {
    if (!busy && canLeave()) void run(() => loadCommunity(id));
    else {
      url.searchParams.set('community', guildId);
      history.pushState(null, '', url);
    }
    return;
  }
  const view = url.searchParams.get('view');
  if (isOrganizerView(view)) viewsByCommunity.set(guildId, view);
  selectedQueue = url.searchParams.get('queue') ?? '';
  category = (url.searchParams.get('section') ?? 'basics') as Category;
  render();
});
installViewport();
const recheckAfterDiscord = () => {
  if (!awaitingDiscordReturn || document.visibilityState === 'hidden' || busy || !snapshot) return;
  awaitingDiscordReturn = false;
  void run(refreshSetup);
};
window.addEventListener('focus', recheckAfterDiscord);
document.addEventListener('visibilitychange', recheckAfterDiscord);

async function start() {
  bootstrap = await api<Bootstrap>('/api/bootstrap');
  document.querySelector('#environment')!.textContent = bootstrap.environment.toUpperCase();
  document.querySelector('#version')!.textContent = `v${bootstrap.version}`;
  if (bootstrap.user) {
    guilds = (await api<{ communities: Guild[] }>('/api/communities')).communities;
    render();
    const requested = new URL(location.href).searchParams.get('community');
    const first = guilds.find((g) => g.id === requested) ?? guilds[0];
    if (first) await loadCommunity(first.id);
  } else render();
  app.setAttribute('aria-busy', 'false');
}
(location.pathname === '/stats' ? showStats(app) : start()).catch((error) => {
  if (!document.querySelector('#notice'))
    app.innerHTML =
      '<h1>Setup could not load.</h1><div id="notice" role="alert"></div><a class="button" href="/">Try again</a>';
  notice(error instanceof Error ? error.message : 'Try again.', true);
  app.setAttribute('aria-busy', 'false');
});
