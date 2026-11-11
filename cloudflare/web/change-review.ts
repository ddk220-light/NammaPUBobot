import type { Settings } from '../src/domain/config';
import { defaultRatingSettings } from '../src/domain/rating';
import { defaultRecords } from '../src/domain/records';
import { defaultMaps, aoe2Maps } from '../src/domain/maps';
import { gameCapabilities } from '../src/games/catalog';
import { operations } from '../src/games/types';
import { htmlEscape as esc } from '../src/public-site';
import { connectionLabels } from './connectors';
import { durationText, valueLabels } from './labels';

const labels: Record<string, string> = {
  checkIn: 'Check-in',
  seconds: 'Time limit',
  controls: 'Access and timeouts',
  reuseSeconds: 'Ready lasts for',
  waitingSeconds: 'Remove waiting players after',
  matchSeconds: 'End unreported matches after',
  gameId: 'Game',
  channelId: 'Discord channel',
  size: 'Players per match',
  enabled: 'Enabled',
  name: 'Name',
  ratingSettings: 'Shared ratings',
  ratingEngine: 'Rating system',
  modules: 'Features',
  teamMethod: 'How teams are made',
  aliases: 'Queue nicknames',
  voicePreview: 'Show who is in voice',
  taxMinuteUtc: 'Weekly gold tax time (UTC)',
  records: 'Leaderboard and ranks',
  autoStart: 'Start automatically',
  isDefault: 'Default queue',
  keepQueueVisible: 'Keep queue card visible',
  adminRoles: 'Bot moderator roles',
  rally: 'Rally',
  cooldownSeconds: 'Time between rallies',
  mentionLimit: 'People mentioned per rally',
  economy: 'Gold',
  taxEnabled: 'Weekly gold tax',
  civs: 'Civilization picks',
  stories: 'Match stories',
  pool: 'Map pool',
  mode: 'How the map is chosen',
  count: 'Maps per match',
  shortlist: 'Maps on the ballot',
  voting: 'Votes per player',
  tie: 'When maps tie',
  noVotes: 'When nobody votes',
  recentAvoid: 'Skip maps from recent matches',
  countsAsCheckIn: 'Voting also checks players in',
  visibility: 'Statistics visibility',
  searchPublishing: 'Publish to search engines',
  minimumMatches: 'Matches before a player is listed',
  recentDays: 'Hide players inactive for (days)',
  decorateNickname: 'Show rating in nicknames',
  inactivity: 'Inactive players',
  weeklyRating: 'Weekly rating drop',
  weeklyDeviation: 'Weekly uncertainty increase',
  ranks: 'Ranks',
  threshold: 'From rating',
  label: 'Rank name',
  roleId: 'Discord role',
  connector: 'Lobby tracking',
  capabilities: 'Connections',
  autoAttach: 'Find a public lobby by name',
  lobbyName: 'Lobby name',
  initial: 'Starting rating',
  deviation: 'Starting uncertainty',
  minDeviation: 'Lowest uncertainty',
  scale: 'Rating change size (%)',
  winScale: 'Win size (%)',
  lossScale: 'Loss size (%)',
  drawBonus: 'Draw bonus (%)',
  winStreakBoost: 'Bigger gains on a win streak',
  lossStreakBoost: 'Bigger losses on a losing streak',
  publicPage: 'Public community page',
  displayName: 'Public community name',
  ...connectionLabels,
};
/** Stored values shown in organizer words: durations, clock times and named choices. */
const durationKeys = new Set([
  'seconds',
  'reuseSeconds',
  'waitingSeconds',
  'matchSeconds',
  'cooldownSeconds',
]);
const choiceKeys: Record<string, keyof typeof valueLabels> = {
  teamMethod: 'teamMethod',
  mode: 'mapMode',
  voting: 'mapVoting',
  tie: 'mapTie',
  noVotes: 'mapNoVotes',
  visibility: 'visibility',
  ratingEngine: 'ratingEngine',
};
function readable(key: string, value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    if (durationKeys.has(key)) return durationText(value);
    if (key === 'taxMinuteUtc')
      return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')} UTC`;
  }
  if (typeof value === 'string' && choiceKeys[key])
    return (valueLabels[choiceKeys[key]] as Record<string, string>)[value] ?? value;
  return value;
}
const human = (key: string) =>
  labels[key] ??
  key
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replaceAll('_', ' ')
    .replace(/^./, (c) => c.toUpperCase());
