import { lobbyEventSchema } from './domain/lobby';
import { Hono, type Context } from 'hono';
import { getCookie, setCookie, deleteCookie } from 'hono/cookie';
import { z } from 'zod';
import type { Env, Session } from './env';
import { catalog, saveSchema, snowflake } from './domain/config';
import {
  canManage,
  channels,
  discord,
  userGuilds,
  DiscordFailure,
  REQUIRED_CHANNEL_PERMISSIONS,
} from './discord/client';
import { interactions } from './discord/interactions';
import { PlayError, actionSchema, type Action } from './domain/play';
import { memberIds, resolveMember } from './discord/members';
import { importSchema, recordActionSchema, recordPolicy } from './domain/records';
import { economyImportSchema } from './domain/economy-import';
import { privilegedRankPermissions } from './domain/rank-roles';
export { AoE2LobbyFeed } from './storage/aoe2-lobby-feed';
export { Community } from './storage/community';
export { AuthStore } from './storage/auth';
export { DiscordGate } from './storage/discord-gate';
export { VoicePresence } from './storage/voice-presence';

type App = { Bindings: Env; Variables: { requestId: string; session: Session; sessionId: string } };
const app = new Hono<App>();
class Problem extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
function origin(env: Env) {
  try {
    const url = new URL(env.APP_ORIGIN);
    if (
      url.origin !== env.APP_ORIGIN ||
      (url.protocol !== 'https:' &&
        !(
          url.protocol === 'http:' &&
          env.APP_ENV === 'local' &&
          ['localhost', '127.0.0.1'].includes(url.hostname)
        ))
    )
      throw new Error();
    return url.origin;
  } catch {
    throw new Problem(
      503,
      'setup_incomplete',
      'The operator must configure this deployment’s public URL.',
    );
  }
}
const secure = (env: Env) => env.APP_ENV !== 'local';
const sessionCookie = (env: Env) => (secure(env) ? '__Host-cg-session' : 'cg-session');
const tokenId = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) =>
    b.toString(16).padStart(2, '0'),
  ).join('');
const isTokenId = (s: string | undefined): s is string => !!s && /^[a-f0-9]{64}$/.test(s);

async function sessionFor(c: Context<App>): Promise<{ id: string; session: Session } | null> {
  const id = getCookie(c, sessionCookie(c.env));
  if (!isTokenId(id)) return null;
  const session = await c.env.AUTH.getByName(`session:${id}`).session();
  return session ? { id, session } : null;
}

async function readJson(request: Request, limit = 262144) {
  if (!request.headers.get('Content-Type')?.startsWith('application/json'))
    throw new Problem(415, 'content_type', 'Send JSON.');
  const reader = request.body?.getReader();
  if (!reader) throw new Problem(400, 'invalid_json', 'The request is empty.');
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > limit) {
      await reader.cancel();
      throw new Problem(413, 'too_large', 'The request is too large.');
    }
    chunks.push(value);
  }
  const body = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(body));
  } catch {
    throw new Problem(400, 'invalid_json', 'The JSON file is invalid.');
  }
}

app.use('*', async (c, next) => {
  const requestId = crypto.randomUUID();
  c.set('requestId', requestId);
  const started = Date.now();
  await next();
  c.header('X-Request-ID', requestId);
  c.header('X-Content-Type-Options', 'nosniff');
  c.header('Referrer-Policy', 'same-origin');
  c.header('X-Frame-Options', 'DENY');
  if (
    c.req.path.startsWith('/api/') ||
    c.req.path.startsWith('/auth/') ||
    c.req.path.startsWith('/statistics/') ||
    c.req.path.startsWith('/ops/') ||
    c.req.path.startsWith('/connectors/')
  )
    c.header('Cache-Control', 'no-store');
  console.log(
    JSON.stringify({
      requestId,
      path: c.req.path,
      method: c.req.method,
      status: c.res.status,
      elapsedMs: Date.now() - started,
    }),
  );
});

