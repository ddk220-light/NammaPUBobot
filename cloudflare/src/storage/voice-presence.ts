import { DurableObject } from 'cloudflare:workers';
import type { Env } from '../env';
import { PresenceSnapshot } from '../domain/presence';

/** Optional shared outbound Gateway client. Never started by the matchmaking object. */
export class VoicePresence extends DurableObject<Env> {
  private socket: WebSocket | null = null;
  private snapshot = new PresenceSnapshot();
  private starting: Promise<void> | null = null;
  private seq: number | null = null;
  private session: string | null = null;
  private resumeUrl = 'https://gateway.discord.gg';
  private heartbeat: ReturnType<typeof setInterval> | undefined;
  private awaitingAck = false;
  private identifiedAt = 0;
  private wantedUntil = 0;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
  }
  async query(guild: string) {
    this.wantedUntil = Date.now() + 3600000; // Hour without requests stops the optional idle connection.
    if (!this.socket && !this.starting) {
      this.starting = this.openGateway().finally(() => {
        this.starting = null;
      });
      await this.starting.catch(() => {});
    }
    return {
      members: this.snapshot.members(guild),
      healthy: this.snapshot.healthy,
      scope: 'voice-only',
      idleStopAt: this.wantedUntil,
    };
  }
  private async openGateway() {
    const blocked = (await this.ctx.storage.get<number>('reconnectAt')) ?? 0;
    if (blocked > Date.now()) return;
    if (!this.env.DISCORD_BOT_TOKEN) throw Error('Voice adapter is not configured.');
    if (!this.session) {
      const response = await fetch('https://discord.com/api/v10/gateway/bot', {
        headers: { Authorization: `Bot ${this.env.DISCORD_BOT_TOKEN}` },
        signal: AbortSignal.timeout(3000),
      });
      if (!response.ok) throw Error('Gateway metadata unavailable.');
      const info = (await response.json()) as {
        url: string;
        shards: number;
        session_start_limit: { remaining: number; reset_after: number };
      };
      if (info.shards > 1)
        throw Error('This pilot voice adapter requires reviewed multi-shard configuration.');
      if (!info.session_start_limit.remaining) {
        await this.ctx.storage.put(
          'reconnectAt',
          Date.now() + info.session_start_limit.reset_after,
        );
        return;
      }
      this.resumeUrl = info.url.replace(/^wss:/, 'https:');
      this.seq = null;
      this.snapshot.guilds.clear();
    }
    const response = await fetch(`${this.resumeUrl}/?v=10&encoding=json`, {
      headers: { Upgrade: 'websocket' },
      signal: AbortSignal.timeout(5000),
    });
    const socket = response.webSocket;
    if (!socket) throw Error('Gateway upgrade failed.');
    this.socket = socket;
    socket.accept();
    socket.addEventListener('message', (event) => {
      if (typeof event.data !== 'string' || event.data.length > 1048576) {
        this.disconnect();
        return;
      }
      try {
        const data = JSON.parse(event.data);
        this.consume(data);
      } catch {
        this.disconnect();
      }
    });
    socket.addEventListener('close', (event) => {
      if (this.socket !== socket) return;
      this.snapshot.invalidate();
      this.socket = null;
      clearInterval(this.heartbeat);
      if ([4004, 4010, 4011, 4013, 4014].includes(event.code)) {
        this.ctx.waitUntil(this.ctx.storage.put('reconnectAt', Date.now() + 3600000));
        this.session = null;
        this.seq = null;
        return;
      }
      if ([4007, 4009].includes(event.code)) {
        this.session = null;
        this.seq = null;
        this.snapshot.guilds.clear();
      }
      if (this.wantedUntil > Date.now())
        this.ctx.waitUntil(this.ctx.storage.setAlarm(Date.now() + 5000));
    });
    socket.addEventListener('error', () => this.disconnect());
    await this.ctx.storage.setAlarm(Date.now() + 60000);
  }
  private send(op: number, d: unknown) {
    this.socket?.send(JSON.stringify({ op, d }));
  }
  private consume(event: { op: number; d: any; s: number | null; t: string | null }) {
    if (typeof event.s === 'number') {
      if (this.seq !== null && event.s > this.seq + 1) {
        this.disconnect();
        return;
      }
      this.seq = event.s;
    }
    if (event.op === 10) {
      const interval = event.d?.heartbeat_interval;
      if (!Number.isFinite(interval) || interval < 1000) throw Error();
      clearInterval(this.heartbeat);
      this.awaitingAck = false;
      this.heartbeat = setInterval(() => {
        if (this.awaitingAck) {
          this.disconnect();
          return;
        }
        this.awaitingAck = true;
        this.send(1, this.seq);
      }, interval);
      if (this.session)
        this.send(6, {
          token: this.env.DISCORD_BOT_TOKEN,
          session_id: this.session,
          seq: this.seq,
        });
      else {
        if (Date.now() - this.identifiedAt < 5000) {
          this.disconnect();
          return;
        }
        this.identifiedAt = Date.now();
        this.send(2, {
          token: this.env.DISCORD_BOT_TOKEN,
          intents: 129,
          properties: { os: 'linux', browser: 'community-games', device: 'community-games' },
        });
      }
    } else if (event.op === 11) {
      this.awaitingAck = false;
    } else if (event.op === 1) {
      this.send(1, this.seq);
    } else if (event.op === 7) {
      this.disconnect();
    } else if (event.op === 9) {
      if (!event.d) {
        this.session = null;
        this.seq = null;
        this.snapshot.guilds.clear();
      }
      this.disconnect();
    } else if (event.op === 0) {
      const data = event.d;
      if (event.t === 'READY') {
        this.session = data.session_id;
        this.resumeUrl = String(data.resume_gateway_url).replace(/^wss:/, 'https:');
        this.snapshot.healthy = true;
      } else if (event.t === 'RESUMED') this.snapshot.healthy = true;
      else if (event.t === 'GUILD_CREATE' && !data.unavailable)
        this.snapshot.initialize(data.id, data.voice_states ?? []);
      else if (event.t === 'GUILD_DELETE') this.snapshot.guilds.delete(data.id);
      else if (event.t === 'VOICE_STATE_UPDATE')
        this.snapshot.update(data.guild_id, data.user_id, data.channel_id);
    }
  }
  private disconnect() {
    this.snapshot.invalidate();
    clearInterval(this.heartbeat);
    const socket = this.socket;
    this.socket = null;
    try {
      socket?.close(4000, 'Reconnect');
    } catch {}
    if (this.wantedUntil > Date.now())
      this.ctx.waitUntil(this.ctx.storage.setAlarm(Date.now() + 5000));
  }
  async stop() {
    this.wantedUntil = 0;
    this.snapshot.invalidate();
    clearInterval(this.heartbeat);
    const socket = this.socket;
    this.socket = null;
    try {
      socket?.close(1000, 'Voice adapter idle');
    } catch {}
    this.socket = null;
    this.session = null;
    this.seq = null;
    await this.ctx.storage.deleteAlarm();
  }
  async alarm() {
    if (Date.now() >= this.wantedUntil) {
      await this.stop();
      return;
    }
    if (!this.socket) await this.openGateway().catch(() => {});
    await this.ctx.storage.setAlarm(Date.now() + 60000);
  }
}
