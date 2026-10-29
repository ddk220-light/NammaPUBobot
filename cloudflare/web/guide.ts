import type { Snapshot } from '../src/domain/config';
import { gameCatalog } from '../src/games/catalog';
import type { EditorSetup } from './settings';
import type { SettingsDraft } from './draft';
import { esc } from './ui';

export type InviteUrls = { basic: string | null; ranks: string | null; nicknames: string | null };
export const guideSteps = ['access', 'game', 'queue', 'try'] as const;
export type GuideStep = (typeof guideSteps)[number];
export function inviteLink(url: string | null | undefined, guild: string, label: string) {
  if (!url) return '';
  const target = new URL(url);
  target.searchParams.set('guild_id', guild);
  target.searchParams.set('disable_guild_select', 'true');
  return `<a class="button" data-discord-invite target="_blank" rel="noopener" href="${esc(target.href)}">${esc(label)}</a>`;
}
export function savedReady(snapshot: Snapshot, setup: EditorSetup) {
  return (
    setup.access.status === 'ready' &&
    snapshot.settings.queues.some((q) => {
      const game = snapshot.settings.games.find((g) => g.id === q.gameId);
      const ranks = game?.records?.ranks.filter((r) => r.roleId) ?? [];
      return (
        q.enabled &&
        Boolean(game) &&
        setup.channels.some((c) => c.id === q.channelId && c.permissions.every((p) => p.granted)) &&
        (!game?.records?.decorateNickname || setup.access.manageNicknames) &&
        (!ranks.length ||
          (setup.access.manageRoles &&
            ranks.every((r) =>
              setup.roles.some((role) => role.id === r.roleId && role.rankEligible),
            )))
      );
    })
  );
}
export function permissionRecovery(
  draft: SettingsDraft,
  setup: EditorSetup,
  urls: InviteUrls,
  checked: Date | null,
) {
  const settings = draft.materialize();
  const known = setup.access.status === 'ready';
  const roleGames = settings.games.filter((g) => g.records?.ranks.some((r) => r.roleId));
  const nicknameGames = settings.games.filter((g) => g.records?.decorateNickname);
  const row = (label: string, status: string, detail: string) =>
    `<tr><th scope="row">${esc(label)}</th><td>${esc(status)}</td><td>${esc(detail)}</td></tr>`;
  const channelIds = [...new Set(settings.queues.map((q) => q.channelId))];
  const channelRows = channelIds
    .map((id) => {
      const channel = setup.channels.find((c) => c.id === id);
      const missing = channel?.permissions.filter((p) => !p.granted) ?? [];
      return row(
        channel ? `#${channel.name}` : `Channel ${id || 'not selected'}`,
        !known
          ? 'Not verified'
          : !channel
            ? 'Unavailable'
            : missing.length
              ? 'Needs access'
              : 'Ready',
        !known
          ? 'Recheck Discord access to verify this channel.'
          : !channel
            ? 'Select an available channel or restore the bot’s access.'
            : missing.length
              ? `Grant ${missing.map((p) => p.name).join(', ')}.`
              : 'View Channel, Send Messages, Embed Links and Read Message History granted.',
      );
    })
    .join('');
  const hierarchy = roleGames
    .flatMap((g) =>
      g
        .records!.ranks.filter((r) => r.roleId)
        .map((r) => {
          const role = setup.roles.find((v) => v.id === r.roleId);
          return row(
            `${g.name} · ${r.label}`,
            !known ? 'Not verified' : role?.rankEligible ? 'Eligible' : 'Needs attention',
            !known
              ? 'Verify Discord access before checking role eligibility.'
              : (role?.reason ??
                  (role
                    ? 'Cosmetic role is below the bot.'
                    : `Role ${r.roleId} is unavailable. Select a replacement in Games.`)),
          );
        }),
    )
    .join('');
  return `<section class="panel" id="discord-access"><div class="section-heading"><h2>Discord access</h2>${inviteLink(urls.basic, draft.saved.communityId, 'Invite bot to server')}</div>
    <p class="notice${known ? '' : ' error'}">${esc(setup.access.message)}</p>
    <p class="record-meta">${checked ? `Last checked ${esc(checked.toLocaleTimeString())}.` : 'Not checked yet.'} Changes in Discord take effect here after a new check.</p>
    <div class="actions"><button type="button" data-action="check-access">Check access again</button></div>
    <div class="table-wrap"><table><caption>Permissions for the current settings draft</caption><thead><tr><th>Channel or feature</th><th>Status</th><th>Requirement</th></tr></thead><tbody>${channelRows || row('Queue channels', 'Not selected', 'Choose a channel when creating your first queue.')}
    ${row('Rank roles', !roleGames.length ? 'Optional · not configured' : !known ? 'Not verified' : setup.access.manageRoles ? 'Granted' : 'Missing', 'Manage Roles is needed only when assigning Discord rank roles.')}
    ${row('Nickname decoration', !nicknameGames.length ? 'Optional · not configured' : !known ? 'Not verified' : setup.access.manageNicknames ? 'Granted' : 'Missing', 'Manage Nicknames is needed only when decorating player nicknames.')}${hierarchy}</tbody></table></div>
    <div class="actions">${roleGames.length && (!known || !setup.access.manageRoles) ? inviteLink(urls.ranks, draft.saved.communityId, 'Grant Manage Roles') : ''}${nicknameGames.length && (!known || !setup.access.manageNicknames) ? inviteLink(urls.nicknames, draft.saved.communityId, 'Grant Manage Nicknames') : ''}</div>
    <details class="rating-options"><summary>How to fix access in Discord</summary><ol class="recovery-steps"><li>Open Server Settings → Roles. Find the Community Games bot role.</li><li>For rank roles, grant Manage Roles and move the bot above the cosmetic rank roles. Remove management permissions from rank roles.</li><li>For nickname decoration, grant Manage Nicknames and move the bot above the players it should update. Discord prevents changing the server owner’s nickname and members above the bot.</li><li>Open each queue channel → Edit Channel → Permissions. Allow View Channel, Send Messages, Embed Links and Read Message History for the bot, checking channel overrides too.</li><li>Return here and select Check access again. Inviting again cannot repair role order or channel overrides.</li></ol></details>
    <p class="form-help">A temporary Discord failure does not erase saved configuration. You can continue editing and retry access checks.</p></section>`;
}