app.onError((error, c) => {
  let status = 500,
    code = 'internal_error',
    message = 'Something went wrong. Retry or give the request ID to the operator.';
  if (error instanceof Problem) ({ status, code, message } = error);
  else if (error instanceof PlayError) {
    status = 409;
    code = 'play_conflict';
    message = error.message;
  } else if (error instanceof z.ZodError) {
    status = 400;
    code = 'invalid_settings';
    message = error.issues.map((i) => i.message).join(' ');
  } else if (error instanceof DiscordFailure) {
    status = error.status === 404 || error.status === 403 ? 409 : 503;
    code = 'discord_unavailable';
    message =
      status === 409
        ? 'Invite the bot and check its permissions in this server.'
        : 'Discord could not verify access. Retry shortly.';
    if (error.status === 429) {
      code = 'discord_rate_limited';
      message = `Discord temporarily limited setup requests. Retry${error.retryAfter ? ` after ${error.retryAfter} seconds` : ' shortly'}.`;
    }
    if (error.retryAfter) c.header('Retry-After', String(Math.ceil(Number(error.retryAfter))));
  }
  // Never log OAuth tokens, callback URLs, request bodies, or session cookies.
  console.error(
    JSON.stringify({
      requestId: c.get('requestId'),
      path: c.req.path,
      code,
      status,
      ...(error instanceof DiscordFailure
        ? { providerStatus: error.status, retryAfter: error.retryAfter }
        : {}),
    }),
  );
  return c.json({ error: message, code, requestId: c.get('requestId') }, status as 400);
});

app.get('/health', (c) =>
  c.json({
    ok: true,
    environment: c.env.APP_ENV,
    milestone: 'pickup-engagement',
    version: '0.12.0',
  }),
);
app.post('/interactions', (c) => interactions(c.req.raw, c.env, c.executionCtx));

