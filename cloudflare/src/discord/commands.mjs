const queue = {
  name: 'queue',
  description: 'Queue name or alias when this channel has more than one',
  type: 3,
};
const match = {
  name: 'match',
  description: 'Match ID from the web organizer (defaults to your active match)',
  type: 3,
};
const user = {
  name: 'user',
  description: 'Player (administrator permission required to change someone else)',
  type: 6,
};
const result = {
  name: 'result',
  description: 'Ranked outcome to record',
  type: 3,
  required: true,
  choices: ['alpha', 'beta', 'draw', 'abort'].map((value) => ({ name: value, value })),
};
const sub = (name, description, options = []) => ({
  name,
  description,
  type: 1,
  ...(options.length ? { options } : {}),
});
// Operation definitions retain the handler argument contracts. Only `commands`
// below is registered with Discord; administrator operations share one menu.
const operations = [
  {
    name: 'setup',
    description: 'Check or open this community’s setup',
    type: 1,
    default_member_permissions: '32',
    options: [
      sub('status', 'Check saved games and queues for this channel'),
      sub('web', 'Open the community setup app'),
    ],
  },
  { name: 'add', description: 'Join a game queue', type: 1, options: [queue, user] },
  { name: 'remove', description: 'Leave a waiting queue', type: 1, options: [queue, user] },
  {
    name: 'queue',
    description: 'View and control a waiting queue',
    type: 1,
    options: [
      sub('show', 'Show the saved waiting queue', [queue]),
      sub('start', 'Administrator: start with available players', [
        queue,
        { name: 'skip_check_in', description: 'Skip check-in only for this manual start', type: 5 },
      ]),
      sub('clear', 'Administrator: clear waiting players without changing matches', [queue]),
      sub('split', 'Administrator: split waiting players into separate matches', [
        {
          name: 'group_size',
          description: 'Even players per match; leftovers stay queued',
          type: 4,
          min_value: 2,
          max_value: 16,
        },
        {
          name: 'sort_by_rating',
          description: 'Group players by rating before balancing each match',
          type: 5,
        },
        queue,
      ]),
    ],
  },
  {
    name: 'lobby',
    description: 'Link a game profile and track your match lobby',
    type: 1,
    options: [
      sub('list', 'Show up to ten public waiting lobbies or running games', [
        {
          name: 'phase',
          description: 'Waiting rooms or running matches',
          type: 3,
          required: true,
          choices: [
            { name: 'Waiting lobbies', value: 'waiting' },
            { name: 'Running games', value: 'running' },
          ],
        },
        queue,
      ]),
      sub('attach', 'Match players: attach a lobby ID or link', [
        {
          name: 'reference',
          description: 'Lobby ID or supported HTTPS lobby link',
          type: 3,
          required: true,
        },
        match,
      ]),
      sub('unlink', 'Captain/admin: stop tracking the attached lobby', [match]),
      sub('status', 'Show saved lobby evidence and manual fallback', [match]),
      sub('confirm', 'Captain/admin: confirm the attached lobby and roster', [
        {
          name: 'binding_id',
          description: 'Association ID shown by /lobby status',
          type: 3,
          required: true,
        },
        match,
      ]),
      sub('facts', 'Declare your faction; captains may add map, duration and recording reference', [
        {
          name: 'selection',
          description: 'Your actual civilization, faction, god, clan or race',
          type: 3,
          max_length: 100,
        },
        { name: 'map', description: 'Captain/admin: map played', type: 3, max_length: 100 },
        {
          name: 'elapsed_seconds',
          description: 'Captain/admin: elapsed game duration in seconds',
          type: 4,
          min_value: 0,
          max_value: 31536000,
        },
        {
          name: 'recording_url',
          description: 'Captain/admin: external HTTPS recording reference (no file upload)',
          type: 3,
          max_length: 2000,
        },
        match,
      ]),
      sub('replays', 'Refresh provider recording availability and links (no file download)', [
        match,
      ]),
      sub('link', 'Declare your game profile (does not prove account ownership)', [
        {
          name: 'profile_id',
          description: 'Native profile ID, SteamID64, region:realm:profile or network:id',
          type: 3,
          required: true,
        },
        queue,
        { ...user, description: 'Organizer only: assign this player’s profile' },
      ]),
    ],
  },
  { name: 'teams', description: 'Show saved teams and captains', type: 1, options: [match] },
  {
    name: 'rally',
    description: 'Organizer: invite eligible voice participants to this queue',
    type: 1,
    options: [
      queue,
      {
        name: 'voice_channel',
        description: 'Allowed voice channel (defaults to yours)',
        type: 7,
        channel_types: [2, 13],
      },
    ],
  },
  {
    name: 'gold',
    description: 'Show your balance, ledger or this game’s gold board',
    type: 1,
    options: [
      queue,
      {
        name: 'view',
        description: 'Gold view',
        type: 3,
        choices: ['balance', 'history', 'leaderboard'].map((value) => ({ name: value, value })),
      },
    ],
  },
  {
    name: 'maps',
    description: 'View or redo the match’s optional map selection',
    type: 1,
    options: [
      sub('show', 'Show current maps and voting', [match]),
      sub('redo', 'Captain/admin: redo map selection', [match]),
    ],
  },
  {
    name: 'predictions',
    description: 'Manage the current prediction pool',
    type: 1,
    options: [
      sub('leaderboard', 'Show correct predictions with sample counts', [queue]),
      sub('close', 'Captain/admin: close betting before play', [match]),
    ],
  },
  {
    name: 'rank',
    description: 'Show a player’s local game rating',
    type: 1,
    options: [
      user,
      queue,
      {
        name: 'detailed',
        description: 'Include recent results, peak rating and profile references',
        type: 5,
      },
    ],
  },
  {
    name: 'leaderboard',
    description: 'Show this community’s eligible game ratings',
    type: 1,
    options: [
      queue,
      {
        name: 'page',
        description: 'Page number, starting at 1',
        type: 4,
        min_value: 1,
        max_value: 10000,
      },
    ],
  },
  {
    name: 'profile',
    description: 'Manage optional game profile references',
    type: 1,
    options: [
      sub('link', 'Link your external profile without requiring API verification', [
        {
          name: 'provider',
          description: 'Game provider, for example steam or aoe2',
          type: 3,
          required: true,
        },
        { name: 'profile_id', description: 'External profile ID', type: 3, required: true },
        { name: 'url', description: 'HTTPS profile URL when available', type: 3 },
        queue,
      ]),
      sub('unlink', 'Remove your profile reference', [
        { name: 'provider', description: 'Game provider', type: 3, required: true },
        { name: 'profile_id', description: 'External profile ID', type: 3, required: true },
        queue,
      ]),
      sub('show', 'Show your linked profiles and detailed stats', [queue]),
      sub('verify', 'Verify control of your supported Steam, Battle.net or BAR account', [queue]),
      sub('hide', 'Hide yourself from this game’s leaderboards and others’ statistics', [queue]),
      sub('unhide', 'Show yourself again on this game’s leaderboards and statistics', [queue]),
    ],
  },
  {
    name: 'subme',
    description: 'Request or cancel looking for a substitute',
    type: 1,
    options: [match],
  },
  {
    name: 'subfor',
    description: 'Take the place of a player who requested a substitute',
    type: 1,
    options: [{ ...user, required: true }, match],
  },
  {
    name: 'subauto',
    description: 'Replace a player from the queue; keep teams once play has started',
    type: 1,
    options: [user, match],
  },
  {
    name: 'report',
    description: 'Captain: report a result',
    type: 1,
    options: [
      sub('loss', 'Losing captain: finalize the opposing team’s win', [match]),
      sub('draw', 'Both captains must agree to a draw', [match]),
      sub('abort', 'Both captains must agree to abort without rating changes', [match]),
    ],
  },
  {
    name: 'rating',
    description: 'Administrator: adjust a local game rating',
    type: 1,
    default_member_permissions: '32',
    options: [
      sub('seed', 'Explicitly seed or adjust one player’s rating', [
        { ...user, required: true },
        {
          name: 'rating',
          description: 'Rating for this community and game',
          type: 4,
          required: true,
          min_value: 0,
          max_value: 10000,
        },
        queue,
        {
          name: 'deviation',
          description: 'Optional rating deviation',
          type: 4,
          min_value: 1,
          max_value: 3000,
        },
      ]),
      sub('hide', 'Hide a player from public and member statistics', [
        { ...user, required: true },
        queue,
      ]),
      sub('unhide', 'Restore a player’s visibility subject to leaderboard thresholds', [
        { ...user, required: true },
        queue,
      ]),
      sub('sync', 'Retry configured rank role and nickname delivery', [
        { ...user, required: true },
        queue,
      ]),
    ],
  },
  {
    name: 'match',
    description: 'Administrator: manage match rosters and results',
    type: 1,
    default_member_permissions: '32',
    options: [
      sub('abort', 'Release an unfinished match without rating changes', [
        { ...match, required: true },
      ]),
      sub('report', 'Record a result, or re-report an undone match', [
        { ...match, required: true },
        result,
      ]),
      sub('undo', 'Reverse recorded rating deltas and retain an audit', [
        { ...match, required: true },
      ]),
      sub('recover', 'Recover a deleted card or retry its blocked delivery', [
        { ...match, required: true },
      ]),
      sub('substitute', 'Force a player replacement in an unfinished match', [
        { ...match, required: true },
        { ...user, required: true },
        {
          name: 'replacement',
          description: 'Human server member replacing the outgoing player',
          type: 6,
          required: true,
        },
      ]),
      sub('put', 'Place or move a player on a team', [
        { ...match, required: true },
        { ...user, required: true },
        {
          name: 'side',
          description: 'Team to place the player on',
          type: 3,
          required: true,
          choices: [
            { name: 'Alpha', value: 'alpha' },
            { name: 'Beta', value: 'beta' },
          ],
        },
      ]),
      sub('create', 'Record a game played outside the bot with two teams', [
        {
          name: 'alpha',
          description: 'Alpha players: mentions or Discord IDs, separated by spaces',
          type: 3,
          required: true,
        },
        {
          name: 'beta',
          description: 'Beta players: mentions or Discord IDs, separated by spaces',
          type: 3,
          required: true,
        },
        { ...result, choices: result.choices.filter((c) => c.value !== 'abort') },
        queue,
      ]),
    ],
  },
  {
    name: 'moderation',
    description: 'Administrator: community queue access restrictions',
    type: 1,
    default_member_permissions: '32',
    options: [
      sub('noadd', 'Temporarily prevent a player joining queues in this community', [
        { ...user, required: true },
        {
          name: 'seconds',
          description: 'Restriction duration in seconds',
          type: 4,
          required: true,
          min_value: 1,
          max_value: 1209600,
        },
        { name: 'reason', description: 'Reason retained in the audit', type: 3, max_length: 200 },
        queue,
      ]),
      sub('forgive', 'Remove a player’s active no-add restriction', [
        { ...user, required: true },
        queue,
      ]),
    ],
  },
];

