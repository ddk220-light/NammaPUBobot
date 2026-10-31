import type { Settings } from '../src/domain/config';
import { defaultRatingSettings } from '../src/domain/rating';
import { defaultRecords } from '../src/domain/records';
import { defaultMaps, aoe2Maps } from '../src/domain/maps';
import { gameCapabilities } from '../src/games/catalog';
import { operations } from '../src/games/types';
import { htmlEscape as esc } from '../src/public-site';

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
  const shown = (v: unknown) => (typeof v === 'string' && names[v] ? names[v] : display(v));
  saved = effective(saved);
  current = effective(current);
  const changes: { path: string; before: string; after: string }[] = [];
  const shared = new Set<string>();
  function walk(before: unknown, after: unknown, path: string) {
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
          );
    } else changes.push({ path, before: shown(before), after: shown(after) });
  }
  for (const kind of ['games', 'queues'] as const) {
    for (const id of new Set([...saved[kind], ...current[kind]].map((v) => v.id))) {
      const before = saved[kind].find((v) => v.id === id),
        after = current[kind].find((v) => v.id === id);
      const name = `${kind === 'games' ? 'Game' : 'Queue'} · ${after?.name || before?.name || 'Unnamed draft'}`;
      if (!before)
        changes.push({
          path: name,
          before: 'Not configured',
          after:
            kind === 'games'
              ? 'Add game; shared rating and leaderboard policy'
              : `Create queue · ${'channelId' in after! ? (names[after.channelId] ?? 'selected Discord channel') : ''} · ${'size' in after! ? after.size : ''} players · ${'teamMethod' in after! ? after.teamMethod : ''} teams`,
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
            walk(before.ratingEngine, after.ratingEngine, `${base} → Rating system`);
            walk(before.ratingSettings, after.ratingSettings, `${base} → Shared ratings`);
            walk(
              before.economy?.taxMinuteUtc,
              after.economy?.taxMinuteUtc,
              `${base} → Thursday tax time (UTC minutes)`,
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
  function walk(v: unknown, path: string, raw?: unknown) {
    if (v && typeof v === 'object')
      for (const [key, child] of Object.entries(v)) {
        if (key !== 'id')
          walk(
            child,
            `${path} → ${/^\d+$/.test(key) ? `Item ${Number(key) + 1}` : human(key)}`,
            raw && typeof raw === 'object' ? (raw as Record<string, unknown>)[key] : undefined,
          );
      }
    else
      rows.push(
        `<li>${esc(path)}: ${esc(display(v))}${raw === undefined ? ' (default)' : ''}</li>`,
      );
  }
  const normalized = effective(settings);
  for (const kind of ['games', 'queues'] as const)
    for (const item of normalized[kind])
      walk(
        item,
        item.name,
        settings[kind].find((v) => v.id === item.id),
      );
  return rows.join('');
}
export function changeReview(
  saved: Settings,
  current: Settings,
  names: Record<string, string> = {},
) {
  const changes = changesBetween(saved, current, names);
  return `<p>${changes.length} changes across this community. Save applies all settings together. Live Discord actions are separate.</p><div class="change-list">${changes.map((c) => `<article class="change-item"><h3>${esc(c.path)}</h3><p>${esc(c.before)} → ${esc(c.after)}</p></article>`).join('') || '<p>No effective settings changed.</p>'}</div><details><summary>Show every saved field</summary><p>Complete effective settings, including domain defaults for optional fields.</p><ul>${fullFields(current)}</ul></details>`;
}
