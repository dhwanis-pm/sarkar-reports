# Sarkar Reports — Setup Guide

## What this does
- `fetch_meta_ads.py` pulls performance + funnel data from both Meta ad accounts into a local DuckDB file.
- `generate_dashboard.py` reads that DuckDB file and writes `dashboard.html` — the dashboard you open in your browser.
- `daily_refresh.sh` runs both, in order, in one go.
- At 8am daily, macOS runs `daily_refresh.sh` automatically — no need to open Terminal.
- Any other time (12pm, 4pm, 9pm, whenever), you can run the same script manually to refresh on demand.

## One-time setup

### 1. Folder location
Keep everything in one folder: `~/sarkar reports` (you've already got this — `.env`, the Python scripts, etc. all live together here).

### 2. Install Python packages (already done)
```
cd ~/"sarkar reports"
pip3 install -r requirements.txt
```

### 3. Credentials (already done)
Your `.env` already has `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_IDS`, `META_ACCOUNT_LABELS`, and `SARKAR_DB_PATH` filled in.

### 4. Test the full chain manually
```
cd ~/"sarkar reports"
chmod +x daily_refresh.sh
./daily_refresh.sh
```
This runs the fetch, then rebuilds `dashboard.html`, one after the other. You should see the fetch's usual output, followed by `Dashboard written to ...`. Open `dashboard.html` afterward to confirm it picked up the refresh.

## Set up the automatic 8am run

1. Edit `com.sarkar.metaads.fetch.plist`:
   - Replace all instances of `YOUR_USERNAME` with your actual Mac username (`whoami` in Terminal if unsure)
   - Confirm the folder path matches exactly where `sarkar reports` actually lives
2. Create a logs folder:
   ```
   mkdir -p ~/"sarkar reports"/logs
   ```
3. Copy the plist into macOS's scheduling folder:
   ```
   cp "com.sarkar.metaads.fetch.plist" ~/Library/LaunchAgents/
   ```
4. Load it:
   ```
   launchctl load ~/Library/LaunchAgents/com.sarkar.metaads.fetch.plist
   ```
5. Done — every day at 8am, macOS runs `daily_refresh.sh` on its own: fetches fresh data, then rebuilds `dashboard.html`. You just open the file each morning.

**If you already loaded an older version of this plist before today**, unload it first, then load the new one:
```
launchctl unload ~/Library/LaunchAgents/com.sarkar.metaads.fetch.plist
cp com.sarkar.metaads.fetch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.sarkar.metaads.fetch.plist
```

**To check it's working:** the morning after, check `~/sarkar reports/logs/fetch.log` — you should see both the fetch output and "Dashboard written to ..." in there.

**To turn it off later:**
```
launchctl unload ~/Library/LaunchAgents/com.sarkar.metaads.fetch.plist
```

## Manual / on-demand refresh (12pm, 4pm, 9pm, or anytime)

```
cd ~/"sarkar reports"
./daily_refresh.sh
```
Safe to run as many times a day as you like — each run just overwrites that day's numbers and rebuilds the dashboard with the latest data.
