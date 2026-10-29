import { env } from 'cloudflare:workers';
import { runInDurableObject, runDurableObjectAlarm } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import { harness } from './harness';
import fixture from './fixtures/connectors/aoe2-ongoing-v1.json';
import { createAoE2Adapter } from '../src/games/aoe2';
import {
  AoE2LobbySnapshot,
  aoe2OngoingFeedUrl,
  parseAoE2OngoingSnapshot,
} from '../src/games/aoe2-feed';
import type { LobbyObservation, ProviderResult } from '../src/games/types';

harness();
const capturedAt = Date.parse(fixture.capturedAt);
const events = () => structuredClone(fixture.events);
const feed = (name: string) => env.AOE2_LOBBIES.getByName(`ongoing:${name}`);

/** In-process fake keeps these tests deterministic and never opens a network socket. */
class SnapshotSocket extends EventTarget {
  readyState = WebSocket.CONNECTING;
  accept = vi.fn(() => {
    this.readyState = WebSocket.OPEN;
    this.accepted(this);
  });
  close = vi.fn(() => {
    this.readyState = WebSocket.CLOSED;
    this.dispatchEvent(new Event('close'));
  });
  constructor(private accepted: (socket: SnapshotSocket) => void = () => {}) {
    super();
  }
  message(data: unknown) {
    this.dispatchEvent(new MessageEvent('message', { data }));
  }
}
function upgrade(socket: SnapshotSocket): Response {
  return {
    status: 101,
    ok: false,
    webSocket: socket,
    headers: new Headers(),
    body: null,
  } as unknown as Response;
}
function network(accepted: (socket: SnapshotSocket) => void) {
  const sockets: SnapshotSocket[] = [];
  vi.mocked(fetch).mockImplementation(async () => {
    const socket = new SnapshotSocket(accepted);
    sockets.push(socket);
    return upgrade(socket);
  });
  return sockets;
}
function shortDeadline() {
  const original = globalThis.setTimeout;
  return vi
    .spyOn(globalThis, 'setTimeout')
    .mockImplementation((callback, delay, ...args) =>
      original(callback, delay === 10000 ? 5 : delay, ...args),
    );
}

