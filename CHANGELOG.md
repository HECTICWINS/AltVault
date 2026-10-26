# Changelog

Every release on the [Releases](../../releases) page is built by GitHub from the tagged commit, comes with a build attestation, and is scanned by VirusTotal. See [Verify your download](README.md#verify-your-download).

## 2.1.1

- **Anti-AFK is far more reliable with several clients.** Each Roblox window is tracked on its own, so a window that misses a ping (for example mid-teleport) is retried by itself without delaying the rest. Any window that reaches 15 minutes is pinged straight away. Pings happen every 6–9 minutes.
- The jump key is held a little longer, so hidden Roblox windows, which run slowly, don't miss it.
- Missed pings are logged with the account name and the reason.
- **Security:** "Log in with browser" now only accepts the login cookie from `roblox.com` and its subdomains. Before, a lookalike domain ending in "roblox.com" (such as `evilroblox.com`) would also have been accepted.
- Releases are now built on GitHub with a signed build attestation, scanned by VirusTotal, and tested automatically.

## 2.1.0

- **Log in with browser:** add accounts by logging in normally in a throwaway browser profile. No cookie copying needed; pasting a cookie stays available as an option.
- **System tray:** closing the window keeps AltVault running in the tray, with Anti-AFK still on.
- Anti-AFK no longer flashes Roblox windows over other apps, can't freeze on a "Not responding" client, and logs every pass.
- App icon, and background errors no longer pop up over other apps.

## 2.0.0

- First release as AltVault (formerly PyRAM): encrypted accounts, Multi-Roblox, same-server joins, auto-tile, live status and built-in Anti-AFK, as a portable download built from the signed python.org runtime.
- The release zip doesn't contain the build machine's paths, username or build time.
