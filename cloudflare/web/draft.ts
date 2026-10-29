import {
  type Settings,
  type Snapshot,
  type Queue,
  emptyModules,
  settingsSchema,
} from '../src/domain/config';
import { defaultRatingSettings } from '../src/domain/rating';
import { defaultMaps, aoe2Maps } from '../src/domain/maps';
import { defaultRecords } from '../src/domain/records';
import type { GameState } from '../src/domain/setup';

export type Fields = Record<string, string[]>;
export const value = (f: Fields, name: string) => f[name]?.[0] ?? '';
const number = (f: Fields, name: string) =>
  value(f, name).trim() === '' ? NaN : Number(value(f, name));
const bool = (f: Fields, name: string) => Boolean(f[name]?.length);
const list = (f: Fields, name: string) =>
  value(f, name)
    .split(/[\n,]/)
    .map((v) => v.trim())
    .filter(Boolean);

export function fields(form: HTMLFormElement): Fields {
  const data: Fields = {};
  for (const el of [...form.elements]) {
    if (
      !(
        el instanceof HTMLInputElement ||
        el instanceof HTMLSelectElement ||
        el instanceof HTMLTextAreaElement
      ) ||
      !el.name
    )
      continue;
    data[el.name] ??= [];
    if (el instanceof HTMLInputElement && ['checkbox', 'radio'].includes(el.type) && !el.checked)
      continue;
    if (el instanceof HTMLSelectElement && el.multiple)
      data[el.name].push(...[...el.selectedOptions].map((o) => o.value));
    else data[el.name].push(el.value);
  }
  return data;
}

