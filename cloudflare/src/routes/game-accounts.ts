import { Hono, type Context } from 'hono';
import { z } from 'zod';
import type { Env, Session } from '../env';
import type { CatalogId } from '../games/types';
import { fetchOwnedSc2Characters } from '../games/sc2';
import {
  authorizationUrl,
  completeGameOAuth,
  gameAccountAvailability,
  GameAccountError,
  randomState,
  supportsOwnership,
  validState,
  verifySteamAssertion,
  type GameAccountCredentials,
  type GameAccountProof,
  type GameVerificationState,
} from '../auth/game-accounts';

type Bindings = Env & GameAccountCredentials;
export type GameAccountApp = {
  Bindings: Bindings;
  Variables: { requestId: string; session: Session; sessionId: string };
};
interface GameAccountDependencies {
  session(c: Context<GameAccountApp>): Promise<{ id: string; session: Session } | null>;
  /** Verify current Discord community membership and game configuration, even for first-time players. */
  authorize(
    env: Bindings,
    communityId: string,
    gameId: string,
    session: Session,
  ): Promise<{ catalogId: CatalogId }>;
  /** Only this server-side callback may mark ownership_verified; ordinary record actions cannot. */
  link(env: Bindings, proof: GameAccountProof): Promise<{ ok: boolean; content: string }>;
}
const escape = (value: string) =>
  value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
const page = (title: string, body: string) =>
  `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(title)}</title><body><main><h1>${escape(title)}</h1>${body}<p><a href="/">Return to the bot</a></p></main></body></html>`;
function origin(env: Bindings): string {
  const url = new URL(env.APP_ORIGIN);
  if (
    url.origin !== env.APP_ORIGIN ||
    (url.protocol !== 'https:' &&
      !(
        env.APP_ENV === 'local' &&
        url.protocol === 'http:' &&
        ['localhost', '127.0.0.1'].includes(url.hostname)
      ))
  )
    throw new GameAccountError(
      'setup_incomplete',
      'The operator must configure this deployment’s public URL.',
      503,
    );
  return url.origin;
}
async function body(request: Request): Promise<string> {
  const reader = request.body?.getReader();
  if (!reader) throw new GameAccountError('invalid_request', 'The request is empty.');
  let size = 0;
  const parts: Uint8Array[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 8192)
        throw new GameAccountError('invalid_request', 'The request is too large.', 413);
      parts.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const data = new Uint8Array(size);
  let position = 0;
  for (const part of parts) {
    data.set(part, position);
    position += part.length;
  }
  return new TextDecoder().decode(data);
}
function csrf(c: Context<GameAccountApp>, session: Session, token?: string | null) {
  if (c.req.header('Origin') !== origin(c.env) || !token || token !== session.csrf)
    throw new GameAccountError('csrf', 'Refresh the bot page and start verification again.', 403);
}
const startSchema = z
  .object({
    communityId: z.string().regex(/^\d{17,20}$/),
    gameId: z.uuid(),
    provider: z.enum(['steam', 'battlenet', 'bar']),
  })
  .strict();
const proofContext = (state: GameVerificationState) => ({
  communityId: state.communityId,
  gameId: state.gameId,
  catalogId: state.catalogId,
  discordUserId: state.discordUserId,
  verifiedAt: Date.now(),
});

