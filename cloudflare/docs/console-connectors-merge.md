# Responsive console and game connectors

Version 0.21.0 combines the responsive console from PR #46 with the seven-game connector framework from PR #47.

Both Setup → Games and the first-queue guide use the selectable catalog: AoE2 DE, AoE3 DE, AoE4, AoM Retold, Beyond All Reason, Warcraft III through W3Champions, and conditional Zero Hour / GeneralsOnline. Original Generals, SC2, Northgard and Tempest Rising remain available to existing configurations and historical records, but cannot be newly selected.

Connector options are edited once per game inside the existing community-wide draft. Capability switches, lobby-name discovery, queue changes and rating policy save together using the original revision. Refresh, navigation and failed saves retain raw edits. Optional connector diagnostics share the setup response’s stale-community guard. Public-game browsing uses the saved game endpoint and preserves pending settings. Match evidence appears beside the teams; public profiles retain section navigation, exact chart tables and declared/verified account labels. Member account verification uses `/link` without organizer discovery and retains HTTP error request IDs.

All existing responsive UI checks remain in CI alongside the provider contracts. Local validation passed 1,345 Worker cases, the 110-case Chromium/WebKit browser coverage (including ten new integration cases), typechecks, web and Worker builds, and eleven converter tests. Browser screenshots include the Warcraft connector on phones. The standalone connector smoke command now delegates to the maintained Playwright integration suite.

The connector framework introduces application schema 8 and Durable Object migration v5 for the provider coordinator. This supersedes the earlier UI-only release’s no-migration scope. A later deployment must follow the connector backup/acceptance guidance. This merge does not deploy the Worker or register Discord commands; the existing Cloudflare app remains on 0.20.0.

Automatic observation and mode gates remain enforced by `CONNECTOR_ACCEPTED_GAMES` and `CONNECTOR_ACCEPTED_CAPABILITIES`. Provider credentials and controlled-game/OAuth acceptance are documented in [connector acceptance](connectors/acceptance.md). Selecting a game does not grant provider access or mark its automatic observer accepted.