describe('AoE2 captured ongoing snapshot contract', () => {
  it('normalizes real flat-player events with actual civs, teams, build and elapsed seconds', () => {
    const result = parseAoE2OngoingSnapshot(events(), capturedAt);
    expect(result.map((x) => x.externalId)).toEqual(['9102', '9101', '9100']);
    expect(result[0]).toMatchObject({
      phase: 'started',
      referenceKind: 'match',
      schemaVersion: 2,
      provider: 'aoe2companion',
      accountNamespace: 'aoe2:profile',
      source: 'aoe2companion-ongoing-matches',
      nativeMatchId: '9102',
      nativeLobbyId: '9102',
      observedAt: capturedAt,
      finishedAt: null,
      capacity: null,
      map: 'Arena',
      gameBuild: '1824',
      mode: 'random_map',
      visibility: 'public',
      confidence: 'provisional',
      elapsedDuration: { value: 80.921, unit: 'seconds' },
      players: [
        { profileId: '1020', team: '1', selection: 'Mongols', selectionId: 'mongols' },
        { profileId: '1021', team: '2', selection: 'Burgundians', selectionId: 'burgundians' },
      ],
    });
    expect(result[2].players).toHaveLength(8);
    expect(result[0].players.every((p) => p.outcome === undefined)).toBe(true);
    expect(result[0].replayReferences?.every((r) => r.availability === 'unknown')).toBe(true);
    expect(result[0].simulationDuration).toBeUndefined();
  });
  it('preserves unknown civilization IDs and keeps missing names unknown', () => {
    const data = events();
    data[0].data.players[0].civ = 'future_civilization';
    delete (data[0].data.players[0] as { civName?: string }).civName;
    const player = parseAoE2OngoingSnapshot(data, capturedAt).at(-1)!.players[0];
    expect(player.selectionId).toBe('future_civilization');
    expect(player.selection).toBeUndefined();
  });
  it('accepts an explicit empty snapshot and excludes completed/private matches', () => {
    expect(parseAoE2OngoingSnapshot([], capturedAt)).toEqual([]);
    const data = events();
    (data[0].data as { finished: string | null }).finished = fixture.capturedAt;
    data[1].data.privacy = 'private';
    expect(parseAoE2OngoingSnapshot(data, capturedAt).map((x) => x.externalId)).toEqual(['9102']);
  });
  it.each([
    { success: false },
    [{ type: 'matchUpdated', data: { matchId: 1 } }],
    [{ type: 'lobbyAdded', data: { matchId: 1 } }],
    [{ type: 'matchAdded', data: null }],
    [...fixture.events, fixture.events[0]],
    [{ type: 'matchAdded', data: { ...fixture.events[0].data, matchId: '9007199254740993' } }],
    [{ type: 'matchAdded', data: { ...fixture.events[0].data, players: undefined } }],
    [{ type: 'matchAdded', data: { ...fixture.events[0].data, started: null } }],
    [{ type: 'matchAdded', data: { ...fixture.events[0].data, started: '2099-01-01T00:00:00Z' } }],
  ])('rejects malformed, partial and conflicting snapshots %#', (input) => {
    expect(() => parseAoE2OngoingSnapshot(input, capturedAt)).toThrow();
  });
  it('bounds large directories and rejects a duplicated participant without returning partial data', () => {
    const data = Array.from({ length: 250 }, (_, i) => ({
      ...fixture.events[0],
      data: { ...fixture.events[0].data, matchId: 10000 + i },
    }));
    expect(parseAoE2OngoingSnapshot(data, capturedAt)).toHaveLength(200);
    const duplicate = events();
    duplicate[1].data.players.push(duplicate[1].data.players[0]);
    expect(() => parseAoE2OngoingSnapshot(duplicate, capturedAt)).toThrow('Invalid roster');
  });
  it('uses only configured shared runtime callbacks and preserves failure semantics', async () => {
    const empty = createAoE2Adapter();
    expect(await empty.discover!()).toEqual({ status: 'unavailable' });
    expect(await empty.discoverOngoing!()).toEqual({ status: 'unavailable' });
    const ongoing = vi.fn(async (): Promise<ProviderResult<LobbyObservation[]>> => ({
      status: 'throttled',
      retryAfterMs: 120000,
    }));
    const adapter = createAoE2Adapter({
      aoe2OngoingDiscovery: ongoing,
      aoe2Discovery: async () => ({ status: 'ok', value: [] }),
    });
    expect(await adapter.discover!()).toEqual({ status: 'ok', value: [] });
    expect(await adapter.discoverOngoing!()).toEqual({ status: 'throttled', retryAfterMs: 120000 });
    expect(ongoing).toHaveBeenCalledOnce();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('bounded shared AoE2 ongoing WebSocket capture', () => {
  it('coalesces discovery, caches original observations briefly and closes every capture', async () => {
    const sockets = network((socket) => {
      socket.message('pong');
      socket.message(JSON.stringify(events()));
    });
    const stub = feed('cache');
    const [first, second] = await Promise.all([stub.browseOngoing(), stub.browseOngoing()]);
    expect(first.status).toBe('ok');
    expect(second).toEqual(first);
    expect(await stub.browseOngoing()).toEqual(first);
    expect(fetch).toHaveBeenCalledOnce();
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(aoe2OngoingFeedUrl);
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(sockets[0].close).toHaveBeenCalledOnce();
    expect((await stub.status()).tracked).toBe(0);
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30001);
    expect((await stub.browseOngoing()).status).toBe('ok');
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(sockets[1].close).toHaveBeenCalledOnce();
    clock.mockRestore();
  });
  it('distinguishes an empty successful directory from provider failures', async () => {
    const sockets = network((socket) => socket.message('[]'));
    expect(await feed('empty').browseOngoing()).toEqual({ status: 'ok', value: [] });
    expect(sockets[0].close).toHaveBeenCalledOnce();
  });
  it.each([
    '{',
    '{"success":false,"message":"provider unavailable"}',
    new ArrayBuffer(8),
    'x'.repeat(2 * 1024 * 1024 + 1),
    '漢'.repeat(800000),
  ])('closes malformed, binary or oversized snapshots %#', async (data) => {
    const sockets = network((socket) => socket.message(data));
    expect(await feed('invalid').browseOngoing()).toEqual({ status: 'invalid_response' });
    expect(sockets[0].close).toHaveBeenCalledOnce();
    expect(await feed('invalid').browseOngoing()).toEqual({ status: 'invalid_response' });
    expect(fetch).toHaveBeenCalledOnce();
  });
  it.each([
    [401, 'authentication_required'],
    [403, 'authentication_required'],
    [200, 'invalid_response'],
    [503, 'unavailable'],
  ])('classifies HTTP %s without an upgraded socket', async (status, result) => {
    vi.mocked(fetch).mockImplementation(
      async () => new Response('{}', { status: status as number }),
    );
    expect(await feed(`http-${status}`).browseOngoing()).toEqual({ status: result });
  });
  it('honors Retry-After and coalesces throttled callers without returning old data', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(null, { status: 429, headers: { 'Retry-After': '600' } }),
    );
    const stub = feed('throttled');
    expect(await stub.browseOngoing()).toEqual({ status: 'throttled', retryAfterMs: 600000 });
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 180000);
    expect(await stub.browseOngoing()).toEqual({ status: 'throttled', retryAfterMs: 600000 });
    expect(fetch).toHaveBeenCalledOnce();
    clock.mockRestore();
  });
  it('bounds a connected feed that never delivers a snapshot and closes it', async () => {
    const timeout = shortDeadline();
    const sockets = network((socket) => socket.message('pong'));
    expect(await feed('delayed').browseOngoing()).toEqual({
      status: 'delayed',
      retryAfterMs: 5000,
    });
    expect(timeout.mock.calls.some(([, ms]) => ms === 10000)).toBe(true);
    expect(sockets[0].close).toHaveBeenCalledOnce();
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(true);
  });
  it('bounds a stuck handshake and closes a socket arriving after the deadline', async () => {
    shortDeadline();
    let finishHandshake!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          finishHandshake = resolve;
        }),
    );
    expect(await feed('handshake').browseOngoing()).toEqual({
      status: 'unavailable',
      retryAfterMs: 5000,
    });
    const socket = new SnapshotSocket();
    finishHandshake(upgrade(socket));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(socket.accept).toHaveBeenCalledOnce();
    expect(socket.close).toHaveBeenCalledOnce();
  });
  it('treats premature connection loss as unavailable and does not cache an empty list', async () => {
    const sockets = network((socket) => socket.dispatchEvent(new Event('error')));
    expect(await feed('disconnect').browseOngoing()).toEqual({ status: 'unavailable' });
    expect(sockets[0].close).toHaveBeenCalledOnce();
  });
});

