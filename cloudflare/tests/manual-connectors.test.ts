import { afterEach, describe, expect, it, vi } from 'vitest';
import { gameCapabilities, gameCatalog } from '../src/games/catalog';
import { gameAdapter } from '../src/games/registry';
import {
  createManualAdapter,
  externalRecordingReference,
  parseGeneralsAccountReference,
  parseManualLobbyReference,
  parseSteamAccountReference,
} from '../src/games/manual';
import { catalogIds, operations } from '../src/games/types';

const steamId = '76561198012345678';
afterEach(() => vi.restoreAllMocks());

describe('catalog capability contract', () => {
  it('registers every edition with one canonical manifest and manual fallback', () => {
    expect(gameCatalog.map((game) => game.id).sort()).toEqual([...catalogIds].sort());
    for (const entry of gameCatalog) {
      const adapter = gameAdapter(entry.id);
      expect(adapter?.id).toBe(entry.id);
      expect(adapter?.manifest).toBe(entry.manifest);
      expect(entry.manual).toBe(true);
      expect(Object.keys(entry.manifest.capabilities).sort()).toEqual([...operations].sort());
      expect(entry.manifest.acceptance).toBe('pending');
      expect(entry.manifest.edition).toBeTruthy();
      expect(entry.manifest.accountNamespace).toBeTruthy();
      expect(entry.lobby).toBe(entry.manifest.capabilities.lobbyObservation !== 'unsupported');
    }
  });
  it('preserves the original Generals identifier and adds Zero Hour separately', () => {
    expect(gameCapabilities('generals')).toMatchObject({
      id: 'generals',
      name: 'Command & Conquer Generals',
      lobby: false,
      manifest: { provider: 'manual', accountNamespace: 'generals-network' },
    });
    expect(gameCapabilities('zero_hour')).toMatchObject({
      id: 'zero_hour',
      manifest: { provider: 'generalsonline', acceptance: 'pending' },
    });
    expect(gameCapabilities('sc2')?.manifest.capabilities.lobbyDiscovery).toBe('unsupported');
    for (const unknown of [undefined, null, '', 'toString', '__proto__', 'unlisted-game'])
      expect(gameAdapter(unknown)).toBeUndefined();
  });
});

describe('manual connectors', () => {
  it.each(['generals', 'northgard', 'tempest_rising'] as const)(
    '%s never simulates a provider lookup or observation',
    async (id) => {
      const request = vi.spyOn(globalThis, 'fetch').mockRejectedValue(Error('No request expected'));
      const adapter = createManualAdapter(id);
      expect(await adapter.profile('declared-account')).toEqual({ status: 'unsupported' });
      expect(await adapter.observe('host-room-1', false)).toEqual({ status: 'unsupported' });
      expect(adapter.discover).toBeUndefined();
      expect(adapter.match).toBeUndefined();
      expect(adapter.history).toBeUndefined();
      expect(adapter.replays).toBeUndefined();
      expect(adapter.parseLobbyReference('  host-room-1  ')).toBe('host-room-1');
      expect(adapter.parseLobbyReference('network:host-room-1')).toBe('network:host-room-1');
      expect(adapter.lobbyUrl('host-room-1')).toBe('');
      expect(adapter.joinUri('host-room-1')).toBe('');
      expect(request).not.toHaveBeenCalled();
    },
  );
  it('accepts Steam identity declarations without converting their large IDs to numbers', () => {
    expect(parseSteamAccountReference(steamId)).toBe(steamId);
    expect(parseSteamAccountReference(`https://steamcommunity.com/profiles/${steamId}/`)).toBe(
      steamId,
    );
    expect(createManualAdapter('northgard').profileUrl(steamId)).toBe(
      `https://steamcommunity.com/profiles/${steamId}`,
    );
    expect(createManualAdapter('tempest_rising').parseAccountReference!(steamId)).toBe(steamId);
    expect(parseSteamAccountReference('76561197960265728')).toBe('76561197960265728');
    expect(parseSteamAccountReference('76561202255233023')).toBe('76561202255233023');
  });
  it.each([
    '76561197960265727',
    '76561202255233024',
    '123',
    '76561198012345678\n',
    'https://steamcommunity.com/id/display-name',
    `https://steamcommunity.com.evil.test/profiles/${steamId}`,
    `https://name:secret@steamcommunity.com/profiles/${steamId}`,
    `https://steamcommunity.com:444/profiles/${steamId}`,
    `http://steamcommunity.com/profiles/${steamId}`,
    `https://steamcommunity.com/profiles/${steamId}?id=other`,
  ])('rejects a noncanonical Steam identity %s', (value) => {
    expect(() => parseSteamAccountReference(value)).toThrow();
  });
  it('keeps original Generals declarations distinct across networks', () => {
    expect(parseGeneralsAccountReference('GameRanger:12345')).toBe('gameranger:12345');
    expect(parseGeneralsAccountReference('radmin:12345')).toBe('radmin:12345');
    expect(createManualAdapter('generals').profileUrl('gameranger:12345')).toBe('');
    for (const value of [
      '12345',
      'name only',
      ':12345',
      'radmin:',
      'radmin:one\ntwo',
      'steam://12345',
    ])
      expect(() => parseGeneralsAccountReference(value)).toThrow();
  });
  it('accepts declared HTTPS and matching Steam invites without inventing native IDs', () => {
    const link = 'steam://joinlobby/1486920/109775240123456789/76561198012345678';
    const tempest = createManualAdapter('tempest_rising');
    expect(tempest.parseLobbyReference(link)).toBe(link);
    expect(tempest.lobbyUrl(link)).toBe('');
    expect(tempest.joinUri(link)).toBe(link);
    const https = 'https://steamcommunity.com/lobby/example';
    expect(tempest.parseLobbyReference(https)).toBe(https);
    expect(tempest.lobbyUrl(https)).toBe(https);
    expect(() => parseManualLobbyReference(link, 'northgard')).toThrow();
    expect(() => parseManualLobbyReference(link, 'generals')).toThrow();
  });
  it.each([
    '',
    'x'.repeat(101),
    'room\nname',
    'room\u202ename',
    'javascript:alert(1)',
    'data:text/html,hello',
    'file:///etc/passwd',
    'http://example.test/room',
    'https://name:secret@example.test/room',
    'https://example.test:444/room',
    '//example.test/room',
    'steam://joinlobby/1486920/one/two',
    'https://example.test/' + 'é'.repeat(30),
  ])('rejects unsafe or unbounded lobby reference %s', (input) => {
    expect(() => parseManualLobbyReference(input, 'tempest_rising')).toThrow();
  });
  it('represents an external recording as unverified link metadata without reading it', () => {
    const request = vi.spyOn(globalThis, 'fetch').mockRejectedValue(Error('No request expected'));
    expect(
      externalRecordingReference('https://www.gentool.net/data/ccg/example.rep', 'native-1'),
    ).toEqual({
      provider: 'declared-external',
      matchId: 'native-1',
      availability: 'unknown',
      sourceUrl: 'https://www.gentool.net/data/ccg/example.rep',
    });
    expect(request).not.toHaveBeenCalled();
    for (const url of [
      'javascript:alert(1)',
      'http://example.test/a.rep',
      'https://name:secret@example.test/a.rep',
      'https://example.test/a.rep\n',
    ])
      expect(() => externalRecordingReference(url, 'native-1')).toThrow();
  });
});
