import { describe, expect, it, vi } from 'vitest';
import { runProviderChecks } from '../scripts/provider-check-core';
import {
  operations,
  type ConnectorManifest,
  type GameAdapter,
  type LobbyObservation,
  type JsonRequest,
} from '../src/games/types';
const now = Date.parse('2026-10-28T20:00:00Z');
const observation: LobbyObservation = {
  externalId: '123',
  nativeMatchId: '456',
  name: 'Public sample',
  phase: 'finished',
  observedAt: now,
  startedAt: now - 120000,
  finishedAt: now - 1000,
  players: [
    { profileId: '199325', name: 'A', team: '1' },
    { profileId: '200', name: 'B', team: '2' },
  ],
  capacity: 2,
  map: 'Sample',
  source: 'test-provider',
  elapsedDuration: { value: 119, unit: 'seconds' },
};
const manifest = (
  capabilities: Partial<ConnectorManifest['capabilities']> = {},
): ConnectorManifest => ({
  id: 'aoe2',
  edition: 'DE',
  provider: 'test',
  accountNamespace: 'test',
  version: '1',
  acceptance: 'pending',
  limitations: [],
  capabilities: {
    ...Object.fromEntries(operations.map((op) => [op, 'available'])),
    ...capabilities,
  } as ConnectorManifest['capabilities'],
});
function adapter(overrides: Partial<GameAdapter> = {}): GameAdapter {
  return {
    id: 'aoe2',
    manifest: manifest(),
    profileProvider: 'test',
    parseLobbyReference: (x) => x,
    profile: async (id) => ({
      status: 'ok',
      value: { id, name: 'Profile', url: 'https://example.com/profile' },
    }),
    discover: async () => ({ status: 'ok', value: [observation] }),
    observe: async () => ({ status: 'ok', value: observation }),
    match: async () => ({ status: 'ok', value: observation }),
    history: async () => ({ status: 'ok', value: [observation] }),
    replays: async () => ({ status: 'ok', value: [] }),
    profileUrl: () => '',
    lobbyUrl: () => '',
    joinUri: () => '',
    ...overrides,
  };
}
const request = vi.fn(async () => ({ status: 'unavailable' as const }));
const run = (a: GameAdapter) =>
  runProviderChecks({ games: ['aoe2'], createAdapter: () => a, request, now: () => now });

