import type { Page } from '@playwright/test';
import type { Snapshot } from '../src/domain/config';
import { emptyModules, settingsSchema } from '../src/domain/config';
import { defaultRatingSettings } from '../src/domain/rating';
import { defaultRecords } from '../src/domain/records';
import { defaultMaps } from '../src/domain/maps';
import { capabilities } from '../src/domain/setup';

export const guild = '706320010368516198',
  otherGuild = '706320010368516199';
export const game = '4daebc0a-55e8-4d56-a17d-d94913644910',
  queue = '81da571b-6a4a-4e5d-8e32-f4050df35a2d';
const channel = '706320010368516200';
export const user = '100000000000000002';
export function initialSnapshot(): Snapshot {
  return {
    communityId: guild,
    revision: 3,
    settings: {
      games: [
        {
          id: game,
          name: 'Age of Empires II',
          catalogId: 'aoe2',
          records: structuredClone(defaultRecords),
        },
      ],
      queues: [
        {
          id: queue,
          gameId: game,
          channelId: channel,
          name: 'Evening games',
          size: 8,
          enabled: true,
          checkIn: { enabled: true, seconds: 60, reuseSeconds: 900 },
          teamMethod: 'captain',
          ratingEngine: 'aoe2',
          ratingSettings: { ...defaultRatingSettings },
          modules: { ...emptyModules },
          maps: defaultMaps(),
          autoStart: true,
          isDefault: true,
          keepQueueVisible: true,
          economy: { taxEnabled: false, taxMinuteUtc: 600 },
        },
      ],
    },
  };
}
export async function mockApp(page: Page) {
  const state = {
    snapshot: initialSnapshot(),
    saved: [] as Snapshot['settings'][],
    saveRevisions: [] as number[],
    signedIn: true,
    access: 'ready',
    failSave: '',
    saveWait: undefined as Promise<void> | undefined,
    locked: false,
    storedEngine: 'aoe2' as Snapshot['settings']['queues'][number]['ratingEngine'],
    playerName: 'A player with a long name — Καλημέρα',
    rolesAvailable: true,
    importOk: true,
    importApplied: false,
    importCalls: 0,
    hiddenStats: false,
    hiddenPlayer: false,
    playerCount: 1,
    noCommunities: false,
    manageRoles: true,
    manageNicknames: false,
    actions: [] as Record<string, unknown>[],
    playerActions: [] as Record<string, unknown>[],
    matchState: 'playing',
  };
  const player = {
    id: user,
    get name() {
      return state.playerName;
    },
    rating: 1450,
    deviation: 120,
    wins: 10,
    losses: 7,
    draws: 1,
    streak: 2,
  };
  const recordData = (playersPage = 0) => ({
    gameState: [{ id: game, ratingEngineLocked: state.locked, ratingEngine: state.storedEngine }],
    matches: [
      {
        id: 'match-one',
        queue: state.snapshot.settings.queues[0] ?? initialSnapshot().settings.queues[0],
        gameId: game,
        gameName: 'Age of Empires II',
        state: state.matchState,
        ready: [user],
        deadline: Date.UTC(2026, 9, 28, 20),
        createdAt: Date.UTC(2026, 9, 28),
        version: 1,
        roster: [player],
        teams: [[player], []],
        captains: [user, null],
        changes: [],
        note: null,
      },
    ],
    players: Array.from({ length: state.playerCount }, (_, i) => ({
      ...player,
      id: String(BigInt(user) + BigInt(i)),
      name: i ? `Player ${i + 1}` : player.name,
      gameId: game,
      hidden: state.hiddenPlayer,
    })).slice(playersPage * 50, playersPage * 50 + 50),
    playersTotal: state.playerCount,
    playersPage,
    economy: {
      wallets: [{ game_id: game, user_id: user, balance: 510 }],
      ledger: [],
      books: [],
      taxRuns: [],
    },
    onboarding: {},
    pendingReturns: [],
    delivery: [],
    waiting: [],
    noadds: [],
    audit: [],
  });
  const setupData = (id: string) => ({
    snapshot: { ...state.snapshot, communityId: id },
    records: recordData(),
    channels:
      state.access === 'ready'
        ? [
            {
              id: channel,
              name: 'pickups',
              permissions: [
                'View Channel',
                'Send Messages',
                'Embed Links',
                'Read Message History',
              ].map((name) => ({ name, granted: true })),
            },
          ]
        : [],
    moderatorRoles: [{ id: '100000000000000003', name: 'Game hosts' }],
    roles: state.rolesAvailable
      ? [{ id: '100000000000000003', name: 'Game hosts', rankEligible: true, reason: null }]
      : [],
    voiceChannels: [{ id: '100000000000000004', name: 'Game night voice' }],
    gameState: [{ id: game, ratingEngineLocked: state.locked, ratingEngine: state.storedEngine }],
    access: {
      status: state.access,
      message:
        state.access === 'ready'
          ? 'Discord access verified.'
          : 'Invite the bot or restore access, then check again.',
      manageRoles: state.manageRoles,
      manageNicknames: state.manageNicknames,
    },
  });
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === '/api/bootstrap')
      return json({
        environment: 'staging',
        version: '0.16.0',
        oauthReady: true,
        discordReady: true,
        inviteUrl: 'https://discord.com/oauth2/authorize?client_id=123',
        inviteUrls: {
          basic: 'https://discord.com/oauth2/authorize?client_id=123&permissions=84992',
          ranks: 'https://discord.com/oauth2/authorize?client_id=123&permissions=268520448',
          nicknames: 'https://discord.com/oauth2/authorize?client_id=123&permissions=134302720',
        },
        catalog: [{ id: 'aoe2', name: 'Age of Empires II' }],
        user: state.signedIn ? { id: user, username: 'Organizer' } : null,
        csrf: 'test-csrf',
        capabilities,
      });
    if (path === '/api/communities')
      return json({
        communities: state.noCommunities
          ? []
          : [
              { id: guild, name: 'Friends’ game night — a long community name' },
              { id: otherGuild, name: 'Another community' },
            ],
      });
    if (path.endsWith('/setup')) return json(setupData(path.split('/')[3]));
    if (path.endsWith('/records'))
      return json(
        recordData(Number(new URL(route.request().url()).searchParams.get('playersPage') ?? 0)),
      );
    if (path.endsWith('/config') && route.request().method() === 'PUT') {
      const { settings, expectedRevision } = route.request().postDataJSON();
      state.saved.push(settings);
      state.saveRevisions.push(expectedRevision);
      if (state.saveWait) await state.saveWait;
      if (state.failSave)
        return json(
          {
            code: state.failSave,
            error: 'Settings changed in another tab. Your changes have not been saved.',
            requestId: 'test-conflict',
          },
          409,
        );
      if (expectedRevision !== state.snapshot.revision)
        return json({ code: 'revision_conflict', error: 'Settings changed in another tab.' }, 409);
      const valid = settingsSchema.safeParse(settings);
      if (!valid.success)
        return json(
          { code: 'invalid_settings', error: 'Invalid settings', issues: valid.error.issues },
          400,
        );
      const inconsistent = valid.data.queues.some((q) =>
        valid.data.queues.some(
          (peer) =>
            peer.gameId === q.gameId &&
            (peer.ratingEngine !== q.ratingEngine ||
              JSON.stringify(peer.ratingSettings) !== JSON.stringify(q.ratingSettings)),
        ),
      );
      if (inconsistent)
        return json(
          { code: 'settings_conflict', error: 'Queues for the same game share rating policy.' },
          409,
        );
      state.snapshot = { ...state.snapshot, revision: state.snapshot.revision + 1, settings };
      return json(state.snapshot);
    }
    if (path.endsWith('/import/preview')) {
      const bundle = route.request().postDataJSON();
      return json({
        id: 'digest',
        target: 'review-target',
        alreadyApplied: state.importApplied,
        ok: state.importOk,
        counts: {
          players: bundle.players.length,
          newPlayers: bundle.players.length,
          existingPlayers: 0,
          matches: bundle.matches.length,
          changes: bundle.changes.length,
        },
        errors: state.importOk ? [] : ['Destination cannot accept this import.'],
        warnings: [],
      });
    }
    if (path.endsWith('/import/apply')) {
      state.importCalls++;
      state.importApplied = true;
      const { bundle } = route.request().postDataJSON();
      return json({
        alreadyApplied: false,
        counts: {
          players: bundle.players.length,
          matches: bundle.matches.length,
          changes: bundle.changes.length,
        },
      });
    }
    if (path.endsWith('/player')) {
      const action = route.request().postDataJSON();
      state.playerActions.push(action);
      if (action.kind === 'hide') state.hiddenPlayer = true;
      if (action.kind === 'unhide') state.hiddenPlayer = false;
      return json({ content: 'Player updated.' });
    }
    if (path.endsWith('/actions')) {
      state.actions.push(route.request().postDataJSON());
      return json({ reply: { content: 'Action applied.' }, records: recordData() });
    }
    if (path.endsWith('/logout')) {
      state.signedIn = false;
      return json({ ok: true });
    }
    return json({ error: `Unexpected fixture request ${path}` }, 500);
  });
  await page.route('**/statistics/**', async (route) => {
    const url = new URL(route.request().url());
    if (state.hiddenStats)
      return route.fulfill({ status: 403, json: { error: 'Sign in to view these statistics.' } });
    return route.fulfill({
      json: {
        gameId: game,
        gameName: 'Age of Empires II',
        visibility: 'public',
        policy: { minimumMatches: 1, recentDays: 30 },
        page: Number(url.searchParams.get('page') ?? 0),
        pages: 2,
        total: 12,
        leaderboard: [{ ...player, lastRankedAt: Date.UTC(2026, 9, 28), rank: 'A' }],
        player: url.searchParams.get('user')
          ? {
              ...player,
              hidden: false,
              lastRankedAt: Date.UTC(2026, 9, 28),
              source: 'local',
              rank: 'A',
              peak: 1500,
              matchCount: 18,
              winRate: 10 / 18,
              eligible: true,
              history: [
                {
                  before: 1440,
                  after: 1450,
                  changed_at: Date.UTC(2026, 9, 28),
                  reason: 'Match result',
                  source: 'local',
                },
                {
                  before: 1450,
                  after: 1440,
                  changed_at: Date.UTC(2026, 9, 27),
                  reason: 'Match result',
                  source: 'local',
                },
              ],
              profiles: [],
              civilizations: [],
              relationships: [],
              recent: [
                {
                  id: 'one',
                  at: Date.UTC(2026, 9, 28),
                  state: 'finished',
                  result: 'alpha',
                  queue: 'Evening games',
                  teams: [[{ id: user, name: player.name }], []],
                  note: null,
                },
              ],
            }
          : null,
      },
    });
  });
  return state;
}
