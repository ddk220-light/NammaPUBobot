import type { Match } from './play';
import type { Player } from './rating';
const mention = (id: string) => `<@${id}>`;
export function streakLine(player: Player) {
  const phrase =
    player.streak >= 10
      ? 'is **beyond GODLIKE**, someone kill them!!'
      : player.streak === 9
        ? 'is **GODLIKE**!'
        : player.streak === 8
          ? 'is on a **Monster Kill** streak!'
          : player.streak === 7
            ? 'is **Wicked Sick**!'
            : player.streak === 6
              ? 'is **Unstoppable**!'
              : player.streak === 5
                ? 'is on a **Mega Kill** streak!'
                : player.streak === 4
                  ? 'is **Dominating**!'
                  : 'is on a **Killing Spree**!';
  return `${mention(player.id)} ${phrase} · ${player.streak} consecutive wins.`;
}
export function beforeStories(m: Match, history: Match[]): string[] {
  const lines: string[] = [];
  m.storyFacts = [];
  for (const [side, team] of m.teams.entries()) {
    const top = Math.max(...team.map((p) => p.streak));
    if (top < 3) continue;
    const leaders = team.filter((p) => p.streak === top);
    lines.push(
      leaders.length === 1
        ? `${side === 0 ? 'Alpha' : 'Beta'}: ${streakLine(leaders[0])}`
        : `${side === 0 ? 'Alpha' : 'Beta'}: ${leaders.map((p) => mention(p.id)).join(' and ')} share a ${top}-win streak.`,
    );
  }
  let rivalry: { a: string; b: string; wins: number; count: number } | undefined;
  for (const a of m.teams[0])
    for (const b of m.teams[1]) {
      const meetings = history.filter(
        (h) =>
          h.state === 'finished' &&
          h.result !== 'draw' &&
          h.roster.some((p) => p.id === a.id) &&
          h.roster.some((p) => p.id === b.id) &&
          h.teams.some((t) => t.some((p) => p.id === a.id) && !t.some((p) => p.id === b.id)),
      );
      if (meetings.length < 5) continue;
      const aWins = meetings.filter((h) =>
        h.teams[h.result === 'alpha' ? 0 : 1].some((p) => p.id === a.id),
      ).length;
      const wins = Math.max(aWins, meetings.length - aWins);
      if (wins / meetings.length < 0.7) continue;
      if (!rivalry || meetings.length > rivalry.count)
        rivalry = {
          a: aWins >= meetings.length - aWins ? a.id : b.id,
          b: aWins >= meetings.length - aWins ? b.id : a.id,
          wins,
          count: meetings.length,
        };
    }
  if (rivalry) {
    lines.push(
      `${mention(rivalry.a)} leads ${mention(rivalry.b)} ${rivalry.wins}–${rivalry.count - rivalry.wins} in their ${rivalry.count} recent meetings. Time to change that?`,
    );
    m.storyFacts.push({
      kind: 'rivalry',
      players: [rivalry.a, rivalry.b],
      side: m.teams[0].some((p) => p.id === rivalry!.a) ? 0 : 1,
      wins: rivalry.wins,
      games: rivalry.count,
    });
  }
  for (const [side, team] of m.teams.entries()) {
    if (lines.length >= 4) break;
    const groups =
      team.length >= 3
        ? [
            team.map((p) => p.id),
            ...team.flatMap((p, i) => team.slice(i + 1).map((q) => [p.id, q.id])),
          ]
        : team.length === 2
          ? [team.map((p) => p.id)]
          : [];
    for (const group of groups) {
      const together = history.filter(
        (h) =>
          h.state === 'finished' &&
          h.result !== 'draw' &&
          h.teams.some((t) => group.every((id) => t.some((p) => p.id === id))),
      );
      if (together.length < (group.length >= 3 ? 2 : 5)) continue;
      const won = together.filter((h) =>
        group.every((id) => h.teams[h.result === 'alpha' ? 0 : 1].some((p) => p.id === id)),
      ).length;
      if (won / together.length < 0.8 && won / together.length > 0.2) continue;
      const names = group.map(mention).join(' + ');
      lines.push(
        `${names}: ${won}/${together.length} wins together recently. ${won / together.length >= 0.8 ? 'Can they keep it going?' : 'A chance to turn it around.'}`,
      );
      m.storyFacts.push({
        kind: group.length >= 3 ? 'lineup' : 'duo',
        players: group,
        side: side as 0 | 1,
        wins: won,
        games: together.length,
      });
      break;
    }
  }
  return lines.slice(0, 4);
}
export function afterStories(m: Match, rated: Player[]): string[] {
  if (m.result === 'draw') return ['Evenly matched — this one ends in a draw.'];
  if (m.state !== 'finished') return [];
  const winners = new Set(m.teams[m.result === 'alpha' ? 0 : 1].map((p) => p.id)),
    lines: string[] = [];
  const ended = m.roster
    .filter((p) => !winners.has(p.id) && p.streak >= 3)
    .sort((a, b) => b.streak - a.streak)[0];
  if (ended) lines.push(`${mention(ended.id)}'s ${ended.streak}-win streak ends here.`);
  for (const fact of m.storyFacts ?? []) {
    if (lines.length >= 3) break;
    const won = fact.side === (m.result === 'alpha' ? 0 : 1);
    if (fact.kind === 'rivalry')
      lines.push(
        won
          ? `${mention(fact.players[0])} keeps the edge: ${fact.wins + 1}–${fact.games - fact.wins} against ${mention(fact.players[1])}.`
          : `${mention(fact.players[1])} gets one back against ${mention(fact.players[0])}: ${fact.games - fact.wins + 1}–${fact.wins}.`,
      );
    else
      lines.push(
        `${fact.players.map(mention).join(' + ')} ${won ? 'take another win' : 'drop this one'} — now ${fact.wins + (won ? 1 : 0)}/${fact.games + 1} wins together.`,
      );
  }
  const top = Math.max(0, ...rated.filter((p) => winners.has(p.id)).map((p) => p.streak));
  if (top >= 3) {
    const leaders = rated.filter((p) => winners.has(p.id) && p.streak === top);
    lines.push(
      leaders.length === 1
        ? streakLine(leaders[0])
        : `${leaders.map((p) => mention(p.id)).join(' and ')} keep their ${top}-win streak going.`,
    );
  }
  return lines.slice(0, 4);
}