const adminRoutes = new Map();
// [operation, subcommand, /manage name, { required, description }]
const adminGroups = [
  {
    name: 'setup',
    description: 'Check community setup or open the web organizer',
    actions: [
      ['setup', 'status'],
      ['setup', 'web'],
    ],
  },
  {
    name: 'queue',
    description: 'Manage waiting players and start matches',
    actions: [
      ['add', '', 'add', { required: ['user'], description: 'Add a player to a waiting queue' }],
      [
        'remove',
        '',
        'remove',
        { required: ['user'], description: 'Remove a player from a waiting queue' },
      ],
      ['queue', 'start'],
      ['queue', 'clear'],
      ['queue', 'split'],
      ['rally', '', 'rally'],
    ],
  },
  {
    name: 'match',
    description: 'Manage teams, results, maps and betting',
    actions: [
      ['match', 'abort', 'cancel'],
      ['match', 'report'],
      ['match', 'undo'],
      ['match', 'recover'],
      ['match', 'substitute'],
      [
        'subauto',
        '',
        'sub-from-queue',
        {
          required: ['match', 'user'],
          description: 'Replace a player with the next eligible player waiting in the queue',
        },
      ],
      ['match', 'put'],
      ['match', 'create', 'record'],
      ['maps', 'redo', 'maps-redo'],
      ['predictions', 'close', 'close-betting'],
    ],
  },
  {
    name: 'player',
    description: 'Manage ratings, visibility, ranks and queue restrictions',
    actions: [
      ['rating', 'seed'],
      ['rating', 'hide'],
      ['rating', 'unhide'],
      ['rating', 'sync'],
      ['moderation', 'noadd', 'restrict'],
      ['moderation', 'forgive', 'unrestrict'],
    ],
  },
];
export const adminCommand = {
  name: 'manage',
  description: 'Organizer tools: setup, queues, matches and players',
  type: 1,
  default_member_permissions: '32',
  options: adminGroups.map((group) => ({
    name: group.name,
    description: group.description,
    type: 2,
    options: group.actions.map(([command, action, alias, extra = {}]) => {
      const operation = operations.find((c) => c.name === command);
      const source = action ? operation?.options?.find((o) => o.name === action) : operation;
      if (!source) throw new Error(`Missing admin operation: ${command} ${action}`);
      const name = alias || action;
      const options = (source.options ?? [])
        .map((o) => (extra.required?.includes(o.name) ? { ...o, required: true } : o))
        .sort((a, b) => Number(!!b.required) - Number(!!a.required));
      adminRoutes.set(`${group.name}:${name}`, {
        command,
        subcommand: action,
        required: options.filter((o) => o.required).map((o) => o.name),
        arguments: options.map((o) => o.name),
      });
      const description =
        extra.description ??
        source.description
          .replace(/^(Administrator|Captain\/admin|Organizer): /, '')
          .replace(/^[a-z]/, (letter) => letter.toUpperCase());
      return sub(name, description, options);
    }),
  })),
};

