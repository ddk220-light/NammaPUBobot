import { gameCopy } from './game-copy.mjs';
import { identity } from './load-identity.mjs';
import { legalArticles } from './legal.mjs';
import { examples, shot } from './examples.mjs';
const legal = legalArticles(identity);
const N = identity.publicName;
const repo = identity.repository;
const guide = '/guides/getting-started/';
const ul = (items) => `<ul>${items.map((v) => `<li>${v}</li>`).join('')}</ul>`;
const dl = (items) =>
  `<dl class="feature-list">${items.map(([t, d]) => `<div><dt>${t}</dt><dd>${d}</dd></div>`).join('')}</dl>`;
const actions = (second = [guide, 'Read the setup guide']) =>
  `<div class="hero-actions"><a class="button" href="/invite">Add to Discord</a><a class="button secondary" href="${second[0]}">${second[1]}</a></div>`;
const cantYet = (items) => `<h2>What it can’t do yet</h2>${ul(items)}`;

// [catalog id, URL slug, display name, edition, selection noun]; build-site refreshes the name
// and selection from src/games/catalog.ts so the two cannot drift apart.
export const games = [
  ['aoe2', 'age-of-empires-2', 'Age of Empires II', 'Definitive Edition', 'civilization'],
  ['aoe3', 'age-of-empires-3', 'Age of Empires III', 'Definitive Edition', 'civilization'],
  ['aoe4', 'age-of-empires-4', 'Age of Empires IV', 'Age of Empires IV', 'civilization'],
  ['aom', 'age-of-mythology', 'Age of Mythology', 'Retold', 'god'],
  ['bar', 'beyond-all-reason', 'Beyond All Reason', 'Beyond All Reason', 'faction'],
  ['wc3', 'warcraft-3', 'Warcraft III', 'W3Champions', 'race'],
  ['zero_hour', 'generals-zero-hour', 'Generals: Zero Hour', 'Zero Hour', 'faction'],
];
// Pages folded into another one. Each old URL keeps a short page with a canonical link.
export const moved = {
  ...Object.fromEntries(games.map((g) => [`/games/${g[1]}/setup/`, guide])),
  '/features/queues-and-teams/': '/features/queues-and-check-in/',
  '/guides/import-ratings/': '/features/import-ratings/',
  '/guides/switch-from-pubobot/': '/features/import-ratings/',
};

export const features = [
  [
    'The game night',
    [
      [
        '/features/queues-and-check-in/',
        'Queues and check-in',
        'Players type /add; everyone checks in before the game.',
      ],
      [
        '/features/balanced-teams/',
        'Balanced teams and captains',
        'Even teams by rating, with a captain on each side.',
      ],
      [
        '/features/reporting-results/',
        'Reporting results',
        'A captain reports the result; organizers can fix mistakes.',
      ],
      [
        '/features/substitutes/',
        'Substitutes',
        'Swap a player out without starting the match again.',
      ],
      ['/features/map-voting/', 'Map voting', 'Players vote on the map, or captains choose.'],
    ],
  ],
  [
    'Ratings and privacy',
    [
      [
        '/features/ratings-and-leaderboards/',
        'Ratings and leaderboards',
        'Elo, Glicko-2 or TrueSkill ladders for your server.',
      ],
      [
        '/features/privacy-controls/',
        'Privacy controls',
        'Hide yourself, delete your data or delete a whole community.',
      ],
    ],
  ],
  [
    'Extras',
    [
      [
        '/features/aoe2-profiles-and-lobbies/',
        'AoE2 profiles and lobby tracking',
        'Link profiles and follow the game’s lobby. Beta.',
      ],
      [
        '/features/predictions/',
        'Predictions with play-money gold',
        'Bet play-money gold on the result. Beta, opt-in.',
      ],
    ],
  ],
  [
    'For organizers',
    [
      [
        '/features/web-dashboard/',
        'The organizer console',
        'Queues, matches, players and setup on one website.',
      ],
      [
        '/features/import-ratings/',
        'Import ratings',
        'Bring ratings over from PUBobot or a spreadsheet.',
      ],
    ],
  ],
];

export const cards = (items, level = 2) =>
  `<div class="cards">${items.map(([url, name, desc]) => `<article><h${level}><a href="${url}">${name}</a></h${level}><p>${desc}</p></article>`).join('')}</div>`;

