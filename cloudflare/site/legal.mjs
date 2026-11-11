// About, Privacy and Terms. Every operator or contact fact comes from src/identity.ts;
// a value the owner has not supplied yet (null) is left out rather than invented.

/** Change this whenever the privacy policy or terms change in substance. */
export const legalUpdated = { iso: '2026-11-11', label: '11 November 2026' };

const esc = (v) =>
  String(v).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );

const updated = () =>
  `<p class="legal-updated">Last updated: <time datetime="${legalUpdated.iso}">${legalUpdated.label}</time></p>`;

/** The routes that exist, in the order people should try them. */
export function contactRoutes(identity) {
  const { email, supportServer, issues } = identity.contact;
  return [
    supportServer && [
      'Support Discord server',
      `<a href="${esc(supportServer)}">Join the support server</a> for help with setup and everyday questions.`,
    ],
    email && [
      'Email',
      `<a class="contact-email" href="mailto:${esc(email)}">${esc(email)}</a> for anything private, including privacy and deletion requests.`,
    ],
    issues && [
      'GitHub Issues',
      `<a href="${esc(issues)}">Open an issue</a> for bugs and feature ideas. Issues are public, so never post tokens or personal details there.`,
    ],
  ].filter(Boolean);
}

export function contactList(identity) {
  return `<ul class="contact-routes">${contactRoutes(identity)
    .map(([label, text]) => `<li><strong>${label}.</strong> ${text}</li>`)
    .join('')}</ul>`;
}

/** Where a private request goes, given the routes that exist today. */
function privateRequest(identity) {
  const { email, supportServer } = identity.contact;
  if (email) return `email <a href="mailto:${esc(email)}">${esc(email)}</a>`;
  if (supportServer)
    return `ask in the <a href="${esc(supportServer)}">support server</a> and we will move the conversation somewhere private`;
  return 'open a GitHub issue that says only that you have a private request, with no personal details, and we will arrange a private way to talk';
}

function runBy(identity) {
  return identity.operator
    ? `${esc(identity.operator)} runs the hosted ${esc(identity.publicName)} service.`
    : `The hosted ${esc(identity.publicName)} service is run by the maintainers of its open-source project.`;
}

function about(identity) {
  const name = esc(identity.publicName);
  return [
    `About ${identity.publicName}`,
    'A free, open-source Discord bot for community game nights.',
    `<p>${name} is a Discord bot and web dashboard for pickup games. Players join a queue with <code>/add</code>, the bot checks who is ready and forms teams, and the community keeps its own results, ratings and match history. It is for Discord communities that play together regularly and want game nights that are easy to run and fair to join.</p>
<h2>Free and open source</h2><p>${name} is free. There are no paid plans, no charges of any kind and no ads, and we do not sell data. The code is public under GPL-3.0-only: anyone can read it, suggest changes or <a href="/guides/self-hosting/">run their own copy</a>. See <a href="/open-source/">open source</a> for details.</p>
<h2>Where it came from</h2><p>It began as an Age of Empires II community's Discord bot, a fork of <a href="https://github.com/Leshaka/PUBobot2">PUBobot2 by Leshaka</a>. It keeps that project's ideas about queues, ratings and configuration, and was rebuilt on Cloudflare as one application that serves many games and many communities.</p>
<h2>How it is run</h2><p>${runBy(identity)} It runs on Cloudflare and talks to Discord through Discord's bot API. Each server's organizers decide how their community uses it: which games and queues exist, how teams are made and whether statistics are public.</p><p>We care about community control, permissions you can understand, results that players confirm and code that anyone can inspect. Read our <a href="/privacy/">privacy policy</a> and <a href="/terms/">terms of use</a>.</p>
<section class="contact-block" id="contact"><h2>Contact</h2><p>Reach us in whichever way suits you:</p>${contactList(identity)}</section>`,
  ];
}

