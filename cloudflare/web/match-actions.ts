import type { Match } from '../src/domain/play';
import { esc } from './ui';

export type MemberChoice = { id: string; name: string; username?: string; context?: string };
export function memberOptions(members: MemberChoice[], selected = '') {
  return (
    '<option value="">Choose a player</option>' +
    members
      .map(
        (p) =>
          `<option value="${esc(p.id)}" ${p.id === selected ? 'selected' : ''}>${esc(p.name)}${p.username ? ` (@${esc(p.username)})` : ''}${p.context ? ` · ${esc(p.context)}` : ''}</option>`,
      )
      .join('')
  );
}
function memberPicker(name: string, label: string, members: MemberChoice[]) {
  return `<fieldset class="member-picker" data-member-picker><legend>${esc(label)}</legend><div class="member-search"><label>Search server members<input type="search" data-member-query minlength="2" maxlength="80" placeholder="At least 2 characters" /></label><button type="button" data-search-members>Search</button></div><label>${esc(label)}<select name="${name}" aria-label="${esc(label)}" required>${memberOptions(members)}</select></label><p class="form-help" data-member-status role="status">Showing loaded players. Search to find another server member. Eligibility is checked when you confirm.</p></fieldset>`;
}
export function matchRepairs(m: Match, candidates: MemberChoice[]) {
  const active = ['playing', 'check_in'].includes(m.state);
  const roster = m.teams.flatMap((team, i) =>
    team.map((p) => ({ ...p, context: i ? 'Beta' : 'Alpha' })),
  );
  const form = (kind: string, body: string, button: string) =>
    `<form data-kind="repair" data-id="${esc(m.id)}" class="repair-form"><input type="hidden" name="kind" value="${kind}" />${body}<button>${button}</button></form>`;
  const outgoing = `<label>Who is leaving?<select name="target" aria-label="Who is leaving?" required>${memberOptions(roster)}</select></label>`;
  return `<details class="rating-options match-admin"><summary>Administrator actions</summary><div class="match-repair-actions">${
    active
      ? `
    <details><summary>Replace a player</summary>${form(
      'force_sub',
      `<p>Replace one roster member. Everyone else stays on their team. You can choose someone who is paused from queues; they must be a server member who is not in another active match.</p>${outgoing}${memberPicker(
        'replacement',
        'Who replaces them?',
        candidates.filter((p) => !m.roster.some((v) => v.id === p.id)),
      )}`,
      'Review replacement',
    )}</details>
    <details><summary>Place or move a player</summary>${form('put', `<p>Move a roster member or add a server member to the chosen team. You can choose someone who is paused from queues. Captains are updated if needed.</p>${memberPicker('target', 'Player to place', [...roster, ...candidates.filter((p) => !m.roster.some((v) => v.id === p.id))])}<label>Move to<select name="result" aria-label="Move to"><option value="alpha">Alpha</option><option value="beta">Beta</option></select></label>`, 'Review placement')}</details>
    <details><summary>Cancel match</summary>${form('recover_abort', '<p>End this match without rating changes. Open predictions are refunded; ones already paid out stay paid. Players can rejoin the queue.</p>', 'Review cancellation')}</details>`
      : ''
  }
    ${m.state === 'finished' ? form('undo', '<p>Reverse this result’s rating changes and win/loss counts. Not possible while any of its players is in an active match. Predictions already paid out stay paid.</p>', 'Review undo') : ''}
    ${m.state === 'undone' ? form('admin_report', '<p>Record the correct result, using players’ current ratings. Predictions already paid out stay paid.</p><label>Corrected result<select name="result"><option value="alpha">Alpha won</option><option value="beta">Beta won</option><option value="draw">Draw</option><option value="abort">Cancel without ratings</option></select></label>', 'Review corrected result') : ''}
    ${form('recover', '<p>Refresh the Discord card for this match. The result and ratings do not change.</p>', 'Refresh the Discord card')}
    </div></details>`;
}