export function home(gameGrid) {
  return `<section class="site-hero" data-site-hero><div class="hero-copy"><h1>A free Discord bot for RTS pickup games</h1><p class="lead">${N} is a free, open-source Discord bot that runs pickup games for Age of Empires, Warcraft III, Beyond All Reason and more. Players type <code>/add</code>, check in and get balanced teams, and your server keeps its own Elo ladder.</p>${actions([guide, 'See how setup works'])}<p class="hero-note">Free and open source. <a href="/open-source/">Read the code or host your own copy.</a></p></div><div class="hero-example">${examples.queue('Friday 4v4')}</div></section>
<section class="site-section games-section"><div class="site-section-heading"><div><h2>Games it runs</h2><p>Each game gets its own queues and its own ladder.</p></div><a href="/games/">All games →</a></div>${gameGrid}<p class="section-note">Playing something else? Add a custom game: queues, teams and captain-reported results work for any game.</p></section>
<section class="site-section" id="how"><div class="site-section-heading"><div><h2>How a game night goes</h2><p>The bot does the waiting-around part.</p></div><a href="${guide}">Setup guide →</a></div><ol class="workflow-grid"><li><span>1</span><h3>Join the queue</h3><p>Players type <code>/add</code> or press Join on the queue card.</p></li><li><span>2</span><h3>Check in</h3><p>When the queue fills, everyone presses Check in. Missing players can be swapped for people still waiting.</p></li><li><span>3</span><h3>Play with even teams</h3><p>The bot splits players by rating and names a captain for each side.</p></li><li><span>4</span><h3>Report the result</h3><p>A captain presses Report result and the ladder updates.</p></li></ol><div class="example-pair">${examples.checkIn()}${examples.result()}</div></section>
<section class="site-section feature-section"><div class="site-section-heading"><div><h2>What it does</h2><p>Everything is free. Beta features say so.</p></div><a href="/features/">All features →</a></div>${cards(
    features.flatMap(([, items]) => items).map(([u, t, d]) => [u, t, d]),
    3,
  )}</section>
<section class="site-section dashboard-section"><div class="site-section-heading"><div><h2>A console for organizers</h2><p>Set up queues, fix results and manage players from a website that works on your phone.</p></div><a href="/features/web-dashboard/">About the console →</a></div>${shot('console-queues', 1200, 530, 'The Queues page of the organizer console, showing a queue with four waiting players', 'The console’s Queues page with demo data.')}</section>
<section class="site-section source-section"><div><h2>Free and open source</h2><p>Read how the bot works, suggest a change or run your own copy. The code is public under GPL-3.0, with credit to PUBobot2 by Leshaka, which it grew out of.</p></div><div class="hero-actions"><a class="button secondary" href="/open-source/">About the source</a><a class="button secondary" href="${repo}">View on GitHub ↗</a></div></section>`;
}

export function landing(g) {
  const [id, slug, name] = g;
  const copy = gameCopy[id];
  const related = games.filter((v) => v[0] !== id).slice(0, 3);
  return `<p>Players type <code>/add</code>, check in and get balanced teams, and your server keeps its own ladder of ${name} games. It is free for any server, with nothing held back.</p>${actions()}
<h2>How a game night goes</h2><p>${copy.night}</p>${examples.teams(name, `A ${name} match card once teams are made. The first name on each side is the captain.`)}
<h2>What’s specific to ${name}</h2>${dl(copy.features)}
${cantYet(copy.limits)}<p>Optional game tracking is in beta. Queues, teams, ratings and captain-reported results work without it.</p>
<h2>Set it up</h2><p>Setup is the same for every game: add the bot, sign in to the console, pick ${name} and create a queue. The <a href="${guide}">five-step setup guide</a> walks through it with pictures.</p>${actions()}
<h2>Questions</h2><h3>${copy.question[0]}</h3><p>${copy.question[1]}</p><h3>Is it free?</h3><p>Yes. ${N} is free and open source under GPL-3.0. <a href="/open-source/">Read the code</a> or host your own copy.</p><h3>Is it made by the game’s publisher?</h3><p>No. ${N} is a community project and isn’t affiliated with ${name} or its publisher.</p>
<p>Also for ${related.map((v) => `<a href="/games/${v[1]}/">${v[2]}</a>`).join(', ')}. <a href="/games/">See all games</a>.</p>`;
}

export function movedBody(from, to, title) {
  return `<p>This page has moved. ${from.startsWith('/games/') ? 'Setup is the same for every game, so the steps now live in one place:' : 'You can find it here:'} <a href="${to}">${title}</a>.</p>`;
}

