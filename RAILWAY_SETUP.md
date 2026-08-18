# Railway Deployment Setup

## 1. Create a Railway Project

1. Go to [railway.app](https://railway.app) and sign in
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select the **NammaPUBobot** repository

## 2. Add a MySQL Database

1. In your project dashboard, click **"+ New"** (top right of the canvas)
2. Select **"Database"** → **"MySQL"**
3. Railway will spin up a MySQL instance automatically
4. The connection variables (`MYSQLHOST`, `MYSQLPORT`, `MYSQLUSER`, `MYSQLPASSWORD`, `MYSQLDATABASE`) are auto-provided — `start.py` picks them up automatically

## 3. Link the Database to Your Bot Service

1. Click on your **bot service** (the one deployed from GitHub)
2. Go to the **"Variables"** tab
3. Click **"Add Reference Variable"** to link the MySQL variables from the database service
   - Railway may do this automatically if the services are connected on the canvas (drag a line between them)

## 4. Set Environment Variables

In the **bot service** → **"Variables"** tab, add:

| Variable | Value |
|----------|-------|
| `DC_BOT_TOKEN` | Your full Discord bot token |
| `DC_CLIENT_ID` | `1479682589374808166` |
| `DC_OWNER_ID` | `622810653878648873` |
| `PUBOBOT_USER_ID` | Discord user ID of the original Pubobot whose rating messages are synced |
| `LOBBYBOT_USER_ID` | Discord user ID of AOE2LobbyBOT, used by the legacy civ-sync fallback |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DC_CLIENT_SECRET` | `""` | OAuth client secret (for web interface) |
| `DC_INVITE_LINK` | `""` | Bot invite URL |
| `DC_SLASH_SERVERS` | `""` | Comma-separated server IDs for slash commands |
| `LOG_LEVEL` | `INFO` | `CHAT`, `DEBUG`, `COMMANDS`, `INFO`, or `ERRORS` |
| `STATUS` | `NammaAoe2Bot` | Bot presence/status text |
| `DEPLOYMENT_MODE` | `self_hosted` | `hosted` hard-disables replay compute; `self_hosted` permits the local pipeline |
| `REPLAY_INGEST_ENABLED` | `True` | Operator replay switch; effective only in `self_hosted` mode and for communities that opt in |
| `FLAGSHIP_GUILD_IDS` | `""` | Comma-separated guild IDs that retain full replay detail |
| `WS_ROOT_URL` | `""` | HTTPS public dashboard base URL, required for OAuth and lobby Join/Spectate buttons |

For the admin dashboard, create an OAuth redirect in the Discord Developer
Portal at `<WS_ROOT_URL>/auth/callback`, then set `DC_CLIENT_SECRET` and the
same `WS_ROOT_URL` on Railway. Do not include a trailing callback path in
`WS_ROOT_URL` itself.

## 5. Deploy

Once the database and environment variables are configured, Railway will auto-deploy on each push to the connected branch. You can also trigger a manual deploy from the dashboard.

## Getting Your Discord Bot Token

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application → **Bot** tab
3. Click **"Reset Token"** to generate a new token
4. Copy the token and set it as `DC_BOT_TOKEN` in Railway

## Getting Your Discord User ID

1. Open Discord → **Settings** → **Advanced** → Enable **Developer Mode**
2. Right-click your username → **Copy User ID**
3. Set it as `DC_OWNER_ID` in Railway
