/** Human names for stored values; every place that shows one reads it from here. */
export const valueLabels = {
  teamMethod: {
    captain: 'Captain-based balance',
    balanced: 'Automatic balance',
    random: 'Random teams',
  },
  mapMode: { vote: 'Player voting', random: 'Random selection', direct: 'Captain picks' },
  mapVoting: { plurality: 'One choice each', approval: 'Approve several maps' },
  mapTie: { random: 'Pick one of the tied maps at random', alphabetical: 'First alphabetically' },
  mapNoVotes: { random: 'Pick a map at random', cancel: 'Cancel the vote' },
  visibility: { public: 'Public', members: 'Server members', admins: 'Administrators' },
  earlyStart: { moderators: 'Organizers and moderators', anyone: 'Anyone in the channel' },
  ratingEngine: {
    aoe2: 'AoE2-style Elo',
    glicko2: 'Glicko-2',
    trueskill: 'TrueSkill',
    flat: 'Flat points',
  },
} satisfies Record<string, Record<string, string>>;

/** "1 h 30 min" style text for a number of seconds. */
export function durationText(n: number) {
  if (!Number.isFinite(n)) return '';
  if (n === 0) return '0 seconds';
  const days = Math.floor(n / 86400),
    hours = Math.floor((n % 86400) / 3600),
    minutes = Math.floor((n % 3600) / 60),
    seconds = Math.round(n % 60);
  return (
    [
      days ? `${days} ${days === 1 ? 'day' : 'days'}` : '',
      hours ? `${hours} h` : '',
      minutes ? `${minutes} min` : '',
      seconds ? `${seconds} s` : '',
    ]
      .filter(Boolean)
      .join(' ') || '0 seconds'
  );
}
