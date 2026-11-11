import { describe, expect, it, vi } from 'vitest';
import { commands, commandsFor } from '../src/discord/commands.mjs';
// Pure helpers: importing these scripts never reads credentials or calls Discord.
import {
  main,
  parseRegisterArgs,
  printedBody,
  registrationPlan,
} from '../scripts/register-commands.mjs';
import { checkApplicationName, envArg, PLACEHOLDER_ORIGIN, usableOrigin } from '../scripts/env.mjs';
import { testInteraction } from '../src/discord/test-interactions';
import type { Interaction } from '../src/discord/interactions';
import type { Env } from '../src/env';

const app = '100000000000000001',
  server = '100000000000000002',
  other = '100000000000000003';

describe('per-environment command registration', () => {
  it('omits /test from production and keeps it manager-only elsewhere', () => {
    const production = commandsFor('production').map((c) => c.name);
    expect(production).not.toContain('test');
    expect(commandsFor('staging').map((c) => c.name)).toEqual(['test', ...production]);
    expect(commandsFor('local')).toEqual(commandsFor('staging'));
    expect(commandsFor('staging')[0].default_member_permissions).toBe('32');
    expect(commands).toEqual(commandsFor('staging'));
    expect(() => commandsFor('prod')).toThrow(/Unknown app environment/);
  });
  it('limits every top-level command to guild installs and guild channels', () => {
    for (const env of ['staging', 'production'])
      for (const c of commandsFor(env)) {
        expect(c.contexts, c.name).toEqual([0]);
        expect(c.integration_types, c.name).toEqual([0]);
        expect(c).not.toHaveProperty('dm_permission');
      }
  });
  it('registers globally by default, per server on request, and clears a server last', () => {
    expect(registrationPlan({ env: 'production', applicationId: app })).toEqual([
      { scope: 'global', path: `/applications/${app}/commands`, body: commandsFor('production') },
    ]);
    const [guild, cleared] = registrationPlan({
      env: 'staging',
      applicationId: app,
      guild: server,
      clearGuild: other,
    });
    expect(guild).toMatchObject({ path: `/applications/${app}/guilds/${server}/commands` });
    expect(guild.body).toEqual(commandsFor('staging'));
    expect(cleared).toMatchObject({
      path: `/applications/${app}/guilds/${other}/commands`,
      body: [],
    });
  });
  it('prints exactly the body a registration sends, without credentials or network', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const write = vi.spyOn(process.stdout, 'write').mockImplementation(() => true);
    try {
      for (const env of ['staging', 'production']) {
        write.mockClear();
        await main(['--env', env, '--print']);
        expect(write).toHaveBeenCalledWith(printedBody(env));
        expect(JSON.parse(printedBody(env))).toEqual(commandsFor(env));
        const [global] = registrationPlan({ env, applicationId: app });
        expect(`${JSON.stringify(global.body)}\n`).toBe(printedBody(env));
      }
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      write.mockRestore();
      fetchSpy.mockRestore();
    }
  });
  it('parses explicit arguments and rejects ambiguous ones', () => {
    expect(parseRegisterArgs(['--env=production', '--clear-guild', server, '--print'])).toEqual({
      env: 'production',
      guild: null,
      clearGuild: server,
      print: true,
    });
    expect(() => parseRegisterArgs(['--print'])).toThrow(/--env staging\|production/);
    expect(() => parseRegisterArgs(['--env', 'staging', '--guild', 'abc'])).toThrow(/server ID/);
    expect(() =>
      parseRegisterArgs(['--env', 'staging', '--guild', server, '--clear-guild', server]),
    ).toThrow(/same server/);
    expect(() => parseRegisterArgs(['--env', 'staging', '--global'])).toThrow(/Unknown argument/);
  });
});

describe('environment safeguards', () => {
  it('requires Staging in the staging application name and forbids it in production', () => {
    expect(() => checkApplicationName('staging', 'Community Games Staging')).not.toThrow();
    expect(() => checkApplicationName('staging', 'Community Games')).toThrow(/Staging/);
    expect(() => checkApplicationName('production', 'Community Games')).not.toThrow();
    expect(() => checkApplicationName('production', 'Community Games Staging')).toThrow(
      /production needs its own application/,
    );
  });
  it('names the environment explicitly and refuses the placeholder origin', () => {
    expect(envArg(['--env', 'production'])).toBe('production');
    expect(envArg([], 'staging')).toBe('staging');
    expect(() => envArg([])).toThrow(/--env/);
    expect(() => envArg(['--env', 'local'])).toThrow(/--env/);
    expect(usableOrigin(PLACEHOLDER_ORIGIN)).toBe(false);
    expect(usableOrigin('http://example.com')).toBe(false);
    expect(usableOrigin('https://games.example.com')).toBe(true);
  });
});

describe('/test in production', () => {
  it('refuses privately without reaching community storage', async () => {
    const getByName = vi.fn();
    const env = { APP_ENV: 'production', COMMUNITIES: { getByName } } as unknown as Env;
    const interaction = {
      id: '100000000000000010',
      type: 2,
      token: 'token',
      guild_id: server,
      channel_id: '100000000000000011',
      member: { user: { id: '100000000000000012', username: 'a' }, permissions: '32', roles: [] },
      data: { name: 'test', options: [{ name: 'populate', type: 1, options: [] }] },
    } as unknown as Interaction;
    const reply = await testInteraction(interaction, env, { waitUntil: vi.fn() }).json();
    expect(reply).toMatchObject({
      type: 4,
      data: { content: expect.stringContaining('not available'), flags: 64 },
    });
    expect(getByName).not.toHaveBeenCalled();
  });
});
