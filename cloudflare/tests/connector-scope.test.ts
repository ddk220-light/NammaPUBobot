import { describe, expect, it, vi } from 'vitest';
import { env } from 'cloudflare:workers';
import { selectableCatalog, gameCapabilities } from '../src/games/catalog';
import { gameAdapter } from '../src/games/registry';
import { connectorAvailability, runtimeAdapter } from '../src/games/runtime';
import { publicGameList } from '../src/games/discovery';
import { normalizeObservation } from '../src/domain/lobby';
import type { LobbyObservation } from '../src/games/types';
import wc3 from './fixtures/connectors/wc3/v1/contracts.json';
import { gameListReply } from '../src/discord/game-list';

const observation = (): LobbyObservation => ({
  schemaVersion: 2,
  referenceKind: 'match',
  externalId: 'original-1',
  nativeMatchId: 'original-1',
  providerIds: { recordId: 'a'.repeat(24), floGameId: '9007199254740993' },
  name: 'Match',
  phase: 'started',
  observedAt: Date.now(),
  startedAt: Date.now() - 60000,
  finishedAt: null,
  players: [{ profileId: 'One#1234', name: 'One', team: '1', selection: 'Unknown race 999' }],
  capacity: 2,
  map: null,
  source: 'fixture',
  mode: '1',
  replayReferences: [
    {
      provider: 'fixture',
      matchId: 'original-1',
      availability: 'unknown',
      sourceUrl: 'https://example.com/private-reference',
    },
  ],
});

