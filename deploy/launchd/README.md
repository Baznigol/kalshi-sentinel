# launchd (Mac) service templates

These plists are templates for running Kalshi Sentinel continuously with auto-restart.

## Install

```bash
mkdir -p ~/Library/LaunchAgents
cp deploy/launchd/com.kalshi-sentinel.*.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.kalshi-sentinel.backend.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.kalshi-sentinel.autotrader.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.kalshi-sentinel.backend.plist
launchctl load ~/Library/LaunchAgents/com.kalshi-sentinel.autotrader.plist
```

## Logs

```bash
tail -f data/launchd-backend.out.log
tail -f data/launchd-autotrader.out.log
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.kalshi-sentinel.backend.plist
launchctl unload ~/Library/LaunchAgents/com.kalshi-sentinel.autotrader.plist
rm ~/Library/LaunchAgents/com.kalshi-sentinel.backend.plist
rm ~/Library/LaunchAgents/com.kalshi-sentinel.autotrader.plist
```

Note: these assume your repo is at `/Users/richard/.openclaw/workspace/kalshi-sentinel`.
