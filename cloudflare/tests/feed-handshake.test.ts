import { env } from 'cloudflare:workers';
import { expect, it, vi } from 'vitest';
import { harness } from './harness';
harness();
it('keeps the connection usable after the six-second handshake deadline', async () => {
  const pair = new WebSocketPair();
  pair[1].accept();
  let handshakeSignal: AbortSignal | null | undefined;
  vi.mocked(fetch).mockImplementation(async (_url, init) => {
    handshakeSignal = init?.signal;
    return new Response(null, { status: 101, webSocket: pair[0] });
  });
  const feed = env.AOE2_LOBBIES.getByName('handshake-regression');
  expect((await feed.query('9001')).healthy).toBe(true);
  pair[1].send(
    JSON.stringify([
      { type: 'lobbyAdded', data: { matchId: 9001, totalSlotCount: 2, blockedSlotCount: 0 } },
      {
        type: 'slotAdded',
        data: { matchId: 9001, slot: 0, profileId: 100, name: 'Player', team: 1 },
      },
    ]),
  );
  await new Promise((resolve) => setTimeout(resolve, 6200));
  expect(handshakeSignal?.aborted).toBe(false);
  const result = await feed.query('9001');
  expect(result.healthy).toBe(true);
  expect(pair[1].readyState).toBe(WebSocket.OPEN);
  pair[1].close(1000, 'Test complete');
}, 10000);
