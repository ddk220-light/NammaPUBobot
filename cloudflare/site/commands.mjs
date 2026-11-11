// Groups the registered command tree by who uses it. The tree itself comes from
// src/discord/commands.mjs, so a command added or removed there appears or disappears here; one
// this map does not know is placed by its permissions and wording, never dropped.
export const groups = [
  ['Players', 'Everything a player needs on a game night. Most people only ever type /add.'],
  [
    'Captains',
    'The first name on each team is its captain. Captains also have the player commands.',
  ],
  ['Organizers', 'For server managers and bot moderator roles. The same tools are in the console.'],
];
const audience = {
  '/report': 'Captains',
  '/maps redo': 'Captains',
  '/predictions close': 'Captains',
  '/lobby unlink': 'Captains',
  '/lobby confirm': 'Captains',
  '/admin': 'Organizers',
  '/manage': 'Organizers',
  '/rally': 'Organizers',
  '/test': 'Organizers',
  '/queue clear': 'Organizers',
};
// Plainer one-liners than the 100-character Discord descriptions; unknown commands keep theirs.
const plain = {
  '/help': 'Show what you can do and where to start.',
  '/add': 'Join a queue. Add the queue’s name if the channel has more than one.',
  '/remove': 'Leave the queue you are waiting in.',
  '/queue show': 'See who is waiting.',
  '/queue start': 'Start a match now with whoever is waiting, if at least two are.',
  '/teams': 'Show the teams and captains of your match.',
  '/rank': 'Show a rating. Add detailed for recent results and peak rating.',
  '/leaderboard': 'Show the server’s ladder for a game.',
  '/profile link': 'Add a game profile so others can look you up.',
  '/profile unlink': 'Remove a game profile you added.',
  '/profile show': 'Show your linked profiles and stats.',
  '/profile hide': 'Leave this game’s leaderboards and other people’s stats.',
  '/profile unhide': 'Show up on this game’s leaderboards again.',
  '/subme': 'Ask for a substitute, or cancel the request.',
  '/subfor': 'Take the place of a player who asked for a substitute.',
  '/subauto': 'Bring in the next player from the queue as a substitute.',
  '/gold': 'Show your play-money gold, its history or the gold table.',
  '/predictions leaderboard': 'Show who predicts results best.',
  '/maps show': 'Show the maps and the vote for your match.',
  '/maps redo': 'Start the map vote again.',
  '/civpick': 'Start or show secret civ picks for an AoE2 match.',
  '/lobby list': 'Show public lobbies or running games.',
  '/lobby attach': 'Attach the game’s lobby to your match by ID or link.',
  '/lobby link': 'Declare your game profile for lobby tracking.',
  '/lobby status': 'Show what the bot has seen of your lobby.',
  '/lobby facts': 'Declare your civ or faction; captains can add the map and length.',
  '/lobby replays': 'Refresh the replay links for your match.',
  '/lobby unlink': 'Stop tracking the attached lobby.',
  '/lobby confirm': 'Confirm the attached lobby and its players.',
  '/report loss': 'Report that your team lost.',
  '/report draw': 'Ask for a draw. The other captain has to agree.',
  '/report abort': 'Ask to cancel with no rating changes. The other captain has to agree.',
  '/predictions close': 'Close betting before the game starts.',
  '/admin': 'Open a panel with queue status, recent matches and quick actions.',
  '/manage setup status': 'Check the games and queues saved for this channel.',
  '/manage setup web': 'Get a link to the console.',
  '/manage match undo': 'Withdraw a result and reverse its rating changes.',
  '/manage match recover': 'Post a deleted match card again, or retry one that failed.',
  '/manage player sync': 'Retry a player’s rank role and nickname.',
};
// The order players meet commands in; anything not listed follows in registration order.
const order = Object.keys(plain);
const rank = (path) => (order.includes(path) ? order.indexOf(path) : order.length);
const leaves = (c, prefix) =>
  c.options?.some((o) => o.type === 1 || o.type === 2)
    ? c.options
        .filter((o) => o.type === 1 || o.type === 2)
        .flatMap((o) => leaves(o, `${prefix} ${o.name}`))
    : [{ path: prefix, command: c }];
function groupOf(path, root, command) {
  const parts = path.split(' ');
  for (let i = parts.length; i; i--) {
    const known = audience[parts.slice(0, i).join(' ')];
    if (known) return known;
  }
  if (root.default_member_permissions) return 'Organizers';
  if (/^(Administrator|Organizer|Admins)/.test(command.description)) return 'Organizers';
  if (/^Captain/.test(command.description)) return 'Captains';
  return 'Players';
}
/** The reference page body, grouped Players → Captains → Organizers. */
export function commandReference(commands, e) {
  const byGroup = new Map(groups.map(([g]) => [g, []]));
  for (const root of commands)
    for (const { path, command } of leaves(root, `/${root.name}`)) {
      const args = (command.options ?? [])
        .filter((o) => o.type > 2)
        .map((o) => (o.required ? o.name : `[${o.name}]`));
      byGroup
        .get(groupOf(path, root, command))
        .push([
          path,
          `<div><dt><code>${e([path, ...args].join(' '))}</code></dt><dd>${e(plain[path] ?? command.description)}</dd></div>`,
        ]);
    }
  return `<p>Type <code>/</code> in a channel the bot can see and Discord lists these commands. On a normal night players only need <code>/add</code> and the buttons on the bot’s cards. Names in [brackets] are optional.</p>${groups
    .filter(([g]) => byGroup.get(g).length)
    .map(
      ([g, intro]) =>
        `<h2>${g}</h2><p>${e(intro)}</p><dl class="command-list">${byGroup
          .get(g)
          .sort(([a], [b]) => rank(a) - rank(b))
          .map(([, html]) => html)
          .join('')}</dl>`,
    )
    .join('')}`;
}
