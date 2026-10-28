import { escapeConnectorText as esc } from './connectors';

interface AccountBootstrap {
  user: { id: string; username: string } | null;
  csrf: string | null;
}
/** A member page: account ownership must not require access to organizer settings. */
export async function showAccountLink(app: HTMLElement, bootstrap: AccountBootstrap) {
  const url = new URL(location.href);
  const communityId = url.searchParams.get('community') ?? '';
  const gameId = url.searchParams.get('game') ?? '';
  const provider = url.searchParams.get('provider') ?? '';
  const names: Record<string, string> = { steam: 'Steam', battlenet: 'Battle.net', bar: 'BAR' };
  if (
    !/^\d{17,20}$/.test(communityId) ||
    !/^[a-f\d-]{36}$/i.test(gameId) ||
    !Object.hasOwn(names, provider)
  ) {
    app.innerHTML =
      '<section class="panel"><h1>Verify a game account</h1><p>Use /profile verify in your game queue to open the account-linking page.</p><a href="/">Return to the bot</a></section>';
    return;
  }
  if (!bootstrap.user) {
    const returnTo = url.pathname + url.search;
    app.innerHTML = `<section class="panel"><h1>Verify your ${names[provider]} account</h1><p>Sign in to Discord first so the account is linked to you in this community.</p><a class="button primary" href="/auth/login?${new URLSearchParams({ returnTo })}">Sign in with Discord</a></section>`;
    return;
  }
  app.innerHTML = `<section class="panel"><h1>Verify your ${names[provider]} account</h1><p>Signed in as ${esc(bootstrap.user.username)}. The provider will ask you to sign in and confirm access.</p><p>Verification proves account control. It does not record a match, change ratings or prove game participation.</p><button type="button" id="verify-game-account" class="primary">Continue to ${names[provider]}</button><div id="notice" class="notice" role="status" hidden></div><p class="muted">You can keep playing with a declared account using /lobby link if verification is unavailable.</p><a href="/">Return to the bot</a></section>`;
  const button = app.querySelector<HTMLButtonElement>('#verify-game-account')!;
  button.addEventListener('click', async () => {
    const notice = app.querySelector<HTMLElement>('#notice')!;
    button.disabled = true;
    notice.hidden = true;
    try {
      const response = await fetch('/auth/game/start', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': bootstrap.csrf ?? '' },
        body: JSON.stringify({ communityId, gameId, provider }),
      });
      const result = (await response.json()) as { url?: string; error?: string };
      if (!response.ok || !result.url)
        throw Error(
          result.error ?? 'Account verification is unavailable. You can keep using declared links.',
        );
      const target = new URL(result.url);
      if (target.protocol !== 'https:')
        throw Error('The provider returned an invalid sign-in URL.');
      location.assign(target.toString());
    } catch (error) {
      notice.textContent = error instanceof Error ? error.message : 'Try again.';
      notice.className = 'notice error';
      notice.setAttribute('role', 'alert');
      notice.hidden = false;
      button.disabled = false;
    }
  });
}
