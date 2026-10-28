import { describe, expect, it } from 'vitest';
import type { Match } from '../src/domain/play';
import type { Settings } from '../src/domain/config';
import {
  connectorSettings,
  matchConnectorDetails,
  ownershipProvider,
  verificationHref,
} from '../web/connectors';

const gameId = '4daebc0a-55e8-4d56-a17d-d94913644910';
const configuredGame = (
  catalogId: Settings['games'][number]['catalogId'],
): Settings['games'][number] => ({
  id: gameId,
  name: 'Friends <RTS>',
  catalogId,
  connector: { enabled: false },
});

describe('connector interface', () => {
  it('keeps manual games usable without presenting fictional lobby observation controls', () => {
    for (const game of ['generals', 'northgard', 'tempest_rising', 'sc2'] as const) {
      const html = connectorSettings(configuredGame(game), '100000000000000001', null);
      expect(html).toContain('Manual lobby association and captain reporting remain available');
      expect(html).toContain('Not available from this provider');
      expect(html).not.toContain('name="lobbyConnector"');
      expect(html).not.toContain('name="autoLobby"');
    }
  });
  it('shows pending acceptance and missing access independently of the requested settings', () => {
    const html = connectorSettings(configuredGame('zero_hour'), '100000000000000001', {
      games: [
        {
          gameId,
          catalogId: 'zero_hour',
          unresolvedBindings: 2,
          failures: 1,
          capabilities: {
            accountLookup: 'unsupported',
            ownershipVerification: 'unsupported',
            lobbyDiscovery: 'credentials_required',
            lobbyObservation: 'acceptance_required',
            matchLookup: 'credentials_required',
            historyLookup: 'unsupported',
            replayReferences: 'credentials_required',
          },
        },
      ],
    });
    expect(html).toContain('Credentials required');
    expect(html).toContain('Awaiting game acceptance');
    expect(html).toContain('2 unresolved associations');
    expect(html).toContain('name="capability-matchLookup"');
    expect(html).not.toContain('name="capability-historyLookup"');
  });
  it('does not offer a verification action that was disabled for the game', () => {
    const game = configuredGame('northgard');
    game.connector = { enabled: false, capabilities: { ownershipVerification: false } };
    expect(connectorSettings(game, '100000000000000001', null)).not.toContain(
      'Verify my Steam account',
    );
  });
  it('keeps verification links scoped to the supplied community and game', () => {
    expect(ownershipProvider('sc2')).toBe('battlenet');
    expect(ownershipProvider('bar')).toBe('bar');
    expect(ownershipProvider('northgard')).toBe('steam');
    expect(ownershipProvider('generals')).toBeUndefined();
    const target = new URL(
      verificationHref('100000000000000001', gameId, 'steam'),
      'https://example.test',
    );
    expect(target.pathname).toBe('/link');
    expect(target.searchParams.get('game')).toBe(gameId);
    expect(target.searchParams.get('community')).toBe('100000000000000001');
  });
  it('distinguishes observed, declared and unconfirmed result facts, while escaping external text', () => {
    const match = {
      state: 'playing',
      roster: [{ id: 'player-1', name: '<script>player</script>' }],
      lobby: {
        id: 'binding-1',
        externalId: '<room>',
        url: 'javascript:alert(1)',
        state: 'observed',
        rosterVerified: false,
        suggestedResult: 'alpha',
        observation: {
          phase: 'finished',
          observedAt: 1700000000000,
          source: 'test-provider',
          map: '<b>map</b>',
          elapsedDuration: { value: 1630, unit: 'seconds' },
          simulationDuration: { value: 1497, unit: 'seconds' },
          players: [
            { profileId: '1', name: '<player>', team: 'one', selection: 'Unknown faction 99' },
          ],
          nativeLobbyId: '9007199254740993',
          nativeMatchId: '12345',
          platformLobbyId: '109775240123456789',
          replayReferences: [
            {
              provider: 'test',
              matchId: '12345',
              availability: 'available',
              sourceUrl: 'https://example.test/expired',
              expiresAt: 1,
            },
          ],
        },
        manualFacts: {
          map: 'Declared map',
          elapsedSeconds: 1700,
          players: [{ userId: 'player-1', side: 0, selection: 'Declared faction' }],
          recordingLinks: ['javascript:alert(1)'],
          declaredBy: 'player-1',
          declaredAt: 1700000000000,
        },
      },
    } as unknown as Match;
    const html = matchConnectorDetails(match);
    expect(html).toContain('Elapsed duration: 1630 seconds');
    expect(html).toContain('Simulation duration: 1497 seconds');
    expect(html).toContain('Declared map');
    expect(html).toContain('1700 seconds elapsed');
    expect(html).toContain('Captain confirmation is still required');
    expect(html).toContain('9007199254740993');
    expect(html).toContain('test: expired');
    expect(html).not.toContain('href="https://example.test/expired"');
    expect(html).not.toContain('href="javascript:');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<b>map</b>');
    expect(html).toContain('&lt;b&gt;map&lt;/b&gt;');
  });
});