function privacy(identity) {
  const name = esc(identity.publicName);
  return [
    'Privacy policy',
    `What ${identity.publicName} stores, why, and how to have it removed.`,
    `${updated()}<p>This policy covers the hosted ${name} service: the Discord bot and this website. ${name} is free. We do not show ads and we do not sell data.</p>
<h2>Who runs this</h2><p>${runBy(identity)} Within each Discord server, that server's organizers choose the settings, including whether its statistics are public. For questions about one community's rules, ask its organizers first.</p><p>Anyone can run their own copy of the software. Whoever runs a copy is responsible for it; this policy covers only our hosted service.</p>
<h2>What we store and why</h2><p>We keep what the bot needs to run queues and to keep each community's records. Records are kept separately for each Discord server and each game.</p><ul>
<li><strong>Players.</strong> Your Discord user ID and display name when you join a queue, so the bot can list you and place you in matches.</li>
<li><strong>Server details.</strong> The server, channel and role IDs used in queue settings, and the roles of members where a queue limits who can join.</li>
<li><strong>Queue restrictions.</strong> If an organizer stops someone from joining queues, the reason they give and when it ends.</li>
<li><strong>Match records.</strong> Matches, teams, results, ratings and rating history, plus civilization or faction picks for games that use them.</li>
<li><strong>Gold.</strong> Your play-money balance and a ledger of every change, including bets on matches. Gold has no monetary value and cannot be bought, sold, traded or redeemed.</li>
<li><strong>Linked game accounts.</strong> Optional. Profiles you link yourself, or verify by signing in with a service such as Steam or Battle.net, so results and lobbies can be matched to you.</li>
<li><strong>Organizer settings and audit log.</strong> Each community's settings, and a log of organizer actions such as changing settings, correcting a result or adjusting a rating, with who did it and when.</li>
<li><strong>Voice channels.</strong> Only if an organizer turns on voice features: which members are currently in voice channels the whole server can see, so the bot can list who is around. This is held only while the feature is in use and is not saved with the community's records. The bot never joins, listens to or records voice.</li>
<li><strong>Dashboard sign-in.</strong> When an organizer signs in with Discord, your browser keeps a session cookie. On our side we keep your Discord user ID, username, avatar and the Discord access token used to see which servers you manage. The session lasts at most 8 hours and is deleted when it expires or when you sign out.</li>
<li><strong>Public leaderboards.</strong> Only when an organizer publishes them. Players who hide themselves are left out.</li>
</ul><p>Your colour theme choice on this website is kept in your own browser and never sent to us.</p>
<h2>What we don't do</h2><ul><li>We do not charge anything, run ads or sell, rent or share data for advertising.</li><li>We do not use advertising trackers or third-party analytics. The only cookies are the ones that keep you signed in.</li><li>We do not store your chat messages. The bot reads recent messages in its queue channels only to find and update its own queue cards.</li><li>We do not process voice audio.</li><li>We do not put anyone in search results unless an organizer publishes a public leaderboard.</li></ul>
<h2>Who else is involved</h2><ul><li><strong>Discord</strong> handles sign-in and delivers every bot message. <a href="https://discord.com/privacy">Discord's privacy policy</a> covers your Discord account.</li><li><strong>Cloudflare</strong> hosts the website, the bot and its storage. Like any host it processes technical request data such as IP addresses, and we use a sample of its request logs to find and fix problems.</li><li><strong>Public game-data services</strong> are contacted only when an organizer turns on an optional game connection. Depending on the game, these include AoE2 Companion, the Age of Empires stats service run by World's Edge, AoE4 World, W3Champions, Beyond All Reason and GeneralsOnline. The bot looks up public profiles and matches. If you verify an account, the check happens through that service, for example by signing in on Steam's or Battle.net's own site, and we keep only the account identity it confirms.</li></ul>
<h2>How long we keep it</h2><p>Community records stay while the community uses the bot, until an organizer deletes them or we handle a deletion request. Removing the bot from a server does not delete them on its own. Sign-in sessions are deleted when they expire, after 8 hours at most. Voice-channel lists are not saved.</p><p>Backups that protect against data loss may hold deleted records for a limited time. Search engines may keep an old copy of a leaderboard for a while after it is unpublished.</p>
<h2>Your choices and how to delete</h2><ul><li><strong>Hide yourself.</strong> Use <code>/profile hide</code> to leave that community's leaderboards and statistics, and <code>/profile unhide</code> to come back.</li><li><strong>Unlink an account.</strong> Use <code>/profile unlink</code> to remove a linked game profile.</li><li><strong>Ask an organizer.</strong> A community's organizers can delete one player's data, or all of the community's data, from the web dashboard. They can also download the community's data there.</li><li><strong>Ask us.</strong> To see or delete your data, ${privateRequest(identity)}. We may ask you to confirm that the Discord account is yours.</li></ul>
<h2>Children</h2><p>Discord requires users to be at least 13, or older where local law says so, and ${name} is only for people who meet Discord's minimum age. If you think we hold data about a child under that age, contact us and we will delete it.</p>
<h2>Changes to this policy</h2><p>When this policy changes we update the date at the top of this page, and we note significant changes in the <a href="/changelog/">changelog</a>.</p>
<h2>Contact</h2>${contactList(identity)}<p>All the ways to reach us are on the <a href="/about/#contact">About page</a>.</p>`,
  ];
}

