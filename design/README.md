# Dashboard design handoff

`nammaoe2bot-dashboard-design.zip` is the design package for the web
dashboard's admin area: the "Add to Discord" setup wizard, the server
overview, channel and queue settings, and the admin tool pages (daily quiz,
access & compute, diagnostics, seed ratings, link AoE2 profiles, import match
history), for desktop, tablet and phone.

```bash
unzip design/nammaoe2bot-dashboard-design.zip -d /tmp/design
open /tmp/design/nammaoe2bot-dashboard-design/designs/prototype.html     # clickable prototype
open /tmp/design/nammaoe2bot-dashboard-design/designs/mobile-board.html  # every screen in phone frames
```

Inside:

- `README.md`: start here
- `screens/`: 24 desktop renders, 8 quick phone renders, and the mobile
  design set in `screens/mobile-design/` (27 phone states at 390px, key
  screens at 360 and 430px, tablet at 768px)
- `designs/prototype.html`: a clickable prototype with a fake API
- `designs/mobile-board.html`: every screen running live in phone frames
- `designs/tokens.css`: the design tokens
- `designs/reference/`: the implemented page, plus the server, settings and test diff
- `guides/01–07`: principles, design system, screens and flows, implementation, **backend notes**, QA checklist, **mobile and tablet**
- `tools/`: scripts that rebuild the prototype and screens

The design is already implemented on this branch's history (from branch
`claude/sharp-hopper-i8o8vt`, based on `productionize`). Backend follow-ups
(invite permissions, the unused `prefix` and other dead settings, the
description review, the version label) are in
`guides/05-backend-notes.md`.
