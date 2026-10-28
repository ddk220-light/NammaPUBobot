import { describe, expect, it, vi } from 'vitest';
import fixtures from './fixtures/connectors/sc2/v1/contracts.json';
import {
  createSc2Adapter,
  decodeSc2Characters,
  decodeSc2History,
  decodeSc2Profile,
  fetchOwnedSc2Characters,
  fetchSc2History,
  sc2Character,
} from '../src/games/sc2';
import type { JsonTransport } from '../src/games/types';

describe('SC2 character identity and supporting evidence', () => {
  it('keeps region, realm and large profile IDs separate from account IDs and names', () => {
    const characters = decodeSc2Characters(fixtures.characters);
    expect(characters.map((p) => p.id)).toEqual(['1:1:123', '2:2:9007199254740993']);
    expect(sc2Character('2-S2-2-9007199254740993').id).toBe('2:2:9007199254740993');
    expect(
      sc2Character('https://starcraft2.blizzard.com/en-us/profile/2/2/9007199254740993').id,
    ).toBe('2:2:9007199254740993');
    expect(decodeSc2Profile(fixtures.profile, '1:1:123')).toMatchObject({
      id: '1:1:123',
      name: 'US Character',
      namespace: 'sc2-character',
    });
  });
  it.each([
    'Name#1234',
    '123',
    '5:1:123',
    '1:3:123',
    'https://evil.test/en-us/profile/1/1/123',
    'https://starcraft2.blizzard.com:444/en-us/profile/1/1/123',
  ])('rejects ambiguous or unsupported character reference %s', (id) => {
    expect(() => sc2Character(id)).toThrow();
  });
  it('does not accept an API payload for a different character or a rounded numeric ID', () => {
    expect(() => decodeSc2Profile(fixtures.profile, '1:2:123')).toThrow();
    expect(() => decodeSc2Profile(fixtures.profile, '1:1:124')).toThrow();
    expect(() =>
      decodeSc2Characters([{ ...fixtures.characters[0], profileId: 9007199254740992 }]),
    ).toThrow();
    expect(() => decodeSc2Characters([fixtures.characters[0], fixtures.characters[0]])).toThrow();
  });
  it('keeps unsorted legacy history supporting-only and preserves unknown decisions', () => {
    const history = decodeSc2History(fixtures.history, '1:1:123');
    expect(history.map((h) => h.occurredAt)).toEqual([1761000120000, 1761000060000, 1761000000000]);
    expect(history[1].outcome).toBe('unknown');
    for (const h of history) {
      expect(h).not.toHaveProperty('nativeMatchId');
      expect(h).not.toHaveProperty('phase');
      expect(h).not.toHaveProperty('players');
      expect(h).not.toHaveProperty('elapsedDuration');
    }
    expect(() => decodeSc2History({ matches: [{ date: null }] }, '1:1:123')).toThrow();
    expect(() => decodeSc2History({ matches: [{ date: Date.now() }] }, '1:1:123')).toThrow();
  });
  it('does no network work for unavailable credentials or nonexistent lobby feeds', async () => {
    const request = vi.fn<JsonTransport>();
    const adapter = createSc2Adapter({ request });
    expect(await adapter.profile('1:1:123')).toEqual({ status: 'authentication_required' });
    expect(await adapter.supportingHistory('1:1:123')).toEqual({
      status: 'authentication_required',
    });
    expect(await adapter.observe('host invitation', false)).toEqual({ status: 'unsupported' });
    expect(adapter.discover).toBeUndefined();
    expect(adapter.match).toBeUndefined();
    expect(adapter.history).toBeUndefined();
    expect(adapter.replays).toBeUndefined();
    expect(request).not.toHaveBeenCalled();
  });
  it('uses the correct regional host and an Authorization header, never a query token', async () => {
    const request = vi.fn<JsonTransport>().mockResolvedValue({
      status: 'ok',
      value: { summary: { id: '888', realm: 2, displayName: 'KR Character' } },
    });
    const adapter = createSc2Adapter({ request, battleNetAccessToken: 'private-token' });
    expect((await adapter.profile('3:2:888')).status).toBe('ok');
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: 'https://kr.api.blizzard.com/sc2/profile/3/2/888?locale=en_US',
        headers: { Authorization: 'Bearer private-token' },
      }),
    );
  });
  it.each([
    [{ code: 401 }, 'authentication_required'],
    [{ code: 404 }, 'not_found'],
    [{ code: 429 }, 'throttled'],
    [{ code: 500, detail: 'bad' }, 'invalid_response'],
    [{ summary: {} }, 'invalid_response'],
  ])('normalizes HTTP 200 error/schema payload %j', async (value, status) => {
    const request = vi.fn<JsonTransport>().mockResolvedValue({ status: 'ok', value });
    expect(
      await createSc2Adapter({ request, battleNetAccessToken: 'token' }).profile('1:1:123'),
    ).toEqual({ status });
  });
  it('enumerates owned characters in all supported regions and labels partial coverage', async () => {
    const request = vi.fn<JsonTransport>().mockImplementation(async ({ url }) => {
      if (url.includes('us.api')) return { status: 'ok', value: [fixtures.characters[0]] };
      if (url.includes('eu.api')) return { status: 'ok', value: [fixtures.characters[1]] };
      return { status: 'unavailable' };
    });
    const result = await fetchOwnedSc2Characters('12345', {
      request,
      battleNetAccessToken: 'token',
    });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') throw new Error('missing characters');
    expect(result.value.profiles.map((p) => p.id)).toEqual(['1:1:123', '2:2:9007199254740993']);
    expect(result.value.unavailableRegions).toEqual(['kr']);
    expect(request.mock.calls.every(([r]) => r.url.includes('/sc2/player/12345'))).toBe(true);
  });
  it('fails closed for revoked tokens and failed region enumeration', async () => {
    const request = vi.fn<JsonTransport>().mockResolvedValue({ status: 'authentication_required' });
    expect(
      await fetchOwnedSc2Characters('12345', { request, battleNetAccessToken: 'revoked' }),
    ).toEqual({ status: 'authentication_required' });
    expect(request).toHaveBeenCalledTimes(1);
    request.mockResolvedValue({ status: 'unavailable' });
    expect(
      await fetchOwnedSc2Characters('12345', { request, battleNetAccessToken: 'token' }),
    ).toEqual({ status: 'unavailable' });
  });
  it('distinguishes an empty successful history from private/revoked or malformed history', async () => {
    const request = vi
      .fn<JsonTransport>()
      .mockResolvedValue({ status: 'ok', value: { matches: [] } });
    expect(await fetchSc2History('1:1:123', { request, battleNetAccessToken: 'token' })).toEqual({
      status: 'ok',
      value: [],
    });
    request.mockResolvedValue({ status: 'authentication_required' });
    expect(await fetchSc2History('1:1:123', { request, battleNetAccessToken: 'token' })).toEqual({
      status: 'authentication_required',
    });
    request.mockResolvedValue({ status: 'ok', value: {} });
    expect(await fetchSc2History('1:1:123', { request, battleNetAccessToken: 'token' })).toEqual({
      status: 'invalid_response',
    });
  });
});
