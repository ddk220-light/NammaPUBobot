import { AoE2LobbySnapshot } from '../src/games/aoe2-feed';
import { parseProviderJson } from '../src/games/transport';
import type { LobbyObservation, ProviderResult } from '../src/games/types';

/** One bounded connection per command. Production continues using its existing shared DO. */
export function probeAoE2Feed(): Promise<ProviderResult<LobbyObservation[]>> {
  return new Promise((resolve) => {
    let socket: WebSocket;
    let settled = false;
    let frames = 0;
    let bytes = 0;
    let receivedSnapshot = false;
    let settleTimer: ReturnType<typeof setTimeout> | undefined;
    const snapshot = new AoE2LobbySnapshot();
    const tracked = new Set<string>();
    const finish = (result: ProviderResult<LobbyObservation[]>) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      if (settleTimer) clearTimeout(settleTimer);
      try {
        socket?.close(1000, 'Read-only probe finished');
      } catch {}
      resolve(result);
    };
    const deadline = setTimeout(() => finish({ status: 'unavailable' }), 10000);
    try {
      socket = new WebSocket('wss://socket.aoe2companion.com/listen?handler=lobbies');
      socket.addEventListener('error', () => finish({ status: 'unavailable' }));
      socket.addEventListener('close', () => {
        if (!settled) finish({ status: 'unavailable' });
      });
      socket.addEventListener('message', (event) => {
        if (settled || event.data === 'pong') return;
        if (
          typeof event.data !== 'string' ||
          ++frames > 40 ||
          (bytes += event.data.length) > 4000000
        )
          return finish({ status: 'invalid_response' });
        try {
          const payload = parseProviderJson(event.data);
          const events = Array.isArray(payload) ? payload : [payload];
          if (
            events.length > 10000 ||
            events.some(
              (e) =>
                !e ||
                typeof e !== 'object' ||
                typeof e.type !== 'string' ||
                !e.data ||
                typeof e.data !== 'object',
            )
          )
            return finish({ status: 'invalid_response' });
          for (const event of events) {
            const id = String(event.data.matchId ?? '');
            if (event.type === 'lobbyAdded' && /^[1-9]\d{0,11}$/.test(id) && tracked.size < 16)
              tracked.add(id);
          }
          snapshot.consume(payload, tracked);
          if (!receivedSnapshot) {
            receivedSnapshot = true;
            settleTimer = setTimeout(() => {
              const observations = [...tracked]
                .map((id) => snapshot.read(id))
                .filter((o): o is LobbyObservation => o !== null);
              if (tracked.size && !observations.length) finish({ status: 'invalid_response' });
              else finish({ status: 'ok', value: observations });
            }, 2200);
          }
        } catch {
          finish({ status: 'invalid_response' });
        }
      });
    } catch {
      finish({ status: 'unavailable' });
    }
  });
}
