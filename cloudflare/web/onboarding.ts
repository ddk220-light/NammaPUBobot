import { seedCsv } from '../src/domain/import-csv';
import type { Snapshot } from '../src/domain/config';
import { importSchema, type ImportBundle } from '../src/domain/records';

const esc = (s: unknown) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
interface Preview {
  id: string;
  target: string;
  alreadyApplied: boolean;
  ok: boolean;
  errors: string[];
  warnings: string[];
  counts: Record<string, number>;
}
let pending: { bundle: ImportBundle; preview: Preview; url: string; snapshot: Snapshot } | null =
  null;
let completed: {
  url: string;
  sourceId: string;
  alreadyApplied: boolean;
  counts: Record<string, number>;
  refreshError?: string;
} | null = null;
const countLabels: Record<string, string> = {
  players: 'Players in source',
  newPlayers: 'New players',
  existingPlayers: 'Existing players retained',
  matches: 'Matches',
  changes: 'Rating history rows',
};
const countList = (counts: Record<string, number>) =>
  `<dl class="import-counts">${Object.entries(counts)
    .map(([key, count]) => `<div><dt>${esc(countLabels[key] ?? key)}</dt><dd>${count}</dd></div>`)
    .join('')}</dl>`;
function resultMarkup(url: string) {
  if (!completed || completed.url !== url) return '';
  return `<section class="import-result" role="status"><h3 tabindex="-1" data-import-result-heading>${completed.alreadyApplied ? 'Already imported' : 'Import applied'}</h3><p>Source: ${esc(completed.sourceId)}. ${completed.alreadyApplied ? 'No duplicate changes were made.' : 'The server confirmed this import. Existing seed records were retained.'}</p>${countList(completed.counts)}${completed.refreshError ? `<p class="notice error">The import succeeded, but records could not refresh: ${esc(completed.refreshError)}</p><button type="button" data-action="refresh-records">Retry record refresh</button>` : ''}</section>`;
}
export function importRecordsRefreshed(url: string) {
  if (completed?.url === url) delete completed.refreshError;
}
let generation = 0;
export function invalidateImport() {
  pending = null;
  generation++;
  const box = document.querySelector('#import-preview');
  if (box) box.innerHTML = '';
}
export function onboarding(snapshot: Snapshot, diagnostics: unknown) {
  const s = snapshot.settings;
  const result = resultMarkup(`/api/communities/${snapshot.communityId}/import/apply`);
  const games = s.games.map((g) => `<option value="${g.id}">${esc(g.name)}</option>`).join('');
  const queues = s.queues.map((q) => `<option value="${q.id}">${esc(q.name)}</option>`).join('');
  const playerActions = {
    hide: 'Hide from statistics',
    unhide: 'Show in statistics',
    link: 'Link an external profile',
    unlink: 'Remove a profile link',
    reassign: 'Reassign a profile link',
    sync: 'Retry rank and nickname sync',
  };
  return `<section class="settings-section" aria-labelledby="player-settings-heading"><h2 id="player-settings-heading">Player settings</h2>
    <p class="view-description">Ratings and gold are separate for each community and game. Account links show whether they were declared, assigned by an organizer or verified by signing in.</p>
    ${
      s.games.length
        ? `<details class="rating-options"><summary>Visibility and profile links</summary>
      <p class="form-help">Hide a player from other players’ statistics, manage declared profile links, or retry their Discord rank updates.</p>
      <form data-kind="player-record"><div class="fields">
        <label>Game<select name="gameId">${games}</select></label>
        <label>Player Discord ID<input name="userId" required pattern="[0-9]{17,20}" inputmode="numeric" spellcheck="false" /></label>
        <label>Action<select name="kind">${Object.entries(playerActions)
          .map(([value, label]) => `<option value="${value}">${label}</option>`)
          .join('')}</select></label>
        <label>Provider<input name="provider" placeholder="steam, aoe2, etc." spellcheck="false" /></label>
        <label>External profile ID<input name="externalId" spellcheck="false" /></label>
        <label>HTTPS profile URL<input name="url" type="url" /></label>
        <label>Reason for reassignment<input name="reason" maxlength="200" /></label>
      </div><p class="form-help">Provider, profile ID and URL apply to profile links. Reassignment moves a profile from another player and requires a reason and a second confirmation.</p><div class="actions"><button>Apply player action</button></div></form>
    </details>`
        : ''
    }

    <details class="rating-options" data-import-section ${result ? 'open' : ''}><summary>Import player records</summary>
      <div id="import-result" aria-live="polite">${result}</div>
      <p class="view-description">Seed imports add new players without changing existing records. Full history needs an empty destination game and matching result and rating-history totals.</p>
      ${
        s.games.length && s.queues.length
          ? `<form data-kind="import-preview"><div class="fields">
        <label>Input format<select name="format"><option value="csv">CSV seeds</option><option value="manual">Manual seeds (CSV rows)</option><option value="json">Normalized history / seed JSON</option></select></label>
        <label>Source identifier<input name="sourceId" value="community-seed-1" required maxlength="100" spellcheck="false" /></label>
        <label>Destination game<select name="gameId">${games}</select></label>
        <label>Destination queue<select name="queueId">${queues}</select></label>
        <label>Upload CSV / JSON<input name="file" type="file" accept=".csv,.json" /></label>
      </div><label>Or paste input<textarea name="content" rows="8" spellcheck="false" placeholder="id,name,rating,deviation,wins,losses,draws,streak,hidden,lastRankedAt"></textarea></label>
      <p class="form-help">Choose a file or paste the input. CSV dates need a timezone. JSON uses its own game and queue mappings; review their destinations in the preview.</p>
      <p class="form-help">The offline converter supports PUBobot ZIP and NammaPUBobot CSV snapshots. Gold, quiz and drafting data are not part of this ratings import.</p>
      <div class="actions"><button>Preview import</button></div></form><div id="import-preview" aria-live="polite"></div>`
          : '<p class="empty-state">Create a game and queue in Setup before importing player records.</p>'
      }
    </details>
    <details class="technical-details"><summary>Import, rank and inactivity diagnostics</summary><pre>${esc(JSON.stringify(diagnostics, null, 2))}</pre></details>
  </section>`;
}