export class SettingsDraft {
  working: Settings;
  raw = new Map<string, Fields>();
  private initial = new Map<string, Fields>();
  private policies = new Map<
    string,
    Pick<Queue, 'ratingEngine' | 'ratingSettings'> & { taxMinuteUtc: number }
  >();
  gameState: GameState[];
  constructor(
    public saved: Snapshot,
    gameState: GameState[] = [],
  ) {
    this.working = structuredClone(saved.settings);
    this.gameState = gameState;
    for (const q of this.working.queues) this.ensurePolicy(q.gameId, q);
  }
  private ensurePolicy(gameId: string, queue?: Queue) {
    if (!this.policies.has(gameId)) {
      const state = this.gameState.find((g) => g.id === gameId);
      this.policies.set(gameId, {
        ratingEngine:
          state?.ratingEngineLocked && state.ratingEngine
            ? state.ratingEngine
            : (queue?.ratingEngine ?? 'aoe2'),
        ratingSettings: structuredClone(queue?.ratingSettings ?? defaultRatingSettings),
        taxMinuteUtc: queue?.economy?.taxMinuteUtc ?? 0,
      });
    }
    return this.policies.get(gameId)!;
  }
  restoreEngine(gameId: string) {
    const state = this.gameState.find((g) => g.id === gameId);
    if (!state?.ratingEngineLocked || !state.ratingEngine) return;
    this.ensurePolicy(gameId).ratingEngine = state.ratingEngine;
    for (const q of this.working.queues.filter((q) => q.gameId === gameId))
      q.ratingEngine = state.ratingEngine;
    const key = `rating:${gameId}`;
    const raw = this.raw.get(key);
    if (raw) {
      raw.ratingEngine = [state.ratingEngine];
      if (JSON.stringify(raw) === JSON.stringify(this.initial.get(key))) this.raw.delete(key);
    }
  }
  get dirty() {
    return (
      this.raw.size > 0 || JSON.stringify(this.working) !== JSON.stringify(this.saved.settings)
    );
  }
  mount(root: ParentNode) {
    root.querySelectorAll<HTMLFormElement>('form[data-config]').forEach((form) => {
      const key = form.dataset.config!;
      if (!this.initial.has(key)) this.initial.set(key, fields(form));
      const values = this.raw.get(key);
      if (!values) return;
      for (const el of [...form.elements]) {
        if (
          !(
            el instanceof HTMLInputElement ||
            el instanceof HTMLSelectElement ||
            el instanceof HTMLTextAreaElement
          ) ||
          !(el.name in values)
        )
          continue;
        if (el instanceof HTMLInputElement && ['checkbox', 'radio'].includes(el.type)) {
          el.checked = values[el.name].includes(el.value);
          const text = el.closest('.switch-control')?.querySelector('.switch-text');
          if (text) text.textContent = el.checked ? 'On' : 'Off';
        } else {
          if (el instanceof HTMLSelectElement) {
            for (const value of values[el.name])
              if (value && ![...el.options].some((option) => option.value === value))
                el.add(new Option(`Unavailable selection · ${value}`, value));
          }
          if (el instanceof HTMLSelectElement && el.multiple)
            [...el.options].forEach((o) => (o.selected = values[el.name].includes(o.value)));
          else el.value = values[el.name][0] ?? '';
        }
      }
    });
  }
  capture(form: HTMLFormElement) {
    const key = form.dataset.config!;
    const data = fields(form);
    if (JSON.stringify(data) === JSON.stringify(this.initial.get(key))) this.raw.delete(key);
    else this.raw.set(key, data);
  }
  /** Update a compact editor without dropping fields from the full settings form. */
  patchFields(key: string, changes: Fields) {
    const baseline = this.raw.get(key) ?? this.initial.get(key);
    if (!baseline) throw new Error('Open this settings editor before changing its fields.');
    const data = { ...baseline, ...changes };
    if (JSON.stringify(data) === JSON.stringify(this.initial.get(key))) this.raw.delete(key);
    else this.raw.set(key, data);
  }
  changedFields(key: string) {
    const raw = this.raw.get(key),
      initial = this.initial.get(key);
    return new Set(
      raw
        ? Object.keys(raw).filter(
            (name) => JSON.stringify(raw[name]) !== JSON.stringify(initial?.[name]),
          )
        : [],
    );
  }
  materialize(): Settings {
    const settings = structuredClone(this.working);
    for (const q of settings.queues) {
      const f = this.raw.get(`queue:${q.id}`);
      if (!f) continue;
      const peer = settings.queues.find((v) => v.id !== q.id && v.gameId === value(f, 'gameId'));
      if (q.gameId !== value(f, 'gameId') && peer) {
        q.ratingEngine = peer.ratingEngine;
        q.ratingSettings = structuredClone(peer.ratingSettings ?? defaultRatingSettings);
        q.economy = {
          taxEnabled: bool(f, 'taxEnabled'),
          taxMinuteUtc: peer.economy?.taxMinuteUtc ?? 0,
        };
      }
      Object.assign(q, {
        name: value(f, 'name').trim(),
        gameId: value(f, 'gameId'),
        channelId: value(f, 'channelId'),
        size: number(f, 'size'),
        enabled: bool(f, 'enabled'),
        teamMethod: value(f, 'teamMethod'),
        checkIn: {
          enabled: bool(f, 'checkIn'),
          seconds: number(f, 'seconds'),
          reuseSeconds: number(f, 'reuseSeconds'),
        },
        autoStart: bool(f, 'autoStart'),
        isDefault: bool(f, 'isDefault'),
        aliases: list(f, 'aliases'),
        keepQueueVisible: bool(f, 'keepQueueVisible'),
        adminRoles: f.adminRoles ?? [],
        controls: {
          waitingSeconds: number(f, 'waitingSeconds'),
          matchSeconds: number(f, 'matchSeconds'),
          requiredRole: value(f, 'requiredRole') || null,
          blockedRole: value(f, 'blockedRole') || null,
        },
        voicePreview: bool(f, 'voicePreview'),
        rally: {
          enabled: bool(f, 'rallyEnabled'),
          channels: f.rallyChannels ?? [],
          roles: f.rallyRoles ?? [],
          cooldownSeconds: number(f, 'rallyCooldown'),
          mentionLimit: number(f, 'rallyMentions'),
        },
        economy: {
          taxEnabled: bool(f, 'taxEnabled'),
          taxMinuteUtc: q.economy?.taxMinuteUtc ?? peer?.economy?.taxMinuteUtc ?? 0,
        },
        modules: Object.fromEntries(
          Object.keys(emptyModules).map((k) => [k, bool(f, `module-${k}`)]),
        ),
      });
      const pool = value(f, 'mapPool')
        .split('\n')
        .map((v) => v.trim())
        .filter(Boolean);
      if (pool.length)
        q.maps = {
          pool,
          mode: value(f, 'mapMode') as 'vote',
          count: number(f, 'mapCount'),
          shortlist: number(f, 'mapShortlist'),
          voting: value(f, 'mapVoting') as 'plurality',
          tie: value(f, 'mapTie') as 'random',
          noVotes: value(f, 'mapNoVotes') as 'random',
          seconds: number(f, 'mapSeconds'),
          recentAvoid: number(f, 'mapRecent'),
          countsAsCheckIn: bool(f, 'mapCheckIn'),
        };
      else delete q.maps;
    }
    for (const g of settings.games) {
      const f = this.raw.get(`game:${g.id}`);
      if (!f) continue;
      g.name = value(f, 'name').trim();
      if ('lobbyConnector' in f)
        g.connector = {
          enabled: bool(f, 'lobbyConnector'),
          ...(bool(f, 'autoLobby')
            ? { autoAttach: { lobbyName: value(f, 'lobbyName').trim() } }
            : {}),
        };
      const ranks = Object.keys(f)
        .filter((key) => /^rank-\d+-threshold$/.test(key))
        .map((key) => {
          const prefix = key.replace('threshold', '');
          return {
            threshold: number(f, key),
            label: value(f, `${prefix}label`),
            roleId: value(f, `${prefix}role`) || null,
          };
        });
      g.records = {
        ...defaultRecords,
        visibility: value(f, 'visibility') as 'public',
        minimumMatches: number(f, 'minimumMatches'),
        recentDays: number(f, 'recentDays'),
        decorateNickname: bool(f, 'decorateNickname'),
        ranks,
        inactivity: {
          enabled: bool(f, 'decay'),
          weeklyRating: number(f, 'weeklyRating'),
          weeklyDeviation: number(f, 'weeklyDeviation'),
        },
      };
    }
    // Rating policy and tax time have one editor per game, despite queue-based storage.
    for (const g of settings.games) {
      const queues = settings.queues.filter((q) => q.gameId === g.id);
      if (!queues.length) continue;
      const policy = this.ensurePolicy(g.id, queues[0]);
      const f = this.raw.get(`rating:${g.id}`);
      for (const q of queues) {
        q.ratingEngine = policy.ratingEngine;
        q.ratingSettings = structuredClone(policy.ratingSettings);
        q.economy = {
          taxEnabled: q.economy?.taxEnabled ?? false,
          taxMinuteUtc: policy.taxMinuteUtc,
        };
        if (!f) continue;
        q.ratingEngine = value(f, 'ratingEngine') as Queue['ratingEngine'];
        q.ratingSettings = {
          ...defaultRatingSettings,
          ...Object.fromEntries(
            [
              'initial',
              'deviation',
              'minDeviation',
              'scale',
              'winScale',
              'lossScale',
              'drawBonus',
            ].map((k) => [k, number(f, k)]),
          ),
          winStreakBoost: bool(f, 'winStreakBoost'),
          lossStreakBoost: bool(f, 'lossStreakBoost'),
        };
        q.economy = {
          taxEnabled: q.economy?.taxEnabled ?? false,
          taxMinuteUtc: number(f, 'taxMinuteUtc'),
        };
      }
    }
    return settings;
  }
  validate() {
    return settingsSchema
      .superRefine((settings, ctx) => {
        settings.queues.forEach((q, index) => {
          const state = this.gameState.find((g) => g.id === q.gameId);
          if (
            state?.ratingEngineLocked &&
            state.ratingEngine &&
            state.ratingEngine !== q.ratingEngine
          )
            ctx.addIssue({
              code: 'custom',
              path: ['queues', index, 'ratingEngine'],
              message:
                'This game now has player ratings. Use its saved rating system to keep your other edits.',
            });
        });
      })
      .safeParse(this.materialize());
  }
  addQueue(gameId: string, channelId: string) {
    const current = this.materialize();
    const game = current.games.find((g) => g.id === gameId)!;
    const peer = this.ensurePolicy(
      gameId,
      current.queues.find((q) => q.gameId === gameId),
    );
    const q: Queue = {
      id: crypto.randomUUID(),
      gameId,
      channelId,
      name: `Pickup ${current.queues.length + 1}`,
      size: 8,
      enabled: false,
      checkIn: { enabled: true, seconds: 60, reuseSeconds: 900 },
      teamMethod: 'captain',
      ratingEngine: peer?.ratingEngine ?? 'aoe2',
      ratingSettings: structuredClone(peer?.ratingSettings ?? defaultRatingSettings),
      economy: { taxEnabled: false, taxMinuteUtc: peer.taxMinuteUtc },
      modules: { ...emptyModules },
      ...(game.catalogId === 'aoe2' ? { maps: defaultMaps(aoe2Maps) } : {}),
    };
    this.working.queues.push(q);
    return q.id;
  }
  removeQueue(id: string) {
    this.working.queues = this.working.queues.filter((q) => q.id !== id);
    this.raw.delete(`queue:${id}`);
  }
}
