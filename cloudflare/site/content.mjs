export const games = [
  [
    'aoe2',
    'age-of-empires-2',
    'Age of Empires II',
    'Definitive Edition',
    'civilizations',
    'A familiar format is eight friends playing a four-versus-four team game. Discuss the map and starting conditions before opening the queue: an Arabia evening and a Nomad evening ask different things of players. Your community rating belongs to this server and is not a replacement for the official ranked ladder. Someone who mainly plays one-versus-one may need time to establish a useful team-game rating here.',
    'Public Companion data can help identify lobbies, but a lobby disappearing does not prove that play began. Similar lobby names are not proof of a matching roster. Private histories and rematches can be ambiguous, so captains confirm results. Profile references and provider recording links depend on what the provider actually supplies; they are not an archive of every replay.',
  ],
  [
    'aoe3',
    'age-of-empires-3',
    'Age of Empires III',
    'Definitive Edition',
    'civilizations',
    'Supremacy and treaty communities often organize their evenings differently. Agree on the mode and any treaty duration before inviting people into the queue, and put that context in a clear queue name. Players should know whether a session is competitive or a relaxed learning game. Use separate queues for distinct formats while remembering that queues for the same configured game share their rating policy.',
    'The account namespace and supported provider data concern Definitive Edition. Older editions should not be assumed to have the same integration. Steam identity alone does not prove ownership of a particular native game profile: a verified mapping is required. Public history may omit private games, and the absence of a history entry cannot establish a result.',
  ],
  [
    'aoe4',
    'age-of-empires-4',
    'Age of Empires IV',
    'Age of Empires IV',
    'civilizations',
    'Community team nights give returning players a way to arrange games with familiar teammates. Choose a team size and agree on the lobby mode before opening the queue. A balanced split uses your local community records, which may differ from a player’s official matchmaking rating. Start with captain selection if your group prefers to discuss roles, then consider rating-based teams as local results accumulate.',
    'An observed ongoing matchmaking game and a waiting custom lobby are different kinds of evidence. Available modes depend on provider support and released capability gates. Do not interpret an approximate time match as confirmation that a public game belongs to your group. Account links and public histories can assist association, while captains remain responsible for confirming the result.',
  ],
  [
    'aom',
    'age-of-mythology',
    'Age of Mythology: Retold',
    'Retold',
    'gods',
    'Retold communities can use a named queue for scheduled team evenings or spontaneous pickup games. Agree on the mode, map and any god-selection conventions in Discord before starting. Newer players can join captain-selected teams while the group learns each person’s strengths. The community leaderboard reflects games played through these queues, rather than claiming to reproduce an official ladder.',
    'The connector targets Retold. Do not assume the original release or Extended Edition uses the same accounts, lobby feeds or history. God selections are game-specific data and should not be described as Age of Empires civilizations. Missing public history or a closed lobby does not prove a winner. Provider observation remains optional and captains confirm the final outcome.',
  ],
  [
    'bar',
    'beyond-all-reason',
    'Beyond All Reason',
    'Beyond All Reason',
    'factions',
    'BAR communities often gather players with different experience and preferred roles. A queue gives the host a concrete list of people ready to play before creating teams. Choose a supported even match size for the session; the current queue limit is sixteen players. Explain whether players are joining a learning game or a competitive session, and keep your community’s map and conduct expectations visible in Discord.',
    'BAR identities use their native account namespace. Battle, match and replay references are related but should not be treated as interchangeable identifiers. A list of occupants does not by itself establish that the declared teams match the observed game. Provider authentication and acceptance can be required before optional observations are usable. Results are confirmed by captains rather than inferred from a missing lobby.',
  ],
  [
    'wc3',
    'warcraft-3',
    'Warcraft III',
    'W3Champions supported modes',
    'races',
    'Warcraft III communities can organize a regular team-game group with queues, check-in and a local rating history. Make the supported mode clear in the queue description and choose a team method suited to your group. Captain selection is useful when players want to discuss race combinations, while balanced teams use the results recorded by this community. Local standings remain distinct from W3Champions ladder standings.',
    'Provider integration focuses on W3Champions and supported modes. It does not imply arbitrary Battle.net custom-game or private-lobby tracking. A BattleTag reference is not equivalent to a proven claim of account ownership. The game mode and player roster must match before evidence can be associated confidently; ambiguous observations leave the manual captain-confirmed workflow available.',
  ],
  [
    'zero_hour',
    'generals-zero-hour',
    'Generals Zero Hour',
    'Generals: Zero Hour',
    'factions',
    'Zero Hour groups can use Discord queues to gather players and agree on a format before the host creates a game. State the rules, team size and map expectations in the channel so participants know what they are joining. Community ratings can help with repeat evenings, but captain selection remains useful when the group wants to account for faction preferences or experience on a particular map.',
    'Optional provider integration depends on GeneralsOnline access and operator configuration. It is conditional support, not a promise that every installation can inspect lobbies. Original C&C Generals is a separate legacy entry and must not be confused with Zero Hour. Manual queueing and captain-confirmed reporting remain the reliable baseline when credentials, acceptance or provider evidence are unavailable.',
  ],
];
export function landing(g) {
  const [, slug, name, edition, selection, night, limits] = g;
  return `
<p>Community Games is a free, open-source Discord bot for ${name} pickup games. Organize a waiting queue, ask players to check in, form teams and keep a community rating history through the organizer dashboard.</p>
<h2>How a ${name} night works</h2><p>${night}</p>
<p>Players join with <code>/add</code> or the queue card. Check-in asks them to confirm they are ready before the match proceeds. Depending on the saved rules, the bot forms balanced teams, selects random teams or lets captains pick. Players then play in ${name}; Discord remains the place where the group coordinates and confirms what happened.</p>
<h2>Queues that fit your community</h2><p>Name each queue for the session it represents and choose its Discord channel, even player count and team method. An enabled queue can start automatically when full. Organizers can also review a manual start using the available waiting roster. Pausing is a saved configuration change, while starting or clearing a queue is a live action with its own confirmation.</p>
<p>Check-in timeouts help prevent a match from being held up by someone who stepped away. Waiting expiry and match timeouts serve different purposes, so review their units and descriptions before changing them. You can keep different session queues while editing shared rating settings once for the game.</p>
<h2>Ratings and team choices</h2><p>Community ratings summarize results recorded here. They are not an official ${name} skill certification or an import of every match a player has ever played. Balanced teams depend on those local records. Captain-picked and random teams offer alternatives when a group prefers a different approach. Choose the rating engine before accumulating records: engine locks protect existing ratings from an incompatible change.</p>
<p>Leaderboard policy controls who can see statistics and which players qualify for the board. Minimum-match and recent-activity rules make that policy visible. Rank labels can be used without Discord roles; assigning rank roles requires the optional Manage Roles permission and correct role hierarchy. Nickname decoration has a separate optional permission. Neither is required merely to run a queue.</p>
<h2>Game connections and ${selection}</h2><p>The game-specific scope is ${edition}. Optional connections are labelled Beta alongside their actual availability. Available, Configuration required and Awaiting activation mean different things. A Beta label never guarantees that observations are enabled on the hosted deployment, and the manual workflow remains usable without a provider connection.</p><p>${limits}</p>
<h2>A dashboard for organizers</h2><p>Queues, Matches, Players and Setup cover the main jobs. Settings edits stay in a community draft as you move between sections. Save reviews the community changes together; errors and revision conflicts preserve your work. Match actions and player adjustments are separate confirmed operations, so a live result is not silently mixed into a settings save.</p>
<p>The phone layout keeps the same destinations and stacks settings for touch use. Imports follow file selection, preview, apply and result: the server preview decides whether a file can be applied. You can inspect player histories and ratings without pretending that a short recent-history window is a complete lifetime record.</p>
<h2>Free software and community control</h2><p>The source is public under GPL-3.0-only. You can inspect it, contribute changes and operate your own installation. Self-hosting requires your own Discord application, configuration and hosting; any provider credentials and infrastructure costs belong to that installation. Consult the current hosted setup path for service availability rather than assuming every source-code feature is already released there.</p>
<h2>Common questions</h2><h3>Can we use more than one game?</h3><p>Yes. Each configured game has its own identity and records. Queues for the same game share rating policy. The setup picker offers seven supported catalog choices plus custom games; legacy saved games remain readable.</p><h3>Will our players appear in search?</h3><p>Existing link-public statistics are unlisted. An organizer must explicitly publish a public leaderboard to search engines. Hidden players remain excluded. Publishing makes names and rankings discoverable, and a search engine may retain an earlier copy after removal.</p><h3>Where do we start?</h3><p>Follow the <a href="/games/${slug}/setup/">${name} setup guide</a>, read the <a href="/guides/discord-permissions/">permission requirements</a>, or <a href="/app/">open the dashboard</a>. This is a community project and is not affiliated with the game's publisher.</p>`;
}
export function setup(g) {
  return `<p>Set up a community queue for ${g[2]} using saved settings and verified Discord permissions. Scope: ${g[3]}. Optional connections are Beta and may require configuration or activation.</p><ol><li>Open <a href="/app/">the dashboard</a>, sign in with Discord and select a server you can manage.</li><li>Open Setup and start guided setup. Invite the bot to the selected server, then return and check access.</li><li>Choose ${g[2]} from the supported game cards. This creates a draft; it does not publish a queue yet.</li><li>Choose a descriptive queue name, an accessible text channel, an even player count between 2 and 16, and captain, balanced or random teams.</li><li>Review check-in, permissions and the shared rating system. Existing same-game queues supply the shared policy. Do not change a locked rating engine.</li><li>Review changes and save. Resolve field errors or revision conflicts without discarding edits accidentally.</li><li>Open the configured channel and use <code>/add</code> or the queue card. Ask players to complete check-in, play, and confirm the result in Discord.</li></ol><h2>Verify the first match</h2><p>Saved configuration and verified permissions mean setup is ready; delivery can still be pending. Check Matches for a failed card delivery, correct the channel permissions, and use a supported refresh action. A missing provider observation does not stop manual reporting.</p><h2>Connection limitations</h2><p>${g[6]}</p><h2>If setup cannot finish</h2><p>Recheck the selected Discord account and Manage Server access. An invitation cannot repair a channel override or role order. Missing channels remain visible until corrected; cached names are unverified labels, not permission checks. A temporary Discord outage retains saved settings and drafts. Review <a href="/guides/discord-permissions/">permissions</a> and <a href="/guides/getting-started/">getting started</a> for recovery.</p><p><a href="/games/${g[1]}/">Read the ${g[2]} feature and workflow explanation</a>.</p>`;
}
export const articles = {
  '/about/': [
    'About Community Games',
    'A community project for easier game nights.',
    `<p>Our goal is to make community game nights easier to organize with transparent tools for queues, teams, ratings and match history.</p><p>The project grew from PUBobot2 by Leshaka and an Age of Empires community bot into a multi-game Cloudflare application. The repository retains the NammaPUBobot name; the internal project name is NammaMatchBot.</p><p>We value community control, understandable permissions, confirmed results and source that people can inspect. Maintainer and funding details will be added when confirmed; GitHub issues are the current project contact.</p>`,
  ],
  '/open-source/': [
    'Free and open source',
    'Inspect the code, contribute improvements or host your own installation.',
    `<p>The bot and web application are public under GPL-3.0-only. You can use, study and modify the software. Distributing a modified version carries the license's source and licensing obligations; merely hosting a modified service does not create AGPL-style publication requirements.</p><p><a href="https://github.com/ddk220-light/NammaPUBobot">View source on GitHub</a> · <a href="https://github.com/ddk220-light/NammaPUBobot/blob/main/LICENSE">Read the license</a> · <a href="https://github.com/ddk220-light/NammaPUBobot/issues">Report an issue</a>.</p><p>Credit goes to PUBobot2 by Leshaka and the community data providers identified in each connector. See <a href="/guides/self-hosting/">self-hosting</a> for the operational requirements.</p>`,
  ],
  '/guides/getting-started/': [
    'Get started on Discord',
    'Invite, configure, save and verify your first queue.',
    `<p>Sign in through <a href="/app/">the dashboard</a> with an account that can manage your server. Open Setup, verify bot access, choose a game and configure the first queue. Review and save before asking players to join with <code>/add</code>.</p><p>Setup readiness requires saved configuration and verified permissions. A pending card delivery is a separate issue shown in Matches. Follow your <a href="/games/">game-specific guide</a> for detailed steps.</p>`,
  ],
  '/guides/discord-permissions/': [
    'Discord permissions',
    'Required channel access and optional role and nickname features.',
    `<p>The base invitation requests View Channel, Send Messages, Embed Links and Read Message History (84992). Check channel overrides as well as server roles.</p><p>Manage Roles is optional for rank-role assignment. The bot must be above cosmetic rank roles. Manage Nicknames is optional for nickname decoration; the bot cannot change the server owner or a member above its role.</p><p>Setup offers separate permission invitations when needed. Inviting again cannot fix role order or channel overrides. Recheck access after making changes; last-known names during an outage do not verify permissions.</p>`,
  ],
  '/guides/import-ratings/': [
    'Import ratings',
    'Select a file, inspect the authoritative preview and apply safely.',
    `<p>Open Players and choose the destination game. Select a supported CSV or normalized JSON file. Review destination, counts, warnings and row errors before Apply. Blocking preview errors disable application.</p><p>Changing community, source file or mapping invalidates the previous preview. Duplicate application is protected by server idempotency. Keep the result summary and resolve errors before trying a different file.</p><p>Legacy conversion is an offline preparation step; converted data must still pass the Cloudflare preview. Back up source records and verify identities instead of assuming display names uniquely identify Discord members.</p>`,
  ],
  '/guides/switch-from-pubobot/': [
    'Move records from PUBobot',
    'Prepare and review a migration without replacing your working bot blindly.',
    `<p>This project credits PUBobot2 by Leshaka as its foundation. Migration is not an automatic copy of every legacy feature or database. Export and back up the source, use the maintained offline converter, select the target community/game, and inspect the normalized import preview.</p><p>Resolve Discord identity mappings and all blocking errors before applying. Verify a sample of ratings and player records afterward. Keep the existing bot available until you have tested a first queue, confirmed permissions and agreed a cutover.</p>`,
  ],
  '/guides/self-hosting/': [
    'Self-host Community Games',
    'Operate your own Discord application and Cloudflare deployment.',
    `<p>You need Node 22.12 or newer, a Cloudflare account and your own Discord application. Clone <a href="https://github.com/ddk220-light/NammaPUBobot">the repository</a>, enter cloudflare, run npm ci and npm run check.</p><p>Follow <a href="https://github.com/ddk220-light/NammaPUBobot/blob/main/cloudflare/README.md">the maintained operator instructions</a> to configure Wrangler bindings, APP_ORIGIN, environment variables, Discord callback URLs and secrets. Register commands for your application and verify invitation permissions.</p><p>Before upgrading, take an operational backup, inspect storage migrations and test restore in isolation. Optional providers can need separate credentials and acceptance. Keep secret tokens out of commits, screenshots and client assets. Hosting and provider costs depend on your installation; free software does not guarantee free infrastructure.</p>`,
  ],
  '/features/queues-and-teams/': [
    'Queues, check-in and teams',
    'Gather ready players and organize the next match.',
    `<p>Players join a named queue with /add or a queue card. Configure even match sizes, check-in, automatic start, waiting expiry and captain, balanced or random teams. Organizers can review manual start and clear actions separately from configuration saves.</p><p>Same-game queues share rating policy. Pausing is staged until saved; live match actions use saved rules. Review game-specific expectations before starting.</p>`,
  ],
  '/features/ratings-and-leaderboards/': [
    'Ratings and leaderboards',
    'Community records with clear visibility and publication controls.',
    `<p>Choose Elo-style, Glicko-2, TrueSkill or flat community rating behavior before accumulating records. Ratings belong to the configured game, shared across its queues. Engine locks prevent incompatible changes after ratings exist.</p><p>Control eligibility, ranks and public/member/admin visibility. Public statistics remain unlisted until an organizer explicitly enables search publishing. Hidden players stay excluded. Optional role and nickname decoration need extra Discord permissions.</p>`,
  ],
  '/features/web-dashboard/': [
    'The organizer dashboard',
    'Manage queues, matches, players and setup on desktop or phone.',
    `<p>Four destinations keep the main jobs close: Queues, Matches, Players and Setup. Community drafts preserve edits as you navigate. Review and save settings atomically; validation, network and revision failures retain the draft.</p><p>Use confirmed actions for results and player adjustments, authoritative previews for imports and public statistics with a rating-history chart and accessible table. Phone and tablet layouts retain the same workflows.</p>`,
  ],
  '/privacy/': [
    'Privacy and public records',
    'Understand what the application stores and publishes.',
    `<p>The application stores Discord identifiers and display names, community configuration, ratings, match history, optional game-account references and virtual-gold activity. Authentication uses Discord OAuth and server-side session cookies. Optional providers process the account information needed for their connection.</p><p>Statistics visibility is controlled by organizers. Search publishing is a separate explicit choice for public leaderboards. Hidden players are excluded from public statistics. Search engines may retain earlier published copies temporarily.</p><p>Marketing analytics are disabled by default. For a data-access or removal request, contact your community organizer and use the project's GitHub issue contact without posting private tokens or personal records. Self-hosted operators control their own data and must provide their own operational contact.</p>`,
  ],
  '/terms/': [
    'Hosted service information',
    'Current availability and the software license.',
    `<p>The software is free under GPL-3.0-only. Hosted availability and limits depend on the operator; this preview does not promise unlimited installations, uptime or free hosting forever.</p><p>Organizers must have permission to manage their Discord community and should explain public-record policies to players. The project is independent of Discord and game publishers. Review the software license and deployment-specific policies before relying on a hosted installation.</p>`,
  ],
  '/changelog/': [
    'Changelog',
    'Released behavior and implementation history.',
    `<p>The responsive Cloudflare console and seven-game connector source were integrated in <a href="https://github.com/ddk220-light/NammaPUBobot/pull/46">PR 46</a>. The default Players game filter was corrected in <a href="https://github.com/ddk220-light/NammaPUBobot/pull/48">PR 48</a>.</p><p>Source integration and hosted release are separate. Connector availability and acceptance must be checked in the deployed application.</p>`,
  ],
};