export function adminCommandRoute(group, action) {
  return adminRoutes.get(`${group}:${action}`) ?? null;
}

// Public wording keyed by command path; generic options fall back to `optionText`.
const plain = {
  help: 'How to join, play and report matches here',
  add: 'Join the waiting queue in this channel',
  remove: 'Leave the waiting queue',
  queue: 'See who is waiting, or start the match early',
  'queue show': 'Show who is waiting in the queue',
  'queue start': 'Start the match now with the players who are waiting',
  teams: 'Show your match: teams, captains and maps',
  report: 'Report how your match ended (for captains)',
  'report loss': 'Your team lost: give the win to the other team',
  'report draw': 'Ask for a draw; the other captain must agree',
  'report abort': 'Ask to cancel with no rating change; the other captain must agree',
  subme: 'Ask for someone to take your place (run it again to cancel)',
  subfor: 'Take the place of a player who asked for a substitute',
  'subfor user': 'The player you are replacing',
  rank: 'Show your rating, or another player’s',
  'rank user': 'Player to look up (defaults to you)',
  'rank detailed': 'Also show recent results, peak rating and linked accounts',
  leaderboard: 'Show the top-rated players',
  gold: 'Your play-money gold: balance, history and top players',
  'gold view': 'What to show (defaults to your balance)',
  profile: 'Your linked game accounts and privacy',
  'profile link': 'Link your in-game account for this channel’s game',
  'profile link profile_id': 'Your in-game profile ID or SteamID64',
  'profile link provider': 'Only for other sites, for example steam; leave empty for this game',
  'profile link url': 'Profile page link, if the site has one',
  'profile link user': 'Organizers only: link this player instead of you',
  'profile unlink': 'Remove a linked account',
  'profile unlink provider': 'Account site, as listed by /profile show',
  'profile unlink profile_id': 'Account ID, as listed by /profile show',
  'profile show': 'Show your linked accounts and detailed stats',
  'profile verify': 'Prove you own your Steam, Battle.net or BAR account',
  'profile hide': 'Hide yourself from leaderboards and other players’ stats',
  'profile unhide': 'Show yourself again on leaderboards and stats',
  lobby: 'Connect your match to its in-game lobby',
  'lobby list': 'Show open lobbies or games already running',
  'lobby list phase': 'Open lobbies or running games',
  'lobby attach': 'Attach your match’s in-game lobby by ID or link',
  'lobby attach reference': 'Lobby ID or lobby link',
  'lobby unlink': 'Stop tracking the attached lobby (captains)',
  'lobby status': 'Show what the bot knows about your match’s lobby',
  'lobby confirm': 'Confirm the attached lobby and players (captains)',
  'lobby confirm binding_id': 'The ID shown by /lobby status',
  'lobby facts': 'Add the civilization you played; captains can add map, length and recording',
  'lobby facts selection': 'The civilization, faction or race you played',
  'lobby facts map': 'Map played (captains)',
  'lobby facts elapsed_seconds': 'Game length in seconds (captains)',
  'lobby facts recording_url': 'Link to a recording of the game (captains)',
  'lobby replays': 'Check for replays and recording links of your match',
};
const optionText = {
  queue: 'Which queue, if this channel has more than one',
  match: 'Match ID (defaults to your current match)',
};
// Which options of an operation are registered publicly; unlisted paths keep all of them.
const shown = {
  add: ['queue'],
  remove: ['queue'],
  queue: ['show', 'start'],
  'queue start': ['queue'],
  lobby: ['list', 'attach', 'unlink', 'status', 'confirm', 'facts', 'replays'],
};
/** @type {<T extends { description: string }>(o: T, path: string) => T} */
const reword = (o, path) => ({
  ...o,
  description:
    plain[path] ?? (o.type === 1 || o.type === 2 ? null : optionText[o.name]) ?? o.description,
  ...(o.options && {
    options: o.options
      .filter((x) => !shown[path] || shown[path].includes(x.name))
      .map((x) => reword(x, `${path} ${x.name}`)),
  }),
});
const operation = (name) => operations.find((c) => c.name === name);
const profileLink = operation('profile').options.find((o) => o.name === 'link').options;
const lobbyLink = operation('lobby').options.find((o) => o.name === 'link').options;
// /profile link without a provider is the game-aware /lobby link handler (interactions.ts).
/** @type {Record<string, (typeof operations)[number]>} */
const publicOperations = {
  profile: {
    ...operation('profile'),
    options: operation('profile').options.map((o) =>
      o.name === 'link'
        ? {
            ...o,
            options: [
              lobbyLink.find((x) => x.name === 'profile_id'),
              { ...profileLink.find((x) => x.name === 'provider'), required: false },
              profileLink.find((x) => x.name === 'url'),
              queue,
              user,
            ],
          }
        : o,
    ),
  },
  gold: {
    ...operation('gold'),
    options: operation('gold').options.map((o) =>
      o.name === 'view'
        ? {
            ...o,
            choices: [
              { name: 'My balance', value: 'balance' },
              { name: 'My history', value: 'history' },
              { name: 'Richest players', value: 'richest' },
              { name: 'Best predictors', value: 'predictors' },
            ],
          }
        : o,
    ),
  },
};
const everyone = [
  'add',
  'remove',
  'queue',
  'teams',
  'report',
  'subme',
  'subfor',
  'rank',
  'leaderboard',
  'gold',
  'profile',
  'lobby',
].map((name) => reword(publicOperations[name] ?? operation(name), name));