describe('shared waiting-lobby browse compatibility', () => {
  const waiting = [
    {
      type: 'lobbyAdded',
      data: { matchId: 9001, name: 'Fixture lobby', totalSlotCount: 2, blockedSlotCount: 0 },
    },
    {
      type: 'slotAdded',
      data: { matchId: 9001, profileId: 101, slot: 0, team: 1, civ: 'franks', civName: 'Franks' },
    },
  ];
  it('requires a baseline, preserves actual civs and prunes wildcard records after the lease ends', () => {
    const snapshot = new AoE2LobbySnapshot();
    expect(snapshot.browse(capturedAt)).toBeNull();
    snapshot.consume(waiting, new Set(['browse:*']), capturedAt);
    expect(snapshot.browse(capturedAt + 1999)).toBeNull();
    expect(snapshot.browse(capturedAt + 2000)).toMatchObject([
      { phase: 'open', startedAt: null, players: [{ profileId: '101', selection: 'Franks' }] },
    ]);
    snapshot.consume([], new Set(), capturedAt + 3000);
    expect(snapshot.browse(capturedAt + 3000)).toEqual([]);
    snapshot.clear();
    expect(snapshot.browse(capturedAt + 3000)).toBeNull();
  });
  it('does not turn provider errors or isolated deltas into an empty waiting directory', () => {
    const snapshot = new AoE2LobbySnapshot();
    expect(() => snapshot.consume({ success: false }, new Set(['browse:*']), capturedAt)).toThrow();
    expect(() => snapshot.consume(waiting[1], new Set(['browse:*']), capturedAt)).toThrow();
    expect(snapshot.browse(capturedAt + 3000)).toBeNull();
    snapshot.consume([], new Set(['browse:*']), capturedAt);
    expect(snapshot.browse(capturedAt + 3000)).toEqual([]);
  });
  it('leases one waiting feed briefly and keeps it independent from ongoing captures', async () => {
    const sockets: SnapshotSocket[] = [];
    vi.mocked(fetch).mockImplementation(async (url) => {
      const socket = new SnapshotSocket((s) =>
        queueMicrotask(() =>
          s.message(JSON.stringify(String(url) === aoe2OngoingFeedUrl ? events() : waiting)),
        ),
      );
      sockets.push(socket);
      return upgrade(socket);
    });
    const stub = feed('waiting');
    expect((await stub.query('9001')).healthy).toBe(true);
    const result = await stub.browse();
    expect(result).toMatchObject({ status: 'ok', value: [{ externalId: '9001' }] });
    expect((await stub.browseOngoing()).status).toBe('ok');
    expect(await stub.status()).toMatchObject({ healthy: true, tracked: 2 });
    expect(sockets.at(-1)!.close).toHaveBeenCalledOnce();
    expect(sockets[1].readyState).toBe(WebSocket.OPEN);
    await runInDurableObject(stub, async (instance, state) => {
      const leases = (instance as unknown as { leases: Map<string, number> }).leases;
      expect(leases.get('browse:*')! - Date.now()).toBeLessThanOrEqual(30000);
      leases.set('browse:*', Date.now() - 1);
      await state.storage.setAlarm(Date.now());
    });
    await runDurableObjectAlarm(stub);
    expect(await stub.status()).toMatchObject({ healthy: true, tracked: 1 });
    expect((await stub.query('9001')).observation?.players[0].selection).toBe('Franks');
  });
});