function secretMatches(actual: string | undefined, expected: string | undefined) {
  if (!actual || !expected || actual.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= actual.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}
app.use('/ops/*', async (c, next) => {
  if (!secretMatches(c.req.header('Authorization')?.replace(/^Bearer /, ''), c.env.OPS_TOKEN))
    throw new Problem(403, 'operator_required', 'Operator authorization required.');
  c.header('Cache-Control', 'no-store');
  await next();
});
function operationalCommunity(c: Context<App>) {
  const guild = snowflake.parse(c.req.param('guild'));
  const copy = c.req.query('copy');
  if (copy && !/^[a-z0-9-]{1,40}$/.test(copy))
    throw new Problem(400, 'invalid_copy', 'Use a simple recovery copy name.');
  return { guild, stub: c.env.COMMUNITIES.getByName(copy ? `recovery:${copy}:${guild}` : guild) };
}
app.get('/ops/aoe2/feed', async (c) =>
  c.json(await c.env.AOE2_LOBBIES.getByName(c.env.DISCORD_APPLICATION_ID ?? 'local').status()),
);
app.get('/ops/voice', async (c) =>
  c.json(await c.env.VOICE_PRESENCE.getByName(c.env.DISCORD_APPLICATION_ID ?? 'local').status()),
);
app.post('/ops/communities/:guild/features/voice-preview', async (c) => {
  if (c.env.APP_ENV !== 'staging')
    throw new Problem(403, 'staging_only', 'Feature rollout is limited to staging.');
  const { guild, stub } = operationalCommunity(c);
  const input = z
    .object({
      expectedRevision: z.number().int().nonnegative(),
      queueIds: z.array(z.uuid()).min(1).max(200),
      enabled: z.boolean(),
      apply: z.boolean().default(false),
    })
    .strict()
    .parse(await readJson(c.req.raw));
  const snapshot = await stub.read(guild);
  await validateChannels(
    c.env,
    guild,
    snapshot.settings.queues.filter((q) => input.queueIds.includes(q.id)),
  );
  const result = await stub.setVoicePreview(
    guild,
    input.expectedRevision,
    input.queueIds,
    input.enabled,
    input.apply,
  );
  return c.json(result, result.ok ? 200 : 409);
});
app.get('/ops/aoe2/discovery', async (c) => {
  const name = z.string().trim().min(1).max(80).parse(c.req.query('name'));
  return c.json(
    await c.env.AOE2_LOBBIES.getByName(c.env.DISCORD_APPLICATION_ID ?? 'local').discover(name),
  );
});
app.post('/ops/communities/:guild/features/aoe2', async (c) => {
  if (c.env.APP_ENV !== 'staging')
    throw new Problem(403, 'staging_only', 'Feature rollout is limited to staging.');
  const { guild, stub } = operationalCommunity(c);
  const input = z
    .object({
      expectedRevision: z.number().int().nonnegative(),
      queueIds: z.array(z.uuid()).min(1).max(200),
      lobbyName: z.string().trim().min(1).max(80),
      apply: z.boolean().default(false),
    })
    .strict()
    .parse(await readJson(c.req.raw));
  const snapshot = await stub.read(guild);
  await validateChannels(
    c.env,
    guild,
    snapshot.settings.queues.filter((q) => input.queueIds.includes(q.id)),
  );
  const result = await stub.enableAoE2Features(
    guild,
    input.expectedRevision,
    input.queueIds,
    input.lobbyName,
    input.apply,
  );
  return c.json(result, result.ok ? 200 : 409);
});
app.get('/ops/aoe2/lobbies/:id', async (c) => {
  const id = z
    .string()
    .regex(/^[1-9]\d{0,11}$/)
    .parse(c.req.param('id'));
  return c.json(
    await c.env.AOE2_LOBBIES.getByName(c.env.DISCORD_APPLICATION_ID ?? 'local').query(id),
  );
});
app.get('/ops/communities/:guild/backup', async (c) => {
  const { guild, stub } = operationalCommunity(c);
  return c.json(await stub.operationalBackup(guild));
});
app.get('/ops/communities/:guild/checkpoint', async (c) => {
  const { guild, stub } = operationalCommunity(c);
  return c.json(await stub.operationalCheckpoint(guild));
});
app.post('/ops/communities/:guild/restore', async (c) => {
  const { guild, stub } = operationalCommunity(c);
  const backup = z
    .object({
      format: z.literal(1),
      communityId: snowflake,
      sourceApplicationId: snowflake,
      createdAt: z.string(),
      tables: z.record(
        z.string(),
        z.array(z.record(z.string(), z.union([z.string(), z.number(), z.null()]))),
      ),
    })
    .strict()
    .parse(await readJson(c.req.raw, 25 * 1024 * 1024));
  const result = await stub.operationalRestore(guild, backup);
  return c.json(result, result.restored ? 200 : 409);
});
app.get('/ops/communities/:guild/inspect', async (c) => {
  const { guild, stub } = operationalCommunity(c);
  return c.json(await stub.records(guild));
});
app.post('/ops/communities/:guild/economy-import', async (c) => {
  const { guild, stub } = operationalCommunity(c);
  const input = z
    .object({ gameId: z.uuid(), apply: z.boolean().default(false), bundle: economyImportSchema })
    .strict()
    .parse(await readJson(c.req.raw, 25 * 1024 * 1024));
  const bytes = await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(JSON.stringify(input.bundle)),
    ),
    digest = Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, '0')).join('');
  const result = await stub.economyImport(guild, input.gameId, input.bundle, digest, input.apply);
  return c.json(result, result.ok ? 200 : 409);
});
app.post('/connectors/game-ended', async (c) => {
  if (!secretMatches(c.req.header('Authorization')?.replace(/^Bearer /, ''), c.env.CONNECTOR_TOKEN))
    throw new Problem(403, 'connector_required', 'Connector authorization required.');
  const event = lobbyEventSchema.parse(await readJson(c.req.raw));
  const stub = c.env.COMMUNITIES.getByName(event.communityId),
    result = await stub.connectorEnded(event.communityId, event);
  c.executionCtx.waitUntil(stub.flush());
  return c.json(result);
});

