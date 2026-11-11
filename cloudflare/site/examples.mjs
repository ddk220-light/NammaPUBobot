// Site renditions of what the bot posts. The wording follows src/discord/cards.ts and the real
// cards in the test channel; names and ratings are illustrative and every figure says "Example".
import { identity } from './load-identity.mjs';

const at = (name) => `<span class="mention">@${name}</span>`;
// A balanced 4v4 as src/domain/play.ts would split it: the two highest ratings captain opposite sides.
const alpha = [
  ['Morgan', 1336],
  ['Jamie', 1250],
  ['Dev', 1142],
  ['Kai', 1105],
];
const beta = [
  ['Casey', 1293],
  ['Sky', 1207],
  ['Rin', 1188],
  ['Alex', 1160],
];
const waiting = [...alpha, ...beta].map(([n]) => n);
const team = (label, players, row, captain = true) =>
  `<p class="bot-team"><strong>${label}</strong>${players.map((p, i) => `<span>${row(p, i)}${i || !captain ? '' : ' · captain'}</span>`).join('')}</p>`;
const teams = (row = ([n, r]) => `${at(n)} · ${r}`) =>
  team('Alpha', alpha, row) + team('Beta', beta, row);

function card({ title, body, buttons = [], select, accent = false }) {
  const controls = [
    select &&
      `<span class="bot-select"><span class="visually-hidden">Menu: </span>${select}</span>`,
    buttons.length &&
      `<span class="bot-buttons"><span class="visually-hidden">Buttons: </span>${buttons.map((b, i) => `<span class="bot-button${i ? '' : ' primary'}">${b}</span>`).join('<span class="visually-hidden">, </span>')}</span>`,
  ]
    .filter(Boolean)
    .join('');
  return `<div class="bot-card${accent ? ' accent' : ''}"><p class="bot-from"><img src="/favicon.svg" width="20" height="20" alt="" loading="lazy">${identity.publicName}</p><p class="bot-title">${title}</p><div class="bot-body">${body}</div>${controls ? `<div class="bot-controls">${controls}</div>` : ''}</div>`;
}
const figure = (caption, ...cards) =>
  `<figure class="bot-example">${cards.join('')}<figcaption><span class="example-tag">Example</span> ${caption}</figcaption></figure>`;

export const examples = {
  queue: (
    game = 'Pickup',
    caption = 'The queue card in your channel. Players press Join or type <code>/add</code>.',
  ) =>
    figure(
      caption,
      card({
        title: `${game} · 6/8`,
        body: `<p>${waiting.slice(0, 6).map(at).join(' · ')}</p>`,
        buttons: ['Join', 'Leave'],
      }),
    ),
  emptyQueue: () =>
    figure(
      'A new queue card, waiting for its first player.',
      card({
        title: 'Friday 4v4 · 0/8',
        body: '<p>No one’s waiting yet.</p>',
        buttons: ['Join', 'Leave'],
      }),
    ),
  checkIn: (game = 'Age of Empires II') =>
    figure(
      'The match card during check-in. A tick means that player has pressed Check in.',
      card({
        title: `Pickup · ${game} · check in`,
        body: `<p><strong>6/8 ready</strong> · closes in 40 seconds<br>Check in to keep your place.</p>${teams(([n, r]) => `${['Dev', 'Rin'].includes(n) ? '○' : '✓'} ${at(n)} · ${r}`)}`,
        buttons: ['Check in', 'Back out'],
      }),
    ),
  teams: (
    game = 'Age of Empires II',
    caption = 'Teams are made and the game is on. The first name on each side is the captain.',
  ) =>
    figure(
      caption,
      card({
        title: `Pickup · ${game} · awaiting result`,
        body: teams(),
        buttons: ['Report result', 'Sub me'],
      }),
    ),
  result: (game = 'Age of Empires II') =>
    figure(
      'The same card after a captain reports the result, with each player’s rating change.',
      card({
        accent: true,
        title: `Pickup · ${game} · Alpha wins`,
        body:
          team('Alpha', alpha, ([n, r]) => `${at(n)} · ${r + 12} (+12)`) +
          team('Beta', beta, ([n, r]) => `${at(n)} · ${r - 12} (-12)`),
      }),
    ),
  substitute: () =>
    figure(
      'A player asked for a substitute. Their place stays reserved until someone takes over.',
      card({
        title: 'Pickup · Age of Empires II · awaiting result',
        body: `${teams()}<p><strong>Looking for a substitute:</strong> ${at('Kai')}</p>`,
        buttons: ['Report result', 'Sub me'],
      }),
    ),
  mapVote: () =>
    figure(
      'Map voting on the match card. Players choose from a short list drawn from your map pool.',
      card({
        title: 'Pickup · Age of Empires II · map selection',
        body: `${teams()}<p><strong>Vote for 1 map</strong> · 5/8 voted · closes in 35 seconds</p>`,
        select: 'Choose your maps: Arabia, Arena, Nomad, Black Forest, Gold Rush',
      }),
    ),
  predictions: () =>
    figure(
      'Predictions on the match card. Gold is play money and can’t be bought or cashed out.',
      card({
        title: 'Pickup · Age of Empires II · awaiting result',
        body: `${teams()}<p><strong>Predictions</strong> · Alpha 150 · Beta 100 gold</p>`,
        buttons: ['Place bet', 'Close betting'],
      }),
    ),
  civPicks: () =>
    figure(
      'Civ picks after the reveal. Everyone picks in secret; the choices show together.',
      card({
        title: 'Pickup · Civilization picks',
        body: `<p>Picks revealed. These are suggestions; played civilizations come from the game.</p>${team('Alpha', alpha, ([n], i) => `${at(n)} · ${['Aztecs', 'Britons', 'Persians', 'Random'][i]}`, false)}${team('Beta', beta, ([n], i) => `${at(n)} · ${['Franks', 'Mongols', 'Byzantines', 'Random (timed out)'][i]}`, false)}`,
      }),
    ),
  undone: () =>
    figure(
      'After an organizer withdraws a result. The rating changes are reversed until the right result is recorded.',
      card({
        title: 'Pickup · Age of Empires II · result withdrawn',
        body: `${teams()}<p>The result and its rating changes were reversed. Awaiting a corrected result.</p>`,
      }),
    ),
  permissions: () =>
    `<figure class="bot-example"><div class="bot-card"><p class="bot-title">What the invitation asks for</p><ul class="check-list"><li>View channels</li><li>Send messages</li><li>Embed links</li><li>Read message history</li></ul></div><figcaption><span class="example-tag">Example</span> The four permissions in the standard invitation. Rank roles and nickname tags ask for more later, only if you turn them on.</figcaption></figure>`,
};

/** A real console screenshot taken from the local preview with demo records. */
export const shot = (name, width, height, alt, caption, eager = false) =>
  `<figure class="console-shot"><a href="/screenshots/${name}.png"><img src="/screenshots/${name}.png" width="${width}" height="${height}" alt="${alt}"${eager ? '' : ' loading="lazy"'} decoding="async"></a><figcaption><span class="example-tag">Example</span> ${caption}</figcaption></figure>`;
