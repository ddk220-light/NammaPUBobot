import type { Settings } from '../src/domain/config';
import { defaultRatingSettings } from '../src/domain/rating';
import { defaultRecords } from '../src/domain/records';
import { defaultMaps, aoe2Maps } from '../src/domain/maps';
import { gameCapabilities } from '../src/games/catalog';
import { operations } from '../src/games/types';
import { esc } from './ui';

const labels: Record<string, string> = {
  checkIn: 'Check-in',
  seconds: 'Time limit (seconds)',
  controls: 'Access and timeouts',
  reuseSeconds: 'Ready validity (seconds)',
  waitingSeconds: 'Waiting expiry (seconds)',
  matchSeconds: 'Match timeout (seconds)',
  gameId: 'Game',
  channelId: 'Discord channel',
  size: 'Players',
  enabled: 'Enabled',
  name: 'Name',
  ratingSettings: 'Shared ratings',
  ratingEngine: 'Rating system',
  modules: 'Features',
  teamMethod: 'Team formation',
  voicePreview: 'Refresh voice availability',
  taxMinuteUtc: 'Thursday tax time (UTC minutes)',
  records: 'Leaderboard and ranks',
};
const human = (key: string) =>
  labels[key] ??
  key
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replaceAll('_', ' ')
    .replace(/^./, (c) => c.toUpperCase());
/** Compare effective defaults so opening a legacy form is not presented as extra edits. */
function effective(settings: Settings): Settings {
  return {
    games: settings.games.map((g) => {
      const catalog = gameCapabilities(g.catalogId);
      return {
        ...g,
        records: g.records ?? defaultRecords,
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
export function changesBetween(saved: Settings, current: Settings) {
  saved = effective(saved);
  current = effective(current);
  const changes: { path: string; before: string; after: string }[] = [];
  function walk(before: unknown, after: unknown, path: string) {
    if (Object.is(before, after)) return;
    if ((before && typeof before === 'object') || (after && typeof after === 'object')) {
      const a = (before ?? {}) as Record<string, unknown>,
        b = (after ?? {}) as Record<string, unknown>;
      for (const key of new Set([...Object.keys(a), ...Object.keys(b)]))
        if (key !== 'id') walk(a[key], b[key], `${path} → ${human(key)}`);
    } else changes.push({ path, before: display(before), after: display(after) });
  }
  for (const kind of ['games', 'queues'] as const) {
    for (const id of new Set([...saved[kind], ...current[kind]].map((v) => v.id))) {
      const before = saved[kind].find((v) => v.id === id),
        after = current[kind].find((v) => v.id === id);
      const name = `${kind === 'games' ? 'Game' : 'Queue'} · ${after?.name || before?.name || 'Unnamed draft'}`;
      if (!before) changes.push({ path: name, before: 'Not configured', after: 'Added to draft' });
      if (!after)
        changes.push({
          path: name,
          before: 'Configured',
          after: 'Remove on save (records retained)',
        });
      else walk(before, after, name);
    }
  }
  return changes;
}
export function changeReview(saved: Settings, current: Settings) {
  const changes = changesBetween(saved, current);
  return `<p>${changes.length} changed values across this community. Save sends all settings together, including defaults for optional fields. Discord actions and record updates are separate.</p><div class="change-list">${changes.map((c) => `<article class="change-item"><h3>${esc(c.path)}</h3><dl><div><dt>Saved</dt><dd>${esc(c.before)}</dd></div><div><dt>Draft</dt><dd>${esc(c.after)}</dd></div></dl></article>`).join('') || '<p>No effective values changed; saving will store the displayed defaults.</p>'}</div>`;
}