app.get('/api/bootstrap', async (c) => {
  const s = await sessionFor(c);
  const oauthReady = !!(
    c.env.DISCORD_APPLICATION_ID &&
    c.env.DISCORD_CLIENT_SECRET &&
    c.env.APP_ORIGIN
  );
  const discordReady = !!(
    c.env.DISCORD_APPLICATION_ID &&
    c.env.DISCORD_PUBLIC_KEY &&
    c.env.DISCORD_BOT_TOKEN
  );
  const invite = c.env.DISCORD_APPLICATION_ID
    ? `https://discord.com/oauth2/authorize?client_id=${c.env.DISCORD_APPLICATION_ID}&scope=bot%20applications.commands&permissions=${REQUIRED_CHANNEL_PERMISSIONS}`
    : null;
  return c.json({
    environment: c.env.APP_ENV,
    milestone: 'pickup-engagement',
    version: '0.12.0',
    oauthReady,
    discordReady,
    inviteUrl: invite,
    catalog,
    user: s?.session.user ?? null,
    csrf: s?.session.csrf ?? null,
    capabilities: {
      matchmaking: true,
      gold: false,
      quiz: false,
      civs: false,
      maps: false,
      stories: false,
    },
  });
});

app.get('/auth/login', async (c) => {
  const base = origin(c.env);
  if (!c.env.DISCORD_APPLICATION_ID || !c.env.DISCORD_CLIENT_SECRET)
    throw new Problem(503, 'setup_incomplete', 'Discord sign-in is not configured yet.');
  const state = tokenId();
  let returnTo = '/';
  const requested = c.req.query('returnTo');
  if (requested) {
    const url = new URL(requested, base);
    if (
      url.origin === base &&
      url.pathname === '/stats' &&
      snowflake.safeParse(url.searchParams.get('community')).success &&
      z.uuid().safeParse(url.searchParams.get('game')).success
    )
      returnTo = url.pathname + url.search;
  }
  await c.env.AUTH.getByName(`oauth:${state}`).createState(Date.now() + 600000, returnTo);
  setCookie(c, 'cg-oauth', state, {
    path: '/auth',
    httpOnly: true,
    secure: secure(c.env),
    sameSite: 'Lax',
    maxAge: 600,
  });
  const url = new URL('https://discord.com/oauth2/authorize');
  url.search = new URLSearchParams({
    client_id: c.env.DISCORD_APPLICATION_ID,
    response_type: 'code',
    redirect_uri: `${base}/auth/callback`,
    scope: 'identify guilds',
    state,
  }).toString();
  return c.redirect(url.href);
});

app.get('/auth/callback', async (c) => {
  const state = c.req.query('state'),
    cookie = getCookie(c, 'cg-oauth');
  deleteCookie(c, 'cg-oauth', { path: '/auth', secure: secure(c.env) });
  const returnTo =
    isTokenId(state) && state === cookie
      ? await c.env.AUTH.getByName(`oauth:${state}`).returnPath()
      : '/';
  if (
    !isTokenId(state) ||
    state !== cookie ||
    !(await c.env.AUTH.getByName(`oauth:${state}`).consumeState())
  )
    throw new Problem(
      400,
      'invalid_state',
      'Sign-in expired or was already used. Start sign-in again.',
    );
  if (c.req.query('error')) return c.redirect('/?login=cancelled');
  const code = c.req.query('code');
  if (!code || !c.env.DISCORD_APPLICATION_ID || !c.env.DISCORD_CLIENT_SECRET)
    throw new Problem(400, 'invalid_code', 'Start Discord sign-in again.');
  const response = await fetch('https://discord.com/api/v10/oauth2/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    signal: AbortSignal.timeout(5000),
    body: new URLSearchParams({
      client_id: c.env.DISCORD_APPLICATION_ID,
      client_secret: c.env.DISCORD_CLIENT_SECRET,
      grant_type: 'authorization_code',
      code,
      redirect_uri: `${origin(c.env)}/auth/callback`,
    }),
  });
  if (!response.ok) throw new Problem(401, 'login_failed', 'Discord sign-in failed. Start again.');
  const token = z
    .object({ access_token: z.string().min(1), expires_in: z.number().positive() })
    .parse(await response.json());
  const user = z
    .object({ id: snowflake, username: z.string(), avatar: z.string().nullable() })
    .parse(await discord('/users/@me', token.access_token, true));
  const id = tokenId(),
    csrf = tokenId(),
    expiresAt = Date.now() + Math.min(token.expires_in, 28800) * 1000;
  await c.env.AUTH.getByName(`session:${id}`).saveSession({
    user,
    csrf,
    accessToken: token.access_token,
    expiresAt,
  });
  setCookie(c, sessionCookie(c.env), id, {
    path: '/',
    httpOnly: true,
    secure: secure(c.env),
    sameSite: 'Lax',
    maxAge: Math.floor((expiresAt - Date.now()) / 1000),
  });
  return c.redirect(returnTo);
});

