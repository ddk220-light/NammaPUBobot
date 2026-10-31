# Contributing

The Cloudflare bot and dashboard live in `cloudflare/`. The public product is currently Community Games; NammaMatchBot is the internal project name.

Use Node 22.12 or newer, run `npm ci` inside `cloudflare/`, then `npm run check`. Run `npm run test:ui` after installing Playwright Chromium and WebKit. The converter tests are `python3 scripts/test-legacy-converter.py` and `python3 scripts/test-economy-converter.py`.

Open a GitHub issue with reproduction steps and expected behavior. Use labelled demo data in screenshots. Never include live tokens, backups, private player information or Discord session cookies.

Changes to setup or public pages must match released capabilities. Preserve atomic revision saves, hidden-player filtering and server authorization. Public game content lives in `cloudflare/site/`; the build checks authored coverage against the selectable catalog.

The project is GPL-3.0-only and credits PUBobot2 by Leshaka. Include appropriate third-party attribution for contributed assets.
