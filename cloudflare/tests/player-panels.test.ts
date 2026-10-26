import { describe, expect, it } from 'vitest';
import type { Queue } from '../src/domain/config';
import type { Match } from '../src/domain/play';
import type { Player } from '../src/domain/rating';
import { substitutionPanel } from '../src/discord/player-panels';

const player = (n: number): Player => ({
  id: String(100000000000000100n + BigInt(n)),
  name: `Player ${n}`,
  rating: 1000,
  deviation: 200,
  wins: 0,
  losses: 0,
  draws: 0,
  streak: 0,
});
const match = (count = 4): Match => {
  const roster = Array.from({ length: count }, (_, n) => player(n));
  return {
    id: '11111111-1111-4111-8111-111111111111',
    queue: { name: 'Pickup' } as Queue,
    gameName: 'Custom game',
    roster,
    teams: [roster.slice(0, count / 2), roster.slice(count / 2)],
    captains: [roster[0].id, roster[count / 2].id],
    ready: [],
    state: 'playing',
    result: null,
    agreements: {},
    createdAt: 0,
    deadline: null,
    closedAt: null,
    version: 12,
    changes: [],
    subRequests: [],
  };
};

describe('private substitution panels', () => {
  it('gives a match player explicit request intent and self replacement, never a volunteer action', () => {
    const m = match(),
      panel = substitutionPanel(m, m.roster[1].id);
    expect(panel.components).toHaveLength(1);
    expect(panel.components[0].components.map((c) => c.custom_id)).toEqual([
      `m:${m.id}:${m.version}:request`,
      `m:${m.id}:${m.version}:subauto`,
    ]);
    expect(panel.content).toContain('**Alpha**');
    expect(panel.content).toContain('keep your place');
    expect(panel.content).toContain('rebalances both teams');
  });

  it('uses cancel intent for an open request, instead of another toggle', () => {
    const m = match();
    m.subRequests = [m.roster[1].id];
    const panel = substitutionPanel(m, m.roster[1].id);
    expect(panel.components[0].components[0].custom_id).toBe(`m:${m.id}:${m.version}:cancel`);
    expect(panel.content).toContain('request is open');
  });

  it('offers spectators only current requested places, with the original team and check-in requirement', () => {
    const m = match();
    m.state = 'check_in';
    m.subRequests = [m.roster[1].id, m.roster[2].id, m.roster[1].id, player(99).id];
    const panel = substitutionPanel(m, player(98).id),
      menu = panel.components[0].components[0];
    expect(panel.components).toHaveLength(1);
    expect(menu.type).toBe(3);
    if (menu.type !== 3) throw new Error('Expected a player choice.');
    expect(menu.custom_id).toBe(`m:${m.id}:${m.version}:take`);
    expect(menu.options.map((o) => o.value)).toEqual([m.roster[1].id, m.roster[2].id]);
    expect(menu.options.map((o) => o.description)).toEqual(['Alpha', 'Beta · Captain']);
    expect(panel.content).toContain('join their team and need to check in');
    expect(panel.content).not.toContain('rebalances');
  });

  it('does not offer a substitute action to spectators when no one requested one', () => {
    const panel = substitutionPanel(match(), player(99).id);
    expect(panel.components).toEqual([]);
    expect(panel.content).toContain('No one needs a sub');
  });

  it('offers captains other roster targets and administrators the full current roster', () => {
    const m = match(),
      captain = substitutionPanel(m, m.captains[0]),
      admin = substitutionPanel(m, player(99).id, true);
    const captainMenu = captain.components[1].components[0],
      adminMenu = admin.components[0].components[0];
    if (captainMenu.type !== 3 || adminMenu.type !== 3) throw new Error('Expected player choices.');
    expect(captainMenu.custom_id).toBe(`m:${m.id}:${m.version}:auto`);
    expect(captainMenu.options.map((o) => o.value)).toEqual(m.roster.slice(1).map((p) => p.id));
    expect(adminMenu.options.map((o) => o.value)).toEqual(m.roster.map((p) => p.id));
    expect(admin.content).toContain('rebalances both teams');
  });

  it('removes every action for every closed match state, including for administrators', () => {
    for (const state of ['finished', 'aborted', 'check_in_failed', 'undone'] as const) {
      const m = match();
      m.state = state;
      m.subRequests = m.roster.map((p) => p.id);
      expect(substitutionPanel(m, m.captains[0], true).components).toEqual([]);
    }
  });

  it('keeps maximum-size rosters within Discord rows, option and custom ID limits without mutating the match', () => {
    const m = match(16);
    m.roster.forEach((p) => {
      p.name = 'P'.repeat(80);
    });
    m.subRequests = m.roster.map((p) => p.id);
    const before = JSON.stringify(m),
      panel = substitutionPanel(m, player(99).id, true);
    expect(JSON.stringify(m)).toBe(before);
    expect(panel.content.length).toBeLessThanOrEqual(2000);
    expect(panel.components.length).toBeLessThanOrEqual(5);
    for (const row of panel.components) {
      expect(row.type).toBe(1);
      expect(row.components.length).toBeLessThanOrEqual(5);
      for (const c of row.components) {
        expect(c.custom_id!.length).toBeLessThanOrEqual(100);
        if (c.type === 3) {
          expect(row.components).toHaveLength(1);
          expect(c.min_values).toBe(1);
          expect(c.max_values).toBe(1);
          expect(c.options.length).toBeLessThanOrEqual(25);
          expect(c.options.every((o) => o.label.length <= 100)).toBe(true);
          expect(new Set(c.options.map((o) => o.value)).size).toBe(c.options.length);
        }
      }
    }
    expect(panel.content).not.toContain(m.id);
    expect(panel.content).not.toMatch(/\/sub|\/add|\/remove/);
  });
});
