# GitHub Actions + Telegram Setup — Daily Signal Automation

Complete guide to run `check_signal.py` automatically every day in the cloud (free),
with Telegram push alerts when a signal fires.

Total setup time: **~15 minutes** (one-time).
Ongoing cost: **$0** forever.

---

## Step 1 — Create Telegram bot (3 min)

1. Open Telegram on your phone, search for **`@BotFather`**, start chat.
2. Send `/newbot`
3. Choose a name (e.g., "My BTC Signal Bot")
4. Choose a username ending in `bot` (e.g., `paul_btc_signal_bot`)
5. BotFather gives you a **TOKEN** like `1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`
   → **Save this token**
6. Search for your new bot in Telegram, open chat, click **Start**

## Step 2 — Get your Chat ID (1 min)

1. After clicking Start on your bot, open this URL in a browser
   (replace `<TOKEN>` with the token from step 1):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
2. Look for `"chat":{"id":123456789,...}` in the response
3. **Save the chat ID number**

## Step 3 — Create GitHub account & repo (5 min)

1. Go to **github.com**, sign up if you don't have an account (free)
2. Click **New repository** (top right "+" button)
3. Repo name: `m-trade-private` (or anything)
4. Visibility: **Private** (recommended)
5. **Don't** initialize with README/gitignore (we have our own)
6. Click **Create repository**
7. Note the URL: `https://github.com/<YOUR_USERNAME>/m-trade-private.git`

## Step 4 — Push your local code to GitHub (3 min)

Open PowerShell or terminal in `D:\m-trade`:

```bash
cd D:\m-trade

# Initialize git (if not already)
git init
git branch -M main

# Add files
git add .

# First commit
git commit -m "Initial commit: BTC backtest framework + signal checker"

# Connect to GitHub repo (replace URL with yours)
git remote add origin https://github.com/<YOUR_USERNAME>/m-trade-private.git

# Push
git push -u origin main
```

If prompted for credentials, use:
- Username: your GitHub username
- Password: a **Personal Access Token** (NOT your GitHub password!)
  - Create at: github.com → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic) → check "repo" scope

## Step 5 — Add Telegram secrets to GitHub (2 min)

1. Go to your repo on github.com
2. **Settings** (top right of repo page) → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `TELEGRAM_BOT_TOKEN`
   Value: paste the token from Step 1
   → Click **Add secret**
5. Click **New repository secret** again
6. Name: `TELEGRAM_CHAT_ID`
   Value: paste the chat ID from Step 2
   → Click **Add secret**

## Step 6 — Enable Actions & test (1 min)

1. Go to **Actions** tab in your repo
2. If actions are disabled, click **I understand my workflows, go ahead and enable them**
3. You'll see "Daily BTC Signal Check (D-Alt-Med)" workflow listed
4. Click on it → **Run workflow** (right side) → **Run workflow** (green button)
5. Wait ~1-2 minutes for it to complete
6. Click on the running job → **check-signal** → expand "Run signal check" step
7. You should see the script output. If a signal fires, you'll get Telegram message.

If no error and no Telegram message → ALL OK, just no signal today. The cron will run daily at 00:30 UTC automatically.

---

## How it works

- **00:30 UTC daily**: GitHub spins up an Ubuntu runner in the cloud
- Runs your `check_signal.py` with your Telegram credentials
- Fetches latest BTC data from Binance
- Checks D-Alt-Med (40/15) entry/exit conditions
- **Only sends Telegram message if signal fires** (entry or exit)
- Runner shuts down after ~1-2 minutes

**You get notification on phone only on signal days (~3-4 times/year).**
Other days: complete silence.

---

## Troubleshooting

### "Resource not accessible by integration"
→ Repo is in an organization with restricted Actions. Make repo private under your personal account.

### Workflow doesn't trigger on schedule
→ GitHub Actions schedules can be delayed up to 15 minutes (free tier). Normal.
→ For repos with no commits in 60 days, schedules pause. Solution: push any tiny change once a month, or use `workflow_dispatch` to manually trigger.

### Telegram message not received
→ Check secrets are EXACTLY named `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (case-sensitive).
→ Make sure you sent `/start` to the bot first — bots can't message users who haven't started a conversation.
→ Check Actions run log for "[telegram]" lines.

### "no module named 'config'"
→ Check that all .py files are in the repo. Run `git status` in D:\m-trade and `git add` any missing files.

---

## Customization

- **Change schedule**: edit `.github/workflows/daily-signal.yml`, line `cron: '30 0 * * *'`
  - `0 12 * * *` = noon UTC
  - Note: cron times in GitHub Actions are UTC

- **Switch to D-Primary or D-Alt-Short**: edit `check_signal.py` lines 26-27:
  ```python
  N_ENTRY = 40   # change to 55 for Primary, 20 for Alt-Short
  N_EXIT = 15    # change to 20 for Primary, 10 for Alt-Short
  ```
  Then `git commit -am "switch to D-Primary"` and `git push`.

- **Test manually anytime**: Actions tab → Run workflow.

---

## Cost & usage

- GitHub Actions free tier: 2000 min/month for private repos
- Our usage: ~2 min/day × 30 = ~60 min/month
- **3% of free quota used.** $0 forever.
