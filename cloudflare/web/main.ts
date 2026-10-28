import { gameCapabilities } from '../src/games/catalog';
import './style.css';
import { onboarding, onboardingSubmit, applyImport } from './onboarding';
import { showStats } from './stats';
import { emptyModules, type Queue, type Settings, type Snapshot } from '../src/domain/config';
import { defaultRatingSettings, type Player } from '../src/domain/rating';
import { aoe2Maps, defaultMaps } from '../src/domain/maps';
import type { Action, Match } from '../src/domain/play';
interface Records {
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
interface ModeratorRole {
  id: string;
  name: string;
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
  channelList: Channel[] = [],
  moderatorRoles: ModeratorRole[] = [];
let busy = false;
let records: Records | null = null;
const organizerViews = ['queues', 'matches', 'players', 'setup'] as const;
type OrganizerView = (typeof organizerViews)[number];
const viewNames: Record<OrganizerView, string> = {
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
const checked = (b: boolean) => (b ? ' checked' : '');
const isOrganizerView = (value: string | null): value is OrganizerView =>
  organizerViews.includes(value as OrganizerView);

function currentView(): OrganizerView {
  const saved = viewsByCommunity.get(guildId);
  if (saved) return saved;
  const view =
    !initialViewUsed && isOrganizerView(requestedView)
      ? requestedView
      : snapshot?.settings.queues.length
        ? 'queues'
        : 'setup';
  initialViewUsed = true;
  viewsByCommunity.set(guildId, view);
  return view;
}
function selectView(view: OrganizerView, focus = false) {
  viewsByCommunity.set(guildId, view);
  const url = new URL(location.href);
  url.searchParams.set('view', view);
  history.replaceState(null, '', url);
  app.querySelectorAll<HTMLButtonElement>('[data-view]').forEach((button) => {
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
  if (body) body.innerHTML = playerRatingRows();
  applyFilters();
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  if (bootstrap?.csrf) headers.set('X-CSRF-Token', bootstrap.csrf);
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  const body = (await response.json()) as { error?: string; requestId?: string };
  if (!response.ok)
    throw new Error(
      `${body.error ?? 'Request failed.'}${body.requestId ? ` (Request ${body.requestId})` : ''}`,
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
      box.focus();
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
  app
    .querySelectorAll<
      HTMLInputElement | HTMLSelectElement | HTMLButtonElement | HTMLTextAreaElement
    >('input, select, button, textarea')
    .forEach((b) => (b.disabled = true));
  try {
    await action();
  } catch (error) {
    notice(error instanceof Error ? error.message : 'Try again.', true);
  } finally {
    busy = false;
    app.setAttribute('aria-busy', 'false');
    app
      .querySelectorAll<
        HTMLInputElement | HTMLSelectElement | HTMLButtonElement | HTMLTextAreaElement
      >('input, select, button, textarea')
      .forEach((b) => (b.disabled = b.dataset.unavailable === 'true'));
  }
}
async function save(settings: Settings) {
  if (!snapshot) return;
  snapshot = await api<Snapshot>(communityUrl('config'), {
    method: 'PUT',
    body: JSON.stringify({ expectedRevision: snapshot.revision, settings }),
  });
  render();
  notice('Settings saved.');
}
async function loadCommunity(id: string) {
  guildId = id;
  snapshot = null;
  records = null;
  channelList = [];
  moderatorRoles = [];
  const url = new URL(location.href);
  url.searchParams.set('community', id);
  history.replaceState(null, '', url);
  render();
  const result = await api<{
    snapshot: Snapshot;
    channels: Channel[];
    moderatorRoles: ModeratorRole[];
    records: Records;
  }>(communityUrl('setup'));
  if (guildId !== id) return;
  snapshot = result.snapshot;
  channelList = result.channels;
  moderatorRoles = result.moderatorRoles;
  records = result.records;
  refreshedAt = new Date();
  render();
}
function moderatorRoleFields(q?: Queue) {
  const chosen = q?.adminRoles ?? [];
  const unavailable = chosen.filter((id) => !moderatorRoles.some((r) => r.id === id));
  return `<fieldset class="moderator-roles"><legend>Bot moderator roles</legend>
    <p class="muted">Server owners and members with Administrator or Manage Server permission already have access. Anyone can open /admin and start a match. Select additional roles that can clear this queue and report results without being captain.</p>
    <div class="moderator-role-list">${moderatorRoles.map((r) => `<label class="toggle"><input type="checkbox" name="adminRoles" value="${escape(r.id)}"${checked(chosen.includes(r.id))} /><span>${escape(r.name)} <small>${escape(r.id)}</small></span></label>`).join('')}
    ${unavailable.map((id) => `<label class="toggle"><input type="checkbox" name="adminRoles" value="${escape(id)}" checked /><span>Unavailable role <small>${escape(id)}</small> — deselect to remove access</span></label>`).join('')}</div>
    ${!moderatorRoles.length ? '<p class="muted">No selectable roles. Create a role in Discord and refresh this page.</p>' : ''}
    <p class="muted">These roles also allow match cancellation and betting closure. They do not grant access to website settings or /manage. No Discord Administrator permission is needed for a bot moderator.</p>
    </fieldset>`;
}
function queueFields(q?: Queue) {
  const s = snapshot!.settings;
  const rating = q?.ratingSettings ?? defaultRatingSettings;
  const catalogGame = s.games.find((g) => g.id === (q?.gameId ?? s.games[0]?.id));
  const maps = q?.maps ?? defaultMaps(catalogGame?.catalogId === 'aoe2' ? aoe2Maps : []);
  return `<div class="fields">
    <label>Queue name<input name="name" required maxlength="40" value="${escape(q?.name ?? 'Pickup')}" /></label>
    <label>Game<select name="gameId" required>${s.games.map((g) => `<option value="${g.id}"${selected(g.id, q?.gameId ?? '')}>${escape(g.name)}</option>`).join('')}</select></label>
    <label>Discord channel<select name="channelId" required>${channelList.map((c) => `<option value="${c.id}"${selected(c.id, q?.channelId ?? '')}>#${escape(c.name)}</option>`).join('')}</select></label>
    <label>Players<input name="size" type="number" min="2" step="1" required value="${q?.size ?? 8}" /></label>
    <label>Team formation<select name="teamMethod"><option value="captain"${selected('captain', q?.teamMethod ?? 'captain')}>Captain-based balance</option><option value="balanced"${selected('balanced', q?.teamMethod ?? '')}>Automatic balance</option><option value="random"${selected('random', q?.teamMethod ?? '')} >Random teams</option></select></label>
    <label>Rating system<select name="ratingEngine">${['aoe2', 'glicko2', 'trueskill', 'flat'].map((r) => `<option value="${r}"${selected(r, q?.ratingEngine ?? 'aoe2')}>${{ aoe2: 'AoE2-style Elo', glicko2: 'Glicko2', trueskill: 'TrueSkill', flat: 'Flat' }[r]}</option>`).join('')}</select></label>
    <label>Check-in seconds<input name="seconds" type="number" min="1" step="1" required value="${q?.checkIn.seconds ?? 60}" /></label>
    <label>Ready validity seconds<input name="reuseSeconds" type="number" min="0" max="900" value="${q?.checkIn.reuseSeconds ?? 900}" /></label>
    <label class="toggle"><input name="checkIn" type="checkbox"${checked(q?.checkIn.enabled ?? true)} /> Require check-in</label>
    <label class="toggle"><input name="enabled" type="checkbox"${checked(q?.enabled ?? false)} /> Enable this queue</label>
  </div><details class="rating-options"><summary>Queue controls and access</summary><p class="muted">Zero disables waiting expiry. Match lifetime is at most 12 hours. Role IDs can be copied from Discord with Developer Mode.</p><div class="fields">
    <label class="toggle"><input type="checkbox" name="autoStart"${checked(q?.autoStart ?? true)} /> Start automatically at capacity</label>
    <label class="toggle"><input type="checkbox" name="isDefault"${checked(q?.isDefault ?? false)} /> Default queue in this channel</label>
    <label class="toggle"><input type="checkbox" name="keepQueueVisible"${checked(q?.keepQueueVisible ?? true)} /> Keep queue card near the bottom</label>
    <p class="muted">Every 15 minutes, move the queue card to the bottom if it is outside the channel’s last five messages. Turning this off stops periodic checks; player updates still refresh the card.</p>
    <label>Queue aliases<input name="aliases" value="${escape(q?.aliases?.join(', ') ?? '')}" placeholder="Comma-separated nicknames" /></label>
    <label>Waiting expiry seconds<input type="number" min="0" max="1209600" name="waitingSeconds" value="${q?.controls?.waitingSeconds ?? 0}" required /></label>
    <label>Match timeout seconds<input type="number" min="0" max="43200" name="matchSeconds" value="${q?.controls?.matchSeconds || 43200}" required /></label>
    <label>Required role ID<input name="requiredRole" value="${escape(q?.controls?.requiredRole ?? '')}" placeholder="Blank allows everyone" /></label>
    <label>Blocked role ID<input name="blockedRole" value="${escape(q?.controls?.blockedRole ?? '')}" placeholder="Blank blocks no role" /></label>
  </div>${moderatorRoleFields(q)}</details><details class="rating-options"><summary>Rating settings</summary><p class="muted">Shared by all queues for this game. The rating system cannot change once the game has player records. Starting ratings can be set in Players.</p><div class="fields">${Object.entries(
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
    .map(
      ([key, label]) =>
        `<label>${label}<input type="number" name="rating-${key}" required value="${rating[key as keyof typeof rating]}" /></label>`,
    )
    .join(
      '',
    )}<label class="toggle"><input type="checkbox" name="rating-winStreakBoost"${checked(rating.winStreakBoost)} /> Win streak boost</label><label class="toggle"><input type="checkbox" name="rating-lossStreakBoost"${checked(rating.lossStreakBoost)} /> Loss streak boost</label></div></details><fieldset class="module-options"><legend>Optional features</legend><p class="muted">Enable the extras your community uses.</p>${Object.entries(
    {
      gold: 'Gold',
      predictions: 'Predictions',
      quiz: 'Quizzes',
      civs: 'Civ picking',
      maps: 'Map picking',
      stories: 'Match stories',
    },
  )
    .map(
      ([key, label]) =>
        `<label class="toggle${['quiz'].includes(key) ? ' unavailable-option' : ''}"><input type="checkbox" name="module-${key}"${checked(q?.modules[key as keyof typeof emptyModules] ?? false)}${['quiz'].includes(key) ? ' disabled data-unavailable="true"' : ''} /> ${label}${['quiz'].includes(key) ? ' (not available yet)' : ''}</label>`,
    )
    .join(
      '',
    )}</fieldset><details class="rating-options"><summary>Gold tax settings</summary><p class="muted">Thursday tax takes 10% for inactivity and never reduces a balance below 500 gold. Bets kept until betting closes count as participation. A seven-day grace period and a betting opportunity are required.</p><label class="toggle"><input name="taxEnabled" type="checkbox"${checked(q?.economy?.taxEnabled ?? false)} /> Enable weekly tax for this game</label><label>Thursday UTC minute (0–1439)<input name="taxMinuteUtc" type="number" min="0" max="1439" value="${q?.economy?.taxMinuteUtc ?? 0}" /></label><p class="muted">Minutes after midnight UTC: 0 is 00:00, 600 is 10:00.</p></details><details class="rating-options"><summary>Map selection settings</summary><p class="muted">Selected maps belong to one ranked match, not separate rated rounds. AoE2 has a starter pool; custom games use your map names.</p>
    <label>Map pool (one per line)<textarea name="mapPool" rows="6">${escape(maps.pool.join('\n'))}</textarea></label>
    <div class="fields"><label>Mode<select name="mapMode">${['vote', 'random', 'direct'].map((v) => `<option value="${v}"${selected(v, maps.mode)}>${{ vote: 'Player voting', random: 'Random selection', direct: 'Captain picks' }[v]}</option>`).join('')}</select></label>
    <label>Maps to play<input name="mapCount" type="number" min="1" max="5" value="${maps.count}" /></label>
    <label>Shortlist size<input name="mapShortlist" type="number" min="1" max="25" value="${maps.shortlist || 5}" /></label>
    <label>Voting<select name="mapVoting">${['plurality', 'approval'].map((v) => `<option value="${v}"${selected(v, maps.voting)}>${v === 'plurality' ? 'One choice per player' : 'Approve multiple maps'}</option>`).join('')}</select></label>
    <label>Ties<select name="mapTie">${['random', 'alphabetical'].map((v) => `<option value="${v}"${selected(v, maps.tie)}>${v}</option>`).join('')}</select></label>
    <label>No votes<select name="mapNoVotes">${['random', 'cancel'].map((v) => `<option value="${v}"${selected(v, maps.noVotes)}>${v}</option>`).join('')}</select></label>
    <label>Selection seconds<input name="mapSeconds" type="number" min="10" max="3600" value="${maps.seconds}" /></label>
    <label>Recent matches to avoid<input name="mapRecent" type="number" min="0" max="20" value="${maps.recentAvoid}" /></label></div>
    <label class="toggle"><input name="mapCheckIn" type="checkbox"${checked(maps.countsAsCheckIn)} /> A map vote also checks the player in (voting mode)</label></details><details class="rating-options"><summary>Voice presence</summary><label class="toggle"><input type="checkbox" name="voicePreview"${checked(q?.voicePreview ?? false)} /> Show up to five people in public voice channels who are not queued or playing</label><p class="muted">Refreshes every five minutes while this queue is enabled and has a card. Uses a shared voice connection with ongoing hosting cost. Names are displayed without notifications. Ping sends a separate invitation to eligible voice participants, at most once every five minutes per server.</p><p class="muted">Rally can also use the shared voice connection. Without preview refreshes, it stops an hour after the last rally request. No voice audio is processed. This adds connection duration to hosting usage.</p><label class="toggle"><input type="checkbox" name="rallyEnabled"${checked(q?.rally?.enabled ?? false)} /> Enable /rally</label><label>Allowed voice channel IDs (comma-separated, blank uses caller's channel)<input name="rallyChannels" value="${escape(q?.rally?.channels.join(',') ?? '')}" /></label><label>Additional rally role IDs (comma-separated)<input name="rallyRoles" value="${escape(q?.rally?.roles.join(',') ?? '')}" /></label><label>Cooldown seconds<input name="rallyCooldown" type="number" min="60" max="3600" value="${q?.rally?.cooldownSeconds ?? 300}" /></label><label>Maximum mentions<input name="rallyMentions" type="number" min="1" max="8" value="${q?.rally?.mentionLimit ?? 8}" /></label></details>`;
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
  return `<section class="panel"><div class="section-heading"><h2>Games</h2></div><p class="muted">Add a catalog game or your own. Each game keeps separate ratings and gold.</p>
    <div class="game-list">${snapshot!.settings.games.map((g) => `<form data-kind="rename" data-id="${g.id}" class="game-row"><label class="sr-only" for="game-${g.id}">Game name</label><input id="game-${g.id}" name="name" maxlength="80" required value="${escape(g.name)}" />${gameCapabilities(g.catalogId)?.lobby ? `<label><input type="checkbox" name="lobbyConnector"${g.connector?.enabled ? ' checked' : ''} /> Track lobby start/end (manual results)</label><label><input type="checkbox" name="autoLobby"${g.connector?.autoAttach ? ' checked' : ''} /> Automatically attach matching public lobby</label><label>Lobby name<input name="lobbyName" maxlength="80" value="${escape(g.connector?.autoAttach?.lobbyName ?? 'NammaNomad')}" /></label>` : '<span class="muted">Manual lobby and result reporting</span>'}<button>Save game</button></form>`).join('') || '<p class="empty-state">Add your first game to create a queue.</p>'}</div>
    <form data-kind="game" class="add-game"><label>Game<select name="catalogId"><option value="">Custom game</option>${bootstrap.catalog.map((g) => `<option value="${g.id}">${escape(g.name)}</option>`).join('')}</select></label><label>Custom name<input name="name" maxlength="80" placeholder="Game name" /></label><button class="primary">Add game</button></form></section>
    <section class="panel"><div class="section-heading"><h2>Discord access</h2>${bootstrap.inviteUrl ? `<a class="button" href="${escape(bootstrap.inviteUrl)}&guild_id=${guildId}&disable_guild_select=true">Invite bot to server</a>` : ''}</div><p class="muted">Check the channels used by your queues.</p><div class="channel-list">${inaccessible.map((id) => `<p class="notice error">The bot cannot access configured channel ${escape(id)}. Check its Discord permissions or choose another channel in Queues.</p>`).join('')}${configuredChannels.map(channelRow).join('')}${!configured.size ? '<p class="empty-state">No queue channels selected yet.</p>' : ''}</div>
    ${otherChannels.length ? `<details class="rating-options"><summary>Other available channels (${otherChannels.length})</summary><div class="channel-list">${otherChannels.map(channelRow).join('')}</div></details>` : ''}${!channelList.length ? '<p class="notice error">No accessible text channels found. Check the bot’s channel permissions.</p>' : ''}<details class="technical-details"><summary>Configuration details</summary><p>Saved settings revision ${snapshot!.revision}</p><p>Community ${escape(guildId)}</p></details></section>`;
}
function queueSection(openQueues: Set<string>) {
  return `<section class="panel"><div class="section-heading"><h2>Queues</h2></div><p class="muted">Players join and play in Discord. Choose a queue to edit its rules.</p>
    ${snapshot!.settings.queues.map((q) => `<details class="queue"${openQueues.has(q.id) ? ' open' : ''}><summary class="queue-summary">${escape(q.name)} <span>${escape(gameName(q.gameId))} · #${escape(channelList.find((c) => c.id === q.channelId)?.name ?? q.channelId)} · ${q.size} players</span><span class="status-pill">${q.enabled ? 'Enabled' : 'Paused'}${q.isDefault ? ' · Default' : ''}</span></summary><form data-kind="queue" data-id="${q.id}">${queueFields(q)}<div class="actions"><button class="primary">Save queue</button><button type="button" data-action="remove" data-id="${q.id}">Remove queue</button></div></form></details>`).join('')}
    ${snapshot!.settings.games.length && channelList.length ? `<details class="queue"${snapshot!.settings.queues.length ? '' : ' open'}><summary>Add a queue</summary><form data-kind="queue">${queueFields()}<button class="primary">Save new queue</button></form></details>` : '<p class="notice">Add a game and check Discord channel access in Setup before creating a queue.</p>'}</section>`;
}
function render() {
  const openQueues = new Set(
    Array.from(
      app.querySelectorAll<HTMLFormElement>('details[open] > form[data-kind="queue"][data-id]'),
    ).map((form) => form.dataset.id!),
  );
  if (!bootstrap.user) {
    app.innerHTML = `<section class="intro"><h1>Community organizer</h1><p class="lead">Set up Discord queues, manage game-night rules and review match results. Players join and play in Discord.</p><div class="actions">${bootstrap.oauthReady ? '<a class="button primary" href="/auth/login">Sign in with Discord</a>' : '<p class="notice">Discord sign-in will be available when the operator finishes setup.</p>'}${bootstrap.inviteUrl ? `<a class="button" href="${escape(bootstrap.inviteUrl)}">Invite the bot</a>` : ''}</div></section>`;
    return;
  }
  const current = guilds.find((g) => g.id === guildId);
  app.innerHTML = `<div class="page-heading"><div><h1>${escape(current?.name ?? 'Choose your community')}</h1><p class="muted">Community organizer</p></div><button data-action="logout" class="text-button">Sign out ${escape(bootstrap.user.username)}</button></div>
    <div id="notice" class="notice" role="status" hidden></div>
    <div class="community-bar"><label>Discord server<select id="community"><option value="">Choose a server</option>${guilds.map((g) => `<option value="${g.id}"${selected(g.id, guildId)}>${escape(g.name)}</option>`).join('')}</select></label><button data-action="reload">Refresh</button>${snapshot && refreshedAt ? `<span class="record-meta">Last refreshed ${escape(refreshedAt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }))}</span>` : ''}</div>
    ${!guilds.length ? '<p class="notice">No servers with Manage Server permission were found. An administrator can grant that permission or configure the bot for you.</p>' : ''}
    ${guildId && !snapshot ? '<section class="panel"><p>Loading settings and checking channel permissions…</p></section>' : ''}
    ${snapshot ? `<nav class="workspace-nav" role="tablist" aria-label="Organizer sections">${organizerViews.map((view) => `<button type="button" class="workspace-tab" id="tab-${view}" role="tab" data-view="${view}" aria-controls="view-${view}" aria-selected="false" tabindex="-1">${viewNames[view]}</button>`).join('')}</nav>${organizerViews.map((view) => `<section class="workspace-panel" role="tabpanel" id="view-${view}" data-view-panel="${view}" aria-labelledby="tab-${view}" hidden>${{ queues: () => queueSection(openQueues), matches: recordSection, players: playerSection, setup: setupSection }[view]()}</section>`).join('')}` : ''}`;
  if (snapshot) {
    selectView(currentView());
    applyFilters();
  }
}
app.addEventListener('submit', (event) => {
  event.preventDefault();
  const form = (event.target as Element).closest<HTMLFormElement>('form');
  if (!form || !snapshot) return;
  const data = new FormData(form),
    settings = structuredClone(snapshot.settings);
  void run(async () => {
    if (
      await onboardingSubmit(form, data, snapshot!, {
        api,
        url: communityUrl,
        save,
        reload: () => loadCommunity(guildId),
        notice,
      })
    )
      return;
    if (['repair', 'queue-control', 'manual', 'seed', 'noadd'].includes(form.dataset.kind ?? '')) {
      await submitRepair(form, data);
      return;
    }
    if (form.dataset.kind === 'game') {
      const catalogId = String(data.get('catalogId') ?? ''),
        item = bootstrap.catalog.find((g) => g.id === catalogId);
      const name = item?.name ?? String(data.get('name') ?? '').trim();
      if (!name) throw new Error('Enter a custom game name or choose a catalog game.');
      settings.games.push({
        id: crypto.randomUUID(),
        name,
        catalogId: (item?.id ?? null) as Settings['games'][number]['catalogId'],
      });
    } else if (form.dataset.kind === 'rename') {
      const game = settings.games.find((g) => g.id === form.dataset.id)!;
      game.name = String(data.get('name')).trim();
      if (gameCapabilities(game.catalogId)?.lobby)
        game.connector = {
          enabled: data.has('lobbyConnector'),
          ...(data.has('autoLobby')
            ? { autoAttach: { lobbyName: String(data.get('lobbyName')).trim() } }
            : {}),
        };
    } else if (form.dataset.kind === 'queue') {
      const existing = settings.queues.find((q) => q.id === form.dataset.id);
      const q: Queue = {
        id: existing?.id ?? crypto.randomUUID(),
        name: String(data.get('name')).trim(),
        gameId: String(data.get('gameId')),
        channelId: String(data.get('channelId')),
        size: Number(data.get('size')),
        enabled: data.has('enabled'),
        checkIn: {
          enabled: data.has('checkIn'),
          seconds: Number(data.get('seconds')),
          reuseSeconds: Number(data.get('reuseSeconds')),
        },
        teamMethod: String(data.get('teamMethod')) as Queue['teamMethod'],
        ratingEngine: String(data.get('ratingEngine')) as Queue['ratingEngine'],
        ratingSettings: {
          ...Object.fromEntries(
            [
              'initial',
              'deviation',
              'minDeviation',
              'scale',
              'winScale',
              'lossScale',
              'drawBonus',
            ].map((key) => [key, Number(data.get(`rating-${key}`))]),
          ),
          winStreakBoost: data.has('rating-winStreakBoost'),
          lossStreakBoost: data.has('rating-lossStreakBoost'),
        } as typeof defaultRatingSettings,
        autoStart: data.has('autoStart'),
        isDefault: data.has('isDefault'),
        aliases: String(data.get('aliases') ?? '')
          .split(',')
          .map((v) => v.trim())
          .filter(Boolean),
        controls: {
          waitingSeconds: Number(data.get('waitingSeconds')),
          matchSeconds: Number(data.get('matchSeconds')),
          requiredRole: String(data.get('requiredRole') ?? '').trim() || null,
          blockedRole: String(data.get('blockedRole') ?? '').trim() || null,
        },
        voicePreview: data.has('voicePreview'),
        keepQueueVisible: data.has('keepQueueVisible'),
        adminRoles: data.getAll('adminRoles').map(String),
        rally: {
          enabled: data.has('rallyEnabled'),
          channels: String(data.get('rallyChannels') ?? '')
            .split(',')
            .map((v) => v.trim())
            .filter(Boolean),
          roles: String(data.get('rallyRoles') ?? '')
            .split(',')
            .map((v) => v.trim())
            .filter(Boolean),
          cooldownSeconds: Number(data.get('rallyCooldown')),
          mentionLimit: Number(data.get('rallyMentions')),
        },
        economy: {
          taxEnabled: data.has('taxEnabled'),
          taxMinuteUtc: Number(data.get('taxMinuteUtc')),
        },
        ...(String(data.get('mapPool') ?? '').trim()
          ? {
              maps: {
                pool: String(data.get('mapPool'))
                  .split('\n')
                  .map((v) => v.trim())
                  .filter(Boolean),
                mode: String(data.get('mapMode')) as 'direct' | 'random' | 'vote',
                count: Number(data.get('mapCount')),
                shortlist: Number(data.get('mapShortlist')),
                voting: String(data.get('mapVoting')) as 'plurality' | 'approval',
                tie: String(data.get('mapTie')) as 'random' | 'alphabetical',
                noVotes: String(data.get('mapNoVotes')) as 'random' | 'cancel',
                seconds: Number(data.get('mapSeconds')),
                recentAvoid: Number(data.get('mapRecent')),
                countsAsCheckIn: data.has('mapCheckIn'),
              },
            }
          : {}),
        modules: Object.fromEntries(
          Object.keys(emptyModules).map((key) => [key, data.has(`module-${key}`)]),
        ) as Queue['modules'],
      };
      if (q.isDefault)
        settings.queues = settings.queues.map((v) =>
          v.channelId === q.channelId ? { ...v, isDefault: false } : v,
        );
      settings.queues = existing
        ? settings.queues.map((v) => (v.id === q.id ? q : v))
        : [...settings.queues, q];
    }
    await save(settings);
  });
});
app.addEventListener('click', (event) => {
  const tab = (event.target as Element).closest<HTMLButtonElement>('[data-view]');
  if (tab && !busy && isOrganizerView(tab.dataset.view ?? null)) {
    selectView(tab.dataset.view as OrganizerView);
    return;
  }
  const button = (event.target as Element).closest<HTMLElement>('[data-action]');
  if (!button) return;
  void run(async () => {
    switch (button.dataset.action) {
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
          save,
          reload: () => loadCommunity(guildId),
          notice,
        });
        break;
      case 'logout':
        await api('/api/logout', { method: 'POST' });
        location.reload();
        break;
      case 'reload':
        if (guildId) await loadCommunity(guildId);
        else location.reload();
        break;
      case 'remove':
        if (button.dataset.confirm !== 'yes') {
          button.dataset.confirm = 'yes';
          button.textContent = 'Confirm removal';
          button.insertAdjacentHTML(
            'afterend',
            '<button type="button" data-action="cancel-remove">Cancel removal</button>',
          );
          notice(
            'Remove this queue from setup? Its player records and match history will remain. Click Confirm removal to apply.',
          );
        } else if (snapshot)
          await save({
            ...snapshot.settings,
            queues: snapshot.settings.queues.filter((q) => q.id !== button.dataset.id),
          });
        break;
      case 'cancel-remove': {
        const remove = button.closest('form')?.querySelector<HTMLElement>('[data-action="remove"]');
        if (remove) {
          delete remove.dataset.confirm;
          remove.textContent = 'Remove queue';
        }
        button.remove();
        notice('Queue removal cancelled.');
        break;
      }
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
  const tab = (event.target as Element).closest<HTMLButtonElement>('[data-view]');
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
  if (input.id === 'community' && input.value) void run(() => loadCommunity(input.value));
  if (input.name === 'gameId' && form?.dataset.kind === 'queue') {
    const pool = form.querySelector<HTMLTextAreaElement>('[name="mapPool"]');
    const selected = snapshot?.settings.games.find((g) => g.id === input.value);
    if (pool && !pool.value.trim() && selected?.catalogId === 'aoe2')
      pool.value = aoe2Maps.join('\n');
  }
});
app.addEventListener('input', (event) => {
  const input = event.target as HTMLInputElement;
  if (input.dataset.filter === 'player-search') updateFilter(input);
});

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
