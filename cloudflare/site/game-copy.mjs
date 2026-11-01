// Authored game-night examples. Connection claims remain conditional on Setup.
export const gameCopy = {
  aoe2: {
    short: '4v4 team nights, Arabia and Nomad queues',
    heading: 'How an AoE2 night works',
    specific: [
      [
        'Choose the evening before choosing teams',
        'A queue called “Friday 4v4 Arabia” tells players much more than “Main queue”. For a Nomad group, agree on your usual lobby rules and how civilizations are chosen before asking everyone to join. Keep separate queues when the same friends enjoy different formats, so a player does not wait for Arabia while the host prepares a water map.',
      ],
      [
        'Give team-game experience room to develop',
        'An experienced one-versus-one player and a regular team-game player may bring different strengths. Start with captains if the group wants to discuss positions, communication and familiarity with the map. Local ratings become more useful as your community records its own games; an official ranked rating is useful context, not a guarantee that two teams will feel equal.',
      ],
      [
        'Keep the lobby easy to recognize',
        'The host still creates the game in Definitive Edition and shares the lobby details in Discord. A distinctive agreed lobby name helps everyone find the same game. Optional AoE2 connections use public Companion data when activated; they cannot make every private game visible. Keep the captain-reporting routine familiar even when a connection helps associate a lobby with the match.',
      ],
    ],
    limits: [
      'This workflow targets Definitive Edition; other editions are not a verified connection path.',
      'Private games and ambiguous rematches may not appear in public data.',
      'A disappearing lobby does not establish a start or a winner.',
      'Profile and replay links depend on what the public data service supplies.',
    ],
    question: [
      'Can we run Arabia and Nomad nights?',
      'Yes. Give each format a named queue and explain its rules in the Discord channel. Queues for the same configured game share community rating settings.',
    ],
  },
  aoe3: {
    short: 'Separate Supremacy and treaty pickup sessions',
    heading: 'How an AoE3 night works',
    specific: [
      [
        'Make the mode visible',
        'Supremacy and treaty games ask players to make different commitments. Put the mode in the queue name, and state the treaty duration and any community rules in the channel. For a learning night, say that new players are welcome before the queue opens. That is more useful than asking people to join first and discovering that half the group expected another format.',
      ],
      [
        'Discuss civilizations and experience',
        'Captains can consider civilization preferences, team experience and the format when choosing sides. Balanced teams instead use the local ratings built from your community’s recorded results. Neither method knows every detail of a player’s official ladder history. Agree on expectations for unfamiliar civilizations or returning players so that the first evening produces games people want to repeat.',
      ],
      [
        'Use Definitive Edition as the starting point',
        'The host creates the lobby and shares its details with the checked-in group. Optional account and game connections target Definitive Edition and need their own availability checks in Setup. A Steam account reference alone does not prove ownership of a specific game profile. Players can still join a queue, play and report a result while a connection is unavailable.',
      ],
    ],
    limits: [
      'Connection scope is Definitive Edition, not every older AoE3 release.',
      'A Steam reference alone does not verify ownership of a game profile.',
      'Public match history can omit private games.',
      'Captains confirm results; missing history is not a loss.',
    ],
    question: [
      'Can we organize treaty games?',
      'Yes. Queues organize the people and teams; agree on the treaty duration and lobby rules in Discord. The bot does not enforce in-game treaty rules.',
    ],
  },
  aoe4: {
    short: 'Community team games with local ratings',
    heading: 'How an AoE4 night works',
    specific: [
      [
        'Bring familiar teammates together',
        'A regular team-night queue gives friends a clear place to gather before opening the game. Name the queue for the intended team size and tell players whether the evening is competitive or casual. If your group mixes returning players with frequent ladder players, allow time to discuss civilization choices and communication before the host creates the lobby.',
      ],
      [
        'Build a rating for this group',
        'Official matchmaking results and a community’s private evenings are different records. Community ratings follow results confirmed here, so they describe this group’s history. Captains are a useful starting point while local records are sparse. As more games are recorded, organizers can try balanced teams and compare whether the resulting matches suit the group’s expectations.',
      ],
      [
        'Know which kind of game you are finding',
        'A waiting custom lobby and an ongoing matchmaking game are different things. Optional AoE4 connections can only help with the supported information available to them; a similar start time does not establish that a game belongs to your group. Share lobby details in Discord and let captains report the actual result when the game finishes, whether or not an optional connection is active.',
      ],
    ],
    limits: [
      'Community ratings do not replace official AoE4 matchmaking ratings.',
      'Custom-lobby and ongoing-game discovery have different coverage.',
      'Approximate times alone cannot identify your group’s game.',
      'Private or missing history cannot establish a result.',
    ],
    question: [
      'Do players need an official rating first?',
      'No. The manual queue workflow uses local community records. Captains can form teams while new players establish a history with your group.',
    ],
  },
  aom: {
    short: 'Retold team nights and god-choice discussions',
    heading: 'How a Retold night works',
    specific: [
      [
        'Set expectations for your Retold group',
        'A named queue works for both a scheduled mythology night and a spontaneous team game. Put the team size and map expectations where players can see them before joining. If the evening has conventions about god choices or random selections, explain them in the channel rather than relying on everyone remembering what happened last week.',
      ],
      [
        'Let captains account for familiarity',
        'Players can be comfortable with very different gods and play styles. Captains can discuss those preferences when making teams, especially when the group includes newcomers. Balanced teams use this community’s confirmed results instead. Local ratings help describe repeated evenings together; they do not claim to measure every player’s ability across all gods, maps and official ranked modes.',
      ],
      [
        'Keep editions distinct',
        'These setup instructions target Age of Mythology: Retold. The original game and Extended Edition should not be treated as sharing the same accounts or optional connections. The host creates the Retold game and shares its lobby details in Discord. If optional game information is unavailable, check-in and manual reporting still give the group a complete way to organize the night.',
      ],
    ],
    limits: [
      'Connection scope is Retold; original and Extended Edition coverage is not implied.',
      'God preferences and lobby rules still need player agreement.',
      'A closed lobby does not prove a winner.',
      'Optional public information may omit private games.',
    ],
    question: [
      'Does the bot choose gods for everyone?',
      'These instructions use player-agreed god choices in the game lobby. Queueing and team formation do not enforce your in-game selection rules.',
    ],
  },
  bar: {
    short: 'Ready-player queues for games up to 8v8',
    heading: 'How a BAR night works',
    specific: [
      [
        'Gather people before assigning roles',
        'BAR evenings can bring together players who enjoy different positions and levels of competition. Explain the map, learning expectations and team size before opening the queue. Check-in gives the host a concrete list of people ready to join, rather than a list of everyone who expressed interest earlier in the day.',
      ],
      [
        'Match the queue to the lobby size',
        'Configure an even match size between two and sixteen players. An 8v8 queue reaches the current maximum; larger community events need separate matches or another organizing approach. Captains can discuss role preferences before choosing sides. Balanced teams use local records and do not automatically understand every player’s preferred position or experience on a particular map.',
      ],
      [
        'Keep account and replay references understandable',
        'The host creates the BAR battle and shares where to join. Optional BAR connections use native account information and can require operator setup before they are available. A battle reference, a completed match and a replay are related but different records. Treat the actual players and agreed teams as the source of truth, with captains confirming the result after the game.',
      ],
    ],
    limits: [
      'A single queue supports at most sixteen players, including 8v8.',
      'Connections may require BAR service access and operator configuration.',
      'An occupant list alone does not verify the declared teams.',
      'Replay availability depends on the game information service.',
    ],
    question: [
      'Can we run an 8v8 evening?',
      'Yes. Set sixteen players per match and agree on the map and role expectations. Larger single matches exceed the current queue limit.',
    ],
  },
  wc3: {
    short: 'Team nights with optional W3Champions connections',
    heading: 'How a Warcraft III night works',
    specific: [
      [
        'Say which Warcraft III evening you mean',
        'A queue name should tell players the intended team format and where the group will play. Friends arranging a team-game night have different needs from a public ladder search. Agree on the map and race-selection expectations before the host creates the game, and explain whether newcomers or returning players are joining a learning session.',
      ],
      [
        'Choose teams with race preferences in mind',
        'Captains can discuss race combinations, communication and familiarity with the map before selecting sides. Balanced teams use your community’s recorded results, which remain separate from W3Champions ladder standings. Keep those two ratings distinct when explaining teams to players. A newly joined friend may need several community games before a local rating describes their experience well.',
      ],
      [
        'Treat W3Champions as a specific connection',
        'Optional Warcraft III connections focus on W3Champions and its supported modes. That does not promise tracking for arbitrary Battle.net custom games or every private lobby. A BattleTag reference also does not prove that a person owns the account. The manual queue and captain-reporting path is useful even when no supported connection can identify the group’s game.',
      ],
    ],
    limits: [
      'Optional connections cover supported W3Champions modes, not all Battle.net games.',
      'Community standings are separate from the W3Champions ladder.',
      'A BattleTag reference alone does not verify account ownership.',
      'Ambiguous or private games need manual coordination and reporting.',
    ],
    question: [
      'Must our community use W3Champions?',
      'Manual queues and confirmed results do not require an active connection. W3Champions coverage applies specifically to the optional Warcraft III connection.',
    ],
  },
  zero_hour: {
    short: 'Zero Hour pickup groups with manual reporting',
    heading: 'How a Zero Hour night works',
    specific: [
      [
        'Make the rules part of the invitation',
        'Zero Hour groups often have established map, faction and conduct conventions. Put those expectations in the Discord channel before people join the queue. A descriptive queue name can distinguish a casual evening from a more competitive session, and helps a returning player understand what the host is preparing without reading a long conversation.',
      ],
      [
        'Use the team method your group understands',
        'Captains can discuss faction preferences and experience on the chosen map before selecting teams. Balanced teams use local results recorded by this community. The bot does not inspect every detail of a player’s past games or enforce your in-game rules. Keep the confirmed roster visible while the host creates the game and tells everyone how to join.',
      ],
      [
        'Keep Zero Hour separate from original Generals',
        'This selectable game is Generals: Zero Hour. Original C&C Generals is a distinct legacy entry, not the same optional connection. GeneralsOnline support is conditional on service access, operator configuration and release approval. Start with the manual game-night routine; optional information should help an established workflow rather than become a prerequisite for gathering friends and confirming their results.',
      ],
    ],
    limits: [
      'Zero Hour and original C&C Generals are distinct game entries.',
      'Optional GeneralsOnline access is conditional and may be unavailable.',
      'The bot does not enforce map, faction or other in-game rules.',
      'Captains report the outcome when game information is missing or ambiguous.',
    ],
    question: [
      'Does a Zero Hour queue need GeneralsOnline access?',
      'No. The manual workflow organizes players and records captain-confirmed results. Optional connections have separate availability requirements.',
    ],
  },
};
