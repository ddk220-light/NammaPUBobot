# Dota streak announcer clips

Classic Dota 2 announcer audio, owned by its respective rights holders (Valve;
these assets are not relicensed under this repository's GPL license).

Downloaded from [MoNoLidThZ/DotaKillStreak](https://github.com/MoNoLidThZ/DotaKillStreak/tree/97686e4cb69313cabc0996446c7529a30c38e3f7/sound/dota_killing_spree)
at the pinned commit `97686e4cb69313cabc0996446c7529a30c38e3f7`.
`manifest.json` records each original URL and SHA-256, prepared-file SHA-256,
duration and size. The original filenames distinguish the eight streak calls
from double/triple/ultra kills and rampage; those multi-kill clips are not used.

Each file was prepared offline with FFmpeg using:

```sh
ffmpeg -i ORIGINAL.mp3 -map_metadata -1 -af volume=0.7 \
  -c:a libopus -ar 48000 -ac 2 -frame_duration 20 -b:a 64k STREAK.ogg
```

The bot reads the prepared 20 ms Opus packets directly. No FFmpeg, speech
generation, remote downloads, encoder or soundboard provisioning runs on Railway.
The `10.ogg` clip says "Holy Shit!", the classic audio for Beyond Godlike, and
is also used for streaks above ten.