/** Compare effective defaults so opening an older form is not presented as extra edits. */
function effective(settings: Settings): Settings {
  return {
    ...(settings.publicPage ? { publicPage: settings.publicPage } : {}),
    games: settings.games.map((g) => {
      const catalog = gameCapabilities(g.catalogId);
      return {
        ...g,
        records: { ...defaultRecords, ...g.records },
        connector: catalog
          ? {
              enabled: false,
              ...g.connector,
              capabilities: {
                ...Object.fromEntries(
                  operations
                    .filter((op) => catalog.manifest.capabilities[op] !== 'unsupported')
                    .map((op) => [op, true]),
                ),
                ...g.connector?.capabilities,
              },
            }
          : (g.connector ?? { enabled: false }),
      };
    }),
    queues: settings.queues.map((q) => ({
      ...q,
      ratingSettings: q.ratingSettings ?? defaultRatingSettings,
      checkIn: { reuseSeconds: 900, ...q.checkIn },
      autoStart: q.autoStart ?? true,
      isDefault: q.isDefault ?? false,
      aliases: q.aliases ?? [],
      adminRoles: q.adminRoles ?? [],
      keepQueueVisible: q.keepQueueVisible ?? true,
      voicePreview: q.voicePreview ?? false,
      economy: q.economy ?? { taxEnabled: false, taxMinuteUtc: 0 },
      controls: q.controls ?? {
        waitingSeconds: 0,
        matchSeconds: 43200,
        requiredRole: null,
        blockedRole: null,
      },
      rally: q.rally ?? {
        enabled: false,
        channels: [],
        roles: [],
        cooldownSeconds: 300,
        mentionLimit: 8,
      },
      maps:
        q.maps ??
        defaultMaps(
          settings.games.find((g) => g.id === q.gameId)?.catalogId === 'aoe2' ? aoe2Maps : [],
        ),
    })),
  };
}
const display = (value: unknown): string =>
  value === undefined
    ? 'Not set'
    : value === null
      ? 'None'
      : typeof value === 'boolean'
        ? value
          ? 'On'
          : 'Off'
        : typeof value === 'number' && !Number.isFinite(value)
          ? 'Invalid or empty number'
          : String(value);
