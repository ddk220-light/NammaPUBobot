import { storyCandidates, selectStories, seededRandom } from './story-candidates';
import { teasePhrase, payoffPhrase } from './story-phrases';
import { storyFrame, subjectSide } from './story-framing';
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
  const random = seededRandom(`${m.id}:${m.rosterVersion ?? 0}`),
    choose = (xs: string[]) => xs[Math.floor(random() * xs.length)];
  const rosters = m.teams.map((t) => t.map((p) => p.id)),
    nick = Object.fromEntries(m.roster.map((p) => [p.id, mention(p.id)]));
  const candidates = selectStories(storyCandidates(m, history), random);
  m.storyFacts = [];
  m.storyContext = { version: 1, rosters, candidates, seed: `${m.id}:${m.rosterVersion ?? 0}` };
  const streaks: string[] = [];
  for (const [side, team] of m.teams.entries()) {
    const top = Math.max(...team.map((p) => p.streak));
    if (top < 3) continue;
    const leaders = team.filter((p) => p.streak === top);
    streaks.push(
      `**${side ? 'Beta' : 'Alpha'}:** ${leaders.map((p) => mention(p.id)).join(', ')} — **${top}** consecutive wins${leaders.length > 1 ? ' (tied)' : ''}`,
    );
  }
  const top = Math.max(...m.roster.map((p) => p.streak)),
    leaders = m.roster.filter((p) => p.streak === top);
  if (top >= 3 && leaders.length === 1) streaks.push(`👑 ${streakLine(leaders[0])}`);
  return [
    ...(streaks.length ? [`**🔥 Current win streaks**\n${streaks.join('\n')}`] : []),
    ...candidates.map((c) => teasePhrase(c, nick, storyFrame(c, rosters, nick, choose), choose)),
  ];
}
export function afterStories(m: Match, rated: Player[]): string[] {
  if (!m.storyContext) return legacyAfterStories(m, rated);
  if (m.state !== 'finished') return [];
  if (m.result === 'draw') return ['Evenly matched — this one ends in a draw.'];
  const ctx = m.storyContext,
    rosters = m.teams.map((t) => t.map((p) => p.id));
  if (
    rosters.some(
      (team, i) =>
        team.length !== ctx.rosters[i].length || team.some((id) => !ctx.rosters[i].includes(id)),
    )
  )
    return [];
  const random = seededRandom(`${ctx.seed}:payoff`),
    choose = (xs: string[]) => xs[Math.floor(random() * xs.length)],
    nick = Object.fromEntries(m.roster.map((p) => [p.id, mention(p.id)]));
  return ctx.candidates.map((c) => {
    const won = subjectSide(c, rosters) === (m.result === 'alpha' ? 0 : 1);
    return payoffPhrase(c, nick, storyFrame(c, rosters, nick, choose, won), choose, won);
  });
}
function legacyAfterStories(m: Match, rated: Player[]): string[] {
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
