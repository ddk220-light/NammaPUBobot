# NammaPUBobot

**NammaPUBobot** is a fork of [nammaoe2bot.__main__](https://github.com/Leshaka/nammaoe2bot.__main__) built
for organising Age of Empires II pickup games. Everything upstream does — queues,
rating matches, drafts, rank roles — still works; the sections below the fold are
upstream's own README and remain accurate for setup, credits and licensing.

### What this fork adds

- **Replay ingest and scouting reports** — matches are linked to their recorded
  games, so `/rank` reports what a player actually does: opening tendencies, the
  units they mass, eAPM, and who they beat and lose to.
- **Gold, betting and a daily quiz** — play-money betting on live matches
  (pari-mutuel, 10-minute window, players may back only their own team) and a
  public daily AoE2 poll that pays gold for taking part. Full rules in
  [docs/GOLD.md](docs/GOLD.md).
- **Civ statistics and balanced random draws**, and a public web dashboard with
  leaderboards, player pages, civ stats and play-style breakdowns.
- **A much smaller command surface than upstream.** 44 commands, 14 of them
  player-facing; the admin groups declare `default_member_permissions` so
  Discord hides them from everyone else. See
  [COMMANDS.md](COMMANDS.md) and the reasoning in
  [docs/superpowers/specs/2026-08-06-command-consolidation.md](docs/superpowers/specs/2026-08-06-command-consolidation.md).

For the complete command list see [COMMANDS.md](COMMANDS.md). Contributor and
architecture notes live in [CLAUDE.md](CLAUDE.md). This fork deploys to Railway
(see [RAILWAY_SETUP.md](RAILWAY_SETUP.md)) rather than the bare-metal setup
described below.

---

# nammaoe2bot.__main__ (upstream)
**nammaoe2bot.__main__** is a Discord bot for pickup games organisation. nammaoe2bot.__main__ have a remarkable list of features such as rating matches, rank roles, drafts, map votepolls and more!

### Some screenshots
![screenshots](https://cdn.discordapp.com/attachments/824935426228748298/836978698321395712/screenshots.png)

### Using the public bot instance
If you want to test the bot, feel free to join [**Pubobot2-dev** discord server](https://discord.gg/rjNt9nC).  
All the bot settings can be found and configured on the [Web interface](https://pubobot.leshaka.xyz/).  
For the complete list of commands see [COMMANDS.md](https://github.com/Leshaka/nammaoe2bot.__main__/blob/main/COMMANDS.md).  
You can invite the bot to your discord server from the [web interface](https://pubobot.leshaka.xyz/) or use the direct [invite link](https://discord.com/oauth2/authorize?client_id=177021948935667713&scope=bot).

### Support
Hosting the service for everyone is not free, not mentioning the actual time and effort to develop the project. If you enjoy the bot please subscribe on [Boosty](https://boosty.to/leshaka).

## Hosting the bot yourself

### Requirements
* **Python 3.9+** 
* **MySQL**.
* **gettext** for multilanguage support.

### Installing
* Create mysql user and database for nammaoe2bot.__main__:
* * `sudo mysql`
* * `CREATE USER 'pubobot'@'localhost' IDENTIFIED BY 'your-password';`
* * `CREATE DATABASE pubodb CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;`
* * `GRANT ALL PRIVILEGES ON pubodb.* TO 'pubobot'@'localhost';`
* Install required modules and configure nammaoe2bot.__main__:
* * `git clone https://github.com/Leshaka/nammaoe2bot.__main__`
* * `cd nammaoe2bot.__main__`
* * `pip3 install -r requirements.txt`
* * `cp config.example.cfg config.cfg`
* * `nano config.cfg` - Fill config file with your discord bot instance credentials and mysql settings and save.
* * Optionally, if you want to use other languages, run script to compile translations: `./compile_locales.sh`.
* * `python3 nammaoe2bot/__main__.py` - If everything is installed correctly the bot should launch without any errors and give you CLI.

## Credits
Developer: **Leshaka**. Contact: leshkajm@ya.ru.  
Used libraries: [discord.py](https://github.com/Rapptz/discord.py), [aiomysql](https://github.com/aio-libs/aiomysql), [emoji](https://github.com/carpedm20/emoji/), [glicko2](https://github.com/deepy/glicko2), [TrueSkill](https://trueskill.org/).

## License
Copyright (C) 2020 **Leshaka**.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 3 as published by the Free Software Foundation.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

See 'GNU GPLv3.txt' for GNU General Public License.