describe('read-only provider probe reporting', () => {
  it('checks response semantics and marks interactive ownership as access-blocked', async () => {
    const report = await run(adapter());
    expect(report.counts).toEqual({ passed: 6, failed: 0, 'access-blocked': 1, unsupported: 0 });
    expect(report.results.find((r) => r.operation === 'ownershipVerification')).toMatchObject({
      status: 'access-blocked',
      detail: 'interactive_sign_in_required',
    });
    expect(report.scope).toContain('not real-game acceptance');
  });
  it('never records missing credentials or missing sample identifiers as successful tests', async () => {
    const profile = vi.fn(adapter().profile);
    const a = adapter({ profile, manifest: manifest({ accountLookup: 'credentials_required' }) });
    const report = await runProviderChecks({
      games: ['sc2'],
      createAdapter: () => a,
      request,
      now: () => now,
    });
    expect(report.counts.passed).toBe(0);
    expect(report.counts['access-blocked']).toBe(7);
    expect(profile).not.toHaveBeenCalled();
    const withCredentials = await runProviderChecks({
      games: ['sc2'],
      createAdapter: () => a,
      request,
      env: { BATTLE_NET_ACCESS_TOKEN: 'secret-token' },
      now: () => now,
    });
    expect(withCredentials.results.find((r) => r.operation === 'matchLookup')).toMatchObject({
      status: 'access-blocked',
      detail: 'sample_identifier_required',
    });
  });
  it('preserves throttled, invalid and authenticated failure states, rather than turning them into empty success', async () => {
    const report = await run(
      adapter({
        discover: async () => ({ status: 'throttled', retryAfterMs: 60000 }),
        profile: async () => ({ status: 'authentication_required' }),
        match: async () => ({ status: 'invalid_response' }),
        replays: async () => ({
          status: 'ok',
          value: [
            {
              provider: 'test',
              matchId: '123',
              availability: 'available',
              sourceUrl: 'https://provider.test/replay?signature=secret',
              expiresAt: now - 1,
            },
          ],
        }),
      }),
    );
    expect(report.results.find((r) => r.operation === 'lobbyDiscovery')).toMatchObject({
      status: 'failed',
      detail: 'throttled',
    });
    expect(report.results.find((r) => r.operation === 'accountLookup')).toMatchObject({
      status: 'access-blocked',
    });
    expect(report.results.find((r) => r.operation === 'matchLookup')).toMatchObject({
      status: 'failed',
      detail: 'invalid_response',
    });
    expect(report.results.find((r) => r.operation === 'replayReferences')).toMatchObject({
      status: 'failed',
      detail: 'invalid_normalized_response',
    });
  });
  it('accepts a valid empty public feed while blocking an observation that has no live sample', async () => {
    const a = adapter({ discover: async () => ({ status: 'ok', value: [] }) });
    const report = await runProviderChecks({
      games: ['bar'],
      createAdapter: () => a,
      request,
      now: () => now,
    });
    expect(report.results.find((r) => r.operation === 'lobbyDiscovery')).toMatchObject({
      status: 'passed',
      detail: 'valid_empty_result',
    });
    expect(report.results.find((r) => r.operation === 'lobbyObservation')).toMatchObject({
      status: 'access-blocked',
      detail: 'no_public_lobby_sample',
    });
    expect(report.results.find((r) => r.operation === 'accountLookup')).toMatchObject({
      status: 'access-blocked',
      detail: 'sample_identifier_required',
    });
  });
  it('does not call unsupported operations or retain signed URLs, profile history, exceptions, or tokens', async () => {
    const profile = vi.fn(adapter().profile);
    const a = adapter({
      profile,
      manifest: manifest({ accountLookup: 'unsupported' }),
      history: async () => {
        throw Error('https://private.example/history?token=secret');
      },
      replays: async () => ({
        status: 'ok',
        value: [
          {
            provider: 'test',
            matchId: '123',
            availability: 'available',
            sourceUrl: 'https://provider.test/replay?signature=secret',
          },
        ],
      }),
    });
    const report = await run(a);
    expect(profile).not.toHaveBeenCalled();
    expect(report.results.find((r) => r.operation === 'accountLookup')?.status).toBe('unsupported');
    expect(report.results.find((r) => r.operation === 'replayReferences')?.status).toBe('passed');
    expect(JSON.stringify(report)).not.toMatch(
      /secret|signature|private\.example|Public sample|199325/,
    );
  });
  it('enforces a fixed request budget even if an adapter tries an excessive history scan', async () => {
    const transport = vi.fn(async (_request: JsonRequest) => ({
      status: 'ok' as const,
      value: [],
    }));
    const report = await runProviderChecks({
      games: ['aoe2'],
      request: transport,
      now: () => now,
      createAdapter: (_game, runtime) =>
        adapter({
          discover: async () => {
            for (let i = 0; i < 20; i++)
              await runtime.request!({ url: 'https://provider.test/public', maxBytes: 9000000 });
            return { status: 'ok', value: [] };
          },
        }),
    });
    expect(transport).toHaveBeenCalledTimes(12);
    expect(transport.mock.calls[0][0]).toMatchObject({ maxBytes: 2000000, cacheTtlMs: 0 });
    expect(report.results[0]).toMatchObject({
      status: 'failed',
      detail: 'per_game_request_budget_exceeded',
      requests: 12,
    });
  });
  it('rejects duplicate participants and malformed metadata even when the adapter returns ok', async () => {
    const report = await run(
      adapter({
        match: async () => ({
          status: 'ok',
          value: { ...observation, players: [observation.players[0], observation.players[0]] },
        }),
      }),
    );
    expect(report.results.find((r) => r.operation === 'matchLookup')).toMatchObject({
      status: 'failed',
      detail: 'invalid_normalized_response',
    });
  });
  it('checks limited SC2 history as supporting evidence without inventing a lobby observation', async () => {
    const a = adapter({
      history: undefined,
      supportingHistory: async () => ({
        status: 'ok',
        value: [
          {
            accountId: '1:1:123',
            source: 'battle-net-history',
            occurredAt: now - 60000,
            observedAt: now,
            outcome: 'win',
          },
        ],
      }),
    });
    const report = await runProviderChecks({
      games: ['sc2'],
      request,
      now: () => now,
      createAdapter: () => a,
      env: { BATTLE_NET_ACCESS_TOKEN: 'test', CONNECTOR_PROBE_SC2_PROFILE_ID: '1:1:123' },
    });
    expect(report.results.find((r) => r.operation === 'historyLookup')).toMatchObject({
      status: 'passed',
      detail: 'valid_metadata_response',
    });
  });
  it('flags a manifest that advertises an operation its adapter cannot implement', async () => {
    const report = await run(adapter({ match: undefined }));
    expect(report.results.find((r) => r.operation === 'matchLookup')).toMatchObject({
      status: 'failed',
      detail: 'declared_operation_unsupported',
    });
  });
  it('distinguishes rejected service credentials from absent credentials without exposing errors', async () => {
    const report = await runProviderChecks({
      games: ['sc2'],
      createAdapter: () => adapter(),
      request,
      now: () => now,
      env: { CONNECTOR_PROBE_SC2_AUTH_ERROR: 'authorization_revoked' },
    });
    expect(report.results.find((r) => r.operation === 'accountLookup')).toMatchObject({
      status: 'access-blocked',
      detail: 'credential_setup_authorization_revoked',
    });
    expect(report.counts.passed).toBe(0);
  });
  it('accepts refreshable availability in match metadata but requires a usable link from replays', async () => {
    const refs = [
      {
        provider: 'test',
        matchId: '123',
        perspectiveId: '456',
        availability: 'available' as const,
      },
    ];
    const report = await run(
      adapter({
        match: async () => ({ status: 'ok', value: { ...observation, replayReferences: refs } }),
        replays: async () => ({ status: 'ok', value: refs }),
      }),
    );
    expect(report.results.find((r) => r.operation === 'matchLookup')?.status).toBe('passed');
    expect(report.results.find((r) => r.operation === 'replayReferences')).toMatchObject({
      status: 'failed',
      detail: 'invalid_normalized_response',
    });
  });
});
