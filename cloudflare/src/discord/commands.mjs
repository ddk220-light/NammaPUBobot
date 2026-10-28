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
      sub('link', 'Declare your game profile (does not prove account ownership)', [
        { name: 'profile_id', description: 'Public game profile ID', type: 3, required: true },
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
        { name: 'url', description: 'HTTPS profile URL', type: 3, required: true },
        queue,
      ]),
      sub('unlink', 'Remove your profile reference', [
        { name: 'provider', description: 'Game provider', type: 3, required: true },
        { name: 'profile_id', description: 'External profile ID', type: 3, required: true },
        queue,
      ]),
      sub('show', 'Show your linked profiles and detailed stats', [queue]),
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
    description: 'Replace a player with the next eligible queued player and rebalance',
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
      ['add', '', 'add'],
      ['remove', '', 'remove'],
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
  description: 'Community administration: setup, queues, matches and players',
  type: 1,
  default_member_permissions: '32',
  options: adminGroups.map((group) => ({
    name: group.name,
    description: group.description,
    type: 2,
    options: group.actions.map(([command, action, alias]) => {
      const operation = operations.find((c) => c.name === command);
      const source = action ? operation?.options?.find((o) => o.name === action) : operation;
      if (!source) throw new Error(`Missing admin operation: ${command} ${action}`);
      const name = alias || action;
      const options = (source.options ?? [])
        .map((o) =>
          ['add', 'remove'].includes(command) && o.name === 'user' ? { ...o, required: true } : o,
        )
        .sort((a, b) => Number(!!b.required) - Number(!!a.required));
      adminRoutes.set(`${group.name}:${name}`, {
        command,
        subcommand: action,
        required: options.filter((o) => o.required).map((o) => o.name),
        arguments: options.map((o) => o.name),
      });
      const description =
        command === 'add'
          ? 'Add a player to a waiting queue'
          : command === 'remove'
            ? 'Remove a player from a waiting queue'
            : source.description
                .replace(/^(Administrator|Captain\/admin|Organizer): /, '')
                .replace(/^[a-z]/, (letter) => letter.toUpperCase());
      return sub(name, description, options);
    }),
  })),
};

export function adminCommandRoute(group, action) {
  return adminRoutes.get(`${group}:${action}`) ?? null;
}

export const commands = [
  {
    name: 'test',
    description: 'Admins and bot moderators: run a temporary, unrated setup test',
    type: 1,
    default_member_permissions: null,
    dm_permission: false,
    options: [
      sub('populate', 'Add random real members to a separate test queue (no pings)', [
        queue,
        {
          name: 'count',
          description: 'Number of players (default 8)',
          type: 4,
          min_value: 2,
          max_value: 16,
        },
      ]),
      sub('start', 'Start an unrated test match from the test queue', [queue]),
      sub('checkin', 'Check in every player in the test match', [queue]),
      sub('maps', 'Simulate everyone’s map votes or the captain’s map selection', [queue]),
      sub('result', 'Finish the test match without ratings, payouts or match history', [
        { ...result, required: true },
        queue,
      ]),
      sub('status', 'Show test progress, expiry and delivery status', [queue]),
      sub('reset', 'Delete only this queue’s temporary test session and its cards', [queue]),
    ],
  },
  {
    name: 'admin',
    description: 'Open queue status, match history and administrator controls',
    type: 1,
    default_member_permissions: null,
    dm_permission: false,
    options: [queue],
  },
  {
    name: 'civpick',
    description: 'Start or show hidden civilization picks for a match',
    type: 1,
    options: [
      { type: 3, name: 'match', description: 'Optional match ID; defaults to your active match' },
      {
        type: 4,
        name: 'minutes',
        description: 'Time to pick (default 3 minutes)',
        min_value: 1,
        max_value: 10,
      },
      { type: 5, name: 'redo', description: 'Start a new round (initiator or administrator only)' },
    ],
  },
  adminCommand,
  ...operations
    .filter((c) => !['setup', 'rating', 'match', 'moderation'].includes(c.name))
    .map((c) =>
      c.name === 'queue'
        ? {
            ...c,
            options: c.options
              .filter((o) => ['show', 'start'].includes(o.name))
              .map((o) =>
                o.name === 'start'
                  ? sub('start', 'Start with waiting players, including uneven teams', [queue])
                  : o,
              ),
          }
        : c,
    ),
];
