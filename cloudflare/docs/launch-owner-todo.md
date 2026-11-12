# Launch: what only the owner can do

These are the steps that need the owner's accounts or decisions. Everything
else on the launch roadmap is code and is tracked in pull requests. Tick an
item here when it is done.

## Now (unblocks testing)

- [ ] **Let the repository's deploy job reach Cloudflare.** Staging deploys
      run from GitHub Actions (workflow "Cloudflare foundation", run manually
      with "Deploy the checked revision to isolated staging" ticked). The job
      needs two secrets that are not set yet.
  1. Cloudflare dashboard → profile icon → My Profile → API Tokens → Create
     Token → "Edit Cloudflare Workers" → Use template → choose your account →
     Create Token. Copy the token (it is shown once).
  2. GitHub repository → Settings → Environments → `cloudflare-staging`
     (create it with exactly this name if it is missing) → Environment secrets
     → add `CLOUDFLARE_API_TOKEN` (the token) and `CLOUDFLARE_ACCOUNT_ID`
     (Cloudflare dashboard → Workers & Pages → Account ID in the right-hand
     column).
- [ ] **Turn on GitHub Issues.** Repository → Settings → General → Features →
      tick Issues. The site's "Report a problem" links already point there and
      currently lead to a 404.
- [ ] **Test in a second server.** Create a throwaway Discord server, sign in
      to the staging site with an account that owns it, and follow the setup
      guide from "Add to Discord". Then join the queue from a second account
      that has no admin rights. This is the path every new community takes and
      it has only been run in one server so far.

## Before the public launch

- [ ] **Confirm the name.** The code now uses "RTS Pickup" (one line in
      `src/identity.ts`). `rtspickup.com` and `rtspickup.gg` did not resolve
      when checked on 11 November 2026, which suggests they are unregistered;
      confirm at a registrar before buying.
- [ ] **Buy the domain and add it to Cloudflare.** Then replace the
      placeholder `APP_ORIGIN` under `env.production` in `wrangler.jsonc`.
- [ ] **Fill in who runs the service** in `src/identity.ts`:
      `operator` (your name or the group's), `contact.email` (a monitored
      address for privacy requests) and `contact.supportServer` (a Discord
      invite link that does not expire). The build refuses to publish a
      search-indexed site while any of the three is empty.
- [ ] **Read the Privacy and Terms pages** (`site/legal.mjs`) and change
      anything that does not match how you intend to run the service. They
      were written from what the code stores; they are not legal advice.
- [ ] **Create the production Discord application** in the Discord Developer
      Portal: name, icon, a one-paragraph description, and under Installation
      choose Guild Install only with scopes `bot` and `applications.commands`
      and permissions View Channels, Send Messages, Embed Links and Read
      Message History (permission number 84992). Set the Interactions
      Endpoint URL and the OAuth2 redirect to the production domain. The exact
      values are in `docs/operator-guide.md` under "Production launch
      checklist".
- [ ] **Create the GitHub environment `cloudflare-production`** (repository →
      Settings → Environments) with required reviewers set to yourself, and
      add the production secrets listed in the operator guide.
- [ ] **Set a Cloudflare budget alert.** Billing → Budget alerts, at a number
      you are comfortable with (the app costs about US$5 a month while small).
      Steps are in `docs/operations-runbook.md`.

## When convenient

- [ ] Delete the unused Workers `community-gaming-rehearsal` and
      `namma-matchbot-pr49-preview` in the Cloudflare dashboard.
- [ ] Take real Discord screenshots (queue card, check-in, teams, result) from
      a game night for the public site. The pages use drawn examples until
      then.
- [ ] Decide whether to list the bot on public bot directories once the site
      is indexed.

## Decisions already made

- Everything is free; nothing is paid or gated.
- A fresh production bot is created at launch; the staging bot stays for testing.
- Predictions and match stories stay opt-in and are labelled beta. Voice
  features, the weekly gold tax and the quiz switch are hidden for the first
  release.
- The original bot on Railway keeps running unchanged and is not part of this
  work.
