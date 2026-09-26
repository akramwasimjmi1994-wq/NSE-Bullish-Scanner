# NSE Bullish Scanner

Windows desktop NSE scanner.

The installed application checks this repository's `update.json` for new versions:

https://raw.githubusercontent.com/akramwasimjmi1994-wq/NSE-Bullish-Scanner/main/update.json

Release assets are published under GitHub Releases. The installed updater downloads the ZIP, verifies SHA-256 when supplied, replaces the EXE and restarts the application.

For each release:
1. Build `NSE_Bullish_Scanner.exe` and `NSE_Bullish_Scanner_Updater.exe`.
2. Create `NSE_Bullish_Scanner_Update.zip` containing both EXEs.
3. Publish it as a GitHub Release asset.
4. Update `update.json` with the new version, release asset URL and SHA-256.
