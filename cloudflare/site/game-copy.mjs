// What is specific to each game. Every claim is checked against src/games/catalog.ts: map votes
// need a pool the organizer types in except for AoE2, and every optional game connection is beta.
const mapPool =
  'Turn on map voting for a queue and type in your map list. Players vote on the match card, or captains choose.';
export const gameCopy = {
  aoe2: {
    short: 'Civ picks, map votes and beta lobby tracking for 4v4 team games',
    title: 'AoE2 Discord bot for pickup games',
    description:
      'Run Age of Empires II pickup games on Discord: /add queues, check-in, balanced 4v4 teams, civ picks, map votes and your own Elo ladder.',
    night:
      'A typical AoE2 night is eight people and a 4v4 team game on Arabia or Nomad. Make one queue per format, like “Friday 4v4 Arabia”, so nobody waits in the wrong line. The ladder belongs to your server and learns from your own team games, not from ranked 1v1.',
    features: [
      [
        'Civ picks',
        'Turn on civ picks and anyone in the match can start a round with <code>/civpick</code>. Everyone picks from the same shortlist in secret and the picks show together; Random is always there. Civ picks are only for AoE2.',
      ],
      [
        'Map votes with a built-in pool',
        'The AoE2 map list comes ready: Arabia, Arena, Nomad, Black Forest, MegaRandom and ten more. Players vote on the match card, or captains choose.',
      ],
      [
        'Profile links',
        'Players add their AoE2 profile with <code>/profile link</code>, so others can look them up. A link points to a profile; it doesn’t prove who owns it.',
      ],
      [
        'Lobby tracking (beta)',
        'A player attaches the game’s lobby with <code>/lobby attach</code>, and the bot follows it through public AoE2 Companion data. When the game ends it suggests a result, and a captain confirms it.',
      ],
    ],
    limits: [
      'It doesn’t create the lobby: the host still makes the game and shares it.',
      'Lobby tracking is in beta and only sees public lobbies; private games and quick rematches may not show up.',
      'A lobby closing doesn’t prove the game started or who won, so a captain always reports the result.',
    ],
    question: [
      'Can we run Arabia and Nomad nights?',
      'Yes. Make one queue for each and name it after the format. Queues for the same game share one ladder, so ratings carry across.',
    ],
  },
  aoe3: {
    short: 'Supremacy or treaty queues; players link their DE profiles',
    title: 'AoE3 Discord bot for pickup games',
    description:
      'Run Age of Empires III pickup games on Discord: supremacy and treaty queues, check-in, balanced teams, map votes and your server’s own ratings.',
    night:
      'AoE3 groups often split their evenings between supremacy and treaty. Make a queue for each and put the mode, and the treaty length, in its name. Ratings belong to your server and grow from the games you play together.',
    features: [
      ['Map votes', mapPool],
      [
        'Profile links',
        'Players add their Definitive Edition profile with <code>/profile link</code>. A link points to a profile; it doesn’t prove who owns it.',
      ],
      [
        'Lobby tracking (beta)',
        'Optional, using public Age of Empires match data. It may not be switched on yet; the console shows its status for your server.',
      ],
    ],
    limits: [
      'Treaty rules are up to your group; the bot can’t enforce them in the game.',
      'Profile links and lobby tracking cover Definitive Edition only.',
      'Public match history leaves out private games, so captains report results.',
    ],
    question: [
      'Can we organize treaty games?',
      'Yes. The bot gathers players and makes teams; agree the treaty length in your channel before the host makes the lobby.',
    ],
  },
  aoe4: {
    short: 'Team-game queues; players link their AoE4 profiles',
    title: 'AoE4 Discord bot for pickup games',
    description:
      'Run Age of Empires IV pickup games on Discord: team-game queues, check-in, balanced teams, map votes and a ladder that belongs to your server.',
    night:
      'AoE4 team nights work best with a queue named for its size and mood, like “4v4 casual” or “2v2 competitive”. Your server’s ratings are separate from ranked matchmaking and start fresh with your group. They get sharper with every game you record.',
    features: [
      ['Map votes', mapPool],
      [
        'Profile links',
        'Players add their AoE4 profile with <code>/profile link</code>. A link points to a profile; it doesn’t prove who owns it.',
      ],
      [
        'Lobby tracking (beta)',
        'Optional, using public AoE4 match data. It may not be switched on yet; the console shows its status for your server.',
      ],
    ],
    limits: [
      'Your server’s ratings don’t replace ranked matchmaking ratings.',
      'Lobby tracking is in beta and can’t tell your game from a stranger’s by start time alone.',
      'Missing match history never counts as a loss; captains report results.',
    ],
    question: [
      'Do players need a ranked rating first?',
      'No. Everyone starts level on your server’s ladder and moves with the games you record.',
    ],
  },
  aom: {
    short: 'Retold team games; players link their Retold profiles',
    title: 'Age of Mythology Discord bot for pickup games',
    description:
      'Run Age of Mythology: Retold pickup games on Discord: queues, check-in, balanced teams, map votes and a ladder for your own community.',
    night:
      'Run a named queue for a scheduled mythology night or a quick team game. Say in the channel how gods are chosen, random or free pick, before people join. Ratings come from your group’s own games and don’t try to copy the official ladder.',
    features: [
      ['Map votes', mapPool],
      [
        'Profile links',
        'Players add their Retold profile with <code>/profile link</code>. A link points to a profile; it doesn’t prove who owns it.',
      ],
      [
        'Lobby tracking (beta)',
        'Optional, using public match data for Retold. It may not be switched on yet; the console shows its status for your server.',
      ],
    ],
    limits: [
      'Profile links and lobby tracking cover Retold only, not the original game or Extended Edition.',
      'Gods are chosen in the game lobby; the bot doesn’t enforce your god rules.',
      'A closed lobby doesn’t prove who won, so captains report results.',
    ],
    question: [
      'Does the bot pick gods for everyone?',
      'No. Players choose gods in the game lobby. The bot gathers the players, makes the teams and keeps the ladder.',
    ],
  },
  bar: {
    short: 'Queues up to 8v8; players link their BAR accounts',
    title: 'Beyond All Reason Discord bot for pickups',
    description:
      'Run Beyond All Reason pickup games on Discord: queues for up to 8v8, check-in, balanced teams, map votes and your community’s own ratings.',
    night:
      'BAR evenings often mix new and experienced players across big team games. One queue holds up to 16 players, enough for an 8v8. Check-in gives the host a firm list of who is actually ready before making the battle.',
    features: [
      ['Map votes', mapPool],
      [
        'Profile links',
        'Players add their BAR account with <code>/profile link</code>. Proving ownership with <code>/profile verify</code> needs extra setup and may not be available yet.',
      ],
      [
        'Battle tracking (beta)',
        'Optional, using public BAR battle data. It may not be switched on yet; the console shows its status for your server.',
      ],
    ],
    limits: [
      'One match holds at most 16 players; anything bigger needs separate matches.',
      'The bot doesn’t know preferred roles or positions; players sort those out in the lobby.',
      'Battle tracking is in beta and may need extra setup before it’s available.',
    ],
    question: [
      'Can we run an 8v8?',
      'Yes. Set 16 players per match. Anything bigger than 8v8 needs to be split into separate matches.',
    ],
  },
  wc3: {
    short: 'Team nights and inhouses; players link their BattleTags',
    title: 'Warcraft III Discord bot for pickup games',
    description:
      'Run Warcraft III pickup games and inhouse nights on Discord: queues, check-in, balanced teams, map votes and a ladder for your own server.',
    night:
      'Warcraft III groups use queues for regular team-game nights and inhouse leagues. Say which mode you play and how races are chosen before the host makes the game. Your server’s ladder is separate from W3Champions standings.',
    features: [
      ['Map votes', mapPool],
      [
        'Profile links',
        'Players add their BattleTag with <code>/profile link</code>. A link points to an account; it doesn’t prove who owns it.',
      ],
      [
        'Game tracking (beta)',
        'Optional, for W3Champions matchmaking games. It may not be switched on yet; the console shows its status for your server.',
      ],
    ],
    limits: [
      'Game tracking covers W3Champions matchmaking only, not Battle.net custom or private games.',
      'Your server’s ladder is separate from the W3Champions ladder.',
      'The bot doesn’t pick races; players choose them in the game.',
    ],
    question: [
      'Do we have to use W3Champions?',
      'No. Queues, teams and captain-reported results work for any Warcraft III game. W3Champions only matters for the optional game tracking.',
    ],
  },
  zero_hour: {
    short: 'Zero Hour pickup groups; captains report results',
    title: 'Zero Hour Discord bot for pickup games',
    description:
      'Run C&C Generals: Zero Hour pickup games on Discord: queues, check-in, balanced teams, map votes and captain-reported results for your server.',
    night:
      'Zero Hour groups usually have house rules about maps, factions and conduct. Put them in the channel before people join, and name the queue for the kind of game you mean. Captains report the result after each game.',
    features: [
      ['Map votes', mapPool],
      [
        'Captain-reported results',
        'Every game is reported by a captain on the match card, so the ladder works without any outside game data.',
      ],
      [
        'GeneralsOnline tracking (beta)',
        'Optional lobby tracking through GeneralsOnline needs a key from GeneralsOnline and may not be available yet.',
      ],
    ],
    limits: [
      'Zero Hour and the original C&C Generals are separate games here.',
      'GeneralsOnline tracking needs extra access and may not be switched on.',
      'The bot doesn’t enforce map or faction rules.',
    ],
    question: [
      'Does a Zero Hour queue need GeneralsOnline?',
      'No. Queues, teams and ratings work without it; captains report each result.',
    ],
  },
};
