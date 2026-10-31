import { identity } from '../src/identity';
import { playerGames } from './player-games';
import { publicGameListText, type PublicGameList } from '../src/games/discovery';
import type { ProviderResult } from '../src/games/types';
import { matchConnectorDetails, type ConnectorDiagnostics } from './connectors';
import { showAccountLink } from './account-link';
import {
  guidedSetup,
  permissionRecovery,
  savedReady,
  guideSteps,
  type GuideStep,
  type InviteUrls,
} from './guide';
import { changeReview, changesBetween } from './change-review';
import { SettingsDraft, fields, value } from './draft';
import {
  queueEditor,
  settingHelp,
  settingReadouts,
  gameEditors,
  categories,
  rankRow,
  type Category,
  type EditorSetup,
} from './settings';
import {
  ApiError,
  openDialog,
  labelTables,
  installViewport,
  requestJson,
  rememberFocus,
} from './ui';
import type { GameState } from '../src/domain/setup';
import { gameCatalog } from '../src/games/catalog';
import './style.css';
import './responsive.css';
import './theme.css';
import {
  onboarding,
  onboardingSubmit,
  applyImport,
  invalidateImport,
  importRecordsRefreshed,
  pageImportRows,
} from './onboarding';
import { showStats } from './stats';
import { type Settings, type Snapshot } from '../src/domain/config';
import { type Player } from '../src/domain/rating';
import type { Action, Match } from '../src/domain/play';
interface Records {
  gameState: GameState[];
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
  players: (Player & { gameId: string; hidden?: boolean })[];
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
  inviteUrls?: InviteUrls;
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
let connectorDiagnostics: ConnectorDiagnostics | null = null;
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
const guideCreatedGames = new Set<string>();
type Issue = { path: (string | number)[]; message: string };
type FieldIssue = Issue & { form: string; name: string };
let validationIssues: FieldIssue[] = [];
let currentNotice: { message: string; error: boolean } | null = null;
let historyIndex = Number(history.state?.organizerIndex ?? 0);
let acceptedUrl = location.href;
let restoringHistory = false;
function writeRoute(url: URL, mode: 'push' | 'replace' = 'push') {
  if (url.href !== location.href || mode === 'replace') {
    if (mode === 'push') historyIndex++;
    history[mode === 'push' ? 'pushState' : 'replaceState'](
      { organizerIndex: historyIndex },
      '',
      url,
    );
  }
  acceptedUrl = url.href;
}
const canLeave = () =>
  !draft?.dirty || confirm('Discard your unsaved settings and leave this community?');
const organizerViews = ['queues', 'matches', 'players', 'setup'] as const;
type OrganizerView = (typeof organizerViews)[number];
const viewNames: Record<OrganizerView, string> = {
  queues: 'Queues',
  matches: 'Matches',
  players: 'Players',
  setup: 'Setup',
};
interface OrganizerFilters {
  playerGame: string;
  playerSearch: string;
  includeWithoutQueues: boolean;
  matchQueue: string;
  matchState: string;
}
const filtersByCommunity = new Map<string, OrganizerFilters>();
let refreshedAt: Date | null = null;
let accessCheckedAt: Date | null = null;
const inviteUrls = (): InviteUrls =>
  bootstrap.inviteUrls ?? { basic: bootstrap.inviteUrl, ranks: null, nicknames: null };
const escape = (s: unknown) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
const selected = (a: string, b: string) => (a === b ? ' selected' : '');
const isOrganizerView = (value: string | null): value is OrganizerView =>
  organizerViews.includes(value as OrganizerView);

function currentView(): OrganizerView {
  const view = new URL(location.href).searchParams.get('view');
  return isOrganizerView(view) ? view : 'queues';
}
function selectView(view: OrganizerView, focus = false, push = true) {
  const url = new URL(location.href);
  url.searchParams.set('view', view);
  if (push) writeRoute(url);
  app.querySelectorAll<HTMLButtonElement>('[role=tab][data-view]').forEach((button) => {
    const active = button.dataset.view === view;
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
    if (active && focus) button.focus();
  });
  app.querySelectorAll<HTMLElement>('[data-view-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
  if (view === 'setup' && draft) {
    const form = app.querySelector<HTMLFormElement>('[data-guide-queue]');
    const queue = draft.materialize().queues.find((q) => q.id === form?.dataset.guideQueue);
    if (form && queue) {
      const raw = draft.raw.get(`queue:${queue.id}`);
      for (const name of ['name', 'channelId', 'size', 'teamMethod'] as const) {
        const input = form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement;
        const current = raw?.[name]?.[0] ?? String(queue[name]);
        if (
          input instanceof HTMLSelectElement &&
          ![...input.options].some((o) => o.value === current)
        )
          input.add(new Option(`Unavailable selection · ${current}`, current));
        input.value = current;
      }
      updateGuidePreview();
    }
  }
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
      (snapshot &&
        playerGames(snapshot.settings).find(
          (g) =>
            records?.players.some((p) => p.gameId === g.id) ||
            records?.economy?.wallets.some((w) => w.game_id === g.id),
        )) ||
      (snapshot && playerGames(snapshot.settings)[0]);
    filters = {
      playerGame: game?.id ?? '',
      playerSearch: '',
      includeWithoutQueues: false,
      matchQueue: '',
      matchState: '',
    };
    filtersByCommunity.set(guildId, filters);
  }
  if (
    snapshot &&
    filters.playerGame &&
    !playerGames(snapshot.settings, filters.includeWithoutQueues).some(
      (g) => g.id === filters!.playerGame,
    )
  )
    filters.playerGame = playerGames(snapshot.settings, filters.includeWithoutQueues)[0]?.id ?? '';
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
  const allowedGames = new Set(
    playerGames(snapshot.settings, filters.includeWithoutQueues).map((g) => g.id),
  );
  for (const [attribute, label] of [
    ['rating', 'player records'],
    ['wallet', 'balances'],
  ] as const) {
    const rows = app.querySelectorAll<HTMLTableRowElement>(`[data-${attribute}-row]`);
    let visible = 0;
    rows.forEach((row) => {
      row.hidden =
        !allowedGames.has(row.dataset.game ?? '') ||
        Boolean(filters.playerGame && row.dataset.game !== filters.playerGame) ||
        Boolean(search && !row.dataset.playerSearch?.includes(search));
      if (!row.hidden) visible++;
    });
    const count = app.querySelector<HTMLElement>(`[data-filter-count="${attribute}"]`);
    const total = records?.playersTotal ?? rows.length;
    const morePlayers = attribute === 'rating' && rows.length < total;
    const countText =
      attribute === 'rating'
        ? `${visible} matching · ${rows.length} of ${total} stored player records loaded${!filters.includeWithoutQueues && playerGames(snapshot.settings).length < snapshot.settings.games.length ? ' (includes games without queues)' : ''}`
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
    case 'player-inactive': {
      filters.includeWithoutQueues = (input as HTMLInputElement).checked;
      const options = playerGames(snapshot.settings, filters.includeWithoutQueues);
      if (filters.playerGame && !options.some((g) => g.id === filters.playerGame))
        filters.playerGame = options[0]?.id ?? '';
      const select = app.querySelector<HTMLSelectElement>('[data-filter="player-game"]')!;
      select.innerHTML = `<option value="">All games</option>${options.map((g) => `<option value="${g.id}"${selected(g.id, filters.playerGame)}>${escape(g.name)}</option>`).join('')}`;
      break;
    }
    case 'player-game':
      filters.playerGame = input.value;
      const policy = app.querySelector<HTMLButtonElement>('[data-player-policy]');
      if (policy) {
        policy.disabled = !input.value;
        policy.dataset.unavailable = String(!input.value);
        policy.title = input.value
          ? 'Edit this game’s shared policy in Setup'
          : 'Choose one game to edit its leaderboard policy';
      }
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
    .map((p) => {
      const queues = snapshot!.settings.queues.filter((q) => q.gameId === p.gameId);
      const queueField = `<label>Queue<select name="queueId">${queues.map((q) => `<option value="${q.id}">${escape(q.name)}</option>`).join('')}</select></label>`;
      const restriction = records!.noadds.find((n) => n.user_id === p.id);
      return `<details class="player-record" data-rating-row data-game="${p.gameId}" data-player-search="${escape(`${p.name.toLocaleLowerCase()} ${p.id}`)}"><summary><span class="player-identity"><span class="player-avatar" aria-hidden="true">${escape(Array.from(p.name)[0] ?? '?')}</span><span><strong>${escape(p.name)}</strong><small>${escape(gameName(p.gameId))}</small></span></span><span class="player-score">${p.rating}<small>Rating</small></span><span class="player-results"><small>W–L–D</small>${p.wins}–${p.losses}–${p.draws}</span><span class="status-pill">${p.hidden === undefined ? 'Visibility not loaded' : p.hidden ? 'Hidden' : 'Visible'}</span></summary><div class="player-detail"><dl class="player-facts"><div><dt>Deviation / uncertainty</dt><dd>${p.deviation}</dd></div><div><dt>Wins / losses / draws</dt><dd>${p.wins} / ${p.losses} / ${p.draws}</dd></div><div><dt>Discord ID</dt><dd>${p.id}</dd></div><div><dt>Queue access</dt><dd>${restriction ? `Restricted until ${escape(new Date(restriction.until_ms).toLocaleString())}${restriction.reason ? ` · ${escape(restriction.reason)}` : ''}` : 'No active restriction'}</dd></div></dl><p class="form-help">Visibility is per game. Hidden players remain available to authorized administrators; public access also follows this game’s statistics policy.</p><div class="actions"><a class="button" href="/stats?community=${guildId}&game=${p.gameId}&user=${p.id}">Open player profile</a><button type="button" data-game-link="${p.gameId}">Game rating and leaderboard policy</button></div>${p.hidden !== undefined ? `<form data-kind="player-record" data-inline-player><input type="hidden" name="gameId" value="${p.gameId}" /><input type="hidden" name="userId" value="${p.id}" /><input type="hidden" name="kind" value="${p.hidden ? 'unhide' : 'hide'}" /><button>${p.hidden ? 'Show in statistics' : 'Hide from statistics'}</button></form>` : ''}${queues.length ? `<details class="rating-options"><summary>Manage ${escape(p.name)}</summary><form data-kind="seed" class="repair-form"><h3>Set local rating</h3>${queueField}<input type="hidden" name="target" value="${p.id}" /><label>Rating for ${escape(p.name)}<input name="rating" type="number" min="0" max="10000" value="${p.rating}" required /></label><button>Review rating change</button></form><form data-kind="noadd" class="repair-form"><h3>Restrict queue access</h3><p class="form-help">Applies to all queues in this community. Maximum 14 days.</p>${queueField}<input type="hidden" name="target" value="${p.id}" /><label>Duration seconds<input name="seconds" type="number" min="1" max="1209600" value="3600" required /></label><label>Reason<input name="reason" maxlength="200" /></label><button>Review restriction</button></form>${restriction ? `<button type="button" data-action="forgive" data-id="${p.id}">Remove restriction</button>` : ''}</details>` : '<p class="form-help">Create a queue for this game to use rating and restriction tools.</p>'}</div></details>`;
    })
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
  if (bootstrap?.csrf) headers.set('X-CSRF-Token', bootstrap.csrf);
  return requestJson<T>(path, { ...options, headers });
}
const communityUrl = (suffix: string) => `/api/communities/${guildId}/${suffix}`;
function notice(message: string, error = false, focus = true) {
  currentNotice = { message, error };
  const box = document.querySelector<HTMLElement>('#notice');
  if (box) {
    box.textContent = message;
    box.className = `notice${error ? ' error' : ''}`;
    box.setAttribute('role', error ? 'alert' : 'status');
    box.hidden = false;
    if (error) {
      box.tabIndex = -1;
      if (focus && !app.querySelector('[aria-invalid="true"]')) box.focus();
    }
  }
}
async function run(action: () => Promise<void>) {
  if (busy) return;
  const restoreFocus = rememberFocus();
  busy = true;
  currentNotice = null;
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
    const invalid = [
      ...app.querySelectorAll<HTMLElement>('[aria-invalid="true"]:not(:disabled)'),
    ].find((el) => el.getClientRects().length);
    if (invalid) invalid.focus();
    else if (document.activeElement === document.body) restoreFocus();
  }
}
async function save(settings: Settings) {
  if (!snapshot) return;
  const community = guildId;
  const saved = await api<Snapshot>(communityUrl('config'), {
    method: 'PUT',
    body: JSON.stringify({ expectedRevision: draft!.saved.revision, settings }),
  });
  if (community !== guildId) return;
  snapshot = saved;
  draft = new SettingsDraft(saved, setup.gameState);
  validationIssues = [];
  conflict = false;
  invalidateImport();
  if (new URL(location.href).searchParams.get('guide') === 'queue') {
    const url = new URL(location.href);
    url.searchParams.set('guide', 'try');
    writeRoute(url);
  }
  render();
  notice('Settings saved. Discord delivery may still be pending.');
}
async function loadCommunity(id: string, navigation: 'push' | 'replace' | 'none' = 'none') {
  const sequence = ++loadSequence;
  const url = new URL(location.href);
  if (navigation === 'push') {
    url.searchParams.set('view', currentView());
    for (const key of ['queue', 'game', 'section', 'guide', 'guideGame', 'guideQueue'])
      url.searchParams.delete(key);
  }
  url.searchParams.set('community', id);
  if (navigation !== 'none') writeRoute(url, navigation);
  selectedQueue = url.searchParams.get('queue') ?? '';
  category = (url.searchParams.get('section') ?? 'basics') as Category;
  settingsSearch = '';
  guildId = id;
  guideCreatedGames.clear();
  snapshot = null;
  draft = null;
  records = null;
  connectorDiagnostics = null;
  channelList = [];
  validationIssues = [];
  currentNotice = null;
  invalidateImport();
  render();
  const [result, connectors] = await Promise.all([
    api<EditorSetup & { snapshot: Snapshot; records: Records }>(communityUrl('setup')),
    api<ConnectorDiagnostics>(communityUrl('connectors')).catch(() => null),
  ]);
  if (guildId !== id || sequence !== loadSequence) return;
  snapshot = result.snapshot;
  draft = new SettingsDraft(snapshot, result.gameState);
  setup = result;
  connectorDiagnostics = connectors;
  accessCheckedAt = new Date();
  channelList = result.channels;
  records = result.records;
  refreshedAt = new Date();
  conflict = false;
  url.searchParams.set('view', currentView());
  writeRoute(url, 'replace');
  render();
}
async function refreshRecords() {
  const id = guildId;
  const result = await api<Records>(communityUrl('records'));
  if (id !== guildId) return;
  records = result;
  importRecordsRefreshed(communityUrl('import/apply'));
  setup.gameState = result.gameState ?? setup.gameState;
  if (draft) draft.gameState = setup.gameState;
  refreshedAt = new Date();
  render();
}
async function refreshSetup() {
  if (!snapshot || !draft) return loadCommunity(guildId);
  const id = guildId;
  const [result, connectors] = await Promise.all([
    api<EditorSetup & { snapshot: Snapshot; records: Records }>(communityUrl('setup')),
    api<ConnectorDiagnostics>(communityUrl('connectors')).catch(() => null),
  ]);
  if (id !== guildId) return;
  setup = result;
  connectorDiagnostics = connectors;
  accessCheckedAt = new Date();
  channelList = result.channels;
  records = result.records;
  refreshedAt = new Date();
  snapshot = result.snapshot;
  draft.gameState = result.gameState;
  if (!draft.dirty) {
    draft = new SettingsDraft(snapshot, result.gameState);
    validationIssues = [];
    conflict = false;
  } else if (snapshot.revision !== draft.saved.revision) conflict = true;
  render();
  notice(
    conflict
      ? 'Saved settings changed elsewhere. Your edits are preserved. Continue editing, or reload the latest settings to discard your changes.'
      : setup.access.message,
    conflict || setup.access.status !== 'ready',
  );
}
function fieldIssue(issue: Issue): FieldIssue {
  const path = issue.path[0] === 'settings' ? issue.path.slice(1) : issue.path;
  const settings = draft!.materialize();
  let form = '',
    name = String(path[2] ?? '');
  const key = path
    .slice(2)
    .filter((part) => typeof part === 'string')
    .join('.');
  if (path[0] === 'publicPage') {
    form = 'publicPage';
    name = 'displayName';
  } else if (path[0] === 'queues') {
    const q = settings.queues[Number(path[1])];
    if (q) {
      form = `queue:${q.id}`;
      const names: Record<string, string> = {
        'checkIn.enabled': 'checkIn',
        'checkIn.seconds': 'seconds',
        'checkIn.reuseSeconds': 'reuseSeconds',
        'controls.requiredRole': 'requiredRole',
        'controls.blockedRole': 'blockedRole',
        'controls.waitingSeconds': 'waitingSeconds',
        'controls.matchSeconds': 'matchSeconds',
        'rally.enabled': 'rallyEnabled',
        'rally.channels': 'rallyChannels',
        'rally.roles': 'rallyRoles',
        'rally.cooldownSeconds': 'rallyCooldown',
        'rally.mentionLimit': 'rallyMentions',
        'economy.taxEnabled': 'taxEnabled',
        maps: 'mapPool',
        'maps.pool': 'mapPool',
        'maps.mode': 'mapMode',
        'maps.count': 'mapCount',
        'maps.shortlist': 'mapShortlist',
        'maps.voting': 'mapVoting',
        'maps.tie': 'mapTie',
        'maps.noVotes': 'mapNoVotes',
        'maps.seconds': 'mapSeconds',
        'maps.recentAvoid': 'mapRecent',
        'maps.countsAsCheckIn': 'mapCheckIn',
      };
      name = names[key] ?? (path[2] === 'modules' ? `module-${path[3]}` : name);
      if (
        ['ratingSettings', 'ratingEngine'].includes(String(path[2])) ||
        key === 'economy.taxMinuteUtc'
      ) {
        form = `rating:${q.gameId}`;
        name = String(path[3] ?? path[2]);
        if (name === 'ratingSettings') name = 'initial';
      }
    }
  } else if (path[0] === 'games') {
    const g = settings.games[Number(path[1])];
    if (g) {
      form = `game:${g.id}`;
      if (path[2] === 'records' && path[3] === 'ranks' && typeof path[4] === 'number') {
        const indexes = draft!.raw.has(form)
          ? Object.keys(draft!.raw.get(form)!)
              .filter((k) => /^rank-\d+-threshold$/.test(k))
              .map((k) => k.split('-')[1])
          : (g.records?.ranks ?? []).map((_, i) => String(i));
        name = `rank-${indexes[path[4]]}-${path[5] === 'roleId' ? 'role' : (path[5] ?? 'threshold')}`;
      } else {
        const names: Record<string, string> = {
          'connector.enabled': 'lobbyConnector',
          'connector.autoAttach.lobbyName': 'lobbyName',
          'records.inactivity.enabled': 'decay',
        };
        name = names[key] ?? String(path.at(-1));
      }
    }
  }
  return { ...issue, form, name };
}
function issueInputs(issue: FieldIssue) {
  return [
    ...app.querySelectorAll<HTMLElement>(
      `[data-config="${CSS.escape(issue.form)}"] [name="${CSS.escape(issue.name)}"]`,
    ),
  ];
}
function paintIssues() {
  app.querySelectorAll('[data-validation-error]').forEach((el) => el.remove());
  app.querySelectorAll('[aria-invalid]').forEach((el) => {
    el.removeAttribute('aria-invalid');
    const ids = el
      .getAttribute('aria-describedby')
      ?.split(' ')
      .filter((id) => !id.startsWith('field-error-'));
    if (ids?.length) el.setAttribute('aria-describedby', ids.join(' '));
    else el.removeAttribute('aria-describedby');
  });
  validationIssues.forEach((issue, index) => {
    const inputs = issueInputs(issue);
    if (!inputs.length) return;
    const id = `field-error-${index}`;
    for (const input of inputs) {
      input.setAttribute('aria-invalid', 'true');
      input.setAttribute(
        'aria-describedby',
        [input.getAttribute('aria-describedby'), id].filter(Boolean).join(' '),
      );
    }
    const container = inputs[0].closest('.choice-list') ?? inputs[0];
    container.insertAdjacentHTML(
      'afterend',
      `<p id="${id}" data-validation-error class="field-error" role="alert">${escape(issue.message)}</p>`,
    );
  });
}
function showIssues(issues: Issue[]) {
  validationIssues = [
    ...new Map(
      issues.map((issue) => {
        const field = fieldIssue(issue);
        return [`${field.form}:${field.name}:${field.message}`, field] as const;
      }),
    ).values(),
  ];
  const first = validationIssues.find((issue) => issue.form);
  if (first) {
    const url = new URL(location.href);
    if (first.form.startsWith('queue:')) {
      selectedQueue = first.form.slice(6);
      url.searchParams.set('view', 'queues');
      url.searchParams.set('queue', selectedQueue);
    } else {
      url.searchParams.set('view', 'setup');
      if (first.form !== 'publicPage') url.searchParams.set('game', first.form.split(':')[1]);
      url.searchParams.delete('guide');
    }
    writeRoute(url);
  }
  render();
  const input = first && issueInputs(first)[0];
  const panel = input?.closest<HTMLElement>('[data-category-panel]');
  if (panel) {
    category = panel.dataset.categoryPanel as Category;
    settingsSearch = '';
    editRoute(undefined, 'replace');
    applyCategories();
  }
  input?.focus();
}
function updateDirty() {
  const permissions = app.querySelector<HTMLElement>('#discord-access');
  if (permissions && draft) {
    const expanded = permissions.querySelector('details')?.open;
    permissions.outerHTML = permissionRecovery(draft, setup, inviteUrls(), accessCheckedAt);
    const replacement = app.querySelector<HTMLElement>('#discord-access')!;
    if (expanded) replacement.querySelector('details')!.open = true;
    labelTables(replacement);
  }
  const dirty = Boolean(draft?.dirty);
  document.body.classList.toggle('has-draft', dirty);
  const bar = app.querySelector<HTMLElement>('#savebar');
  if (bar) bar.hidden = !dirty;
  const count = app.querySelector<HTMLElement>('#savebar small');
  if (count && draft && dirty)
    count.textContent = `${changesBetween(draft.saved.settings, draft.materialize()).length} changes across this community`;
  const saveButton = app.querySelector<HTMLButtonElement>('[data-edit="save"]');
  if (saveButton) saveButton.disabled = busy || !dirty;
  const reload = app.querySelector<HTMLElement>('[data-edit="reload-conflict"]');
  if (reload) reload.hidden = !conflict;
  app.querySelectorAll<HTMLFormElement>('form[data-config]').forEach((form) => {
    const changed = draft?.raw.has(form.dataset.config!);
    form.classList.toggle('is-dirty', Boolean(changed));
    const changedFields = draft!.changedFields(form.dataset.config!);
    form.querySelectorAll<HTMLElement>('[data-setting]').forEach((row) => {
      const changed = [...row.querySelectorAll<HTMLInputElement>('[name]')].some((el) =>
        changedFields.has(el.name),
      );
      row.classList.toggle('field-changed', changed);
      let tag = row.querySelector<HTMLElement>('[data-changed-tag]');
      if (changed && !tag) {
        tag = document.createElement('span');
        tag.dataset.changedTag = '';
        tag.className = 'draft-tag';
        tag.textContent = 'Changed';
        row.querySelector('.setting-title')!.append(tag);
      }
      if (tag) tag.hidden = !changed;
    });
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
  if (!Object.hasOwn(categories, category)) category = 'basics';
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
function matchResult(m: Match) {
  if (!['playing', 'check_in'].includes(m.state)) return '';
  return `<form data-kind="repair" data-id="${m.id}" class="result-form"><input type="hidden" name="kind" value="admin_report" /><fieldset class="result-choices"><legend>Who won?</legend>${[
    ['alpha', 'Alpha won'],
    ['beta', 'Beta won'],
    ['draw', 'Draw'],
  ]
    .map(
      ([id, label]) =>
        `<label><input type="radio" name="result" value="${id}" ${id === 'alpha' ? 'checked' : ''} /><span>${label}</span></label>`,
    )
    .join(
      '',
    )}</fieldset><p class="form-help" data-result-consequence aria-live="polite">Record Alpha as the winning team. The server applies this game’s saved rating rules and updates match history. Paid prediction settlements remain final.</p><button class="primary">Review result</button></form>`;
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
  return `<details class="rating-options match-admin"><summary>Administrator actions</summary><form data-kind="repair" data-id="${m.id}" class="repair-form">${['finished', 'undone'].includes(m.state) ? '<p class="muted">Undo reverses recorded rating changes and counts when the history is complete and players are not in active matches. A corrected result uses current ratings; paid prediction settlements remain final.</p>' : '<p class="muted">Repair the saved match or record its result. You will review the action before applying it.</p>'}<div class="fields"><label>Match action<select name="kind">${options.map(([value, label]) => `<option value="${value}" ${active && value === 'admin_report' ? 'selected' : ''}>${label}</option>`).join('')}</select></label><label>Player ID<input name="target" placeholder="Outgoing player, or player to place" /></label><label>Replacement ID<input name="replacement" placeholder="New server member" /></label><label>Who won?<select name="result"><option value="alpha">Alpha</option><option value="beta">Beta</option><option value="draw">Draw</option><option value="abort">Abort</option></select></label></div><button>Apply match action</button></form></details>`;
}
function channelName(id: string) {
  return (
    setup.channels.find((c) => c.id === id)?.name ??
    (setup.knownChannels?.find((c) => c.id === id)
      ? `${setup.knownChannels.find((c) => c.id === id)!.name} · Not verified`
      : 'Unavailable channel')
  );
}
function queueChoice() {
  return `<label>Queue<select name="queueId">${snapshot!.settings.queues.map((q) => `<option value="${q.id}">${escape(q.name)} · #${escape(channelName(q.channelId))}</option>`).join('')}</select></label>`;
}
function queueSection() {
  const queue = draft!.materialize().queues.find((q) => q.id === selectedQueue);
  if (queue)
    return (
      `<nav class="editor-breadcrumb" aria-label="Queue navigation"><button type="button" data-queue-list>Queues</button><span aria-hidden="true">/</span><span>${escape(queue.name || 'New queue')}</span></nav>` +
      queueEditor(draft!, setup, selectedQueue).replace(
        '<h1>Queues</h1>',
        `<h1>${escape(queue.name || 'New queue')}</h1>`,
      )
    );
  return `<div class="page-heading"><div><h1>Queues</h1><p class="view-description">A queue lives in one Discord channel. Players join with <code>/add</code>; automatic start prepares a match when it fills.</p></div><button type="button" class="primary" data-edit="add-queue" ${!draft!.working.games.length || !setup.channels.length ? 'disabled data-unavailable="true"' : ''}>Add queue</button></div><div class="queue-list-layout"><div>${queueOverview() || '<section class="panel empty-state"><h2>Your first queue starts here</h2><p>Add a game and verify Discord access in Setup.</p><button type="button" data-view="setup">Open setup</button></section>'}</div>${activitySidebar()}</div>`;
}
function activitySidebar() {
  const active = records?.matches.filter((m) => ['playing', 'check_in'].includes(m.state)) ?? [];
  return `<aside class="activity-sidebar"><section class="panel"><div class="section-heading"><h2>Happening now</h2><button type="button" class="text-button" data-view="matches">All matches</button></div>${active.map((m) => `<button type="button" class="match-link" data-view="matches"><span><strong>${escape(m.queue.name)}</strong><small>${escape(m.gameName)}</small></span><span class="status-pill" data-status="${m.state}">${m.state === 'check_in' ? 'Checking in' : 'Playing'}</span></button>`).join('') || '<p class="muted">No active matches in the loaded records.</p>'}<p class="form-help">From the latest ${records?.matches.length ?? 0} records.</p></section><section class="panel"><h2>What players type in Discord</h2><dl class="command-list"><dt><code>/add</code></dt><dd>Join a queue</dd><dt><code>/remove</code></dt><dd>Leave the queue</dd><dt><code>/queue show</code></dt><dd>See who is waiting</dd><dt><code>/report</code></dt><dd>Report a result</dd><dt><code>/rank</code></dt><dd>View your rating</dd></dl><a class="text-link" href="/guides/commands/">All commands</a></section></aside>`;
}
function queueOverview() {
  if (!records || !draft) return '';
  const settings = draft.materialize();
  const cards = settings.queues
    .map((q) => {
      const saved = snapshot!.settings.queues.find((v) => v.id === q.id);
      const waiting = records!.waiting.find((w) => w.queueId === q.id)?.players ?? [];
      const matches = records!.matches.filter(
        (m) => m.queue.id === q.id && ['playing', 'check_in'].includes(m.state),
      );
      const changed =
        !saved || draft!.raw.has(`queue:${q.id}`) || JSON.stringify(saved) !== JSON.stringify(q);
      const meta = `${escape(settings.games.find((g) => g.id === q.gameId)?.name ?? 'Unavailable game')} · #${escape(channelName(q.channelId))}${saved ? ` · ${saved.size} players, ${saved.size / 2}v${saved.size / 2}` : ''}`;
      const roster = `<p class="queue-capacity"><strong>Waiting now</strong><span>${waiting.length}${saved ? ` of ${saved.size}` : ''}</span></p>${saved ? `<progress value="${Math.min(waiting.length, saved.size)}" max="${saved.size}" aria-label="Waiting players for ${escape(q.name)}"></progress>` : ''}<ul class="waiting-roster" aria-label="Waiting players">${
        waiting
          .map((p) => {
            const rating = records!.players.find(
              (v) => v.id === p.id && v.gameId === q.gameId,
            )?.rating;
            return `<li><span class="player-initial" aria-hidden="true">${escape(Array.from(p.name)[0] ?? '?')}</span>${escape(p.name)}<span class="roster-rating">${rating ?? 'rating not loaded'}</span></li>`;
          })
          .join('') || '<li>No players waiting.</li>'
      }</ul>`;
      const actions = saved
        ? `<form data-kind="queue-control" data-id="${q.id}" class="queue-quick-action"><input type="hidden" name="kind" value="start" /><p class="queue-policy"><span>${escape({ captain: 'Captain teams', balanced: 'Balanced teams', random: 'Random teams' }[saved.teamMethod])}</span><span>${saved.checkIn.enabled ? `Check-in ${saved.checkIn.seconds} s` : 'Check-in off'}</span><span>${matches.length} active in ${records!.matches.length} loaded records</span></p><button name="directAction" value="start">Start with ${waiting.length} now</button><button name="directAction" value="clear">Clear queue</button><span class="sr-only">Uses the waiting roster and saved rules. Advanced splitting is under Matches.</span></form>`
        : '<p class="form-help">Save this queue before starting matches.</p>';
      const pausedEmpty = saved && !saved.enabled && !waiting.length && !matches.length;
      return `<article class="panel queue-card"><div class="queue-card-heading"><div class="queue-card-title"><div class="section-heading"><h3>${escape(q.name || 'Unnamed draft')}</h3><span class="status-pill" data-status="${!saved ? 'draft' : saved.enabled ? 'open' : 'paused'}">${!saved ? 'New draft' : saved.enabled ? 'Open' : 'Paused'}</span>${q.isDefault ? '<span class="default-tag">Default in channel</span>' : ''}</div><p class="queue-meta">${meta}</p></div><div class="actions"><button type="button" data-open-queue="${q.id}" aria-label="Settings for ${escape(q.name || 'new queue')}">Settings</button><button type="button" data-toggle-queue="${q.id}" class="${q.enabled ? '' : 'resume-queue'}">${q.enabled ? 'Pause' : 'Resume'}<span class="sr-only"> on save</span></button></div></div>${changed ? `<p class="draft-tag">Unsaved changes${saved && saved.enabled !== q.enabled ? ` · ${q.enabled ? 'resume' : 'pause'} on save` : ''}</p>` : ''}${pausedEmpty ? `<p class="paused-note">Players cannot <code>/add</code> while paused. Ratings and history are kept.</p><details class="queue-manual-actions"><summary>Waiting roster and manual actions</summary>${roster}${actions}</details>` : roster + actions}</article>`;
    })
    .join('');
  return `${!savedReady(snapshot!, setup) ? '<p class="notice">Setup needs attention. <button type="button" data-view="setup">Open Setup</button></p>' : ''}<section class="queue-overview" aria-label="Queue overview"><p class="sr-only queue-list-note">Live rosters use saved rules. Pause and resume take effect when you save.</p><div class="queue-cards">${cards}</div></section>`;
}

function openGameSettings(id: string) {
  const url = new URL(location.href);
  url.searchParams.set('view', 'setup');
  url.searchParams.set('game', id);
  url.searchParams.delete('guide');
  writeRoute(url);
  if (!app.querySelector(`#game-settings-${CSS.escape(id)}`)) render();
  selectView('setup', false, false);
  applyGameRoute();
  const details = document.getElementById(`game-settings-${id}`) as HTMLDetailsElement | null;
  if (details) {
    details.open = true;
    details.scrollIntoView({ block: 'start' });
    details.querySelector<HTMLElement>('summary')?.focus();
  }
}
function openQueue(id: string, focus = true) {
  selectedQueue = id;
  category = 'basics';
  settingsSearch = '';
  editRoute('queues');
  render();
  if (focus) {
    const input = app.querySelector<HTMLElement>(
      `[data-config="queue:${CSS.escape(id)}"] [name="name"]`,
    );
    input?.focus();
    input?.scrollIntoView({ block: 'center' });
  }
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
  const delivery = records!.delivery;
  return `<aside class="activity-sidebar match-sidebar"><section class="panel"><h2>Discord delivery</h2>${delivery.length ? delivery.map((d) => `<div class="activity-item"><strong>${escape(d.status === 'blocked' ? 'A message needs attention' : d.status.replaceAll('_', ' '))}</strong>${d.error ? `<p>${escape(d.error)}</p>` : ''}<details><summary>Delivery details</summary><code>${escape(d.key)}</code></details></div>`).join('') : '<p class="muted">No delivery problems in the loaded records.</p>'}<button type="button" class="text-button" data-view="setup">Check Discord access</button><p class="form-help">After fixing access, refresh the affected card under Administrator actions.</p></section><section class="panel"><h2>Administrator activity</h2>${records!.audit.map((a) => `<div class="activity-item"><time>${escape(new Date(a.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))}</time><p><strong>${escape(playerName(a.actor_id))}</strong> · ${escape(a.action.replaceAll('_', ' '))}${a.match_id ? `<small>Match ${escape(a.match_id)}</small>` : ''}</p></div>`).join('') || '<p class="muted">No administrator activity in the loaded records.</p>'}<p class="form-help">Recent activity only.</p></section></aside>`;
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
  if (draft) draft.gameState = setup.gameState;
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
    const submit = (
      form.dataset.kind === 'queue-control'
        ? form.querySelector<HTMLButtonElement>(`button[name="directAction"][value="${kind}"]`)
        : form.querySelector<HTMLButtonElement>('button:not([type="button"])')
    )!;
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
function matchCard(m: Match) {
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
  const ready = new Set(m.ready ?? []);
  const checked = m.roster.filter((p) => ready.has(p.id)).length;
  const book = records?.economy?.books.find((b) => b.match_id === m.id);
  return `<details class="match-record" data-match-record data-match-id="${m.id}" data-queue="${m.queue.id}" data-state="${m.state}"${['playing', 'check_in'].includes(m.state) ? ' open' : ''}><summary><span>${escape(m.queue.name)} · ${escape(m.gameName)}</span><span class="status-pill" data-status="${m.state}">${escape(stateName(m))}</span><span class="record-meta">${escape(new Date(m.createdAt).toLocaleString())}</span></summary>${m.state === 'check_in' ? `<div class="check-in-progress"><p><strong>${checked} / ${m.roster.length} checked in</strong>${m.deadline ? ` · deadline ${escape(new Date(m.deadline).toLocaleString())}` : ''}</p><progress max="${Math.max(1, m.roster.length)}" value="${checked}" aria-label="Players checked in"></progress></div>` : ''}<div class="record-teams">${m.teams.map((team, side) => `<div><h3>${side === 0 ? 'Alpha' : 'Beta'}</h3>${team.map((p) => `<p><span class="team-player"><strong>${escape(p.name)}</strong>${m.captains[side] === p.id ? '<small>Captain</small>' : ''}${m.state === 'check_in' ? `<small class="readiness" data-ready="${ready.has(p.id)}">${ready.has(p.id) ? '✓ Ready' : 'Waiting for check-in'}</small>` : ''}</span><span class="team-rating">${p.rating}</span></p>`).join('') || '<p>No players assigned.</p>'}</div>`).join('')}</div>${m.changes.length ? `<h3>Recorded rating changes</h3><ul class="rating-changes">${m.changes.map((c) => `<li>${escape(m.roster.find((p) => p.id === c.userId)?.name ?? c.userId)}: ${c.before} → ${c.after} (${c.after - c.before >= 0 ? '+' : ''}${c.after - c.before})</li>`).join('')}</ul>` : '<p class="form-help">No rating changes recorded for this match.</p>'}${book ? `<p class="notice">Predictions: ${escape(book.status)}${book.reason ? ` · ${escape(book.reason)}` : ''}${book.status === 'settled' ? ' · Paid settlements remain final after result corrections.' : ''}</p>` : ''}${m.note ? `<p>${escape(m.note)}</p>` : ''}${matchResult(m)}${matchConnectorDetails(m)}${matchRepairs(m)}<details class="technical-details"><summary>Record identifiers</summary><p>Match ${escape(m.id)}</p>${m.roster.map((p) => `<p>${escape(p.name)} · ${escape(p.id)}</p>`).join('')}</details></details>`;
}
function recordSection() {
  if (!records) return '';
  const blocked = records.delivery.filter((w) => w.status === 'blocked');
  const returns = records.pendingReturns ?? [];
  const filters = currentFilters();
  const queues = new Map(
    [...snapshot!.settings.queues, ...records.matches.map((m) => m.queue)].map((q) => [q.id, q]),
  );
  const matchFilters = `<div class="record-filters"><label><span class="sr-only">Filter matches by queue</span><select data-filter="match-queue"><option value="">All queues</option>${[...queues.values()].map((q) => `<option value="${q.id}"${selected(q.id, filters.matchQueue)}>${escape(q.name)} · ${escape(gameName(q.gameId))}</option>`).join('')}</select></label><label><span class="sr-only">Match status</span><select data-filter="match-state"><option value="">All statuses</option>${Object.entries(
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
  return `<div class="page-heading"><div><h1>Matches</h1><p class="view-description">Follow active games, confirm results, and review recent matches.</p></div></div><section class="panel match-workspace"><p class="sr-only" id="match-filter-scope">Showing the latest 20 match records. Filters and active counts apply to these loaded records.</p><div class="match-toolbar" aria-describedby="match-filter-scope">${matchFilters}<span class="match-limit">Latest 20 records</span></div><div class="match-layout"><div>${blocked.length ? `<p class="notice error">${blocked.length} Discord card delivery task(s) need attention. Check channel permissions in Setup, then refresh the affected match under Administrator actions.</p>` : ''}
    ${returns.length ? `<details><summary>Players awaiting return after a timeout</summary><p class="muted">Overflow waits for a free place. Blocked returns need a fresh join after checking membership and roles.</p>${returns.map((r) => `<p>${escape(r.name)} · ${escape(snapshot?.settings.queues.find((q) => q.id === r.queue_id)?.name ?? r.queue_id)} · ${escape(r.status)}</p>`).join('')}</details>` : ''}
    <div class="record-list">${[true, false]
      .map(
        (active) =>
          `<section data-match-group><h3 class="${active ? 'sr-only' : 'recent-heading'}">${active ? 'Active matches' : 'Recent completed matches'}</h3>${
            records!.matches
              .filter((m) => ['playing', 'check_in'].includes(m.state) === active)
              .map(matchCard)
              .join('') ||
            `<p class="empty-state">No ${active ? 'active' : 'completed'} matches in the loaded records.</p>`
          }</section>`,
      )
      .join('')}<p class="empty-state" data-filter-empty="match" hidden></p></div>
    ${queueAdministration()}</div>${diagnostics()}</div></section>`;
}
function playerSection() {
  if (!records) return '';
  const economy = records.economy;
  const filters = currentFilters();
  const playerFilters = `<div class="table-toolbar"><label>Filter players by game<select data-filter="player-game"><option value="">All games</option>${playerGames(
    snapshot!.settings,
    filters.includeWithoutQueues,
  )
    .map(
      (g) =>
        `<option value="${g.id}"${selected(g.id, filters.playerGame)}>${escape(g.name)}</option>`,
    )
    .join(
      '',
    )}</select></label><label>Find a player<input type="search" data-filter="player-search" value="${escape(filters.playerSearch)}" placeholder="Player name or Discord ID" /></label></div><label class="toggle"><input type="checkbox" data-filter="player-inactive" ${filters.includeWithoutQueues ? 'checked' : ''} /> Include games without queues</label><p class="form-help">Games with queues are shown by default. Older game records remain available when included.</p>`;
  const matchName = (id: string) => {
    const match = records!.matches.find((m) => m.id === id);
    return match
      ? `${match.queue.name} · ${new Date(match.createdAt).toLocaleString()}`
      : 'Earlier match';
  };
  return `<div class="page-heading"><div><h1>Players</h1><p class="view-description">Community ratings, player records, and imported history.</p></div></div><nav class="player-sections" aria-label="Player tools"><a href="#player-ratings" aria-current="page">Ratings</a>${filters.playerGame ? `<button type="button" data-player-policy>Leaderboard and ranks</button>` : ''}<button type="button" data-player-import>Import</button>${economy && (economy.wallets.length || snapshot!.settings.queues.some((q) => q.modules.gold)) ? '<a href="#player-gold">Gold</a>' : ''}</nav><section class="panel players-workspace" id="player-ratings"><div class="section-heading"><h2>Player ratings</h2></div><p class="muted">Ratings and gold are shared by queues for the same game. These filters search loaded ratings and gold balances. Load more players to extend the search.</p>${playerFilters}<p class="record-meta" data-filter-count="rating" role="status" aria-live="polite"></p><div data-filter-table="rating"><div class="player-columns" aria-hidden="true"><span>Player</span><span>Rating</span><span>W–L–D</span><span>Visibility</span></div><div class="player-records" data-rating-body>${playerRatingRows()}</div></div><p class="empty-state" data-filter-empty="rating" hidden></p><div class="actions"><button type="button" data-action="load-players"${records.players.length < (records.playersTotal ?? records.players.length) ? '' : ' hidden'}>Load more players</button></div>${playerAdministration()}</section>
    ${economy && (economy.wallets.length || snapshot!.settings.queues.some((q) => q.modules.gold)) ? `<section class="panel" id="player-gold"><h2>Gold and predictions</h2><p class="record-meta" data-filter-count="wallet" role="status" aria-live="polite"></p><div class="table-wrap" data-filter-table="wallet"><table><thead><tr><th scope="col">Player</th><th scope="col">Game</th><th scope="col">Gold</th></tr></thead><tbody>${economy.wallets.map((w) => `<tr data-wallet-row data-game="${w.game_id}" data-player-search="${escape(playerName(w.user_id, w.game_id).toLocaleLowerCase())}"><td>${escape(playerName(w.user_id, w.game_id))}</td><td>${escape(gameName(w.game_id))}</td><td>${w.balance}</td></tr>`).join('')}</tbody></table></div><p class="empty-state" data-filter-empty="wallet" hidden></p><details class="rating-options"><summary>Gold ledger and prediction history</summary><h3>Gold activity</h3>${economy.ledger.map((l) => `<p>${escape(playerName(l.user_id))} · ${escape(l.kind.replaceAll('_', ' '))} · ${l.amount >= 0 ? '+' : ''}${l.amount} · balance ${l.balance_after}</p>`).join('') || '<p>No gold activity recorded.</p>'}<h3>Prediction pools</h3>${economy.books.map((b) => `<details><summary>${escape(matchName(b.match_id))} · ${escape(b.status)}</summary><p>${escape(b.reason ?? b.result ?? 'Open')}${b.status === 'settled' ? ' · Paid settlement remains final after corrections.' : ''}</p><p class="record-meta">Match ${escape(b.match_id)}</p></details>`).join('') || '<p>No prediction pools recorded.</p>'}</details></section>` : ''}
    ${onboarding(snapshot!, records.onboarding)}`;
}
function publicationEditor() {
  const meta = setup.publication;
  return `<section class="panel"><h2>Public community page</h2><form data-config="publicPage"><label>Public community display name<input name="displayName" maxlength="80" value="${escape(draft!.materialize().publicPage?.displayName ?? '')}" /></label></form><p>Choose public statistics and enable search publishing under each game's leaderboard settings. Player names and results can appear in search engines. Search engines may retain previously crawled copies after you unpublish.</p>${meta?.pending ? '<p role="status">Publishing update pending. We will retry automatically.</p>' : ''}${
    meta?.games
      .filter((v) =>
        snapshot!.settings.games.some((g) => g.id === v.id && g.records?.searchPublishing),
      )
      .map(
        (v) =>
          `<p><a href="/c/${escape(meta.community)}/${escape(v.slug)}/">Published leaderboard · ${escape(gameName(v.id))}</a></p>`,
      )
      .join('') ?? ''
  }</section>`;
}
function setupSection() {
  if (new URL(location.href).searchParams.has('guide'))
    return guidedSetup(draft!, setup, inviteUrls(), new URL(location.href), snapshot!);
  return `<div class="page-heading"><div><p class="eyebrow">Make it yours</p><h1>Setup</h1><p class="view-description">Games, shared ratings and Discord permissions.</p></div></div>${setupJourney() + gameEditors(draft!, setup, connectorDiagnostics) + permissionRecovery(draft!, setup, inviteUrls(), accessCheckedAt) + publicationEditor()}`;
}

function setupJourney() {
  const saved = snapshot!.settings;
  const ready = savedReady(snapshot!, setup);
  const pending =
    records?.delivery.filter((d) => d.status !== 'sent' && d.status !== 'done').length ?? 0;
  const steps = [true, setup.access.status === 'ready', saved.games.length > 0, ready];
  if (ready)
    return `<section class="panel setup-journey setup-ready"><span class="ready-check" aria-hidden="true">✓</span><div><h2>Ready to play</h2><p>Saved queues and required Discord access are verified.${pending ? ` ${pending} recent delivery task(s) are pending; check Matches.` : ''}</p><small>${accessCheckedAt ? `Access checked at ${escape(accessCheckedAt.toLocaleTimeString())}.` : ''}</small></div><button type="button" data-guide-start>Open setup guide</button></section>`;
  return `<section class="panel setup-journey"><div class="section-heading"><h2>${ready ? 'Ready to play' : 'Set up your first game'}</h2><span class="status-pill">${steps.filter(Boolean).length} / 4 complete</span></div><ol class="setup-steps">${['Choose community', 'Verify Discord access', 'Add a game', 'Save an enabled queue'].map((label, i) => `<li class="${steps[i] ? 'complete' : ''}"><span>${steps[i] ? '✓' : i + 1}</span>${label}</li>`).join('')}</ol><p class="form-help">Progress reflects saved settings and verified access.${pending ? ` ${pending} recent delivery task(s) are not complete; check delivery diagnostics.` : ''}</p><div class="actions"><button type="button" data-guide-start>${ready ? 'Open setup guide' : 'Start guided setup'}</button>${!steps[1] && bootstrap.inviteUrl ? `<a class="button primary" data-discord-invite target="_blank" rel="noopener" href="${escape(bootstrap.inviteUrl)}&guild_id=${guildId}&disable_guild_select=true">Add to Discord</a><button type="button" data-action="check-access">I’m back — check access</button>` : ''}${steps[2] ? '<button type="button" data-view="queues">Configure a queue</button>' : ''}</div></section>`;
}

function render() {
  const restoreFocus = rememberFocus();
  // A render replaces the import inputs, so any previous review must expire with them.
  invalidateImport();
  if (!bootstrap.user) {
    document.body.classList.remove('organizer');
    app.innerHTML = `<section class="intro"><p class="eyebrow">A better game night</p><h1>Your community.<br>Your rules.</h1><p class="lead">Set up Discord queues, manage game-night rules and review results in one place.</p><div class="actions">${bootstrap.oauthReady ? `<a class="button primary" href="/auth/login?returnTo=${encodeURIComponent(location.pathname + location.search)}">Sign in with Discord</a>` : '<p class="notice">Discord sign-in is not configured yet.</p>'}${bootstrap.inviteUrl ? `<a class="button" href="${escape(bootstrap.inviteUrl)}">Add to Discord</a>` : ''}</div><div id="notice" hidden></div></section>`;
    return;
  }
  document.body.classList.add('organizer');
  const current = guilds.find((g) => g.id === guildId);
  const iconPaths = {
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
  app.innerHTML = `<div class="organizer-layout"><aside class="organizer-sidebar"><p class="eyebrow">Workspace</p><label class="desktop-community"><span class="sr-only">Community</span><select id="community"><option value="">Choose a server</option>${guilds.map((g) => `<option value="${g.id}"${selected(g.id, guildId)}>${escape(g.name)}</option>`).join('')}</select></label><nav class="workspace-nav" role="tablist" aria-label="Organizer sections">${organizerViews.map((view) => `<button type="button" class="workspace-tab" id="tab-${view}" role="tab" data-view="${view}" aria-controls="view-${view}" aria-selected="false" aria-label="${viewNames[view]}" tabindex="-1"><span aria-hidden="true" class="nav-icon">${icons[view]}</span><span>${viewNames[view]}</span>${view === 'matches' && records?.matches.some((m) => ['playing', 'check_in'].includes(m.state)) ? `<span aria-hidden="true" class="nav-count" title="Active in loaded records">${records.matches.filter((m) => ['playing', 'check_in'].includes(m.state)).length}</span>` : ''}${(view === 'matches' && records?.delivery.some((d) => d.status === 'blocked')) || (view === 'setup' && snapshot && !savedReady(snapshot, setup)) ? '<span aria-hidden="true" class="nav-attention" title="Needs attention"></span>' : ''}</button>`).join('')}</nav><div class="sidebar-footer"><p>${escape(identity.publicName)}</p></div></aside><div class="workspace-content"><div class="context-bar"><button type="button" class="mobile-menu icon-button" data-edit="drawer" aria-label="Choose community or queue">☰</button><span class="context-name">${escape(current?.name ?? 'Choose your community')}</span>${snapshot?.settings.games[0] ? `<a class="context-statistics" href="/stats?community=${guildId}&game=${snapshot.settings.games[0].id}">Statistics</a>` : ''}<button type="button" data-account-menu class="account-avatar" aria-label="Account"><span aria-hidden="true">${escape(Array.from(bootstrap.user.username)[0].toUpperCase())}</span></button><div hidden><button data-action="refresh-records">Refresh</button><button data-action="logout">Sign out</button></div></div><div id="notice" class="notice" role="status" hidden></div><div class="workspace-scroll">
    ${!guilds.length ? `<section class="panel"><h1>No communities yet</h1><p>You need Manage Server permission to configure a community. Check that you signed into the correct Discord account.</p><ol class="recovery-steps"><li>Ask a server administrator for Manage Server permission, or ask them to configure the bot.</li><li>Invite ${escape(identity.publicName)} to your server.</li><li>Refresh the community list after changing permissions.</li></ol><div class="actions">${bootstrap.inviteUrl ? `<a class="button" data-discord-invite target="_blank" rel="noopener" href="${escape(bootstrap.inviteUrl)}">Add to Discord</a>` : ''}<button type="button" data-action="refresh-communities">Refresh communities</button></div></section>` : ''}
    ${guildId && !snapshot ? '<section class="panel"><h1>Checking your community…</h1><p>Loading saved settings and Discord permissions.</p><button data-action="reload">Retry</button></section>' : ''}
    ${snapshot && draft ? organizerViews.map((view) => `<section class="workspace-panel" role="tabpanel" id="view-${view}" data-view-panel="${view}" aria-labelledby="tab-${view}" hidden>${{ queues: queueSection, matches: recordSection, players: playerSection, setup: setupSection }[view]()}</section>`).join('') : ''}
    </div><div id="savebar" class="savebar" hidden><span><strong>Unsaved changes</strong><small>Applies across this community</small></span><button type="button" data-edit="reload-conflict" hidden>Reload latest</button><button type="button" data-edit="review">Review changes</button><button type="button" data-edit="discard">Discard</button><button type="button" class="primary" data-edit="save">Save changes</button></div></div></div>`;
  if (snapshot && draft) {
    if (new URL(location.href).searchParams.get('view') === 'overview') {
      const u = new URL(location.href);
      u.searchParams.set('view', 'queues');
      history.replaceState(history.state, '', u);
    }
    selectView(currentView(), false, false);
    draft.mount(app);
    // A shared guide URL may target a different queue than the visible editor.
    const guideQueue = app.querySelector<HTMLElement>('[data-guide-queue]')?.dataset.guideQueue;
    if (guideQueue && !app.querySelector(`[data-config="queue:${CSS.escape(guideQueue)}"]`)) {
      const detached = document.createElement('div');
      detached.innerHTML = queueEditor(draft, setup, guideQueue);
      draft.mount(detached);
    }
    updateGuidePreview();
    settingReadouts(app);
    applyCategories();
    applyFilters();
    labelTables(app);
    updateDirty();
    const search = app.querySelector<HTMLInputElement>('[data-settings-search]');
    if (search) search.value = settingsSearch;
    applyGameRoute();
    paintIssues();
    if (busy)
      app
        .querySelectorAll<
          HTMLInputElement | HTMLButtonElement | HTMLSelectElement | HTMLTextAreaElement
        >('input,button,select,textarea')
        .forEach((el) => (el.disabled = true));
    else restoreFocus();
  }
  if (currentNotice) notice(currentNotice.message, currentNotice.error, false);
}
function applyGameRoute() {
  const game = new URL(location.href).searchParams.get('game');
  app.querySelectorAll<HTMLDetailsElement>('.game-settings').forEach((details) => {
    details.open = details.id === `game-settings-${game}`;
  });
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
  const submitter = (event as SubmitEvent).submitter as HTMLButtonElement | null;
  if (submitter?.name === 'directAction') data.set('kind', submitter.value);
  void run(async () => {
    if (form.hasAttribute('data-inline-player') && !form.dataset.review) {
      form.dataset.review = 'visibility';
      form.insertAdjacentHTML(
        'beforeend',
        '<p class="notice" data-review>Apply this visibility change for this player and game? Submit again to confirm. <button type="button" data-action="cancel-repair">Cancel</button></p>',
      );
      return;
    }
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
      case 'browse-games': {
        const community = guildId;
        const target = button
          .closest('.connector-settings')
          ?.querySelector<HTMLElement>('[data-game-list]');
        const list = await api<ProviderResult<PublicGameList>>(
          communityUrl(
            `connectors/${encodeURIComponent(button.dataset.game!)}/games?phase=${button.dataset.phase}`,
          ),
        );
        if (community === guildId && target?.isConnected)
          target.textContent = publicGameListText(list, 20000).replace(/<t:(\d+):T>/g, (_, t) =>
            new Date(Number(t) * 1000).toLocaleTimeString(),
          );
        break;
      }
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
        draft = snapshot ? new SettingsDraft(snapshot, setup.gameState) : null;
        location.reload();
        break;
      case 'refresh-communities':
        guilds = (await api<{ communities: Guild[] }>('/api/communities')).communities;
        if (!guildId && guilds[0]) await loadCommunity(guilds[0].id, 'replace');
        else render();
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
    if (input.value !== guildId && canLeave()) void run(() => loadCommunity(input.value, 'push'));
    else input.value = guildId;
  }
});
app.addEventListener('input', (event) => {
  const input = event.target as HTMLInputElement;
  if (input.dataset.filter === 'player-search') updateFilter(input);
});

function editRoute(view?: OrganizerView, mode: 'push' | 'replace' = 'push') {
  const url = new URL(location.href);
  if (view) url.searchParams.set('view', view);
  url.searchParams.set('queue', selectedQueue);
  url.searchParams.set('section', category);
  writeRoute(url, mode);
}
function guideRoute(step: GuideStep) {
  if (currentNotice && !currentNotice.error) currentNotice = null;
  const url = new URL(location.href);
  url.searchParams.set('view', 'setup');
  url.searchParams.set('guide', step);
  writeRoute(url);
  render();
  app.querySelector<HTMLElement>('#guide-heading')?.focus();
}
function updateGuidePreview() {
  const form = app.querySelector<HTMLFormElement>('[data-guide-queue]');
  if (!form) return;
  const data = fields(form);
  const queue = draft?.materialize().queues.find((q) => q.id === form.dataset.guideQueue);
  const rules = app.querySelector('[data-preview-rules]');
  if (queue && rules)
    rules.textContent = `${queue.checkIn.enabled ? `Check-in: ${queue.checkIn.seconds} seconds` : 'Check-in off'} · ${
      Object.entries(queue.modules)
        .filter(([, enabled]) => enabled)
        .map(([name]) => name)
        .join(', ') || 'Optional modules off'
    } · ${queue.enabled ? 'Enabled on save' : 'Paused on save'}`;
  app.querySelector('[data-preview-name]')!.textContent = value(data, 'name') || 'Name your queue';
  const size = Number(value(data, 'size'));
  app.querySelector('[data-preview-details]')!.textContent =
    `${Number.isInteger(size) && size >= 2 && size <= 16 && size % 2 === 0 ? `${size / 2} vs ${size / 2}` : 'Choose an even size from 2 to 16'} · ${form.querySelector<HTMLSelectElement>('[name="teamMethod"]')!.selectedOptions[0]?.textContent ?? ''}`;
}
function guideValid() {
  const form =
    currentView() === 'setup' && app.querySelector<HTMLFormElement>('[data-guide-queue]');
  return !form || form.reportValidity();
}
function reviewChanges() {
  if (!draft || !guideValid()) return;
  const dialog = openDialog(
    'Review community changes',
    `<p class="form-help">Compared with saved revision ${draft.saved.revision}.${conflict ? ' Newer settings were found. Your draft is preserved; saving cannot overwrite the newer revision.' : ''}</p>${changeReview(draft.saved.settings, draft.materialize(), Object.fromEntries([...(setup.knownChannels ?? []), ...setup.channels, ...setup.roles].map((v) => [v.id, v.name])))}<div class="actions review-actions"><button type="button" data-review-edit>Back to editing</button><button type="button" data-review-save class="primary">Save reviewed changes</button></div>`,
  );
  dialog.classList.add('change-review');
  dialog.querySelector('[data-review-edit]')!.addEventListener('click', () => dialog.close());
  dialog.querySelector('[data-review-save]')!.addEventListener('click', () => {
    dialog.close();
    app.querySelector<HTMLButtonElement>('[data-edit="save"]')?.click();
  });
}
function captureEdit(event: Event) {
  const input = event.target as HTMLInputElement;
  if (busy) return;
  if (input.matches('.result-form [name="result"]')) {
    const consequence = input.closest('form')?.querySelector('[data-result-consequence]');
    if (consequence)
      consequence.textContent = `${input.value === 'draw' ? 'Record a draw for both teams.' : `Record ${input.value === 'beta' ? 'Beta' : 'Alpha'} as the winning team.`} The server applies this game’s saved rating rules and updates match history. Paid prediction settlements remain final.`;
  }
  if (input.hasAttribute('data-guide-game') && event.type === 'change' && draft) {
    const url = new URL(location.href);
    const previous = url.searchParams.get('guideGame');
    const abandoned = draft.materialize().games.find((g) => g.id === previous);
    if (
      previous &&
      guideCreatedGames.has(previous) &&
      abandoned &&
      !abandoned.name.trim() &&
      !draft.saved.settings.games.some((g) => g.id === previous) &&
      !draft.materialize().queues.some((q) => q.gameId === previous)
    ) {
      draft.working.games = draft.working.games.filter((g) => g.id !== previous);
      draft.raw.delete(`game:${previous}`);
      guideCreatedGames.delete(previous);
    }
    let id = input.value;
    if (id === 'custom' || id.startsWith('catalog:')) {
      const item = gameCatalog.find((g) => g.id === id.slice(8));
      id = crypto.randomUUID();
      draft.working.games.push({ id, name: item?.name ?? '', catalogId: item?.id ?? null });
      guideCreatedGames.add(id);
    }
    url.searchParams.set('guideGame', id);
    url.searchParams.delete('guideQueue');
    writeRoute(url, 'replace');
    render();
    return;
  }
  if (input.hasAttribute('data-guide-name') && draft) {
    const id = new URL(location.href).searchParams.get('guideGame');
    const form = app.querySelector<HTMLFormElement>(`[data-config="game:${CSS.escape(id ?? '')}"]`);
    const field = form?.elements.namedItem('name') as HTMLInputElement | null;
    if (form && field) {
      field.value = input.value;
      draft.capture(form);
    } else {
      const game = draft.working.games.find((g) => g.id === id);
      if (game) game.name = input.value;
      if (draft.raw.has(`game:${id}`)) draft.patchFields(`game:${id}`, { name: [input.value] });
    }
    const next = app.querySelector<HTMLButtonElement>('[data-guide-next="game"]');
    if (next) {
      next.disabled = !input.value.trim();
      next.dataset.unavailable = String(next.disabled);
    }
    updateDirty();
    return;
  }
  const guideForm = input.closest<HTMLFormElement>('[data-guide-queue]');
  if (guideForm && draft) {
    const key = `queue:${guideForm.dataset.guideQueue!}`;
    draft.patchFields(key, { [input.name]: [input.value] });
    const form = app.querySelector<HTMLFormElement>(`[data-config="${CSS.escape(key)}"]`);
    if (form) (form.elements.namedItem(input.name) as HTMLInputElement).value = input.value;
    validationIssues = validationIssues.filter((i) => i.form !== key || i.name !== input.name);
    paintIssues();
    updateDirty();
    updateGuidePreview();
    return;
  }
  const form = input.closest<HTMLFormElement>('form[data-config]');
  if (form && draft) {
    if (input.name === 'visibility' && input.value !== 'public') {
      const publish = form.querySelector<HTMLInputElement>('[name="searchPublishing"]');
      if (publish) publish.checked = false;
    }
    draft.capture(form);
    validationIssues = validationIssues.filter(
      (issue) => issue.form !== form.dataset.config || issue.name !== input.name,
    );
    paintIssues();
    if (input.type === 'checkbox') {
      const text = input.closest('.switch-control')?.querySelector('.switch-text');
      if (text) text.textContent = input.checked ? 'On' : 'Off';
    }
    updateDirty();
    settingReadouts(app);
    if (event.type === 'change' && ['gameId', 'module-gold'].includes(input.name)) render();
  }
  if (input.hasAttribute('data-settings-search')) {
    settingsSearch = input.value;
    applyCategories();
  }
  if (input.hasAttribute('data-queue-select') && event.type === 'change') {
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
  if (target.closest('[data-player-import]')) {
    const section = app.querySelector<HTMLDetailsElement>('[data-import-section]');
    if (section) {
      section.open = true;
      section.scrollIntoView({ block: 'start' });
      section.querySelector<HTMLElement>('summary')?.focus();
    }
    return true;
  }
  if (target.closest('[data-player-policy]')) {
    const game = currentFilters().playerGame;
    if (game) openGameSettings(game);
    return true;
  }
  if (target.closest('[data-queue-list]')) {
    const url = new URL(location.href);
    url.searchParams.delete('queue');
    url.searchParams.delete('section');
    selectedQueue = '';
    writeRoute(url);
    render();
    return true;
  }
  if (target.closest('[data-account-menu]')) {
    const dialog = openDialog(
      'Account',
      `<label>Theme<select data-theme-choice><option value="light">Light</option><option value="dark">Dark</option><option value="system">System</option></select></label><div class="actions"><button data-menu-action="refresh-records">Refresh</button><button data-menu-action="logout">Sign out</button><a href="/">Website</a>${snapshot?.settings.games[0] ? `<a href="/stats?community=${guildId}&game=${snapshot.settings.games[0].id}">Community statistics</a>` : ''}</div>`,
    );
    dialog.querySelectorAll<HTMLElement>('[data-menu-action]').forEach((button) =>
      button.addEventListener('click', () => {
        const action = button.dataset.menuAction;
        dialog.close();
        app.querySelector<HTMLButtonElement>(`[data-action="${action}"]`)?.click();
      }),
    );
    return true;
  }
  const guide = target.closest<HTMLElement>(
    '[data-guide-start], [data-guide-next], [data-guide-step], [data-guide-close], [data-guide-advanced], [data-guide-review], [data-open-queue], [data-toggle-queue]',
  );
  if (guide) {
    if (busy || !draft) return true;
    if (guide.dataset.openQueue) openQueue(guide.dataset.openQueue);
    if (guide.dataset.toggleQueue) {
      const id = guide.dataset.toggleQueue,
        q = draft.materialize().queues.find((q) => q.id === id)!;
      // The list may be the first screen opened. Establish the full form baseline
      // before staging a toggle so reverting later in the editor clears the draft.
      const detached = document.createElement('div');
      detached.innerHTML = queueEditor(draft, setup, id);
      draft.mount(detached);
      draft.patchFields(`queue:${id}`, { enabled: q.enabled ? [] : ['on'] });
      render();
      notice('Queue state staged. Save changes to apply it in Discord.');
    }
    if (guide.hasAttribute('data-guide-start')) {
      const url = new URL(location.href);
      if (!url.searchParams.has('guideGame') && draft.working.games[0])
        url.searchParams.set('guideGame', draft.working.games[0].id);
      writeRoute(url, 'replace');
      guideRoute('access');
    }
    if (guide.hasAttribute('data-guide-close')) {
      const url = new URL(location.href);
      url.searchParams.delete('guide');
      writeRoute(url);
      render();
    }
    if (guide.dataset.guideStep && guideSteps.includes(guide.dataset.guideStep as GuideStep))
      guideRoute(guide.dataset.guideStep as GuideStep);
    if (guide.dataset.guideAdvanced) openQueue(guide.dataset.guideAdvanced);
    if (guide.hasAttribute('data-guide-review')) reviewChanges();
    if (guide.dataset.guideNext === 'access') guideRoute('game');
    if (guide.dataset.guideNext === 'game') {
      const url = new URL(location.href);
      const game = draft
        .materialize()
        .games.find((g) => g.id === url.searchParams.get('guideGame'));
      if (!game?.name.trim()) return true;
      const existing = draft.working.queues.find(
        (q) => q.id === url.searchParams.get('guideQueue') && q.gameId === game.id,
      );
      const id =
        existing?.id ??
        draft.addQueue(
          game.id,
          setup.channels.find((c) => c.permissions.every((p) => p.granted))?.id ??
            setup.channels[0]?.id ??
            '',
        );
      if (!existing) draft.working.queues.find((q) => q.id === id)!.enabled = true;
      selectedQueue = id;
      url.searchParams.set('queue', id);
      url.searchParams.set('guideQueue', id);
      writeRoute(url, 'replace');
      guideRoute('queue');
    }
    return true;
  }
  const more = target.closest<HTMLElement>('[data-import-page]');
  if (more && !busy) {
    pageImportRows(more);
    return true;
  }
  if (target.closest('[data-discord-invite]')) awaitingDiscordReturn = true;
  const gameSummary = target.closest<HTMLElement>('.game-settings > summary');
  if (gameSummary) {
    event.preventDefault();
    if (busy) return true;
    const details = gameSummary.parentElement as HTMLDetailsElement;
    const url = new URL(location.href);
    if (details.open) url.searchParams.delete('game');
    else url.searchParams.set('game', details.id.replace('game-settings-', ''));
    writeRoute(url);
    applyGameRoute();
    return true;
  }
  const engine = target.closest<HTMLElement>('[data-restore-engine]');
  if (engine && draft && !busy) {
    const game = engine.dataset.restoreEngine!;
    draft.restoreEngine(game);
    validationIssues = validationIssues.filter(
      (issue) => issue.form !== `rating:${game}` || issue.name !== 'ratingEngine',
    );
    render();
    notice('Saved rating system restored. Your other edits are preserved.');
    return true;
  }
  if (target.closest('[data-discard-import]')) {
    invalidateImport();
    return true;
  }
  const help = target.closest<HTMLElement>('[data-help]');
  if (help) {
    openDialog(help.dataset.helpTitle!, settingHelp(help.closest<HTMLElement>('[data-setting]')!));
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
    openGameSettings(gameLink.dataset.gameLink!);
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
        if (b.dataset.community !== guildId)
          void run(() => loadCommunity(b.dataset.community!, 'push'));
      }
      if (b.dataset.drawerQueue) {
        selectedQueue = b.dataset.drawerQueue;
        dialog.close();
        editRoute('queues');
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
  if (action.dataset.edit === 'review') reviewChanges();
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
    draft = new SettingsDraft(snapshot, setup.gameState);
    validationIssues = [];
    currentNotice = null;
    conflict = false;
    render();
  }
  if (action.dataset.edit === 'reload-conflict' && canLeave())
    void run(() => loadCommunity(guildId));
  if (action.dataset.edit === 'save') {
    if (!guideValid()) return true;
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
  if (['/stats', '/link'].includes(location.pathname)) return;
  if (restoringHistory) {
    restoringHistory = false;
    return;
  }
  const url = new URL(location.href);
  const id = url.searchParams.get('community');
  const nextIndex = history.state?.organizerIndex;
  if (busy || (id && id !== guildId && !canLeave())) {
    if (typeof nextIndex === 'number' && nextIndex !== historyIndex) {
      restoringHistory = true;
      history.go(historyIndex - nextIndex);
    } else writeRoute(new URL(acceptedUrl), 'replace');
    return;
  }
  historyIndex = typeof nextIndex === 'number' ? nextIndex : historyIndex;
  acceptedUrl = url.href;
  if (id && id !== guildId) {
    void run(() => loadCommunity(id));
    return;
  }
  selectedQueue = url.searchParams.get('queue') ?? '';
  category = (url.searchParams.get('section') ?? 'basics') as Category;
  settingsSearch = '';
  render();
});
installViewport();
const recheckAfterDiscord = () => {
  if (!awaitingDiscordReturn || document.visibilityState === 'hidden' || busy) return;
  awaitingDiscordReturn = false;
  void run(async () => {
    if (snapshot) await refreshSetup();
    else {
      guilds = (await api<{ communities: Guild[] }>('/api/communities')).communities;
      if (guilds[0]) await loadCommunity(guilds[0].id, 'replace');
      else render();
    }
  });
};
window.addEventListener('focus', recheckAfterDiscord);
document.addEventListener('visibilitychange', recheckAfterDiscord);

async function start() {
  writeRoute(new URL(location.href), 'replace');
  bootstrap = await api<Bootstrap>('/api/bootstrap');
  document.querySelector('#environment')!.textContent = bootstrap.environment.toUpperCase();
  document.querySelector('#version')!.textContent = `v${bootstrap.version}`;
  if (location.pathname === '/link') {
    await showAccountLink(app, bootstrap);
    app.setAttribute('aria-busy', 'false');
    return;
  }
  if (bootstrap.user) {
    guilds = (await api<{ communities: Guild[] }>('/api/communities')).communities;
    render();
    const requested = new URL(location.href).searchParams.get('community');
    const first = guilds.find((g) => g.id === requested) ?? guilds[0];
    if (first) await loadCommunity(first.id, 'replace');
  } else render();
  app.setAttribute('aria-busy', 'false');
}
(location.pathname === '/stats' ? showStats(app) : start()).catch((error) => {
  if (!document.querySelector('#notice'))
    app.innerHTML = `<h1>${location.pathname === '/stats' ? 'Statistics' : 'Setup'} could not load.</h1><div id="notice" role="alert"></div><a class="button" href="${escape(location.pathname + location.search)}">Try again</a>`;
  notice(error instanceof Error ? error.message : 'Try again.', true);
  app.setAttribute('aria-busy', 'false');
});
