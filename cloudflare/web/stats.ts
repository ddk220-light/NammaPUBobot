import type { Statistics as Stats, PlayerStatistics } from '../src/domain/records';

const esc = (s: unknown) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
const date = (at: number) => esc(new Date(Number(at)).toLocaleString());
const streak = (value: number) =>
  value === 0
    ? 'None'
    : `${Math.abs(value)} consecutive ${value > 0 ? 'win' : 'loss'}${Math.abs(value) === 1 ? '' : value > 0 ? 's' : 'es'}`;
const matchOutcome = (state: string, result: string | null) => {
  const status: Record<string, string> = {
    aborted: 'Cancelled',
    check_in_failed: 'Check-in incomplete',
    undone: 'Result withdrawn',
    check_in: 'Checking in',
    playing: 'Awaiting result',
  };
  return (
    status[state] ??
    (result === 'alpha'
      ? 'Alpha wins'
      : result === 'beta'
        ? 'Beta wins'
        : result === 'draw'
          ? 'Draw'
          : state.replaceAll('_', ' '))
  );
};

function playerDetails(p: PlayerStatistics) {
  const metric = (label: string, value: string | number) =>
    `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  return `<section class="panel stats-section" aria-labelledby="player-statistics-heading">
    <div class="section-heading"><h2 id="player-statistics-heading" tabindex="-1">${esc(p.name)}</h2></div>
    ${p.hidden ? '<p class="notice">This player is hidden from other players’ statistics.</p>' : ''}
    <dl class="stats-summary">
      ${metric('Rating', `${p.rating} ±${p.deviation}`)}
      ${metric('Rank', p.rank ?? 'Unranked')}
      ${metric('Peak rating', p.peak)}
      ${metric('Matches', p.matchCount)}
      ${metric('Wins / losses / draws', `${p.wins} / ${p.losses} / ${p.draws}`)}
      ${metric('Win rate', p.winRate === null ? 'No ranked results' : `${(p.winRate * 100).toFixed(1)}%`)}
      ${metric('Current streak', streak(p.streak))}
      ${metric('Last ranked match', p.lastRankedAt === null ? 'Not recorded' : new Date(p.lastRankedAt).toLocaleString())}
    </dl>
    <p class="form-help">${p.eligible ? 'Included in this game’s leaderboard.' : 'Does not meet this game’s leaderboard requirements.'}</p>

    <h3>Played civilizations</h3>
    <p class="form-help">Recorded from linked game profiles. Win rate is wins / completed games, including draws. Inferred links can be corrected with /profile unlink and /lobby link.</p>
    ${p.civilizations?.length ? `<div class="table-wrap"><table><thead><tr><th>Civilization</th><th>Games</th><th>W / L / D</th><th>Win rate</th><th>Inferred identity</th></tr></thead><tbody>${p.civilizations.map((c) => `<tr><td>${esc(c.selection)}</td><td>${c.games}</td><td>${c.wins} / ${c.losses} / ${c.draws}</td><td>${((100 * c.wins) / c.games).toFixed(1)}%</td><td>${c.inferred}</td></tr>`).join('')}</tbody></table></div>` : '<p>No civilizations recorded yet. Unlinked games remain playable.</p>'}
    <h3>Recent rating history</h3>
    ${p.history.length ? `<div class="table-wrap"><table><caption class="sr-only">Recent rating changes for ${esc(p.name)}</caption><thead><tr><th scope="col">Date</th><th scope="col" class="numeric">Before</th><th scope="col" class="numeric">After</th><th scope="col">Reason</th></tr></thead><tbody>${p.history.map((c) => `<tr><td>${date(c.changed_at)}</td><td class="numeric">${c.before}</td><td class="numeric">${c.after}</td><td>${esc(c.reason ?? 'Match or administrator adjustment')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="empty-state">No rating changes recorded.</p>'}

    <h3>Teammates and opponents</h3>
    <p class="form-help">Based on the most recent 30 recorded matches. Comparisons require at least three encounters.</p>
    ${p.relationships.length ? `<div class="table-wrap"><table><caption class="sr-only">Teammate and opponent comparisons for ${esc(p.name)}</caption><thead><tr><th scope="col">Player</th><th scope="col" class="numeric">Together</th><th scope="col" class="numeric">Wins together</th><th scope="col" class="numeric">Against</th><th scope="col" class="numeric">Wins against</th></tr></thead><tbody>${p.relationships.map((r) => `<tr><th scope="row">${esc(r.name)}</th><td class="numeric">${r.teammateMatches}</td><td class="numeric">${r.winsTogether}</td><td class="numeric">${r.opponentMatches}</td><td class="numeric">${r.winsAgainst}</td></tr>`).join('')}</tbody></table></div>` : '<p class="empty-state">No comparisons meet the encounter requirement yet.</p>'}

    <h3>Recent matches</h3>
    ${p.recent.map((m) => `<details class="match-record"><summary>${esc(m.queue)} <span class="record-meta">${esc(matchOutcome(m.state, m.result))} · ${date(m.at)}</span></summary><div class="record-teams">${m.teams.map((team, side) => `<div><strong>${side === 0 ? 'Alpha' : 'Beta'}</strong><p>${team.map((player) => esc(player.name)).join(', ')}</p></div>`).join('')}</div>${m.note ? `<p>${esc(m.note)}</p>` : ''}<p class="record-meta">Match ID: <code>${esc(m.id)}</code></p></details>`).join('') || '<p class="empty-state">No recent matches recorded.</p>'}

    <h3>Profile references</h3><p class="form-help">These links are optional and unverified. They do not prove ownership of a game profile.</p>
    ${p.profiles.length ? `<div class="table-wrap"><table><caption class="sr-only">Unverified external profile references</caption><thead><tr><th scope="col">Provider</th><th scope="col">Profile ID</th><th scope="col">Source</th><th scope="col">Link</th></tr></thead><tbody>${p.profiles.map((r) => `<tr><th scope="row">${esc(r.provider)}</th><td><code>${esc(r.external_id)}</code></td><td>${esc(r.provenance)}</td><td><a href="${esc(r.url)}" rel="noopener noreferrer" target="_blank" aria-label="View ${esc(r.provider)} profile (opens in a new tab)">View profile</a></td></tr>`).join('')}</tbody></table></div>` : '<p class="empty-state">No linked profiles.</p>'}
    <details class="technical-details"><summary>Record source</summary><p>Player records: ${esc(p.source)}</p>${p.history.length ? `<ul>${[...new Set(p.history.map((c) => c.source ?? 'local'))].map((source) => `<li>Rating history: ${esc(source)}</li>`).join('')}</ul>` : ''}</details>
  </section>`;
}

export async function showStats(app: HTMLElement) {
  document.title = 'Community Games — statistics';
  const bootstrapResponse = await fetch('/api/bootstrap', { credentials: 'same-origin' });
  if (!bootstrapResponse.ok) throw new Error('Could not load statistics. Reload to retry.');
  const bootstrap = (await bootstrapResponse.json()) as { environment: string; version: string };
  document.querySelector('#environment')!.textContent = bootstrap.environment.toUpperCase();
  document.querySelector('#version')!.textContent = `v${bootstrap.version}`;
  const query = new URL(location.href).searchParams,
    community = query.get('community'),
    game = query.get('game');
  if (!community || !game)
    throw new Error('Open a statistics link from /rank, /leaderboard or community setup.');
  let page = 0,
    user = query.get('user') ?? '';
  const load = async (focusPlayer = false) => {
    app.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(
        `/statistics/${encodeURIComponent(community)}/${encodeURIComponent(game)}?page=${page}${user ? `&user=${encodeURIComponent(user)}` : ''}`,
        { credentials: 'same-origin' },
      );
      const data = (await response.json()) as Stats & { error?: string };
      if (!response.ok) {
        const needsAccess = response.status === 401 || response.status === 403;
        app.innerHTML = `<section class="panel stats-section"><h1>${needsAccess ? 'Statistics need access' : 'Statistics unavailable'}</h1><p role="alert">${esc(data.error ?? 'Try again shortly.')}</p><div class="actions">${needsAccess ? `<a class="button primary" href="/auth/login?returnTo=${encodeURIComponent(location.pathname + location.search)}">Sign in with Discord</a>` : ''}<button data-stats="reload">Retry</button><a href="/">Community setup</a></div></section>`;
        return;
      }
      const p = data.player;
      app.innerHTML = `<div class="page-heading"><div><h1>${esc(data.gameName)}</h1><p class="view-description">Community statistics</p></div><div class="actions"><a href="/?community=${encodeURIComponent(community)}">Organizer setup</a><button data-stats="reload">Reload statistics</button></div></div>
        <section class="panel stats-section" aria-labelledby="leaderboard-heading"><div class="section-heading"><h2 id="leaderboard-heading">Leaderboard</h2></div>
        <p class="form-help">${esc({ public: 'Public', members: 'Server members', admins: 'Administrators' }[data.visibility])} can view these statistics. Players need ${data.policy.minimumMatches} match${data.policy.minimumMatches === 1 ? '' : 'es'}${data.policy.recentDays ? ` and a match within the last ${data.policy.recentDays} days` : ''} to appear. Updated when you reload.</p>
        <div class="table-wrap"><table><caption class="sr-only">${esc(data.gameName)} leaderboard</caption><thead><tr><th scope="col">Player</th><th scope="col">Rank</th><th scope="col" class="numeric">Rating</th><th scope="col" class="numeric">Wins</th><th scope="col" class="numeric">Losses</th><th scope="col" class="numeric">Draws</th></tr></thead><tbody>${data.leaderboard.map((r) => `<tr><th scope="row"><button class="link-button" data-user="${r.id}">${esc(r.name)}</button></th><td>${esc(r.rank ?? 'Unranked')}</td><td class="numeric">${r.rating} <span class="muted">±${r.deviation}</span></td><td class="numeric">${r.wins}</td><td class="numeric">${r.losses}</td><td class="numeric">${r.draws}</td></tr>`).join('') || '<tr><td colspan="6" class="empty-state">No players meet the leaderboard requirements yet.</td></tr>'}</tbody></table></div>
        <div class="pagination"><p aria-live="polite">Page ${page + 1} of ${Math.max(1, data.pages)} · ${data.total} eligible player${data.total === 1 ? '' : 's'}</p><div class="actions"><button data-stats="previous"${page === 0 ? ' disabled' : ''}>Previous</button><button data-stats="next"${page + 1 >= data.pages ? ' disabled' : ''}>Next</button></div></div>
        <form id="stats-player" class="inline-form"><label>Player Discord ID<input name="user" pattern="[0-9]{17,20}" required value="${esc(user)}" inputmode="numeric" spellcheck="false" /><span class="form-help">Select a player above or enter their Discord ID.</span></label><button>Load player</button></form></section>
        ${p ? playerDetails(p) : user ? '<section class="panel stats-section"><h2>Player record</h2><p class="empty-state">No visible player record for this game. Check the Discord ID or statistics access.</p></section>' : ''}`;
      if (focusPlayer && p) app.querySelector<HTMLElement>('#player-statistics-heading')?.focus();
    } catch (error) {
      app.innerHTML = `<section class="panel stats-section"><h1>Statistics unavailable</h1><p role="alert">${esc(error instanceof Error ? error.message : 'Request failed.')}</p><div class="actions"><button data-stats="reload">Retry</button><a href="/">Community setup</a></div></section>`;
    } finally {
      app.setAttribute('aria-busy', 'false');
    }
  };
  app.addEventListener('click', (event) => {
    if (app.getAttribute('aria-busy') === 'true') return;
    const button = (event.target as Element).closest<HTMLButtonElement>('button');
    if (!button || button.disabled) return;
    if (button.dataset.user) {
      user = button.dataset.user;
      void load(true);
    } else if (button.dataset.stats) {
      if (button.dataset.stats === 'next') page++;
      if (button.dataset.stats === 'previous') page = Math.max(0, page - 1);
      void load();
    }
  });
  app.addEventListener('submit', (event) => {
    event.preventDefault();
    if (app.getAttribute('aria-busy') === 'true') return;
    const form = (event.target as Element).closest<HTMLFormElement>('#stats-player');
    if (form) {
      user = String(new FormData(form).get('user') ?? '');
      void load(true);
    }
  });
  await load();
}
