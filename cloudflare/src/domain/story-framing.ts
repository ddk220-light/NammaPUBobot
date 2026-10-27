import type { StoryCandidate } from './story-candidates';
import { joinNames } from './story-phrases';
const teamName = (i: number) => (i === 0 ? 'Alpha' : 'Beta');
export function subjectSide(c: StoryCandidate, rosters: string[][]) {
  const d = c.data,
    id =
      c.type === 'h2h'
        ? d.winner
        : c.type === 'deadlock'
          ? d.ids[0]
          : ['form', 'mate_wr'].includes(c.type)
            ? d.p
            : null;
  return id ? rosters.findIndex((t) => t.includes(id)) : d.team_idx;
}
export function storyFrame(
  c: StoryCandidate,
  rosters: string[][],
  nick: Record<string, string>,
  choose: (xs: string[]) => string,
  won?: boolean,
): string {
  const name = (id: string) => `**${nick[id] ?? 'someone'}**`;
  const rest = (side: number) => {
    const ids = rosters[side].filter((id) => !c.players.includes(id));
    return ids.length > 2 ? `the rest of ${teamName(side)}` : joinNames(ids.map(name));
  };
  const sentence = (s: string) => s[0]?.toUpperCase() + s.slice(1);
  if (c.teams.length !== 1) {
    const ids = c.type === 'h2h' ? [c.data.winner, c.data.loser] : [...c.players].sort();
    const backers = ids.map((id) => {
      const side = rosters.findIndex((t) => t.includes(id));
      return side < 0 ? null : { id, who: rest(side), team: teamName(side) };
    });
    if (backers.length === 2 && backers.every((x) => x?.who)) {
      const [a, b] = backers as { id: string; who: string; team: string }[];
      const short = (x: typeof a) => (x.who === `the rest of ${x.team}` ? x.team : x.who);
      if (won !== undefined) {
        const subject = c.type === 'h2h' ? c.data.winner : c.data.ids[0],
          first = ids.indexOf(subject);
        const sa = short(backers[first]!),
          sb = short(backers[1 - first]!);
        return choose(
          won
            ? [`${sa} were right to trust it.`, `${sb} never found one.`]
            : [`${sb} had the last word.`, `${sb} settled it.`],
        );
      }
      return choose([
        `${short(a)} behind ${name(a.id)}, ${short(b)} behind ${name(b.id)}.`,
        c.type === 'h2h'
          ? `Does ${short(b)} have an answer for ${name(a.id)}?`
          : `${short(a)} and ${short(b)} both fancy their chances tonight.`,
      ]);
    }
    return choose(
      won !== undefined
        ? ['Their teammates had their own night.', 'The other six were along for the ride.']
        : ['Do their teammates get a say?', 'Six other players would like a word.'],
    );
  }
  const side = c.teams[0],
    who = rest(side),
    team = teamName(side);
  if (won !== undefined)
    return choose(
      !who
        ? [`All of ${team}, in it together.`, `That was the whole of ${team}.`]
        : won
          ? [`${sentence(who)} got to enjoy it.`, `A good night to be ${who}.`]
          : [`${sentence(who)} went down with them.`, `A rough one for ${who} too.`],
    );
  if (!who) return choose([`That is the whole of ${team}.`, `All of ${team}, back together.`]);
  const positive =
    c.type === 'mate_wr'
      ? c.data.kind === 'best'
      : ['perfect', 'mate', 'trio', 'lineup', 'form'].includes(c.type)
        ? !!c.data.won
        : true;
  return choose(
    positive
      ? [`${sentence(who)} along for the ride.`, `Good day to be ${who}.`]
      : [`${sentence(who)} inherit the problem.`, `Good luck to ${who}.`],
  );
}