const gettingStarted = `<p>${N} is a free, open-source Discord bot that runs pickup games for your server. These five steps take you from nothing to your first game. You need the Manage Server permission on the Discord server.</p>${actions(['/app/', 'Open the console'])}
<ol class="guide-steps">
<li><h2>Add the bot to your server</h2><p>Press <a href="/invite">Add to Discord</a> and choose your server. Discord shows the permissions the bot asks for and asks you to confirm.</p><p class="you-see"><strong>What you should see:</strong> the bot in your server’s member list. It stays quiet until you make a queue.</p>${examples.permissions()}</li>
<li><h2>Sign in to the console with Discord</h2><p>The console is the organizer website. If you came from Add to Discord, Discord already signed you in and brought you back to it; otherwise <a href="/app/">open the console</a> and press Sign in with Discord.</p><p class="you-see"><strong>What you should see:</strong> your server, and a setup guide with four parts: Discord access, Choose a game, First queue and Try it in Discord.</p>${shot('console-guide-access', 1200, 500, 'The console setup guide on its first step, confirming the bot can reach the server', 'The console’s setup guide, shown with demo data.')}</li>
<li><h2>Pick your game</h2><p>Choose one of seven games, or add a custom game for anything else. Queues for the same game share one ladder, so you only pick a game once.</p><p class="you-see"><strong>What you should see:</strong> a card for each game. Games with optional game tracking show a Beta label and whether it is available.</p>${shot('console-guide-game', 1200, 640, 'The game picker in the console setup guide, with a card for each supported game', 'Choosing a game in the console, shown with demo data.')}</li>
<li><h2>Create a queue and choose its channel</h2><p>Give the queue a name players will recognize, pick the channel it lives in and set the players per match: 8 makes a 4v4. Choose how teams are made, then save.</p><p>New queues start with a 60-second check-in and start a match as soon as they fill. You can change both later.</p><p class="you-see"><strong>What you should see:</strong> the queue card in your channel within a few seconds. If a channel is marked “missing channel permissions”, see <a href="/guides/discord-permissions/">Discord permissions</a>.</p>${examples.emptyQueue()}</li>
<li><h2>Players type /add</h2><p>Players type <code>/add</code> or press Join. When the queue fills, the bot posts a match card and everyone presses Check in.</p><p class="you-see"><strong>What you should see:</strong> the match card with ticks appearing as people check in.</p>${examples.checkIn()}</li>
</ol>
<h2>What happens next</h2><p>Once everyone has checked in, the bot splits the players into two even teams and names a captain on each side. The host makes the lobby in the game and you play. Afterwards either captain presses Report result on the match card and the ladder updates; organizers can correct a result later from Matches in the console.</p>${examples.result()}
<h2>If something goes wrong</h2>${dl([
  [
    'The queue card didn’t appear',
    'Check that the bot can view and post in that channel, including the channel’s own permission overrides. Then press Check access again in the console. <a href="/guides/discord-permissions/">Permissions guide</a>.',
  ],
  [
    'Nobody can find the commands',
    'Slash commands can take a moment to show up after the bot joins. Typing <code>/add</code> in the queue channel is enough to start.',
  ],
  [
    'You want to undo something',
    'Matches in the console lists every game. Organizers can cancel a match or withdraw a result there; withdrawing reverses its rating changes.',
  ],
])}
<p>Next: read about <a href="/features/queues-and-check-in/">queues and check-in</a>, <a href="/features/balanced-teams/">how teams are made</a> or the <a href="/guides/commands/">command list</a>.</p>`;