function terms(identity) {
  const name = esc(identity.publicName);
  return [
    'Terms of use',
    `The plain-language rules for using the hosted ${identity.publicName} service.`,
    `${updated()}<p>These terms cover the hosted ${name} bot and this website. By adding the bot to a server or using it, you agree to them. Our <a href="/privacy/">privacy policy</a> explains what we store.</p>
<h2>It is free</h2><p>${name} is free. There are no paid plans and no charges of any kind. We do not show ads and we do not sell data.</p>
<h2>What the service is</h2><p>A Discord bot and web dashboard for pickup games: queues, check-in, teams, ratings and match records. Each server's organizers set it up and decide how their community uses it.</p>
<h2>Acceptable use</h2><ul><li>Follow Discord's Terms of Service and Community Guidelines.</li><li>Do not use the bot to harass people, spam, or collect other people's data.</li><li>Do not try to break, overload or get around the service or its permissions.</li><li>Organizers must be allowed to manage the server they configure, and should tell players how results and statistics are shown.</li></ul><p>We may stop serving a server or a user that breaks these rules.</p>
<h2>Gold is play money</h2><p>Gold exists only for fun inside a community. It has no monetary value and cannot be bought, sold, traded or redeemed.</p>
<h2>Results and ratings</h2><p>Results and ratings are community records kept by each server and confirmed by its players and organizers. They are not official rankings. ${name} is not affiliated with Discord or with any game publisher, and game names belong to their owners.</p>
<h2>No warranty</h2><p>We run the service on a best-effort basis. It is provided as is, without any warranty. It may be unavailable at times, features may change, and records could be lost, so organizers can download their community's data from the dashboard. To the extent the law allows, we are not responsible for losses that come from using it.</p>
<h2>Open source</h2><p>The software is open source under <a href="${esc(identity.license)}">GPL-3.0-only</a>, and you can <a href="/guides/self-hosting/">host your own copy</a>. That license governs the software. These terms cover only our hosted service.</p>
<h2>Changes to these terms</h2><p>When these terms change we update the date at the top of this page, and we note significant changes in the <a href="/changelog/">changelog</a>.</p>
<h2>Contact</h2>${contactList(identity)}<p>All the ways to reach us are on the <a href="/about/#contact">About page</a>.</p>`,
  ];
}

/** Article entries for build-site, keyed by path like the rest of `articles`. */
export function legalArticles(identity) {
  return { '/about/': about(identity), '/privacy/': privacy(identity), '/terms/': terms(identity) };
}
