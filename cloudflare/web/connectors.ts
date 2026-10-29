import type { Settings } from '../src/domain/config';
import type { Match } from '../src/domain/play';
import { gameCapabilities } from '../src/games/catalog';
import { operations, type ConnectorOperation } from '../src/games/types';

export interface ConnectorDiagnostics {
  games: {
    gameId: string;
    catalogId: string;
    capabilities: Record<ConnectorOperation, string>;
    lastSuccessAt?: number;
    failures?: number;
    unresolvedBindings: number;
  }[];
}
export const escapeConnectorText = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
const esc = escapeConnectorText;
export function ownershipProvider(
  catalogId: string | null | undefined,
): 'steam' | 'battlenet' | 'bar' | undefined {
  if (['aoe2', 'aoe3', 'aoe4', 'aom', 'northgard', 'tempest_rising'].includes(catalogId ?? ''))
    return 'steam';
  if (catalogId === 'sc2' || catalogId === 'wc3') return 'battlenet';
  if (catalogId === 'bar') return 'bar';
}
export const verificationHref = (communityId: string, gameId: string, provider: string) =>
  `/link?${new URLSearchParams({ community: communityId, game: gameId, provider })}`;
const labels: Record<ConnectorOperation, string> = {
  accountLookup: 'Account lookup',
  ownershipVerification: 'Account ownership verification',
  lobbyDiscovery: 'Public waiting lobbies',
  ongoingDiscovery: 'Ongoing matchmaking games',
  lobbyObservation: 'Lobby observations',
  matchLookup: 'Match lookup',
  historyLookup: 'Recent history',
  replayReferences: 'Provider recording links',
};
const stateLabels: Record<string, string> = {
  available: 'Available',
  unsupported: 'Not available from this provider',
  credentials_required: 'Credentials required',
  authentication_required: 'Sign-in required',
  acceptance_required: 'Awaiting game acceptance',
  disabled: 'Disabled',
};
const stateLabel = (state: string) => stateLabels[state] ?? state.replaceAll('_', ' ');

export function connectorSettings(
  game: Settings['games'][number],
  communityId: string,
  diagnostics: ConnectorDiagnostics | null,
  saved = true,
) {
  const catalog = gameCapabilities(game.catalogId);
  if (!catalog) return '<p class="muted">Manual lobby and result reporting.</p>';
  const health = diagnostics?.games.find((item) => item.gameId === game.id);
  const verification = ownershipProvider(game.catalogId);
  const canVerify =
    verification &&
    game.connector?.capabilities?.ownershipVerification !== false &&
    !['disabled', 'credentials_required', 'unsupported'].includes(
      health?.capabilities.ownershipVerification ?? '',
    );
  return `<details class="rating-options connector-settings"><summary>Game connections · ${esc(catalog.manifest.edition)}</summary>
    <p class="muted">Manual lobby association and captain reporting remain available.</p>
    ${catalog.lobby ? `<label class="toggle"><input type="checkbox" name="lobbyConnector"${game.connector?.enabled ? ' checked' : ''} /> Enable provider observations</label>` : ''}
    <div class="fields">${operations
      .map((operation) => {
        const supported = catalog.manifest.capabilities[operation] !== 'unsupported';
        const state = supported
          ? (health?.capabilities[operation] ?? catalog.manifest.capabilities[operation])
          : 'unsupported';
        return supported
          ? `<label class="toggle"><input type="checkbox" name="capability-${operation}"${game.connector?.capabilities?.[operation] !== false ? ' checked' : ''} /> ${labels[operation]} <span class="muted">${esc(stateLabel(state))}</span></label>`
          : `<p class="muted">${labels[operation]}: ${esc(stateLabel(state))}</p>`;
      })
      .join('')}</div>
    ${catalog.manifest.capabilities.lobbyDiscovery !== 'unsupported' ? `<label class="toggle"><input type="checkbox" name="autoLobby"${game.connector?.autoAttach ? ' checked' : ''} /> Find a public lobby by name</label><label>Lobby name<input name="lobbyName" maxlength="80" value="${esc(game.connector?.autoAttach?.lobbyName ?? 'NammaNomad')}" /></label><p class="muted">Name and player-count matches are provisional until the roster matches or a captain confirms the association.</p>` : ''}
    <p class="record-meta">${health ? `${health.lastSuccessAt ? `Last successful observation: ${esc(new Date(health.lastSuccessAt).toLocaleString())}` : 'No successful observation yet'} · ${health.unresolvedBindings} unresolved association${health.unresolvedBindings === 1 ? '' : 's'}${health.failures ? ` · ${health.failures} recent failures` : ''}` : 'Live connector diagnostics unavailable. Manual reporting remains available.'}</p>
    ${canVerify ? `<p><a class="button" href="${esc(verificationHref(communityId, game.id, verification))}">Verify my ${verification === 'battlenet' ? 'Battle.net' : verification === 'bar' ? 'BAR' : 'Steam'} account</a></p>` : ''}
    ${catalog.lifecycle !== 'legacy' ? `<div class="connector-browse"><button type="button" data-action="browse-games" data-game="${esc(game.id)}" data-phase="waiting" ${saved ? '' : 'disabled data-unavailable="true"'}>Show waiting lobbies</button> <button type="button" data-action="browse-games" data-game="${esc(game.id)}" data-phase="running" ${saved ? '' : 'disabled data-unavailable="true"'}>Show running games</button><pre data-game-list="${esc(game.id)}" role="status"></pre>${saved ? '' : '<p class="form-help">Save this game before browsing public games.</p>'}</div>` : ''}
    <p class="muted">${catalog.manifest.limitations.map(esc).join(' ')}</p></details>`;
}