const featurePages = {
  '/features/queues-and-check-in/': {
    title: 'Discord pickup queues with check-in',
    heading: 'Queues and check-in',
    description:
      'Players join a pickup queue with /add or the Join button. When it fills, everyone checks in before teams are made, so no-shows don’t stall the game.',
    body: `<p>${N} is a free, open-source Discord bot that runs pickup queues. A queue is a list of people ready to play, pinned to one channel. Players type <code>/add</code> or press Join, and <code>/remove</code> or Leave to drop out.</p>${examples.queue()}
<h2>When the queue fills</h2><p>The bot starts a match as soon as the queue is full. With check-in on, each player presses Check in before the timer runs out; new queues use 60 seconds, and the bot pings anyone who hasn’t.</p><p>If someone misses it and enough people are waiting, the bot swaps them for the next players in line. Otherwise the players who did check in go back to the front of the queue.</p>${examples.checkIn()}
<h2>Settings you can change</h2>${dl([
      ['Players per match', 'Any even number from 2 to 16, so 1v1 up to 8v8.'],
      ['Check-in', 'On or off, and how long players get.'],
      [
        'Start early',
        'Anyone can type <code>/queue start</code> once two people are waiting. An odd number makes uneven teams.',
      ],
      [
        'Waiting time limit',
        'Optionally drop people who have waited too long; they can join again.',
      ],
      ['Who can join', 'Require a role, block a role, or stop one player joining for a while.'],
      [
        'Several queues',
        'Run more than one queue in a channel, such as “4v4 Arabia” and “2v2 Nomad”. Players name the one they want after <code>/add</code>.',
      ],
    ])}
<p>Organizers can pause a queue, clear it or start it from the console or from <code>/admin</code> in Discord.</p>
${cantYet(['There is no “ready later” or scheduled start; a queue fills when people join.', 'One match holds at most 16 players.'])}`,
  },
  '/features/balanced-teams/': {
    title: 'Balanced teams and captains for pickup games',
    heading: 'Balanced teams and captains',
    description:
      'The bot splits each pickup game into two even teams using your server’s ratings and names a captain on each side. Choose captain pick, balanced or random.',
    body: `<p>${N} is a free, open-source Discord bot that makes the teams for your pickup games. When check-in ends, it splits the players into Alpha and Beta and posts both teams on the match card. Nobody has to argue about who goes where.</p>${examples.teams()}
<h2>Three ways to make teams</h2>${dl([
      [
        'Captain pick',
        'The two highest-rated players go to opposite sides as captains, and the rest are split so the team totals stay close. New queues use this.',
      ],
      [
        'Balanced by rating',
        'The bot tries every split and takes the one where the two teams’ total ratings are closest.',
      ],
      ['Random', 'A fair random split, for nights when ratings don’t matter.'],
    ])}
<p>In every mode the highest-rated player on each team is its captain, and each team is listed from highest to lowest rating.</p>
<h2>What captains do</h2><p>Captains report the result, agree to a draw or a cancel, and redo the map vote if it goes wrong. When predictions are on they can also close betting before the game starts.</p>
<h2>Fixing a team</h2><p>Organizers can move or place a player with <code>/manage match put</code>, and the teams and captains update on the card. Substitutes are covered on <a href="/features/substitutes/">their own page</a>.</p>
${cantYet(['Captains don’t take turns picking players; the bot makes the split.', 'Teams are only as fair as the ratings. A new group needs a few games before balanced teams feel right.', 'The bot doesn’t know about roles, positions or favourite civs.'])}`,
  },
  '/features/ratings-and-leaderboards/': {
    title: 'Elo ratings and leaderboards on Discord',
    heading: 'Ratings and leaderboards',
    description:
      'Every server keeps its own ladder: AoE2-style Elo, Glicko-2, TrueSkill or flat points, with /rank, /leaderboard, rank roles and an optional public page.',
    body: `<p>${N} is a free, open-source Discord bot that keeps a rating ladder for each game your server plays. Every reported result moves the ratings of everyone in the match, and all queues for the same game share one ladder.</p>${examples.result()}
<h2>Pick a rating system</h2>${dl([
      [
        'AoE2-style Elo',
        'Win and you go up, lose and you go down, by more when the result is a surprise. The default.',
      ],
      ['Glicko-2', 'Like Elo, but it also tracks how sure it is, so new players move faster.'],
      ['TrueSkill', 'Built for team games; it also tracks uncertainty.'],
      ['Flat', 'A fixed number of points for a win or a loss.'],
    ])}
<p>Choose before your first game: the system locks once a player has a rating, so the ladder never mixes two kinds of numbers.</p>
<h2>Seeing the ladder</h2><p>Players type <code>/rank</code> for their own rating, or <code>/rank detailed:true</code> for recent results and their peak. <code>/leaderboard</code> shows the server’s table, a page at a time.</p>
<h2>Leaderboard rules</h2>${dl([
      ['Minimum games', 'Keep people off the table until they have played a few games.'],
      ['Recent activity', 'Only show players who have played in the last so many days.'],
      ['Who can see it', 'Everyone, server members only, or organizers only.'],
      [
        'Rank roles and nicknames',
        'Give Discord roles at rating thresholds, or add the rank to nicknames. These need extra permissions.',
      ],
      ['Inactivity', 'Optionally let ratings drift down each week for players who stop playing.'],
    ])}
<p>A public leaderboard page is never listed in search engines unless an organizer turns that on. Players who hide themselves are always left out; see <a href="/features/privacy-controls/">privacy controls</a>.</p>
${cantYet(['Your server’s ladder doesn’t read or replace official ranked ratings.', 'Changing the rating system after games are recorded isn’t possible; import into a new game instead.'])}`,
  },
  '/features/reporting-results/': {
    title: 'Reporting pickup game results on Discord',
    heading: 'Reporting results and corrections',
    description:
      'After each pickup game either captain presses Report result on the match card. Organizers can withdraw or correct a wrong result from the console.',
    body: `<p>${N} is a free, open-source Discord bot that records the result of every pickup game. The bot never guesses who won: a captain reports it, and then the ladder updates.</p>${examples.teams()}
<h2>Reporting a result</h2><p>When the game ends, either captain presses Report result on the match card and chooses the outcome. The card changes to show the winner and each player’s rating change.</p><p>A draw or a cancel needs both captains to agree: one asks, and the other presses Agree on the message that follows. Organizers and bot moderator roles can report any result.</p>${examples.result()}
<h2>Fixing mistakes</h2>${dl([
      [
        'Withdraw a result',
        'In Matches in the console, or with <code>/manage match undo</code>. The rating changes are reversed and a record of the change is kept.',
      ],
      [
        'Record the right one',
        'Report the corrected result from the console or with <code>/manage match report</code>.',
      ],
      ['Cancel a match', 'Release a match nobody will finish, with no rating changes.'],
      [
        'Add a game played elsewhere',
        '<code>/manage match record</code> adds a game with two teams that didn’t go through a queue.',
      ],
    ])}${examples.undone()}
<h2>Games nobody reports</h2><p>A match can have a time limit. If nobody reports in time, the bot cancels it and no ratings change.</p>
${cantYet(['Results from game data are suggestions only; for AoE2 lobby tracking (beta) a captain still confirms.', 'There is no dispute vote; an organizer settles disagreements.'])}`,
  },
  '/features/substitutes/': {
    title: 'Substitutes for Discord pickup games',
    heading: 'Substitutes',
    description:
      'Someone has to leave? Swap in a substitute from the queue or another player without starting the pickup game again. Teams and captains update on the card.',
    body: `<p>${N} is a free, open-source Discord bot for pickup games, and it handles the player who has to leave. A substitute takes their place and the match carries on.</p>
<h2>Before the game starts</h2><p>During check-in a player can press Back out. If someone is waiting in the queue, the bot moves them in and makes the teams again; the newcomer has to check in.</p>
<h2>During the game</h2>${dl([
      [
        'Ask for a sub',
        'Press Sub me on the match card or type <code>/subme</code>. Your place is kept until someone takes over, and typing it again cancels the request.',
      ],
      [
        'Take someone’s place',
        'Another player types <code>/subfor</code> and names the player who asked.',
      ],
      [
        'Pull in the next player',
        '<code>/subauto</code> brings in the next eligible player from the queue. Once the game has started they join the same team, so the teams stay as they were.',
      ],
    ])}${examples.substitute()}
<p>Replacing someone else needs their request, a captain or an organizer. Organizers can also force a swap with <code>/manage match substitute</code> or move a player with <code>/manage match put</code>.</p>
<h2>Ratings and bets</h2><p>The substitute plays for the rest of the game and gets the rating change for the result. If predictions are on, a roster change refunds the bets placed so far.</p>
${cantYet(['There is no list of standby players beyond the queue itself.', 'The bot can’t tell the game itself that a player changed; the host updates the lobby.'])}`,
  },
  '/features/map-voting/': {
    title: 'Map voting for Discord pickup games',
    heading: 'Map voting',
    description:
      'Let players vote on the map for each pickup game, have captains choose, or draw one at random. AoE2 includes a ready map pool; other games use your own list.',
    body: `<p>${N} is a free, open-source Discord bot for pickup games, and it can settle the map before the game starts. Map voting is off until an organizer turns it on for a queue.</p>${examples.mapVote()}
<h2>How it works</h2><p>When a match starts, the bot draws a short list from your map pool and adds a menu to the match card. When time runs out the chosen maps appear on the card, like “Maps: Arabia · Arena”.</p>
<h2>Choices for each queue</h2>${dl([
      [
        'Who chooses',
        'Every player votes, the captains choose directly, or the bot picks at random.',
      ],
      ['Map pool and short list', 'The full list to draw from, and how many maps go on each vote.'],
      ['Number of maps', 'From one to five, for best-of nights.'],
      ['Voting', 'One vote each, or vote for every map you’d be happy with.'],
      [
        'Ties and silence',
        'Break ties at random or alphabetically. If nobody votes, pick at random or cancel.',
      ],
      ['Time limit', 'From 10 seconds to an hour; 60 seconds by default.'],
      [
        'Votes count as check-in',
        'Players check in by voting, so there is one button to press, not two.',
      ],
      ['Avoid repeats', 'Leave out maps played in the last few matches.'],
    ])}
<p>Age of Empires II comes with 15 maps ready, including Arabia, Arena, Nomad and Black Forest. For other games you type in your own list. Anyone can see the vote with <code>/maps show</code>; captains and organizers can start it again with <code>/maps redo</code>.</p>
${cantYet(['The bot doesn’t set the map in the game; the host does.', 'Map pools are per queue, not shared between servers.'])}`,
  },
  '/features/aoe2-profiles-and-lobbies/': {
    title: 'AoE2 profile links and lobby tracking',
    heading: 'Game profiles and lobby tracking for AoE2',
    description:
      'Link AoE2 profiles to Discord players and, in beta, follow the game’s lobby through public AoE2 Companion data. Captains still confirm every result.',
    body: `<p>${N} is a free, open-source Discord bot that runs Age of Empires II pickup games, and it can connect a match to the real game. Profile links are available to everyone; lobby tracking is in beta.</p>
<h2>Linking a profile</h2><p>Players add their AoE2 profile with <code>/profile link</code>, check it with <code>/profile show</code> and remove it with <code>/profile unlink</code>. Others see the link with <code>/rank detailed:true</code>.</p><p>A link is a pointer, not proof: anyone can type any profile number. For lobby tracking, each player declares their profile with <code>/lobby link</code>.</p>
<h2>Lobby tracking (beta)</h2>${dl([
      [
        'Find the lobby',
        '<code>/lobby list</code> shows up to ten public lobbies or running games.',
      ],
      [
        'Attach it',
        'A player in the match types <code>/lobby attach</code> with the lobby ID or link, and a captain confirms it with <code>/lobby confirm</code>.',
      ],
      [
        'Follow it',
        'The bot watches public AoE2 Companion data. <code>/lobby status</code> shows what it has seen.',
      ],
      [
        'Finish',
        'When the game ends and the players match the teams, the bot suggests the result. A captain still reports it.',
      ],
    ])}
<p>If the roster changes, tracking stops and the lobby has to be attached again. Captains can stop tracking with <code>/lobby unlink</code>.</p>
<h2>Civ picks</h2><p>With civ picks on, anyone in the match types <code>/civpick</code>. Everyone picks in secret from the same shortlist, and the picks show together.</p>${examples.civPicks()}
${cantYet(['Lobby tracking is in beta and may not be switched on for your server yet; the console shows its status.', 'Private lobbies and quick rematches may never show up in public data.', 'A lobby closing doesn’t prove the game started or who won.', 'Replay links depend on what AoE2 Companion has; the bot doesn’t store replays.'])}`,
  },
  '/features/predictions/': {
    title: 'Play-money predictions on pickup games',
    heading: 'Predictions with play-money gold',
    description:
      'Back a team with play-money gold before the pickup game starts. Beta and opt-in. Gold has no value and can’t be bought, sold or cashed out.',
    body: `<p>${N} is a free, open-source Discord bot for pickup games, with an optional bit of fun for the people watching: predictions. Spectators and players back a team with gold, and the winners share the pot. It is in beta and off until an organizer turns it on for a queue.</p>${examples.predictions()}
<h2>Gold is play money</h2><p>Everyone starts with 500 gold for each game. After each finished match the players get up to 100 gold back, but never above 500. Gold has no value: it can’t be bought, sold, traded or cashed out.</p>
<h2>Placing a bet</h2>${dl([
      [
        'Choose a side',
        'Press Place bet on the match card. Players can only back their own team; spectators can back either.',
      ],
      ['Choose a stake', 'Pick an amount from the buttons. Pressing again adds to your stake.'],
      ['Change your mind', 'Cancel bet returns your whole stake while betting is still open.'],
    ])}
<p>Betting closes when a captain or organizer presses Close betting. With AoE2 lobby tracking (beta), it also closes when the game is confirmed to have started.</p>
<h2>Paying out</h2><p>The winning side shares the whole pot in proportion to what each person put in. Draws, cancelled matches, pots with bets on only one side and roster changes refund everyone.</p><p><code>/gold</code> shows your balance, your history and the gold table; <code>/predictions leaderboard</code> shows who calls results best.</p>
${cantYet(['Predictions are in beta and may change.', 'Gold belongs to one game on one server; it doesn’t move between them.'])}`,
  },
  '/features/privacy-controls/': {
    title: 'Privacy controls for players and servers',
    heading: 'Privacy controls',
    description:
      'Players can hide from leaderboards and unlink profiles. Organizers can delete one player’s data or a whole community, and choose who sees the stats.',
    body: `<p>${N} is a free, open-source Discord bot for pickup games, and it keeps as little as it needs: who played, the teams, the results and the ratings. Players and organizers decide who sees it.</p>
<h2>For players</h2>${dl([
      [
        'Hide yourself',
        '<code>/profile hide</code> takes you off that game’s leaderboards and out of other people’s stats. <code>/profile unhide</code> brings you back.',
      ],
      ['Unlink a profile', '<code>/profile unlink</code> removes a game profile you linked.'],
      [
        'Delete your data',
        'Ask an organizer of your server, or contact us as the <a href="/privacy/">privacy policy</a> explains.',
      ],
    ])}
<h2>For organizers</h2>${dl([
      ['Who can see stats', 'Everyone, server members only or organizers only, for each game.'],
      ['Hide a player', '<code>/manage player hide</code> keeps someone off the tables.'],
      ['Delete one player', 'Remove a player’s data from Players in the console.'],
      [
        'Download your data',
        'Download your community’s records from Setup in the console at any time.',
      ],
      [
        'Delete a community',
        'The server owner or an Administrator can delete every record for the server from Setup in the console. It can’t be undone.',
      ],
    ])}
<h2>Search engines</h2><p>Nothing about your server appears in search results unless an organizer makes a leaderboard public and also turns on search publishing. Hidden players are always left out.</p>
${cantYet(['Removing the bot from a server doesn’t delete its records by itself; delete the community first.', 'Search engines can keep an old copy of a page for a while after it is unpublished.'])}`,
  },
  '/features/web-dashboard/': {
    title: 'The organizer console for your pickup games',
    heading: 'The organizer console',
    description:
      'Set up queues, fix results and manage players for your Discord pickup games from one website. Sign in with Discord; it works on desktop and phone.',
    body: `<p>${N} is a free, open-source Discord bot for pickup games, and the console is where organizers run it. Sign in with Discord at <a href="/app/">the console</a>; you see every server where you have the Manage Server permission.</p>${shot('console-queues', 1200, 530, 'The Queues page of the organizer console, showing a queue with four waiting players', 'The console’s Queues page with demo data.')}
<h2>Four pages</h2>${dl([
      ['Queues', 'Who is waiting, and buttons to start, pause or clear each queue.'],
      [
        'Matches',
        'Games in progress and finished. Report, withdraw or correct a result, or cancel a match.',
      ],
      [
        'Players',
        'Ratings and records. Set a starting rating, hide or restrict a player, and import ratings.',
      ],
      [
        'Setup',
        'Games, queue settings, the bot’s Discord access, public leaderboards and your data.',
      ],
    ])}
<h2>Saving changes</h2><p>Edits wait until you press Save, and the console shows what will change first. If two organizers edit at once, nothing is overwritten without a warning.</p>
<h2>In Discord too</h2><p><code>/admin</code> opens a panel with queue status, recent matches and quick actions. <code>/manage</code> has the same tools as slash commands.</p>
${cantYet(['The console only shows servers where you have Manage Server. Other helpers can get a bot moderator role for the Discord actions instead.'])}`,
  },
  '/features/import-ratings/': {
    title: 'Import ratings from PUBobot or another bot',
    heading: 'Import ratings from another bot',
    description:
      'Move your pickup ladder to RTS Pickup: import ratings from a spreadsheet or a PUBobot export, check a preview and apply it once.',
    body: `<p>${N} is a free, open-source Discord bot for pickup games, and your old ladder can come with you. Import a spreadsheet of ratings, or a whole history from PUBobot.</p>
<h2>How to import</h2><ol><li>In the console, open Players, then Import player records.</li><li>Choose the game and queue the ratings belong to.</li><li>Upload a CSV or JSON file, or paste it in.</li><li>Check the preview: how many players, any warnings and any rows with errors.</li><li>Press Apply. Nothing changes before this step.</li></ol>
<h2>What a file looks like</h2><p>A CSV needs the columns <code>id</code>, <code>name</code>, <code>rating</code> and <code>deviation</code>, where <code>id</code> is the player’s Discord user ID. It can also have wins, losses, draws, streak, hidden and last-played columns.</p><p>A ratings import adds new players and leaves existing records alone. A full match history needs an empty game.</p>
<h2>Coming from PUBobot</h2><p>${N} grew out of <a href="https://github.com/Leshaka/PUBobot2">PUBobot2 by Leshaka</a>. A converter script in the <a href="${repo}">source repository</a> turns a PUBobot export into a file the console accepts. Keep your old bot running until your first queue works here.</p>
${cantYet(['Players need Discord user IDs; display names aren’t unique enough to match.', 'Gold and other extras from another bot don’t come across.', 'Other bots’ exports need turning into the CSV columns above first.'])}`,
  },
};

