# Setup checklist

## Run the dashboard now

No API keys are needed to preview the control plane. It is already running in safe dry-run mode.

For a local restart:

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

Then open the dashboard at `http://localhost:5173` and API docs at `http://localhost:8000/docs`.

Start the other two processes for local autonomous queue behavior:

```bash
python -m backend.app.workers.runtime
ENABLE_SCHEDULER=true python -m backend.app.scheduler
```

## What must be added before real automation

The repository now includes the real YouTube OAuth/channel-sync adapter, Gemini structured research adapter, Edge TTS fallback, and FFmpeg rendering path. It still defaults to a safe preview. You must complete OAuth, install FFmpeg, configure a research key, and test a private upload before enabling scheduled publishing. Adding a key alone is not enough: the provider must pass the QA gates.

Do not share secrets in chat. Add them only to `/home/user/yt-automation/.env` on the machine running the app. `.env` is ignored by Git.

| Capability | Where to get it | Environment variable / storage | Status in this repo |
| --- | --- | --- | --- |
| Gemini | [Google AI Studio API keys](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` | Active for research and scripts |
| Groq / Whisper | [Groq Console](https://console.groq.com/keys) | `GROQ_API_KEY` | Reserved fallback; Whisper alignment is next |
| YouTube upload and Analytics | [Google Cloud Console](https://console.cloud.google.com/) | OAuth client JSON plus refresh token storage | Active: connect from Autopilot page |
| TTS | Edge TTS voice service | `TTS_VOICE` | Active fallback; unofficial endpoint |
| FFmpeg | System package / Docker image | `ffmpeg` executable | Active renderer |
| Telegram alerts | [@BotFather](https://t.me/BotFather) for the bot token; chat ID from the Bot API `getUpdates` response | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Notification adapter next |
| External dead-man heartbeat | [Healthchecks.io](https://healthchecks.io/) | `HEALTHCHECKS_HEARTBEAT_URL` | Heartbeat adapter next |
| Cloud database | [Neon](https://neon.tech/) or [Supabase](https://supabase.com/) | Future `DATABASE_URL` / migration config | SQLite is active now |

## Google / YouTube one-time setup

The dashboard's **Autopilot → Connect YouTube** button starts this flow once the OAuth client file exists:

1. Create a Google Cloud project.
2. Enable YouTube Data API v3, YouTube Analytics API and YouTube Reporting API. Enable Gmail API only if notification parsing is desired.
3. Configure the OAuth consent screen and publish it to **Production**. Testing-mode refresh tokens expire quickly.
4. Create an OAuth client for the owner account and authorize the channel once with upload, comment, analytics and reporting scopes.
5. Keep the client JSON and refresh token outside Git, for example under a secrets mount on the VM. Do not put them in `frontend/` or commit them.
6. Complete Google's YouTube API compliance/audit process before expecting non-private scheduled uploads.
7. Install FFmpeg locally (`sudo apt-get install ffmpeg`) or run the included Docker image, which installs FFmpeg automatically.
8. Keep `DRY_RUN=true` until a resumable, idempotent private upload has passed an end-to-end test.

## Safe activation order

1. Configure grounded research and retain source URLs in the fact sheet.
2. Configure TTS/render adapters and make FFprobe/QA tests pass.
3. Configure YouTube OAuth and test private upload only.
4. Configure Analytics snapshots and check quota usage.
5. Configure Telegram and Healthchecks alerts.
6. Run the staging channel for at least seven days.
7. Only then change `DRY_RUN=false` on the production worker, after reviewing the current YouTube policies and API quotas.

The dashboard and API do not need to be open for the scheduler or worker to continue running. They are monitoring and emergency-control surfaces only.