const registered = [
  {
    name: 'test',
    description: 'Admins and bot moderators: populate a live queue or mark a match as a test',
    type: 1,
    default_member_permissions: '32',
    options: [
      sub('populate', 'Add random real members to a live queue (default 8)', [
        queue,
        {
          name: 'count',
          description: 'Number of players to add (default 8)',
          type: 4,
          min_value: 2,
          max_value: 16,
        },
      ]),
      sub('checkin', 'Mark the live match as TEST and check everyone in; no recorded result', [
        { ...match, description: 'Match ID from /admin; required when several matches are active' },
        queue,
      ]),
    ],
  },
  { name: 'help', description: plain.help, type: 1 },
  ...everyone,
  {
    name: 'civpick',
    description: 'Pick civilizations in secret for your match',
    type: 1,
    options: [
      { type: 3, name: 'match', description: optionText.match },
      {
        type: 4,
        name: 'minutes',
        description: 'Time to pick, in minutes (default 3)',
        min_value: 1,
        max_value: 10,
      },
      {
        type: 5,
        name: 'redo',
        description: 'Start a new round (whoever started it, or an organizer)',
      },
    ],
  },
  {
    name: 'admin',
    description: 'Post the queue control panel: start matches, results and history',
    type: 1,
    default_member_permissions: null,
    options: [queue],
  },
  adminCommand,
];

const appEnvs = ['local', 'staging', 'production'];
/** Global registration needs contexts/integration_types, or commands also appear in DMs and user installs. */
export function commandsFor(appEnv) {
  if (!appEnvs.includes(appEnv)) throw new Error(`Unknown app environment: ${appEnv}`);
  return registered
    .filter((c) => appEnv !== 'production' || c.name !== 'test')
    .map((c) => ({ ...c, contexts: [0], integration_types: [0] }));
}

export const commands = commandsFor('staging');