app.get('/statistics/:guild/:game', async (c) => {
  const guild = snowflake.parse(c.req.param('guild')),
    game = z.uuid().parse(c.req.param('game'));
  const stub = c.env.COMMUNITIES.getByName(guild),
    snapshot = await stub.read(guild),
    cfg = recordPolicy(snapshot.settings, game);
  if (!snapshot.settings.games.some((g) => g.id === game))
    throw new Problem(404, 'unknown_game', 'Game not found in this community.');
  const session = await sessionFor(c);
  const member = session
    ? (await userGuilds(session.session.accessToken)).find((g) => g.id === guild)
    : undefined;
  const admin = !!member && canManage(member);
  if (cfg.visibility !== 'public' && (!member || (cfg.visibility === 'admins' && !admin)))
    throw new Problem(
      403,
      'forbidden',
      'This community restricts its statistics. Sign in with an authorized Discord account.',
    );
  const user = c.req.query('user');
  if (user) snowflake.parse(user);
  const page = z.coerce
    .number()
    .int()
    .min(0)
    .max(100000)
    .parse(c.req.query('page') ?? 0);
  return c.json(await stub.statistics(guild, game, user, page, admin, session?.session.user.id));
});
app.use('/api/*', async (c, next) => {
  const s = await sessionFor(c);
  if (!s) throw new Problem(401, 'sign_in_required', 'Sign in with Discord.');
  c.set('session', s.session);
  c.set('sessionId', s.id);
  if (!['GET', 'HEAD'].includes(c.req.method)) {
    if (c.req.header('Origin') !== origin(c.env) || c.req.header('X-CSRF-Token') !== s.session.csrf)
      throw new Problem(403, 'csrf', 'Refresh the page before making this change.');
  }
  await next();
});
app.post('/api/logout', async (c) => {
  await c.env.AUTH.getByName(`session:${c.get('sessionId')}`).remove();
  deleteCookie(c, sessionCookie(c.env), { path: '/', secure: secure(c.env) });
  return c.json({ ok: true });
});
app.get('/api/communities', async (c) =>
  c.json({
    communities: (await userGuilds(c.get('session').accessToken))
      .filter(canManage)
      .map(({ id, name, icon }) => ({ id, name, icon })),
  }),
);

app.use('/api/communities/:guild/*', async (c, next) => {
  const guildId = snowflake.parse(c.req.param('guild'));
  const guild = (await userGuilds(c.get('session').accessToken)).find((g) => g.id === guildId);
  if (!guild || !canManage(guild))
    throw new Problem(403, 'forbidden', 'Manage Server permission is required for this community.');
  await next();
});