export const articles = {
  '/about/': legal['/about/'],
  '/open-source/': {
    title: 'Free and open-source Discord pickup bot',
    heading: 'Free and open source',
    description:
      'RTS Pickup is free and open source under GPL-3.0. Read the code, suggest a change, report a bug or host your own copy of the Discord bot.',
    body: `<p>${N} is a free, open-source Discord bot that runs pickup games. The bot and the console are public under GPL-3.0-only: you can use, study and change them. If you hand out a changed copy, the license asks you to share its source too.</p><p><a href="${repo}">View the code on GitHub</a> · <a href="${identity.license}">Read the license</a> · <a href="${repo}/issues">Report an issue</a>.</p><h2>Credits</h2><p>It grew out of <a href="https://github.com/Leshaka/PUBobot2">PUBobot2 by Leshaka</a>, and uses public data from the community projects named on each game page. See <a href="/guides/self-hosting/">self-hosting</a> to run your own copy.</p>`,
  },
  [guide]: {
    title: 'Set up a Discord pickup bot in five steps',
    heading: 'Get started in five steps',
    description:
      'Add RTS Pickup to your Discord server, sign in to the console, pick your game, create a queue and run your first pickup game. An illustrated guide.',
    body: gettingStarted,
  },
  '/guides/discord-permissions/': {
    title: 'Discord permissions the pickup bot needs',
    heading: 'Discord permissions',
    description:
      'The four channel permissions the pickup bot needs, the two optional ones for rank roles and nicknames, and how to fix a channel it can’t post in.',
    body: `<p>${N} is a free, open-source Discord bot for pickup games. It needs very little: four permissions in each queue channel, and two more only if you use rank roles or nickname tags.</p><h2>Required in every queue channel</h2>${ul(['View Channel', 'Send Messages', 'Embed Links', 'Read Message History'])}<p>The invitation asks for these four. Check the channel’s own permission overrides as well as the server roles, because an override can take them away.</p><h2>Optional</h2>${dl(
      [
        [
          'Manage Roles',
          'Only for rank roles. Move the bot’s role above the rank roles it hands out.',
        ],
        [
          'Manage Nicknames',
          'Only for adding ranks to nicknames. Discord never lets a bot rename the server owner or anyone above it.',
        ],
      ],
    )}<h2>Fixing access</h2><p>The console’s Setup page offers separate invitations for the optional permissions. Inviting the bot again can’t fix role order or channel overrides; change those in Discord, then press Check access again.</p>`,
  },
  '/guides/self-hosting/': {
    title: 'Host your own copy of the pickup bot',
    heading: 'Self-host the bot',
    description:
      'Run your own copy of the open-source pickup bot: what you need, where the operator instructions live and what to check before an upgrade.',
    body: `<p>${N} is free and open source, so you can run your own copy instead of using the hosted bot. You need Node 22.12 or newer, a Cloudflare account and your own Discord application.</p><p>Clone <a href="${repo}">the repository</a>, go into <code>cloudflare</code>, then run <code>npm ci</code> and <code>npm run check</code>. Follow <a href="${repo}/blob/main/cloudflare/README.md">the operator instructions</a> to set up the app, Discord callback URLs and secrets, register the commands and check the invitation permissions.</p><p>Before upgrading, take a backup and test restoring it somewhere separate. Optional game tracking can need its own keys. Keep secret tokens out of commits and screenshots. Running your own copy can cost money for hosting even though the software is free.</p>`,
  },
  ...featurePages,
  '/privacy/': legal['/privacy/'],
  '/terms/': legal['/terms/'],
  '/changelog/': {
    title: 'Changelog',
    heading: 'Changelog',
    description: 'What changed in the bot and the website, release by release.',
    body: `<p>The console that works on phones and the seven-game support were added in <a href="${repo}/pull/46">PR 46</a>. The default game filter on Players was fixed in <a href="${repo}/pull/48">PR 48</a>.</p><p>A change in the source is not live on the hosted bot until it is released; optional game tracking shows its real status in the console.</p>`,
  },
};
export const indexes = {
  '/games/': {
    title: 'Pickup game bot for AoE2, WC3, BAR and more',
    heading: 'Games',
    description:
      'RTS Pickup runs Discord pickup games for Age of Empires II, III and IV, Age of Mythology, Warcraft III, Beyond All Reason and Zero Hour.',
  },
  '/features/': {
    title: 'Pickup bot features: queues, teams, Elo',
    heading: 'Features',
    description:
      'Everything the free Discord pickup bot does: queues and check-in, balanced teams, Elo ladders, results, subs, map votes and more. Beta features are marked.',
  },
  '/guides/': {
    title: 'Guides for running pickup games on Discord',
    heading: 'Guides',
    description:
      'Set up the pickup bot on your Discord server, look up a command, fix permissions or host your own copy.',
  },
};
export const guides = [
  [guide, 'Get started in five steps', 'Add the bot, sign in, pick a game, make a queue, play.'],
  [
    '/guides/commands/',
    'Command reference',
    'Every slash command, grouped for players, captains and organizers.',
  ],
  [
    '/guides/discord-permissions/',
    'Discord permissions',
    'What the bot needs in a channel, and how to fix it.',
  ],
  ['/guides/self-hosting/', 'Self-host the bot', 'Run your own copy of the open-source code.'],
];
