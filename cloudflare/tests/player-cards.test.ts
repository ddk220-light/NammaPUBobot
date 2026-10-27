import { describe, expect, it } from 'vitest';
import { emptyModules, type Queue } from '../src/domain/config';
import type { Match } from '../src/domain/play';
import type { Player } from '../src/domain/rating';
import { matchCard, queueCard, agreementCard, type Card } from '../src/discord/cards';

const queue: Queue = {
  id: '81da571b-6a4a-4e5d-8e32-f4050df35a2d',
  gameId: '4daebc0a-55e8-4d56-a17d-d94913644910',
  channelId: '706320010368516200',
  name: 'Pickup',
  size: 4,
  enabled: true,
  checkIn: { enabled: true, seconds: 60 },
  teamMethod: 'balanced',
  ratingEngine: 'aoe2',
  modules: emptyModules,
};
const player = (n: number): Player => ({
  id: String(100000000000000100n + BigInt(n)),
  name: `Player ${n}`,
  rating: 1000 + n * 100,
  deviation: 200,
  wins: 0,
  losses: 0,
  draws: 0,
  streak: 0,
});
function match(state: Match['state'] = 'check_in'): Match {
  const roster = Array.from({ length: 4 }, (_, n) => player(n));
  return {
    id: 'b17218ed-cb66-4fd6-935f-2380ce1c65f2',
    queue,
    gameName: 'Friends RTS',
    roster,
    teams: [
      [roster[0], roster[1]],
      [roster[2], roster[3]],
    ],
    captains: [roster[1].id, roster[3].id],
    ready: [roster[0].id],
    state,
    result: null,
    agreements: {},
    createdAt: 1_800_000_000_000,
    deadline: state === 'check_in' ? 1_800_000_060_000 : null,
    closedAt: null,
    version: 3,
    changes: [],
  };
}
const buttons = (card: Card) => card.components.flatMap((row) => row.components);
const visible = (card: Card) =>
  card.embeds
    .map((embed) => `${embed.title}\n${embed.description}\n${embed.footer?.text ?? ''}`)
    .join('\n');