function importReview(bundle: ImportBundle, preview: Preview, snapshot: Snapshot) {
  const destinations = bundle.mappings
    .map((mapping) => {
      const game = snapshot.settings.games.find((g) => g.id === mapping.gameId);
      const queues = Object.entries(mapping.queues).map(([source, id]) => {
        const queue = snapshot.settings.queues.find((q) => q.id === id);
        return `<li>${esc(source)} → ${esc(queue?.name ?? id)}</li>`;
      });
      return `<tr><th scope="row">${esc(mapping.ladder)}</th><td>${esc(game?.name ?? mapping.gameId)}</td><td><ul class="review-issues">${queues.join('')}</ul></td></tr>`;
    })
    .join('');
  return `<section class="import-review" aria-labelledby="import-review-heading">
    <h3 id="import-review-heading" tabindex="-1">${preview.alreadyApplied ? 'Already imported' : preview.ok ? 'Review import' : 'Import needs changes'}</h3>
    <p id="import-review-summary" class="form-help">${preview.alreadyApplied ? 'This source was previously applied. No changes will be made.' : preview.ok ? 'Nothing has been imported yet. Check the destinations and counts before applying.' : 'Nothing has been imported. Fix the issues below, then preview again.'}</p>
    <p>Source: ${esc(bundle.sourceId)} · ${bundle.mode === 'seed' ? 'Player seeds' : 'Full match history'}</p>
    <dl class="import-counts">${Object.entries(preview.counts)
      .map(
        ([key, value]) =>
          `<div><dt>${esc(countLabels[key] ?? key)}</dt><dd>${esc(value)}</dd></div>`,
      )
      .join('')}</dl>
    <div class="table-wrap"><table><caption>Import destinations</caption><thead><tr><th scope="col">Source ladder</th><th scope="col">Game</th><th scope="col">Queue mappings</th></tr></thead><tbody>${destinations}</tbody></table></div>
    ${preview.errors.length ? `<div class="notice error"><h4>Fix before importing</h4><ul class="review-issues">${preview.errors.map((message) => `<li>${esc(message)}</li>`).join('')}</ul></div>` : ''}
    ${preview.warnings.length ? `<div class="review-warnings"><h4>Review these notes</h4><ul class="review-issues">${preview.warnings.map((message) => `<li>${esc(message)}</li>`).join('')}</ul></div>` : ''}
    ${(['players', 'matches', 'changes'] as const)
      .filter((kind) => bundle[kind].length)
      .map(
        (kind) =>
          `<details class="import-rows" data-import-history="${kind}"><summary>Review ${bundle[kind].length} ${reviewLabels[kind]}</summary><div data-import-page-content>${reviewPage(bundle, snapshot, kind, 0)}</div></details>`,
      )
      .join('')}
    <details class="technical-details"><summary>Technical mappings and preview</summary><pre>${esc(JSON.stringify({ version: bundle.version, source: bundle.source, mode: bundle.mode, mappings: bundle.mappings, unsupported: bundle.unsupported, ...preview }, null, 2))}</pre></details>
    ${preview.ok && !preview.alreadyApplied ? '<div class="actions import-apply"><button type="button" data-discard-import>Discard preview</button><button type="button" class="primary" data-action="apply-import" aria-describedby="import-review-summary">Apply reviewed import</button></div>' : ''}
  </section>`;
}
function reviewCard(
  title: string,
  values: Record<string, unknown>,
  className = 'import-history-card',
) {
  return `<article class="${className}"><h4>${esc(title)}</h4><dl>${Object.entries(values)
    .map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`)
    .join('')}</dl></article>`;
}
function destination(
  bundle: ImportBundle,
  snapshot: Snapshot,
  ladder: string,
  sourceQueue?: string,
) {
  const mapping = bundle.mappings.find((m) => m.ladder === ladder);
  if (!mapping) return 'Missing mapping';
  const game = snapshot.settings.games.find((g) => g.id === mapping.gameId);
  if (sourceQueue === undefined) return `${game?.name ?? 'Unavailable game'} · ${mapping.gameId}`;
  const id = mapping.queues[sourceQueue];
  const queue = snapshot.settings.queues.find((q) => q.id === id);
  return `${queue?.name ?? 'Unavailable queue'} · ${id ?? 'Missing mapping'}`;
}
const reviewLabels = {
  players: 'player records',
  matches: 'matches',
  changes: 'rating history rows',
};
type ReviewKind = keyof typeof reviewLabels;
function reviewRows(bundle: ImportBundle, snapshot: Snapshot, kind: ReviewKind, offset: number) {
  if (kind === 'players')
    return bundle.players
      .slice(offset, offset + 50)
      .map((row) =>
        reviewCard(
          row.player.name,
          {
            'Discord ID': row.player.id,
            'Source ladder': row.ladder,
            'Destination game': destination(bundle, snapshot, row.ladder),
            Rating: row.player.rating,
            Deviation: row.player.deviation,
            Wins: row.player.wins,
            Losses: row.player.losses,
            Draws: row.player.draws,
            Streak: row.player.streak,
            'Hidden from statistics': row.hidden ? 'Yes' : 'No',
            'Last ranked match':
              row.lastRankedAt == null ? 'Not recorded' : new Date(row.lastRankedAt).toISOString(),
          },
          'import-player-card',
        ),
      )
      .join('');
  if (kind === 'matches')
    return bundle.matches
      .slice(offset, offset + 50)
      .map((m) =>
        reviewCard(m.sourceId, {
          'Source match ID': m.sourceId,
          'Source ladder': m.ladder,
          'Source queue': m.queue,
          'Destination game': destination(bundle, snapshot, m.ladder),
          'Destination queue': destination(bundle, snapshot, m.ladder, m.queue),
          'Started at': new Date(m.at).toISOString(),
          'Ended at': new Date(m.endedAt).toISOString(),
          Result: m.result,
          'Alpha Discord IDs': m.teams[0].join(', '),
          'Beta Discord IDs': m.teams[1].join(', '),
        }),
      )
      .join('');
  return bundle.changes
    .slice(offset, offset + 50)
    .map((c) =>
      reviewCard(c.sourceId, {
        'Source change ID': c.sourceId,
        'Source ladder': c.ladder,
        'Destination game': destination(bundle, snapshot, c.ladder),
        'Player Discord ID': c.userId,
        'Source match ID': c.matchId ?? 'No match',
        'Changed at': new Date(c.at).toISOString(),
        'Rating before': c.before,
        'Rating after': c.after,
        'Deviation before': c.deviationBefore,
        'Deviation after': c.deviationAfter,
        Reason: c.reason,
      }),
    )
    .join('');
}
function reviewPage(bundle: ImportBundle, snapshot: Snapshot, kind: ReviewKind, offset: number) {
  const total = bundle[kind].length;
  return `<p tabindex="-1" data-import-range role="status">Showing ${offset + 1}–${Math.min(total, offset + 50)} of ${total} ${reviewLabels[kind]}</p><div class="import-player-cards">${reviewRows(bundle, snapshot, kind, offset)}</div>${total > 50 ? `<div class="actions"><button type="button" data-import-page="${kind}" data-offset="${offset - 50}" ${offset === 0 ? 'disabled data-unavailable="true"' : ''}>Previous ${reviewLabels[kind]}</button><button type="button" data-import-page="${kind}" data-offset="${offset + 50}" ${offset + 50 >= total ? 'disabled data-unavailable="true"' : ''}>Next ${reviewLabels[kind]}</button></div>` : ''}`;
}
export function pageImportRows(button: HTMLElement) {
  if (!pending) return;
  const kind = button.dataset.importPage as ReviewKind;
  const offset = Number(button.dataset.offset);
  if (offset < 0 || offset >= pending.bundle[kind].length) return;
  const content = button.closest('[data-import-page-content]')!;
  content.innerHTML = reviewPage(pending.bundle, pending.snapshot, kind, offset);
  content.querySelector<HTMLElement>('[data-import-range]')?.focus();
}
interface Helpers {
  api: <T>(path: string, options?: RequestInit) => Promise<T>;
  url: (suffix: string) => string;
  reload: () => Promise<void>;
  notice: (message: string) => void;
}
export async function onboardingSubmit(
  form: HTMLFormElement,
  data: FormData,
  snapshot: Snapshot,
  h: Helpers,
): Promise<boolean> {
  if (form.dataset.kind === 'player-record') {
    const input = {
      id: (form.dataset.actionId ??= crypto.randomUUID()),
      gameId: data.get('gameId'),
      userId: data.get('userId'),
      kind: data.get('kind'),
      ...Object.fromEntries(
        ['provider', 'externalId', 'url', 'reason']
          .filter((k) => data.get(k))
          .map((k) => [k, data.get(k)]),
      ),
    };
    if (input.kind === 'reassign' && form.dataset.review !== JSON.stringify(input)) {
      form.dataset.review = JSON.stringify(input);
      form.querySelector('[data-review]')?.remove();
      form.insertAdjacentHTML(
        'beforeend',
        '<p data-review class="notice" role="alert">This will move the external profile to the selected player. Submit once more to confirm.</p>',
      );
      return true;
    }
    const result = await h.api<{ content: string }>(h.url('player'), {
      method: 'POST',
      body: JSON.stringify(input),
    });
    await h.reload();
    h.notice(result.content);
    return true;
  }
  if (form.dataset.kind === 'import-preview') {
    invalidateImport();
    const current = generation;
    const previewBox = document.querySelector<HTMLElement>('#import-preview')!;
    previewBox.innerHTML = '';
    const file = data.get('file') as File | null;
    if (file && file.size > 5 * 1024 * 1024) throw new Error('Import files are limited to 5 MB.');
    const content = file?.size ? await file.text() : String(data.get('content') ?? '');
    const bundle =
      data.get('format') === 'json'
        ? importSchema.parse(JSON.parse(content))
        : seedCsv(
            content,
            String(data.get('gameId')),
            String(data.get('queueId')),
            String(data.get('sourceId')),
            data.get('format') === 'manual' ? 'manual' : 'csv',
          );
    const preview = await h.api<Preview>(h.url('import/preview'), {
      method: 'POST',
      body: JSON.stringify(bundle),
    });
    if (current !== generation || !previewBox.isConnected) return true;
    pending = { bundle, preview, url: h.url('import/apply'), snapshot };
    previewBox.innerHTML = importReview(bundle, preview, snapshot);
    previewBox.querySelector<HTMLElement>('#import-review-heading')?.focus();
    return true;
  }
  return false;
}
export async function applyImport(h: Helpers) {
  if (
    !pending ||
    !pending.preview.ok ||
    pending.preview.alreadyApplied ||
    pending.url !== h.url('import/apply')
  )
    throw new Error('Preview the import first.');
  const review = pending;
  const result = await h.api<{ alreadyApplied: boolean; counts: Record<string, number> }>(
    h.url('import/apply'),
    {
      method: 'POST',
      body: JSON.stringify({ bundle: review.bundle, target: review.preview.target }),
    },
  );
  invalidateImport();
  completed = { ...result, sourceId: review.bundle.sourceId, url: review.url };
  const showResult = () => {
    const box = document.querySelector('#import-result');
    if (box && h.url('import/apply') === review.url) {
      box.innerHTML = resultMarkup(review.url);
      box.closest('details')!.open = true;
      box.querySelector<HTMLElement>('[data-import-result-heading]')?.focus();
    }
  };
  showResult();
  try {
    await h.reload();
  } catch (error) {
    completed.refreshError = error instanceof Error ? error.message : 'Try again.';
  }
  showResult();
  h.notice(
    `${result.alreadyApplied ? 'This import was already applied.' : 'Import applied. Existing seed records were retained.'}${completed.refreshError ? ' Records could not refresh; retry the refresh below.' : ''}`,
  );
}