/** Mount at /auth/game. OAuth tokens are used in memory and are never persisted. */
export function gameAccountRoutes(deps: GameAccountDependencies) {
  const app = new Hono<GameAccountApp>();
  app.use('*', async (c, next) => {
    c.header('Cache-Control', 'no-store');
    c.header('Referrer-Policy', 'no-referrer');
    c.header(
      'Content-Security-Policy',
      "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    );
    await next();
  });
  app.onError((error, c) => {
    if (error instanceof GameAccountError)
      return c.json({ code: error.code, error: error.message }, error.status as 400);
    if (error instanceof z.ZodError)
      return c.json(
        { code: 'invalid_request', error: 'Check the community, game and identity provider.' },
        400,
      );
    // Never echo provider bodies, OAuth codes, tokens, or callback query parameters.
    return c.json(
      {
        code: 'verification_failed',
        error: 'Account verification could not be completed. Start again.',
      },
      503,
    );
  });
  app.post('/start', async (c) => {
    const login = await deps.session(c);
    if (!login) throw new GameAccountError('login_required', 'Sign in to Discord first.', 401);
    csrf(c, login.session, c.req.header('X-CSRF-Token'));
    if (!c.req.header('Content-Type')?.startsWith('application/json'))
      throw new GameAccountError('invalid_request', 'Send JSON.', 415);
    let raw: unknown;
    try {
      raw = JSON.parse(await body(c.req.raw));
    } catch (error) {
      if (error instanceof GameAccountError) throw error;
      throw new GameAccountError('invalid_request', 'Send valid JSON.');
    }
    const input = startSchema.parse(raw);
    const game = await deps.authorize(c.env, input.communityId, input.gameId, login.session);
    if (!supportsOwnership(game.catalogId, input.provider))
      throw new GameAccountError(
        'unsupported',
        'This game does not support ownership verification through that provider.',
      );
    if (!gameAccountAvailability(input.provider, c.env))
      throw new GameAccountError(
        'credentials_required',
        'This identity provider needs operator credentials. Declared linking remains available.',
        503,
      );
    const nonce = randomState();
    const callback = `${origin(c.env)}/auth/game/callback/${input.provider}`;
    const state: GameVerificationState = {
      ...input,
      catalogId: game.catalogId,
      discordUserId: login.session.user.id,
      sessionId: login.id,
      expiresAt: Date.now() + 600000,
      redirectUri: input.provider === 'steam' ? `${callback}?state=${nonce}` : callback,
      ...(input.provider === 'bar' ? { codeVerifier: randomState() } : {}),
    };
    const url = await authorizationUrl(state, nonce, c.env);
    await c.env.AUTH.getByName(`game-oauth:${nonce}`).createGameVerification(state);
    return c.json({ url, expiresAt: state.expiresAt });
  });
  app.get('/callback/:provider', async (c) => {
    const query = new URL(c.req.url).searchParams;
    const nonce = query.get('state');
    const login = await deps.session(c);
    if (!login)
      throw new GameAccountError(
        'login_required',
        'Sign in to the same Discord account and start verification again.',
        401,
      );
    if (!validState(nonce) || query.getAll('state').length !== 1)
      throw new GameAccountError(
        'invalid_state',
        'Verification expired or was already used. Start again.',
      );
    const state = await c.env.AUTH.getByName(`game-oauth:${nonce}`).consumeGameVerification(
      login.id,
      login.session.user.id,
    );
    if (!state || state.provider !== c.req.param('provider'))
      throw new GameAccountError(
        'invalid_state',
        'Verification expired or was already used. Start again.',
      );
    const game = await deps.authorize(c.env, state.communityId, state.gameId, login.session);
    if (game.catalogId !== state.catalogId || !supportsOwnership(game.catalogId, state.provider))
      throw new GameAccountError(
        'game_changed',
        'Game settings changed. Start verification again.',
      );
    if (query.has('error') || query.get('openid.mode') === 'cancel')
      return c.html(
        page('Verification cancelled', '<p>Your existing account links were not changed.</p>'),
      );
    let proof: GameAccountProof;
    if (state.provider === 'steam') {
      const id = await verifySteamAssertion(query, state);
      proof = {
        ...proofContext(state),
        provider: 'steam',
        externalId: id,
        url: `https://steamcommunity.com/profiles/${id}`,
        namespace: 'steam',
        edition: 'Steam',
        platformId: id,
        verifiedBy: 'steam-openid',
      };
    } else {
      if (query.getAll('code').length !== 1)
        throw new GameAccountError('invalid_code', 'Start verification again.');
      const identity = await completeGameOAuth(state, query.get('code') ?? '', c.env);
      if (state.provider === 'battlenet') {
        const characters = await fetchOwnedSc2Characters(identity.subject, {
          battleNetAccessToken: identity.accessToken,
          // The shared coordinator bounds concurrent regional reads. Bearer-authenticated
          // responses are never cached or written to its diagnostics.
          ...(c.env.PROVIDERS
            ? {
                request: (request) =>
                  c.env.PROVIDERS!.getByName(new URL(request.url).hostname).request(request),
              }
            : {}),
        });
        if (characters.status !== 'ok')
          throw new GameAccountError(
            characters.status,
            'Battle.net could not list your characters. Start verification again.',
            503,
          );
        if (!characters.value.profiles.length)
          return c.html(
            page(
              'No SC2 characters found',
              '<p>Create or select a supported US, EU or KR/TW character in StarCraft II, then verify again. Declared linking remains available.</p>',
            ),
          );
        const choice = randomState();
        await c.env.AUTH.getByName(`game-choice:${choice}`).createGameChoices({
          ...state,
          expiresAt: Date.now() + 600000,
          codeVerifier: undefined,
          accountId: identity.subject,
          ...characters.value,
        });
        const warning = characters.value.unavailableRegions.length
          ? `<p>Some regional requests were unavailable (${escape(characters.value.unavailableRegions.join(', '))}). These are the characters verified by successful requests; retry later to see other regions.</p>`
          : '';
        const options = characters.value.profiles
          .map(
            (p) =>
              `<option value="${escape(p.id)}">${escape(p.name)} — ${escape(p.id)} (region:realm:profile)</option>`,
          )
          .join('');
        return c.html(
          page(
            'Choose your SC2 character',
            `<p>Select the character to link in this community. You can run verification again to link another region.</p>${warning}<form method="post" action="/auth/game/select"><input type="hidden" name="state" value="${choice}"><input type="hidden" name="csrf" value="${escape(login.session.csrf)}"><label>Character <select name="profileId">${options}</select></label><button type="submit">Link verified character</button></form>`,
          ),
        );
      }
      proof = {
        ...proofContext(state),
        provider: 'bar',
        externalId: identity.subject,
        url: 'https://www.beyondallreason.info/',
        namespace: 'bar:user',
        edition: 'Beyond All Reason',
        verifiedBy: 'bar-oauth',
      };
    }
    const result = await deps.link(c.env, proof);
    return c.html(
      page(
        result.ok ? 'Account verified' : 'Account could not be linked',
        `<p>${escape(result.content)}</p>`,
      ),
      result.ok ? 200 : 409,
    );
  });
  app.post('/select', async (c) => {
    const login = await deps.session(c);
    if (!login)
      throw new GameAccountError(
        'login_required',
        'Sign in to Discord and start verification again.',
        401,
      );
    if (!c.req.header('Content-Type')?.startsWith('application/x-www-form-urlencoded'))
      throw new GameAccountError('invalid_request', 'Submit the character selection form.', 415);
    const form = new URLSearchParams(await body(c.req.raw));
    for (const field of ['state', 'csrf', 'profileId'])
      if (form.getAll(field).length !== 1)
        throw new GameAccountError('invalid_request', 'Submit one character selection.');
    csrf(c, login.session, form.get('csrf'));
    const nonce = form.get('state');
    if (!validState(nonce))
      throw new GameAccountError('invalid_state', 'Start verification again.');
    const choices = await c.env.AUTH.getByName(`game-choice:${nonce}`).consumeGameChoices(
      login.id,
      login.session.user.id,
    );
    if (!choices || choices.provider !== 'battlenet')
      throw new GameAccountError(
        'invalid_state',
        'Selection expired or was already used. Start verification again.',
      );
    const game = await deps.authorize(c.env, choices.communityId, choices.gameId, login.session);
    if (game.catalogId !== choices.catalogId || game.catalogId !== 'sc2')
      throw new GameAccountError(
        'game_changed',
        'Game settings changed. Start verification again.',
      );
    const profile = choices.profiles.find((p) => p.id === form.get('profileId'));
    if (!profile)
      throw new GameAccountError(
        'not_owned',
        'That character was not returned for your authorized Battle.net account.',
      );
    const result = await deps.link(c.env, {
      ...proofContext(choices),
      provider: 'battle-net',
      externalId: profile.id,
      url: profile.url,
      namespace: 'sc2-character',
      edition: 'StarCraft II',
      platformId: choices.accountId,
      verifiedBy: 'battlenet-oauth',
    });
    return c.html(
      page(
        result.ok ? 'Character verified' : 'Character could not be linked',
        `<p>${escape(result.content)}</p>`,
      ),
      result.ok ? 200 : 409,
    );
  });
  return app;
}
