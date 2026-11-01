# withBTS.kz news agent

Every 3 hours this agent checks official BTS accounts and your trusted fanbases, writes the news in Kazakh,
designs an Instagram card, adds credits, and publishes to Telegram, Instagram, Threads and X.

How one run works:
1. `collect.py` reads the sources in `config/sources.yaml` and saves new items
2. Claude Code reads `CLAUDE.md` and writes posts in Kazakh into `queue/`
3. `render_card.py` designs the 1080x1350 card
4. `publish.py` checks every post (no credits means no publishing), adds credits and hashtags, and posts
5. Everything is logged in `state/`

## Setup, step by step

### Step 1. GitHub account and repository
1. Create a free account at github.com
2. Click New repository, name it `withbts-news-agent`, choose **Public**, create it
   (Public is needed so Instagram and Threads can load the card images. Your keys stay secret in GitHub Secrets, nobody can see them. Public repos also get free unlimited Actions minutes.)
3. Click "uploading an existing file", drag in all files from this folder, then Commit changes
   (The `.github` folder is hidden on Mac. Press Cmd + Shift + . in Finder to see it, it must be uploaded too.)

### Step 2. Connect your Claude Pro subscription
The agent runs on your Claude Pro plan, no separate API billing.
1. Install Claude Code on your computer (instructions: code.claude.com/docs, Quickstart)
2. Open Terminal (Mac) or PowerShell (Windows) and run: `claude setup-token`
3. A browser window opens, log in with your Claude account and approve
4. Copy the long token it shows
5. In GitHub: your repo, Settings, Secrets and variables, Actions, New repository secret
   Name: `CLAUDE_CODE_OAUTH_TOKEN`, Value: the token

Good to know: agent runs use your Pro usage limits, the same limits as your own chats with Claude. A few short posts every 3 hours is light use, but if you hit your limit one day, that run simply fails and the next one continues.

### Step 3. Telegram (start here)
1. In Telegram open @BotFather, send /newbot, follow the steps, copy the token
2. Add the bot to the withBTS.kz channel as administrator with permission to post
3. Add secrets: `TELEGRAM_BOT_TOKEN` = the token, `TELEGRAM_CHANNEL_ID` = `@your_channel_username`

### Step 4. Test run (nothing is published)
`config/settings.yaml` starts with `dry_run: true`.
1. Repo, Actions tab, enable workflows if asked
2. Click "withBTS.kz news agent", Run workflow
3. After it finishes, open `state/dry_run/` to read the posts it would publish, and `media/` to see the cards
4. Happy with the result? Edit `config/settings.yaml`, set `dry_run: false`, commit. From now on it publishes on its own.

### Step 5. Instagram and Threads (Meta)
1. Instagram account must be Professional (Business or Creator) and linked to a Facebook Page
2. developers.facebook.com, create an app, add the Instagram product (content publishing permission) and the Threads product
3. Generate long-lived access tokens for Instagram and Threads
4. Add secrets: `IG_ACCESS_TOKEN`, `IG_USER_ID`, `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID`
5. In `config/settings.yaml` turn on `instagram: true` and `threads: true`

Important: Meta long-lived tokens expire after about 60 days. Put a reminder every 50 days to generate new ones and update the secrets. When a token expires, the log shows "FAILED ... on instagram".

The Instagram token is also what lets the agent read official and fanbase Instagram accounts (they must be public Business or Creator accounts).

### Step 6. X
1. developer.x.com, create a project and app with Read and Write permission
2. Add secrets: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`
3. Turn on `x: true` in settings
X posts are text only for now. Reading X accounts as a source needs a paid X plan, so it is off in `sources.yaml`. Check current X API prices before paying, they change often.

### Step 7. Sources
All the accounts you gave are already in `config/sources.yaml`: BTS_twt, bts_bighit, HYBEOFFICIALtwt, BTS_Trans, btschartsxdaily on X, bts.bighitofficial and the 7 member accounts on Instagram, BANGTANTV and HYBE LABELS on YouTube.
1. YouTube works from day one, no keys needed
2. Instagram sources start working after Step 5 (same token)
3. X sources need the paid X API, set `enabled: true` under `x:` once you have `X_BEARER_TOKEN`
4. TikTok and Weverse have no public reading API, their news comes in through the accounts above
Add more fanbases any time with `tier: trusted_fanbase`.

## Everyday control

| What | How |
|---|---|
| Stop everything now | `config/settings.yaml`, set `enabled: false`, commit |
| Fix a Kazakh word for the future | add it to `config/glossary.md` |
| Change colours or logo | `design:` in `config/settings.yaml`, logos are in `templates/` |
| Change how often it runs | the `cron` line in `.github/workflows/news-agent.yml` |
| See what was posted | `state/posted.json` |
| See what was skipped and why | `state/skipped_log.json` |
| See rejected posts | `state/rejected/` |
| Run now | Actions tab, Run workflow |

## Good to know
1. GitHub pauses scheduled workflows in public repos after 60 days without activity. The agent commits after every run, which should count as activity, but if GitHub ever emails you that the workflow was disabled, open the Actions tab and enable it again.
2. Weverse has no public API, so Weverse news arrives through official Instagram and the fanbases that repost it.
3. If many news items come at once, only `max_posts_per_run` are posted, the rest are set aside in `state/rejected/` so old news is never posted late.