export function changesBetween(
  saved: Settings,
  current: Settings,
  names: Record<string, string> = {},
) {
  for (const g of [...saved.games, ...current.games]) names[g.id] = g.name;
  const shown = (v: unknown, key = '') =>
    typeof v === 'string' && names[v] ? names[v] : display(readable(key, v));
  saved = effective(saved);
  current = effective(current);
  const changes: { path: string; before: string; after: string }[] = [];
  const shared = new Set<string>();
  function walk(before: unknown, after: unknown, path: string, key = '') {
    if (Object.is(before, after)) return;
    if ((before && typeof before === 'object') || (after && typeof after === 'object')) {
      const a = (before ?? {}) as Record<string, unknown>,
        b = (after ?? {}) as Record<string, unknown>;
      for (const key of new Set([...Object.keys(a), ...Object.keys(b)]))
        if (key !== 'id')
          walk(
            a[key],
            b[key],
            `${path} → ${/^\d+$/.test(key) ? `Item ${Number(key) + 1}` : human(key)}`,
            key,
          );
    } else changes.push({ path, before: shown(before, key), after: shown(after, key) });
  }
  for (const kind of ['games', 'queues'] as const) {
    for (const id of new Set([...saved[kind], ...current[kind]].map((v) => v.id))) {
      const before = saved[kind].find((v) => v.id === id),
        after = current[kind].find((v) => v.id === id);
      const name = `${kind === 'games' ? 'Game' : 'Queue'} · ${after?.name || before?.name || 'Unnamed'}`;
      if (!before)
        changes.push({
          path: name,
          before: 'Not configured',
          after:
            kind === 'games'
              ? 'Add game; shared rating and leaderboard policy'
              : `Create queue · ${'channelId' in after! ? (names[after.channelId] ?? 'selected Discord channel') : ''} · ${'size' in after! ? after.size : ''} players · ${'teamMethod' in after! ? (valueLabels.teamMethod as Record<string, string>)[after.teamMethod] : ''}`,
        });
      if (!after)
        changes.push({
          path: name,
          before: 'Configured',
          after: 'Remove on save (records retained)',
        });
      else if (before) {
        if (kind === 'queues' && 'gameId' in before && 'gameId' in after) {
          if (!shared.has(after.gameId)) {
            shared.add(after.gameId);
            const peers = current.queues.filter((q) => q.gameId === after.gameId);
            const base = `Game · ${names[after.gameId] ?? after.gameId} · Shared policy (${peers.length} queues)`;
            walk(
              before.ratingEngine,
              after.ratingEngine,
              `${base} → Rating system`,
              'ratingEngine',
            );
            walk(before.ratingSettings, after.ratingSettings, `${base} → Shared ratings`);
            walk(
              before.economy?.taxMinuteUtc,
              after.economy?.taxMinuteUtc,
              `${base} → Weekly gold tax time (UTC)`,
              'taxMinuteUtc',
            );
          }
          const a = structuredClone(before),
            b = structuredClone(after);
          delete a.ratingSettings;
          delete b.ratingSettings;
          // Already summarized once per game above.
          a.ratingEngine = b.ratingEngine;
          if (a.economy && b.economy) a.economy.taxMinuteUtc = b.economy.taxMinuteUtc;
          walk(a, b, name);
        } else walk(before, after, name);
      }
    }
  }
  walk(saved.publicPage, current.publicPage, 'Public community page');
  return changes;
}
function fullFields(settings: Settings) {
  const rows: string[] = [];
  function walk(v: unknown, path: string, raw?: unknown, name = '') {
    if (v && typeof v === 'object')
      for (const [key, child] of Object.entries(v)) {
        if (key !== 'id')
          walk(
            child,
            `${path} → ${/^\d+$/.test(key) ? `Item ${Number(key) + 1}` : human(key)}`,
            raw && typeof raw === 'object' ? (raw as Record<string, unknown>)[key] : undefined,
            key,
          );
      }
    else
      rows.push(
        `<li>${esc(path)}: ${esc(display(readable(name, v)))}${raw === undefined ? ' (default)' : ''}</li>`,
      );
  }
  const normalized = effective(settings);
  for (const kind of ['games', 'queues'] as const)
    for (const item of normalized[kind]) {
      const shown = structuredClone(item) as Record<string, unknown>;
      // Features held back from the first release appear only where a community uses them.
      if (kind === 'queues' && 'modules' in item) {
        const modules = shown.modules as Record<string, boolean>;
        if (!item.voicePreview && !item.rally?.enabled) {
          delete shown.voicePreview;
          delete shown.rally;
        }
        if (!item.economy?.taxEnabled) delete shown.economy;
        if (!item.modules.quiz) delete modules.quiz;
      }
      walk(
        shown,
        item.name,
        settings[kind].find((v) => v.id === item.id),
      );
    }
  if (settings.publicPage) walk(settings.publicPage, 'Public community page', settings.publicPage);
  return rows.join('');
}
export function changeReview(
  saved: Settings,
  current: Settings,
  names: Record<string, string> = {},
) {
  const changes = changesBetween(saved, current, names);
  return `<p class="review-intro">${changes.length === 1 ? '1 change' : `${changes.length} changes`} across this community. Saving applies them all together.</p><div class="change-list">${
    changes
      .map((c) => {
        const action =
          c.before === 'Not configured'
            ? c.path.startsWith('Game')
              ? 'Adds'
              : 'Creates'
            : c.after.startsWith('Remove on save')
              ? 'Removes'
              : 'Changes';
        const [entity, ...field] = c.path.split(' → ');
        return `<article class="change-item" data-change="${action.toLowerCase()}"><span class="change-kind">${action}</span><div><h3>${esc(entity)}</h3>${field.length ? `<p class="change-field">${esc(field.join(' → '))}</p>` : ''}<p class="change-values">${action === 'Changes' ? `<del>${esc(c.before)}</del><span aria-label="changes to"> → </span><strong>${esc(c.after)}</strong>` : esc(c.after)}</p></div></article>`;
      })
      .join('') || '<p>Nothing has changed.</p>'
  }</div><p class="form-help">Matches already running keep their saved rules.</p><details class="review-fields"><summary>Show every setting</summary><p>Every setting after saving, including defaults you have not changed.</p><ul>${fullFields(current)}</ul></details>`;
}