app.get('/api/communities/:guild/channels', async (c) =>
  c.json({ channels: await channels(c.env, c.req.param('guild')) }),
);
app.get('/api/communities/:guild/config', async (c) =>
  c.json(await c.env.COMMUNITIES.getByName(c.req.param('guild')).read(c.req.param('guild'))),
);
app.get('/api/communities/:guild/setup', async (c) => {
  const guildId = c.req.param('guild');
  return c.json({
    snapshot: await c.env.COMMUNITIES.getByName(guildId).read(guildId),
    channels: await channels(c.env, guildId),
    records: await c.env.COMMUNITIES.getByName(guildId).records(guildId),
  });
});
app.get('/api/communities/:guild/audit', async (c) =>
  c.json({
    entries: await c.env.COMMUNITIES.getByName(c.req.param('guild')).audit(c.req.param('guild')),
  }),
);
app.get('/api/communities/:guild/records', async (c) => {
  const game = c.req.query('game');
  const playersPage = z.coerce
    .number()
    .int()
    .min(0)
    .max(2000)
    .parse(c.req.query('playersPage') ?? 0);
  if (game) z.uuid().parse(game);
  if (
    game &&
    !(
      await c.env.COMMUNITIES.getByName(c.req.param('guild')).read(c.req.param('guild'))
    ).settings.games.some((g) => g.id === game)
  )
    return c.json({ error: 'Game not found in this community.' }, 404);
  return c.json(
    await c.env.COMMUNITIES.getByName(c.req.param('guild')).records(
      c.req.param('guild'),
      game,
      playersPage,
    ),
  );
});
app.post('/api/communities/:guild/import/preview', async (c) => {
  const bundle = importSchema.parse(await readJson(c.req.raw, 5 * 1024 * 1024));
  return c.json(
    await c.env.COMMUNITIES.getByName(c.req.param('guild')).importPreview(
      c.req.param('guild'),
      bundle,
    ),
  );
});
app.post('/api/communities/:guild/import/apply', async (c) => {
  const input = z
    .object({ bundle: importSchema, target: z.string().max(100) })
    .strict()
    .parse(await readJson(c.req.raw, 5 * 1024 * 1024));
  const stub = c.env.COMMUNITIES.getByName(c.req.param('guild'));
  const result = await stub.importApply(
    c.req.param('guild'),
    input.bundle,
    input.target,
    c.get('session').user.id,
  );
  if (!result.ok)
    throw new Problem(
      'status' in result ? result.status : 409,
      'import_conflict',
      'error' in result ? result.error : result.errors.join(' '),
    );
  c.executionCtx.waitUntil(stub.flush());
  return c.json(result);
});
app.post('/api/communities/:guild/player', async (c) => {
  const a = recordActionSchema.parse(await readJson(c.req.raw));
  const stub = c.env.COMMUNITIES.getByName(c.req.param('guild'));
  const reply = await stub.recordAction(c.req.param('guild'), a, c.get('session').user.id, true);
  if (!reply.ok) throw new Problem(reply.status, 'player_conflict', reply.content);
  c.executionCtx.waitUntil(stub.flush());
  return c.json(reply);
});

const repairSchema = actionSchema
  .omit({ actor: true, admin: true, target: true, replacement: true, teams: true })
  .extend({
    id: z.uuid(),
    kind: z.enum([
      'start',
      'clear',
      'split',
      'seed',
      'force_sub',
      'put',
      'admin_report',
      'manual_create',
      'undo',
      'recover',
      'recover_abort',
      'noadd',
      'forgive',
    ]),
    target: snowflake.optional(),
    replacement: snowflake.optional(),
    teams: z.tuple([z.string().min(1).max(300), z.string().min(1).max(300)]).optional(),
  })
  .strict();
app.post('/api/communities/:guild/actions', async (c) => {
  const input = repairSchema.parse(await readJson(c.req.raw)),
    guild = c.req.param('guild');
  if (
    ['force_sub', 'put', 'admin_report', 'undo', 'recover', 'recover_abort'].includes(input.kind) &&
    (input.version === undefined || !input.matchId)
  )
    throw new Problem(
      400,
      'match_version',
      'Select a saved match and reload its current version before repairing it.',
    );
  const { target, replacement, teams, ...fields } = input;
  const members = await Promise.all([
    target ? resolveMember(c.env, guild, target) : undefined,
    replacement ? resolveMember(c.env, guild, replacement) : undefined,
  ]);
  const action: Action = {
    ...fields,
    actor: { id: c.get('session').user.id, name: c.get('session').user.username },
    admin: true,
    ...(members[0] ? { target: members[0] } : {}),
    ...(members[1] ? { replacement: members[1] } : {}),
  };
  if (teams)
    action.teams = (await Promise.all(
      teams.map((t) => Promise.all(memberIds(t).map((id) => resolveMember(c.env, guild, id)))),
    )) as Action['teams'];
  const stub = c.env.COMMUNITIES.getByName(guild);
  const reply = await stub.play(guild, actionSchema.parse(action));
  c.executionCtx.waitUntil(stub.flush());
  if (reply.ok === false) throw new Problem(409, 'play_conflict', reply.content);
  return c.json({ reply, records: await stub.records(guild) });
});