describe('player-facing cards', () => {
  it('makes each queue directly joinable and leaveable without exposing its identity', () => {
    const card = queueCard(queue, [player(0)]);
    expect(card.embeds[0].title).toContain('Pickup · 1/4');
    expect(buttons(card)).toMatchObject([
      { custom_id: `q:${queue.id}:join`, disabled: false },
      { custom_id: `q:${queue.id}:leave` },
    ]);
    expect(visible(card)).not.toContain(queue.id);
    expect(visible(card)).not.toMatch(/\/add|\/remove|starts automatically/i);
    expect(card.allowed_mentions).toEqual({ parse: [] });
  });

  it('keeps leaving available when a queue is paused and bounds large waiting lists', () => {
    const card = queueCard(
      { ...queue, enabled: false },
      Array.from({ length: 100 }, (_, n) => player(n)),
    );
    expect(buttons(card)[0]).toMatchObject({ disabled: true });
    expect(buttons(card)[1]).not.toHaveProperty('disabled', true);
    expect(card.embeds[0].title).toContain('100/4');
    expect(card.embeds[0].description).toContain('84 more waiting');
    expect(card.embeds[0].description.match(/<@\d+>/g)).toHaveLength(16);
    expect(card.embeds[0].description.length).toBeLessThan(4096);
  });

  it('shows readiness on the teams once, retaining the deadline and captain labels', () => {
    const m = match();
    const card = matchCard(m);
    expect(card.embeds[0].description).toContain('1/4 ready');
    expect(card.embeds[0].description).toContain(`<t:${m.deadline! / 1000}:R>`);
    expect(card.embeds[0].description).toContain(`✓ <@${m.roster[0].id}>`);
    for (const p of m.roster)
      expect(card.embeds[0].description.split(`<@${p.id}>`)).toHaveLength(2);
    expect(card.embeds[0].description.match(/captain/g)).toHaveLength(2);
    expect(buttons(card).map((button) => button.custom_id)).toEqual([
      `m:${m.id}:3:ready`,
      `m:${m.id}:3:back`,
    ]);
  });

  it('keeps captain agreements in a separate card with one contextual action', () => {
    const m = match('playing');
    m.proposal = {
      id: crypto.randomUUID(),
      requester: m.captains[0],
      opposing: m.captains[1],
      kind: 'draw',
    };
    const main = matchCard(m);
    expect(main.embeds[0].description).not.toContain('requested');
    expect(buttons(main).map((b) => (b.type === 2 ? b.label : ''))).toEqual(['Sub me']);
    const card = agreementCard(m);
    expect(card.embeds[0].description).toContain(`<@${m.captains[0]}> requested a draw`);
    expect(card.embeds[0].description).toContain('do you agree?');
    expect(buttons(card).map((b) => (b.type === 2 ? b.label : ''))).toEqual(['Agree to draw']);
    expect(buttons(card).map((b) => b.custom_id)).toEqual([`a:${m.id}:3:${m.proposal.id}:draw`]);
    expect(card.allowed_mentions).toEqual({ parse: [], users: [m.captains[1]] });
    expect(card.components).toHaveLength(1);
  });

  it('uses one result roster with final ratings and deltas, without rejoin actions', () => {
    const m = match('finished');
    m.result = 'alpha';
    m.changes = m.roster.map((p, n) => ({
      userId: p.id,
      before: p.rating,
      after: p.rating + (n < 2 ? 25 : -25),
    }));
    const card = matchCard(m);
    expect(card.embeds[0].title).toContain('Alpha wins');
    expect(card.embeds[0].description).toContain('1025 (+25)');
    expect(card.embeds[0].description).toContain('1175 (-25)');
    for (const p of m.roster)
      expect(card.embeds[0].description.split(`<@${p.id}>`)).toHaveLength(2);
    expect(buttons(card)).toEqual([]);
    expect(visible(card)).not.toContain(m.id);
  });

  it('retains timeout meaning and suppresses an obsolete check-in note after play starts', () => {
    const active = match('playing');
    active.note = 'No-shows replaced from the waiting queue. New players must check in.';
    expect(visible(matchCard(active))).not.toContain('must check in');
    const aborted = match('aborted');
    aborted.note = 'Match timed out without a result.';
    expect(visible(matchCard(aborted))).toContain('No result was reported before the time limit');
    expect(visible(matchCard(aborted))).toContain('No ratings changed');
  });

  it('keeps readiness visible after a failed check-in so players know who returned to the queue', () => {
    const m = match('check_in_failed');
    const card = matchCard(m);
    expect(card.embeds[0].description).toContain(`✓ <@${m.ready[0]}>`);
    expect(card.embeds[0].description).toContain(`○ <@${m.roster[1].id}>`);
    expect(card.embeds[0].description).not.toContain('Check in to keep your place');
    expect(buttons(card)).toEqual([]);
  });

  it.each(['check_in', 'playing', 'finished', 'aborted', 'check_in_failed', 'undone'] as const)(
    'keeps %s payloads within Discord limits and preserves hidden match identity',
    (state) => {
      const m = match(state);
      const card = matchCard(m);
      expect(card.components.length).toBeLessThanOrEqual(5);
      for (const row of card.components) {
        expect(row.components.length).toBeLessThanOrEqual(5);
        for (const component of row.components) {
          expect(component.custom_id?.length).toBeLessThanOrEqual(100);
          expect(component.custom_id).toContain(m.id);
        }
      }
      expect(visible(card)).not.toMatch(/\/[a-z]+|[0-9a-f]{8}-[0-9a-f-]{27}/i);
      expect(card.embeds[0].description.length).toBeLessThanOrEqual(4096);
      expect(card.embeds[0].title.length).toBeLessThanOrEqual(256);
      expect(card.allowed_mentions).toEqual({ parse: [] });
    },
  );
});
