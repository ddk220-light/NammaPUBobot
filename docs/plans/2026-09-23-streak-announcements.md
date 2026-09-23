# Current win-streak leaders and Dota audio

## Approved behavior

At the existing teams-posted announcement, show the highest current ranked win
streak in each team, including all tied players. Use the current streak already
loaded with player ratings, rather than truncating it to the banter's 90-day
history window. Losses and draws follow the existing rating streak rules.

Play one classic Dota clip for the unique highest-streak player across the
entire match: 3 Killing Spree, 4 Dominating, 5 Mega Kill, 6 Unstoppable,
7 Wicked Sick, 8 Monster Kill, 9 Godlike, 10+ Holy Shit (Beyond Godlike).
No audio below three wins or when the top streak is tied, including ties within
one team. The text still appears. Play in the server's most populated permitted
normal voice channel, counting people rather than bots. One person is enough;
participants need not share a channel. Break channel population ties by channel ID.

## Implementation

1. Download the eight named clips from the public DotaKillStreak asset collection,
   pin the source commit and record source URLs/checksums. Convert once locally
   to stereo 48 kHz Opus with 20 ms packets; commit only the small prepared files.
   Playback reads Opus packets directly: no runtime FFmpeg, transcoding,
   soundboard slots, remote audio fetching, TTS, or additional Railway service.
2. Upgrade nextcord 2.6 to 3.2 with voice dependencies and Python 3.11 to 3.12.
   Discord now requires DAVE voice encryption. Check the real runtime and slash
   registration/component payloads in addition to the stubbed unit suite.
3. Preserve streak values from existing rating reads at match creation and
   roster refresh, and in the existing active-match JSON. Add the guaranteed
   summary to Tale of the Tape even if no other storyline qualifies. Keep the
   existing seeded story selection and payoff context intact.
4. Start a bounded background audio task only after a successful text post.
   Allow at most one voice task per guild, no queue or delayed replay, and one
   attempt per match. Restored matches never trigger audio. Recheck roster,
   voice occupancy and permissions after connecting, then disconnect in
   all success/failure/cancellation paths. Never interrupt another voice session.

## Risk review and validation

- The library/runtime upgrade affects all Discord interactions. Run the full
  Python 3.12 suite, import/architecture checks, actual nextcord component and
  slash-payload checks, Docker build and existing MySQL integration checks.
- Ties must consider individual players across both teams, not just the two
  team maxima. Test same-team ties, cross-team ties, all thresholds and 10+.
- Voice failure must not delay check-in, match progression or betting. Test
  connect/play errors, empty channels and population selection, missing permissions,
  concurrent matches, roster/channel changes while connecting, duplicates and cancellation.
- Streak snapshots must survive restore and check-in substitutions without any
  additional database query. Test the live rating-to-announcement path and
  compatibility with old saved matches.
- Validate every bundled clip's source, size, duration, Opus packet framing and
  decoder compatibility. Keep all download and conversion work out of production.
- Voice is used only for the few seconds needed for a clip. This adds a small
  amount of network/CPU work; no permanently connected bot or new paid service.
  Actual automatic playback requires an occupied voice channel; readiness
  checks alone cannot prove an audible end-to-end result.

## Local verification

- Python 3.12: 2,225 tests passed; 17 real-MySQL tests deferred to CI.
- Ruff and whitespace checks passed.
- The real nextcord 3.2 runtime built all 45 slash-command payloads and the
  existing civ-picker controls. Voice-state intents, DAVE/PyNaCl availability,
  all eight asset checksums and 20 ms packet framing passed offline validation.
- The prepared clips total 183,064 bytes. Live audible playback remains to be
  observed after deployment during a qualifying match in an occupied channel.