async function validateChannels(
  env: Env,
  guildId: string,
  queues: { channelId: string; enabled: boolean }[],
) {
  if (!queues.length) return;
  const valid = await channels(env, guildId);
  for (const q of queues) {
    const channel = valid.find((v) => v.id === q.channelId);
    if (!channel)
      throw new Problem(400, 'wrong_channel', 'Choose a text channel belonging to this community.');
    if (q.enabled && channel.permissions.some((p) => !p.granted))
      throw new Problem(
        409,
        'missing_permissions',
        `Fix permissions in #${channel.name} before enabling its queue.`,
      );
  }
}
app.put('/api/communities/:guild/config', async (c) => {
  const { expectedRevision, settings } = saveSchema.parse(await readJson(c.req.raw));
  const guildId = c.req.param('guild');
  await validateChannels(c.env, guildId, settings.queues);
  const roles = settings.queues
    .flatMap((q) => [q.controls?.requiredRole, q.controls?.blockedRole])
    .filter((id): id is string => !!id);
  const rankRoles = settings.games.flatMap(
    (g) => g.records?.ranks.map((r) => r.roleId).filter((id): id is string => !!id) ?? [],
  );
  roles.push(...rankRoles);
  if (roles.length) {
    const available = await discord<{ id: string; managed?: boolean; permissions?: string }[]>(
      `/guilds/${guildId}/roles`,
      c.env.DISCORD_BOT_TOKEN!,
    );
    if (roles.some((id) => !available.some((r) => r.id === id)))
      throw new Problem(400, 'wrong_role', 'Choose role IDs belonging to this Discord server.');
    if (rankRoles.some((id) => id === guildId || available.find((r) => r.id === id)?.managed))
      throw new Problem(
        400,
        'wrong_role',
        'Rank roles must be ordinary roles, not @everyone or managed integration roles.',
      );
    if (
      rankRoles.some(
        (id) =>
          BigInt(available.find((r) => r.id === id)?.permissions ?? '0') &
          privilegedRankPermissions,
      )
    )
      throw new Problem(
        400,
        'privileged_rank_role',
        'Rank roles cannot grant administrator, moderation or server-management permissions. Create cosmetic rank roles.',
      );
    const owners = new Map<string, string>();
    for (const g of settings.games)
      for (const r of g.records?.ranks ?? [])
        if (r.roleId) {
          if (owners.has(r.roleId) && owners.get(r.roleId) !== g.id)
            throw new Problem(
              400,
              'shared_rank_role',
              'Use separate rank roles for each game to avoid conflicting assignments.',
            );
          owners.set(r.roleId, g.id);
        }
  }
  const result = await c.env.COMMUNITIES.getByName(guildId).save(
    guildId,
    expectedRevision,
    settings,
    c.get('session').user.id,
    'save',
  );
  if (!result.ok)
    throw new Problem(
      409,
      'revision_conflict',
      ('reason' in result ? result.reason : null) ??
        'Settings changed in another tab. Refresh before saving.',
    );
  return c.json(result.snapshot);
});
app.all('/api/*', (c) =>
  c.json({ error: 'Unknown API route.', requestId: c.get('requestId') }, 404),
);
app.all('/auth/*', (c) => c.json({ error: 'Unknown authentication route.' }, 404));
app.get('*', (c) => c.env.ASSETS.fetch(c.req.raw));
export default app;