describe('final connector scope and release boundaries', () => {
  it('offers exactly the seven selected games, retaining old identifiers for history', () => {
    expect(selectableCatalog.map((g) => g.id).sort()).toEqual([
      'aoe2',
      'aoe3',
      'aoe4',
      'aom',
      'bar',
      'wc3',
      'zero_hour',
    ]);
    for (const id of ['generals', 'sc2', 'northgard', 'tempest_rising']) {
      expect(gameCapabilities(id)?.lifecycle).toBe('legacy');
      expect(gameAdapter(id)).toBeDefined();
      expect(
        connectorAvailability(
          { ...env, CONNECTOR_ACCEPTED_GAMES: id },
          gameAdapter(id)!,
          'lobbyObservation',
        ),
      ).toBe('unsupported');
    }
  });
  it('requires capability/mode acceptance even if the game itself was accepted', () => {
    const e = {
      ...env,
      CONNECTOR_ACCEPTED_GAMES: 'wc3,aoe4',
      CONNECTOR_ACCEPTED_CAPABILITIES: 'wc3.lobbyObservation:1',
    };
    expect(connectorAvailability(e, gameAdapter('wc3')!, 'lobbyDiscovery')).toBe('unsupported');
    expect(connectorAvailability(e, gameAdapter('wc3')!, 'lobbyObservation', '1')).toBe(
      'available',
    );
    expect(connectorAvailability(e, gameAdapter('wc3')!, 'lobbyObservation', '4')).toBe(
      'acceptance_required',
    );
    expect(connectorAvailability(e, gameAdapter('aoe4')!, 'ongoingDiscovery')).toBe(
      'acceptance_required',
    );
    expect(connectorAvailability(e, gameAdapter('wc3')!, 'ownershipVerification')).toBe(
      'credentials_required',
    );
  });
  it('does not manufacture a lobby ID for a match observation', () => {
    const o = normalizeObservation(observation());
    expect(o.referenceKind).toBe('match');
    expect(o.nativeLobbyId).toBeUndefined();
    expect(o.providerIds?.floGameId).toBe('9007199254740993');
    const old = observation();
    delete old.schemaVersion;
    delete old.referenceKind;
    expect(normalizeObservation(old).nativeLobbyId).toBe(old.externalId);
  });
  it('blocks the new feed before provider requests when disabled or unaccepted', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(Error('No request expected'));
    try {
      expect(
        await runtimeAdapter(
          { ...env, PROVIDERS: undefined, CONNECTOR_ACCEPTED_GAMES: 'wc3' },
          'wc3',
        )!.discoverOngoing!(),
      ).toEqual({ status: 'unsupported' });
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
});

describe('public game listings', () => {
  it('allows public reads before automatic acceptance and retains observation and disable gates', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(wc3.ongoing));
    try {
      const e = { ...env, PROVIDERS: undefined, CONNECTOR_ACCEPTED_GAMES: '' };
      const adapter = runtimeAdapter(e, 'wc3', {}, { publicListing: true })!;
      expect(await publicGameList(adapter, 'running')).toMatchObject({
        status: 'ok',
        value: { games: expect.any(Array) },
      });
      expect(await adapter.observe('original-1', true)).toEqual({ status: 'unsupported' });
      fetch.mockClear();
      expect(
        await publicGameList(
          runtimeAdapter(e, 'wc3', { ongoingDiscovery: false }, { publicListing: true })!,
          'running',
        ),
      ).toEqual({ status: 'unsupported' });
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it('keeps WC3 observation mode gates even on a listing-capable adapter', async () => {
    const match = wc3.teamFinished.matches[0];
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ match }));
    try {
      const adapter = runtimeAdapter(
        {
          ...env,
          PROVIDERS: undefined,
          CONNECTOR_ACCEPTED_GAMES: 'wc3',
          CONNECTOR_ACCEPTED_CAPABILITIES: 'wc3.lobbyObservation:1',
        },
        'wc3',
        {},
        { publicListing: true },
      )!;
      expect(await adapter.observe(match['original-ongoing-match-id'], true)).toEqual({
        status: 'unsupported',
      });
    } finally {
      fetch.mockRestore();
    }
  });
  it('isolates the AoE4 World provider switch', () => {
    const e = {
      ...env,
      CONNECTOR_ACCEPTED_GAMES: 'aoe4',
      CONNECTOR_ACCEPTED_CAPABILITIES: 'aoe4.ongoingDiscovery',
      CONNECTOR_DISABLED: 'worlds-edge',
    };
    expect(connectorAvailability(e, gameAdapter('aoe4')!, 'ongoingDiscovery')).toBe('available');
    expect(
      connectorAvailability(
        { ...e, CONNECTOR_DISABLED: 'aoe4world' },
        gameAdapter('aoe4')!,
        'ongoingDiscovery',
      ),
    ).toBe('disabled');
  });
  it('renders ten cards within the combined Discord embed budget', async () => {
    const adapter = {
      ...gameAdapter('wc3')!,
      discoverOngoing: async () => ({
        status: 'ok' as const,
        value: Array.from({ length: 10 }, () => observation()),
      }),
    };
    const reply = gameListReply(await publicGameList(adapter, 'running'));
    expect(reply.embeds).toHaveLength(10);
    const length = reply.embeds.reduce(
      (n, e) => n + e.title.length + e.description.length + (e.footer?.text.length ?? 0),
      0,
    );
    expect(length).toBeLessThanOrEqual(6000);
  });
  it('returns at most ten current games and omits replay metadata', async () => {
    const adapter = {
      ...gameAdapter('wc3')!,
      discoverOngoing: async () => ({
        status: 'ok' as const,
        value: Array.from({ length: 15 }, () => observation()),
      }),
    };
    const result = await publicGameList(adapter, 'running');
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.value.games).toHaveLength(10);
    expect(result.value.games[0]).not.toHaveProperty('replayReferences');
    expect(result.value.games[0].players[0].selection).toBe('Unknown race 999');
    expect(await publicGameList(adapter, 'waiting')).toEqual({ status: 'unsupported' });
  });
  it('distinguishes failures, stale data, empty success and incompatible phases', async () => {
    const adapter = gameAdapter('wc3')!;
    expect(
      await publicGameList(
        { ...adapter, discoverOngoing: async () => ({ status: 'throttled', retryAfterMs: 40000 }) },
        'running',
      ),
    ).toEqual({ status: 'throttled', retryAfterMs: 40000 });
    expect(
      await publicGameList(
        {
          ...adapter,
          discoverOngoing: async () => ({
            status: 'ok',
            value: [{ ...observation(), observedAt: Date.now() - 600000 }],
          }),
        },
        'running',
      ),
    ).toEqual({ status: 'invalid_response' });
    const result = await publicGameList(
      { ...adapter, discoverOngoing: async () => ({ status: 'ok', value: [] }) },
      'running',
    );
    expect(result).toMatchObject({
      status: 'ok',
      value: { games: [], coverage: 'bounded-snapshot' },
    });
  });
});
