import type { Env } from '../env';
import { page, htmlEscape as e } from '../public-site';
export async function publicPages(request: Request, env: Env): Promise<Response> {
  const launched = env.APP_ENV === 'production' && env.PUBLIC_SITE_INDEXING === 'true';
  const u = new URL(request.url),
    origin = new URL(env.APP_ORIGIN).origin;
  const response = async (
    body: string,
    type = 'text/html; charset=utf-8',
    status = 200,
    index = false,
  ) => {
    const hashes = await Promise.all(
      [...body.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map(
        async (m) => {
          const bytes = new Uint8Array(
            await crypto.subtle.digest('SHA-256', new TextEncoder().encode(m[1])),
          );
          return `'sha256-${btoa(String.fromCharCode(...bytes))}'`;
        },
      ),
    );
    return new Response(body, {
      status,
      headers: {
        'Content-Type': type,
        'Cache-Control': 'no-store',
        'X-Robots-Tag': index && launched ? 'index, follow' : 'noindex',
        'Content-Security-Policy': `default-src 'self'; script-src 'self' ${hashes.join(' ')}; style-src 'self'; img-src 'self' https://cdn.discordapp.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`,
      },
    });
  };
  if (
    launched &&
    u.origin !== origin &&
    !/^\/(api|auth|statistics|ops|connectors|interactions|health)(\/|$)/.test(u.pathname)
  )
    return Response.redirect(new URL(u.pathname + u.search, origin).href, 308);
  if (u.pathname === '/sitemap-pages.xml' && !launched)
    return response(
      '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
      'application/xml',
    );
  const asset = (path: string) =>
    env.ASSETS.fetch(new Request(new URL(path, request.url), request));
  if (
    u.pathname === '/' &&
    ['community', 'view', 'guide', 'queue'].some((k) => u.searchParams.has(k))
  ) {
    u.pathname = '/app/';
    return Response.redirect(u.href, 302);
  }
  if (
    u.pathname === '/app' ||
    u.pathname === '/app/' ||
    u.pathname.startsWith('/app/') ||
    ['/stats', '/link'].includes(u.pathname)
  ) {
    const a = await asset('/app-shell.txt');
    return response(await a.text());
  }
  if (u.pathname.startsWith('/c/')) {
    const parts = u.pathname.split('/').filter(Boolean);
    const entry =
      parts.length === 3
        ? await env.PUBLIC_DIRECTORY?.getByName('directory').resolve(parts[1])
        : null;
    const gameRef = entry?.games.find((g) => g.slug === parts[2]);
    if (entry && gameRef) {
      const stub = env.COMMUNITIES.getByName(entry.guild),
        snapshot = await stub.read(entry.guild);
      const game = snapshot.settings.games.find((g) => g.id === gameRef.id);
      if (
        game?.records?.visibility === 'public' &&
        game.records.searchPublishing &&
        snapshot.settings.publicPage?.displayName
      ) {
        const pageIndex = Number(u.searchParams.get('page') ?? 0);
        if (!Number.isInteger(pageIndex) || pageIndex < 0 || pageIndex > 100000)
          return response('Invalid page', 'text/plain', 400);
        const user = u.searchParams.get('user') ?? undefined;
        if (user && !/^\d{17,20}$/.test(user)) return response('Invalid player', 'text/plain', 400);
        const stats = await stub.statistics(entry.guild, game.id, user, pageIndex, false);
        const updated = Math.max(0, ...stats.leaderboard.map((p) => p.lastRankedAt ?? 0));
        const canonical = `${origin}/c/${entry.community}/${gameRef.slug}/`;
        return response(
          page(
            `${snapshot.settings.publicPage.displayName} ${game.name} leaderboard`,
            'Community ratings and confirmed match records.',
            `<div class="table-wrap"><table><caption>${e(game.name)} · ${stats.total} eligible players</caption><thead><tr><th>Player</th><th>Rank</th><th>Rating ± uncertainty</th><th>Wins</th><th>Losses</th><th>Draws</th></tr></thead><tbody>${stats.leaderboard.map((p) => `<tr><th scope="row"><a href="?user=${e(p.id)}">${e(p.name)}</a></th><td>${e(p.rank ?? 'Unranked')}</td><td>${p.rating} ± ${p.deviation}</td><td>${p.wins}</td><td>${p.losses}</td><td>${p.draws}</td></tr>`).join('')}</tbody></table></div><p>${updated ? `Latest ranked activity on this page: ${e(new Date(updated).toISOString())}` : 'No ranked activity in this page.'}</p><nav aria-label="Leaderboard pages">${pageIndex > 0 ? `<a href="?page=${pageIndex - 1}">Previous</a>` : ''}${pageIndex + 1 < stats.pages ? `<a href="?page=${pageIndex + 1}">Next</a>` : ''}</nav>${stats.player ? `<section><h2>${e(stats.player.name)}</h2><nav aria-label="Player sections"><a href="#overview">Overview</a><a href="#matches">Matches</a><a href="#rating-history">Rating history</a><a href="#details">Details</a></nav><section id="overview"><h3>Overview</h3><p>${stats.player.rating} ± ${stats.player.deviation} · ${stats.player.wins} wins / ${stats.player.losses} losses</p></section><section id="matches"><h3>Matches</h3><p>Recent records only; history can be incomplete.</p>${stats.player.recent.map((m) => `<article><h4>${e(m.queue)}</h4><p>${e(new Date(m.at).toISOString())} · ${e(m.state)} · ${e(m.result ?? 'No confirmed result')}</p><p>${m.teams.map((t) => t.map((p) => e(p.name)).join(', ')).join(' vs ')}</p></article>`).join('')}</section><section id="rating-history"><h3>Rating history</h3><p>Recent recorded changes; inferred or imported records may be unverified.</p>${ratingChart(stats.player.history)}<table><caption>Recent rating changes</caption><thead><tr><th>Date</th><th>Before</th><th>After</th></tr></thead><tbody>${stats.player.history.map((h) => `<tr><td>${e(new Date(h.changed_at).toISOString())}</td><td>${h.before}</td><td>${h.after}</td></tr>`).join('')}</tbody></table></section><section id="details"><h3>Details</h3><p>Source: ${e(stats.player.source)}. Community records are separate from official ladders.</p></section></section>` : ''}<p>Minimum matches: ${stats.policy.minimumMatches}. Recent activity window: ${stats.policy.recentDays || 'unlimited'} days. Local community ratings are separate from official ladders.</p>`,
            canonical,
            launched && u.search === '',
          ),
          'text/html; charset=utf-8',
          200,
          launched && u.search === '',
        );
      }
    }
    return response(
      page(
        'Leaderboard unavailable',
        'This leaderboard is not published.',
        '<p><a href="/games/">Explore games</a></p>',
        origin + u.pathname,
      ),
      'text/html; charset=utf-8',
      404,
    );
  }
  if (u.pathname === '/sitemap.xml' || u.pathname === '/sitemap-leaderboards.xml') {
    if (u.pathname === '/sitemap.xml')
      return response(
        `<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>${e(origin)}/sitemap-pages.xml</loc></sitemap><sitemap><loc>${e(origin)}/sitemap-leaderboards.xml</loc></sitemap></sitemapindex>`,
        'application/xml',
      );
    const urls: string[] = [];
    if (launched)
      for (const entry of (await env.PUBLIC_DIRECTORY?.getByName('directory').entries()) ?? []) {
        const snapshot = await env.COMMUNITIES.getByName(entry.guild).read(entry.guild);
        for (const g of entry.games)
          if (
            snapshot.settings.publicPage?.displayName &&
            snapshot.settings.games.some(
              (v) =>
                v.id === g.id && v.records?.visibility === 'public' && v.records.searchPublishing,
            )
          )
            urls.push(`${origin}/c/${entry.community}/${g.slug}/`);
      }
    return response(
      `<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls.map((url) => `<url><loc>${e(url)}</loc></url>`).join('')}</urlset>`,
      'application/xml',
    );
  }
  if (u.pathname === '/robots.txt')
    return response(`User-agent: *\nAllow: /\nSitemap: ${origin}/sitemap.xml\n`, 'text/plain');
  if (['/site-manifest.json', '/app-shell.txt'].includes(u.pathname))
    return response('Not found', 'text/plain', 404);
  const a = await env.ASSETS.fetch(request);
  if (a.status >= 300 && a.status < 400) return a;
  const type = a.headers.get('Content-Type') ?? '';
  if (type.includes('text/html') || type.includes('xml')) {
    let body = (await a.text()).replaceAll('https://example.invalid', origin);
    if (!launched) body = body.replaceAll('content="index,follow"', 'content="noindex"');
    const indexable = launched && body.includes('content="index,follow"');
    return response(body, type, a.status, indexable);
  }
  return a;
}

function ratingChart(history: { before: number; after: number }[]) {
  if (!history.length) return '<p>No recent rating changes.</p>';
  const values = [...history].reverse().map((h) => h.after),
    lo = Math.min(...values),
    hi = Math.max(...values);
  const points = values
    .map(
      (v, i) =>
        `${20 + (i * 560) / Math.max(1, values.length - 1)},${180 - ((v - lo) * 150) / Math.max(1, hi - lo)}`,
    )
    .join(' ');
  return `<svg class="rating-chart" viewBox="0 0 600 210" role="img" aria-label="Recent ratings from ${values[0]} to ${values.at(-1)}. Exact values are in the table below."><polyline points="${points}" /></svg>`;
}
