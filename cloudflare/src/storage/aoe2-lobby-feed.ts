import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';
import { parseProviderJson, retryDelay } from '../games/transport';
import {
  AoE2LobbySnapshot,
  aoe2OngoingFeedUrl,
  parseAoE2OngoingSnapshot,
} from '../games/aoe2-feed';
import type { LobbyObservation, ProviderResult } from '../games/types';
import { normalizeLobbyName } from '../domain/lobby';
/** One outbound feed per application, leased only while tracked matches need occupancy. */
export class AoE2LobbyFeed extends DurableObject<Env> {
  private socket: WebSocket | null = null;
  private snapshot = new AoE2LobbySnapshot();
  private starting: Promise<void> | null = null;
  private leases = new Map<string, number>();
  private reconnectAt = 0;
  private connectedAt = 0;
  private activeMs = 0;
  private browseStarting: Promise<ProviderResult<LobbyObservation[]>> | null = null;
  private ongoingStarting: Promise<ProviderResult<LobbyObservation[]>> | null = null;
  private ongoingCache: {
    until: number;
    result: ProviderResult<LobbyObservation[]>;
  } | null = null;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      this.leases = new Map((await ctx.storage.get<[string, number][]>('leases')) ?? []);
      this.prune();
    });
  }
  async query(id: string) {
    if (!/^[1-9]\d{0,11}$/.test(id)) throw Error('Invalid lobby ID');
    const healthy = await this.subscribe(id, this.snapshot.has(id));
    return {
      observation: healthy ? this.snapshot.read(id) : null,
      healthy,
      tracked: this.leases.size,
      activeMs: this.activeMs + (this.connectedAt ? Date.now() - this.connectedAt : 0),
    };
  }
  async discover(name: string) {
    if (!name.trim() || name.length > 80) throw Error('Invalid lobby name');
    const healthy = await this.subscribe(`name:${normalizeLobbyName(name)}`, false);
    return { healthy, observations: healthy ? this.snapshot.named(name) : [] };
  }
  /** Browse briefly leases the existing waiting feed; it never opens a community socket. */
  async browse(): Promise<ProviderResult<LobbyObservation[]>> {
    if (!this.browseStarting) {
      this.browseStarting = this.captureWaiting().finally(() => {
        this.browseStarting = null;
      });
    }
    return this.browseStarting;
  }
  private async captureWaiting(): Promise<ProviderResult<LobbyObservation[]>> {
    const deadline = Date.now() + 10000;
    const healthy = await this.subscribe('browse:*', false, 30000);
    if (!healthy) return { status: 'unavailable' };
    // Lobby and slot events may arrive separately. Retain the existing two-second baseline guard.
    while (Date.now() < deadline) {
      if (!this.socket) return { status: 'unavailable' };
      const value = this.snapshot.browse();
      if (value !== null) return { status: 'ok', value };
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return { status: 'delayed', retryAfterMs: 2000 };
  }
  /** All communities share one short-lived capture and its original observation timestamps. */
  async browseOngoing(): Promise<ProviderResult<LobbyObservation[]>> {
    if (this.ongoingCache && this.ongoingCache.until > Date.now()) return this.ongoingCache.result;
    if (!this.ongoingStarting) {
      this.ongoingStarting = this.captureOngoing()
        .then((result) => {
          const ttl = result.status === 'ok' ? 30000 : Math.max(5000, result.retryAfterMs ?? 5000);
          this.ongoingCache = { until: Date.now() + ttl, result };
          return result;
        })
        .finally(() => {
          this.ongoingStarting = null;
        });
    }
    return this.ongoingStarting;
  }
  private captureOngoing(): Promise<ProviderResult<LobbyObservation[]>> {
    return new Promise((resolve) => {
      const controller = new AbortController();
      let ws: WebSocket | null = null,
        completed = false,
        receivedBytes = 0;
      const finish = (result: ProviderResult<LobbyObservation[]>) => {
        if (completed) return;
        completed = true;
        clearTimeout(timeout);
        if (ws) {
          ws.removeEventListener('message', message);
          ws.removeEventListener('close', closed);
          ws.removeEventListener('error', closed);
          try {
            ws.close(1000, 'Snapshot complete');
          } catch {}
        }
        controller.abort();
        resolve(result);
      };
      const closed = () => finish({ status: 'unavailable' });
      const message = (event: MessageEvent) => {
        if (completed) return;
        if (typeof event.data !== 'string' || event.data.length > 2 * 1024 * 1024) {
          finish({ status: 'invalid_response' });
          return;
        }
        receivedBytes += new TextEncoder().encode(event.data).byteLength;
        if (receivedBytes > 2 * 1024 * 1024) {
          finish({ status: 'invalid_response' });
          return;
        }
        if (event.data === 'pong') return;
        try {
          finish({
            status: 'ok',
            value: parseAoE2OngoingSnapshot(parseProviderJson(event.data)),
          });
        } catch {
          finish({ status: 'invalid_response' });
        }
      };
      // This deadline covers the handshake AND first snapshot. The capture never stays live.
      const timeout = setTimeout(
        () => finish({ status: ws ? 'delayed' : 'unavailable', retryAfterMs: 5000 }),
        10000,
      );
      void (async () => {
        try {
          const response = await fetch(aoe2OngoingFeedUrl, {
            headers: { Upgrade: 'websocket' },
            signal: controller.signal,
            redirect: 'manual',
          });
          const socket = response.webSocket;
          if (!socket) {
            await response.body?.cancel().catch(() => {});
            finish({
              status: [401, 403].includes(response.status)
                ? 'authentication_required'
                : response.status === 429
                  ? 'throttled'
                  : response.ok
                    ? 'invalid_response'
                    : 'unavailable',
              ...(response.status === 429 || response.headers.has('Retry-After')
                ? { retryAfterMs: retryDelay(response.headers.get('Retry-After')) ?? 120000 }
                : {}),
            });
            return;
          }
          // A fetch implementation may resolve after abort. Do not retain a late socket.
          if (completed) {
            socket.accept();
            socket.close(1000, 'Snapshot expired');
            return;
          }
          ws = socket;
          ws.addEventListener('message', message);
          ws.addEventListener('close', closed);
          ws.addEventListener('error', closed);
          ws.accept();
        } catch {
          finish({ status: 'unavailable' });
        }
      })();
    });
  }
  private async subscribe(id: string, alreadyObserved: boolean, leaseMs = 120000) {
    this.prune();
    if (!this.leases.has(id) && this.leases.size >= 200) return false;
    const added = !this.leases.has(id);
    this.leases.set(id, Date.now() + leaseMs);
    await this.ctx.storage.put('leases', [...this.leases]);
    // The initial snapshot is the only complete baseline. A new subscription reconnects
    // the shared stream, then all consumers await fresh lobbyAdded + slot events.
    if (added && this.socket && !alreadyObserved) this.disconnect();
    if (!this.socket && !this.starting && Date.now() >= this.reconnectAt) {
      this.starting = this.openFeed().finally(() => {
        this.starting = null;
      });
      await this.starting.catch(() => {
        this.reconnectAt = Date.now() + 30000;
      });
    }
    await this.ctx.storage.setAlarm(Date.now() + 30000);
    return !!this.socket;
  }
  status() {
    return {
      healthy: !!this.socket,
      tracked: this.leases.size,
      leaseUntil: Math.max(0, ...this.leases.values()),
      activeMs: this.activeMs + (this.connectedAt ? Date.now() - this.connectedAt : 0),
      observedAt: Date.now(),
    };
  }
  private prune() {
    for (const [id, until] of this.leases) if (until <= Date.now()) this.leases.delete(id);
  }
  private disconnect() {
    const ws = this.socket;
    this.socket = null;
    this.snapshot.clear();
    if (this.connectedAt) this.activeMs += Date.now() - this.connectedAt;
    this.connectedAt = 0;
    try {
      ws?.close(1000, 'Refresh or idle');
    } catch {}
  }
  private async openFeed() {
    // Bound only the handshake. An AbortSignal.timeout left attached to the
    // upgraded fetch also aborts the live WebSocket after that timeout.
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    let response: Response;
    try {
      response = await fetch('https://socket.aoe2companion.com/listen?handler=lobbies', {
        headers: { Upgrade: 'websocket' },
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }
    const ws = response.webSocket;
    if (!ws) throw Error('Lobby feed unavailable');
    this.socket = ws;
    this.snapshot.clear();
    this.connectedAt = Date.now();
    ws.accept();
    ws.addEventListener('message', (event) => {
      if (this.socket !== ws) return;
      if (typeof event.data !== 'string' || event.data.length > 4000000) {
        this.disconnect();
        return;
      }
      try {
        if (event.data !== 'pong')
          this.snapshot.consume(parseProviderJson(event.data), new Set(this.leases.keys()));
      } catch {
        this.disconnect();
        this.reconnectAt = Date.now() + 30000;
      }
    });
    const closed = () => {
      if (this.socket === ws) {
        this.disconnect();
        this.reconnectAt = Date.now() + 5000;
      }
    };
    ws.addEventListener('close', closed);
    ws.addEventListener('error', closed);
  }
  async alarm() {
    this.prune();
    await this.ctx.storage.put('leases', [...this.leases]);
    if (!this.leases.size) {
      this.disconnect();
      return;
    }
    if (this.socket && Date.now() - (this.snapshot.lastFrameAt || this.connectedAt) > 90000)
      this.disconnect();
    if (!this.socket && !this.starting && Date.now() >= this.reconnectAt) {
      this.starting = this.openFeed().finally(() => {
        this.starting = null;
      });
      await this.starting.catch(() => {
        this.reconnectAt = Date.now() + 30000;
      });
    }
    await this.ctx.storage.setAlarm(Date.now() + 30000);
  }
}
