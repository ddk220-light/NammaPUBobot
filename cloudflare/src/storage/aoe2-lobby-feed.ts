import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';
import { AoE2LobbySnapshot } from '../games/aoe2-feed';
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
  private async subscribe(id: string, alreadyObserved: boolean) {
    this.prune();
    if (!this.leases.has(id) && this.leases.size >= 200) return false;
    const added = !this.leases.has(id);
    this.leases.set(id, Date.now() + 120000);
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
          this.snapshot.consume(JSON.parse(event.data), new Set(this.leases.keys()));
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
