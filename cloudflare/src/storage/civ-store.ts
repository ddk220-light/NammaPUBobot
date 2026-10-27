import type { Match, Action } from '../domain/play';
import { PlayError } from '../domain/errors';
import type { Card } from '../discord/cards';
import { selectionCatalog, selectionPool } from '../games/selections';
export interface CivRound {
  generation: number;
  rosterVersion: number;
  roster: { id: string; side: number }[];
  options: string[];
  initiator: string;
  openedAt: number;
  closesAt: number;
  status: 'open' | 'closed' | 'invalidated';
  picks: Record<string, number>;
  timedOut: string[];
}
export function createCivTables(sql: SqlStorage) {
  sql.exec('CREATE TABLE IF NOT EXISTS civ_rounds(match_id TEXT PRIMARY KEY,config TEXT NOT NULL)');
  sql.exec(
    'CREATE TABLE IF NOT EXISTS civ_pick_history(match_id TEXT NOT NULL,generation INTEGER NOT NULL,user_id TEXT NOT NULL,channel_id TEXT NOT NULL,selection TEXT NOT NULL,at INTEGER NOT NULL,PRIMARY KEY(match_id,generation,user_id))',
  );
  sql.exec('CREATE INDEX IF NOT EXISTS civ_history_channel ON civ_pick_history(channel_id,at)');
}
export function civCard(m: Match, r: CivRound): Card {
  const closed = r.status !== 'open';
  return {
    content: '',
    allowed_mentions: { parse: [] },
    embeds: [
      {
        title: `${m.queue.name} · Civilization picks`,
        color: 0xb9e77c,
        description: `${r.status === 'open' ? `Choose once; choices stay hidden until everyone picks or <t:${Math.floor(r.closesAt / 1000)}:R>. Random can be shared.` : r.status === 'closed' ? 'Picks revealed. These are suggestions; played civilizations come from the game.' : 'Roster or match changed. This round is invalid; use /civpick redo.'}\n\n${[
          0, 1,
        ]
          .map(
            (side) =>
              `**${side ? 'Beta' : 'Alpha'}**\n${r.roster
                .filter((p) => p.side === side)
                .map(
                  (p) =>
                    `<@${p.id}> · ${closed ? (r.options[r.picks[p.id]] ?? 'Random') + (r.timedOut.includes(p.id) ? ' (timed out)' : '') : p.id in r.picks ? 'Picked ✓' : 'Waiting'}`,
                )
                .join('\n')}`,
          )
          .join('\n\n')}`,
        footer: { text: `Round ${r.generation} · Civilization picks` },
      },
    ],
    components: [0, 5, 10].map((start) => ({
      type: 1,
      components: [...r.options, 'Random'].slice(start, start + 5).map((name, i) => ({
        type: 2,
        style: name === 'Random' ? 2 : 1,
        label: name,
        disabled: closed,
        custom_id: `civ:${m.id}:${r.generation}:${start + i}`,
      })),
    })),
  };
}
export class CivStore {
  constructor(
    private sql: SqlStorage,
    private enqueue: (key: string, channel: string, card: Card) => void,
  ) {}
  get(id: string): CivRound | undefined {
    const row = this.sql
      .exec<{ config: string }>('SELECT config FROM civ_rounds WHERE match_id=?', id)
      .toArray()[0];
    return row && JSON.parse(row.config);
  }
  private save(m: Match, r: CivRound) {
    this.sql.exec('INSERT OR REPLACE INTO civ_rounds VALUES(?,?)', m.id, JSON.stringify(r));
    if (r.status === 'open')
      this.sql.exec(
        'INSERT OR REPLACE INTO deadlines VALUES(?,?,?,?)',
        `civ:${m.id}`,
        'civ',
        m.id,
        r.closesAt,
      );
    else this.sql.exec('DELETE FROM deadlines WHERE key=?', `civ:${m.id}`);
    this.enqueue(`civ:${m.id}`, m.queue.channelId, civCard(m, r));
  }
  sync(m: Match, now = Date.now()) {
    const r = this.get(m.id);
    if (
      !r ||
      r.status === 'invalidated' ||
      (r.status === 'closed' && r.rosterVersion === (m.rosterVersion ?? 0))
    )
      return;
    if (!['playing', 'check_in'].includes(m.state) || r.rosterVersion !== (m.rosterVersion ?? 0))
      r.status = 'invalidated';
    else if (r.closesAt <= now) {
      for (const p of r.roster)
        if (!(p.id in r.picks)) {
          r.picks[p.id] = 12;
          r.timedOut.push(p.id);
        }
      r.status = 'closed';
    } else return;
    this.save(m, r);
  }
  action(m: Match, a: Action, catalogId?: string | null) {
    const catalog = selectionCatalog(catalogId);
    if (!catalog || !m.queue.modules.civs)
      throw new PlayError('Enable civilization picking for this AoE2 queue first.');
    if (!['playing', 'check_in'].includes(m.state)) throw new PlayError('This match is closed.');
    this.sync(m);
    let r = this.get(m.id);
    if (a.kind === 'civ_start') {
      if (r && !a.redo)
        return {
          content:
            r.status === 'invalidated'
              ? 'Use /civpick with redo to start a fresh round.'
              : 'The civilization round is already posted.',
        };
      if (!a.admin && !m.roster.some((p) => p.id === a.actor.id))
        throw new PlayError('Only a match player or administrator can start picks.');
      if (r && !a.admin && r.initiator !== a.actor.id)
        throw new PlayError('Only the original initiator or an administrator can redo picks.');
      if (r && Date.now() - r.openedAt < 30000)
        throw new PlayError('Wait 30 seconds before redoing picks.');
      if (m.roster.length > 8 || m.teams.some((t) => !t.length))
        throw new PlayError(
          'Civilization picking needs two formed teams with at most eight players.',
        );
      const history = this.sql
        .exec<{ selection: string; at: number }>(
          'SELECT selection,at FROM civ_pick_history WHERE channel_id=? AND at>=?',
          m.queue.channelId,
          Date.now() - 86400000,
        )
        .toArray();
      r = {
        generation: (r?.generation ?? 0) + 1,
        rosterVersion: m.rosterVersion ?? 0,
        roster: m.teams.flatMap((t, side) => t.map((p) => ({ id: p.id, side }))),
        options: selectionPool(catalog, history),
        initiator: a.actor.id,
        openedAt: Date.now(),
        closesAt: Math.min(Date.now() + (a.minutes ?? 3) * 60000, m.expiresAt ?? Infinity),
        status: 'open',
        picks: {},
        timedOut: [],
      };
      this.save(m, r);
      this.sql.exec('DELETE FROM civ_pick_history WHERE at<?', Date.now() - 86400000);
      return {
        content:
          'Civilization picking started. Choose on the public card; your choice stays private until the reveal.',
      };
    }
    if (!r || r.generation !== a.generation || r.status !== 'open')
      throw new PlayError('This round has ended or changed. Use the latest civilization card.');
    if (!r.roster.some((p) => p.id === a.actor.id))
      throw new PlayError('Only players in this match can pick.');
    if (a.actor.id in r.picks) throw new PlayError('Your pick is already locked.');
    const choice = a.choice;
    if (choice === undefined || choice < 0 || choice > 12)
      throw new PlayError('Choose a current civilization button.');
    if (choice !== 12 && Object.values(r.picks).includes(choice))
      throw new PlayError('That civilization was already taken. Choose another or Random.');
    r.picks[a.actor.id] = choice;
    if (choice !== 12)
      this.sql.exec(
        'INSERT INTO civ_pick_history VALUES(?,?,?,?,?,?)',
        m.id,
        r.generation,
        a.actor.id,
        m.queue.channelId,
        r.options[choice],
        Date.now(),
      );
    if (r.roster.every((p) => p.id in r.picks)) r.status = 'closed';
    this.save(m, r);
    return { content: `${r.options[choice] ?? 'Random'} locked in.` };
  }
}