function safeLink(value: string | undefined): string | undefined {
  if (!value) return;
  try {
    const u = new URL(value);
    if (u.protocol === 'https:' && !u.username && !u.password) return u.toString();
  } catch {
    /* Legacy unvalidated links render as text only. */
  }
}
function referenceLink(url: string | undefined, label: string) {
  const safe = safeLink(url);
  return safe
    ? `<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`
    : esc(label);
}
export function matchConnectorDetails(match: Match) {
  const binding = match.lobby;
  if (!binding) return '';
  const observed = binding.observation;
  const declared = binding.manualFacts;
  const selections = observed?.players.filter((player) => player.selection);
  const replays = observed?.replayReferences ?? [];
  return `<section class="match-evidence"><h3>Game connection</h3>
    <p>${referenceLink(binding.url, `Lobby ${binding.externalId}`)} · ${esc(binding.state)}${binding.availability ? ` · ${esc(stateLabel(binding.availability))}` : ''}</p>
    <p class="muted">${binding.rosterVerified ? 'Declared account roster and teams match.' : binding.confirmedBy ? 'Association confirmed by a captain or organizer.' : 'Provisional association; a matching name or player count alone does not confirm the roster.'}</p>
    ${
      observed
        ? `<p>Observed: ${esc(observed.phase)} · ${esc(new Date(observed.observedAt).toLocaleString())} · ${esc(observed.source)}</p>
      ${observed.map ? `<p>Observed map: ${esc(observed.map)}</p>` : ''}
      ${observed.elapsedDuration ? `<p>Elapsed duration: ${esc(observed.elapsedDuration.value)} seconds</p>` : ''}
      ${observed.simulationDuration ? `<p>Simulation duration: ${esc(observed.simulationDuration.value)} seconds</p>` : ''}
      ${observed.gameBuild ? `<p>Game build: ${esc(observed.gameBuild)}</p>` : ''}
      ${selections?.length ? `<p>Observed factions: ${selections.map((player) => `${esc(player.name)} — ${esc(player.selection)}`).join(' · ')}</p>` : ''}`
        : ''
    }
    ${binding.suggestedResult ? `<p class="notice">Provider suggests ${binding.suggestedResult === 'draw' ? 'a draw' : `${binding.suggestedResult === 'alpha' ? 'Alpha' : 'Beta'} wins`}. Captain confirmation is still required; this does not change ratings.</p>` : ''}
    ${declared ? `<p>Declared facts: ${[declared.map ? `map ${esc(declared.map)}` : '', declared.elapsedSeconds !== undefined ? `${declared.elapsedSeconds} seconds elapsed` : '', ...declared.players.map((player) => `${esc(match.roster.find((p) => p.id === player.userId)?.name ?? player.userId)} — ${esc(player.selection)}`)].filter(Boolean).join(' · ') || 'None'}.</p><p class="record-meta">Declared ${esc(new Date(declared.declaredAt).toLocaleString())}.</p>` : ''}
    ${
      replays.length
        ? `<p>Provider recordings: ${replays
            .map((replay) => {
              const expired = replay.expiresAt !== undefined && replay.expiresAt <= Date.now();
              const label = `${replay.provider}${replay.perspectiveId ? ` · ${replay.perspectiveId}` : ''}: ${expired ? 'expired' : replay.availability}`;
              return replay.availability === 'available' && !expired
                ? referenceLink(replay.sourceUrl, label)
                : esc(label);
            })
            .join(
              ' · ',
            )}</p><p class="muted">Provider links are not archived files. Use /lobby replays to refresh temporary references.</p>`
        : ''
    }
    ${declared?.recordingLinks?.length ? `<p>Participant recording references: ${declared.recordingLinks.map((url, index) => referenceLink(url, `Reference ${index + 1} (availability unknown)`)).join(' · ')}</p>` : ''}
    ${['check_in', 'playing'].includes(match.state) ? '<p class="muted">Use /lobby facts to declare your faction or game facts; captains report results with the existing reporting controls.</p>' : ''}
    <details class="technical-details"><summary>Connection identifiers</summary><p>Association ${esc(binding.id)}</p>${observed?.nativeLobbyId ? `<p>Native lobby ${esc(observed.nativeLobbyId)}</p>` : ''}${observed?.nativeMatchId ? `<p>Native match ${esc(observed.nativeMatchId)}</p>` : ''}${observed?.platformLobbyId ? `<p>Platform lobby ${esc(observed.platformLobbyId)}</p>` : ''}</details></section>`;
}