export function guidedSetup(
  draft: SettingsDraft,
  setup: EditorSetup,
  urls: InviteUrls,
  route: URL,
  saved: Snapshot,
) {
  const stage = route.searchParams.get('guide') as GuideStep;
  if (!guideSteps.includes(stage)) return '';
  const index = guideSteps.indexOf(stage);
  const settings = draft.materialize();
  const game = settings.games.find((g) => g.id === route.searchParams.get('guideGame'));
  const queue = settings.queues.find((q) => q.id === route.searchParams.get('guideQueue'));
  const raw = queue && draft.raw.get(`queue:${queue.id}`);
  const read = (name: string, fallback: unknown) => esc(raw?.[name]?.[0] ?? fallback);
  let body = '';
  if (stage === 'access')
    body = `<h3>Connect your Discord community</h3><p>${esc(setup.access.message)}</p><p>Invite the bot, choose this server, and return here. We recheck saved configuration and permissions when you return.</p><div class="actions">${inviteLink(urls.basic, draft.saved.communityId, 'Add to Discord')}<button type="button" data-action="check-access">I’m back — check access</button></div><p class="form-help">You may prepare a game while access is unavailable. An enabled queue still requires verified channel permissions when you save.</p>`;
  if (stage === 'game')
    body = `<h3>Choose a game</h3><p>Queues for the same game share ratings, leaderboard policy and tax time.</p><label>Game for guided setup<select data-guide-game><option value="">Choose a game</option>${settings.games.map((g) => `<option value="${g.id}" ${g.id === game?.id ? 'selected' : ''}>${esc(g.name || 'Custom game draft')}</option>`).join('')}${gameCatalog
      .filter((g) => !settings.games.some((v) => v.catalogId === g.id))
      .map((g) => `<option value="catalog:${g.id}">${esc(g.name)} · add game</option>`)
      .join(
        '',
      )}<option value="custom">Custom game · add game</option></select></label>${game && !game.catalogId ? `<label>Custom game name<input data-guide-name value="${esc(game.name)}" maxlength="80" required /></label>` : ''}<p class="form-help">Adding a game stages a draft. Nothing is published until Save changes succeeds.</p>`;
  if (stage === 'queue')
    body = !queue
      ? '<p>Choose a game first, then continue to create a queue draft.</p>'
      : `<h3>Your first queue</h3><p>${esc(game?.name ?? 'Game')} · joining will open after a successful save.</p><form data-guide-queue="${queue.id}" novalidate><div class="fields"><label>Queue name<input name="name" value="${read('name', queue.name)}" maxlength="40" required /></label><label>Discord channel<select name="channelId">${setup.channels.map((c) => `<option value="${c.id}" ${c.id === queue.channelId ? 'selected' : ''}>#${esc(c.name)}</option>`).join('')}${!setup.channels.some((c) => c.id === queue.channelId) ? `<option value="${esc(queue.channelId)}" selected>Unavailable channel · ${esc(queue.channelId || 'choose after checking access')}</option>` : ''}</select></label><label>Players<input name="size" type="number" min="2" max="16" step="2" value="${read('size', queue.size)}" required /></label><label>Team formation<select name="teamMethod">${Object.entries(
          { captain: 'Captain pick', balanced: 'Balanced by rating', random: 'Random teams' },
        )
          .map(
            ([id, label]) =>
              `<option value="${id}" ${id === queue.teamMethod ? 'selected' : ''}>${label}</option>`,
          )
          .join(
            '',
          )}</select></label></div></form><aside class="discord-preview" aria-label="Illustrative Discord queue preview"><p class="eyebrow">Preview · draft, not a posted card</p><h4 data-preview-name></h4><p data-preview-details></p><p data-preview-rules></p></aside><p class="form-help">New queues start with 60-second check-in and automatic start. Shared rating settings are inherited for this game. Review advanced settings before saving if you need different rules.</p><button type="button" data-guide-advanced="${queue.id}">Advanced queue settings</button>`;
  if (stage === 'try')
    body = savedReady(saved, setup)
      ? `<h3>Try your queue in Discord</h3><p>Your saved configuration and channel permissions are ready.</p><ol class="recovery-steps"><li>Open the configured Discord channel.</li><li>Use the queue card or <code>/join</code> to join. Invite your players to do the same.</li><li>Complete check-in, play, and report the result in Discord.</li></ol><p class="form-help">This is a manual check. Delivery can still be pending; review Matches → Delivery diagnostics if the card has not appeared.</p><button type="button" data-view="queues">Open queues</button>`
      : '<h3>Finish saving your setup</h3><p>Save an enabled queue and verify its channel permissions. Drafts and a successful HTTP response alone do not mean the community is ready.</p><button type="button" data-action="check-access">Check access again</button>';
  return `<section class="panel guided-setup" aria-labelledby="guide-heading"><div class="section-heading"><h2 id="guide-heading" tabindex="-1">Guided setup</h2><button type="button" data-guide-close>Close guide</button></div><ol class="guide-progress">${['Discord access', 'Game', 'First queue', 'Try Discord'].map((label, i) => `<li ${i === index ? 'aria-current="step"' : ''}>${i + 1}. ${label}</li>`).join('')}</ol><p class="record-meta">Step ${index + 1} of 4 · your draft stays with this community</p>${body}<div class="actions guide-actions">${index ? `<button type="button" data-guide-step="${guideSteps[index - 1]}">Back</button>` : ''}${index < 2 ? `<button type="button" class="primary" data-guide-next="${stage}" ${stage === 'game' && !game?.name.trim() ? 'disabled data-unavailable="true"' : ''}>${stage === 'access' ? 'Choose game' : 'Create queue draft'}</button>` : stage === 'queue' ? '<button type="button" class="primary" data-guide-review>Review and save</button>' : ''}</div></section>`;
}
