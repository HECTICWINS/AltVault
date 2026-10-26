# AltVault

[![Tests](https://github.com/HECTICWINS/AltVault/actions/workflows/ci.yml/badge.svg)](https://github.com/HECTICWINS/AltVault/actions/workflows/ci.yml)
[![Release build](https://github.com/HECTICWINS/AltVault/actions/workflows/release.yml/badge.svg)](https://github.com/HECTICWINS/AltVault/actions/workflows/release.yml)

A secure, lightweight Roblox account manager for Windows. Download, unzip, double-click, and you're running.

> **The only official download is the [Releases](https://github.com/HECTICWINS/AltVault/releases) page of this repository.**
> Fake "AltVault" downloads on other sites, YouTube descriptions or Discord servers are a common way to steal Roblox accounts.
> There's no paid version, no "pro" build and no installer. If someone offers you one, it isn't AltVault.

## Why AltVault

- **No need to turn anything off.** AltVault runs with **Smart App Control** and **Microsoft Defender** left on.
  You don't need to add exclusions, click "Run anyway", or disable your antivirus.
  Every program file in the download is the official, digitally signed runtime from python.org.
  Unsigned `.exe` builds are what usually get blocked or flagged.
- **Nothing to install.** You don't need .NET, Visual C++ redistributables, Python or admin rights; it's one zip.
- **Your accounts stay yours.** Logins are encrypted to your Windows account, and the file is locked so only you can open it.
  Cookies are only ever sent to `roblox.com`, and AltVault has no servers of its own to send anything to.
- **Code you can actually check.** The whole app is a single readable file, `altvault.py`.
  You or anyone you trust can review it before pasting in a login.
- **Anti-AFK that doesn't get in your way.** It keeps every client from being kicked for idling without flashing Roblox windows over your browser or game.
  It also waits until you pause typing.
- **Featherweight.** About 50 MB of RAM and well under 1% of one CPU core while running.
- **Runs quietly in the tray.** Closing the window keeps AltVault running as a small icon next to the clock, with Anti-AFK still on.
  Click the icon to open AltVault, or right-click it to switch Anti-AFK on or off or to quit.
- **One window only.** Opening AltVault again just brings it back, even from the tray.

## Features

- **Log in with browser:** add accounts by logging in normally. You don't need developer tools or cookie copying.
- **Saved accounts:** logins are encrypted with Windows DPAPI.
- **Join:** launch one or many accounts into a game, optionally all into the same server or a specific Job ID.
- **Multi-Roblox:** run several clients at once.
- **Auto-tile:** arrange any number of Roblox windows in an even grid.
- **Live status:** see who's online or in game, with avatars.
- **Anti-AFK:** stops Roblox's 20-minute idle kick for every open client, while you keep using your PC.

## Install

You don't need Python or anything else installed.

1. Download `AltVault-<version>-win64.zip` from the [Releases](../../releases) page (about 12 MB).
2. Unzip it anywhere, such as your Documents folder.
3. Double-click **AltVault.exe**.

### Verify your download

Every release zip is built by GitHub, not on anyone's PC, straight from the tagged source code.
Before it's published, the [release workflow](.github/workflows/release.yml):

1. runs the [automated tests](tests),
2. checks that every program file carries a valid Python Software Foundation or Microsoft signature,
3. checks that the zip contains nothing from the build machine and that its `altvault.py` is identical to the repo's,
4. signs a **build attestation**: a certificate from GitHub saying exactly which commit and workflow produced the zip.

Each published release is then scanned by [VirusTotal](https://www.virustotal.com) with about 70 antivirus engines, and the release notes link to the results for that exact file along with its SHA-256 hash.

To check your download, run this in PowerShell from the folder you saved it to, and compare the hash with the one in the release notes:

```powershell
Get-FileHash .\AltVault-*-win64.zip
```

If you have the [GitHub CLI](https://cli.github.com), you can also confirm GitHub built the file from this repository:

```powershell
gh attestation verify .\AltVault-2.1.1-win64.zip --repo HECTICWINS/AltVault
```

The download includes its own copy of Python, so one or two lesser-known engines may flag it; that's a common false positive for Python apps.
You can always read `altvault.py` yourself, or run it directly with your own Python instead of the download.

`AltVault.exe` is the official, digitally signed Python runtime from python.org, renamed.
That's why Windows Smart App Control and SmartScreen let it run without warnings, unlike unsigned `.exe` builds.
It's also why the file itself shows the Python icon: changing it would break the signature.
The running window and taskbar show AltVault's icon. For a desktop shortcut with the same icon, right-click `AltVault.exe` > **Show more options** > **Create shortcut**, then point the shortcut's icon at `AltVault.ico`.
The download contains no compiled code of ours. The app itself is the readable `altvault.py` next to the exe.

**Already have Python 3.9 or newer?** You can just download `altvault.py` and double-click it, or run `pyw altvault.py`.
There's nothing to `pip install`; AltVault only uses Python's standard library.

**Upgrading from PyRAM?** Close PyRAM, then start AltVault. Your saved accounts, settings and avatars move over automatically.

## Adding an account

Click **Add account**, then **Log in with browser**.

1. A private browser window opens on roblox.com. AltVault uses Chrome, Edge or Brave, whichever you have.
2. Log in as usual. 2-step verification works too.
3. AltVault saves the account, closes the window and deletes that window's temporary profile.
4. Click **Log in another account** to add the next alt straight away.

You only do this once per account. AltVault never logs the account out.
Log in again only if Roblox ends that session, for example after a password change or "Log out of all sessions"; right-click the account > **Log in again**.

The login window uses a brand-new, empty browser profile, so your normal browser, its logins and its history aren't touched.
AltVault reads only the Roblox login from that window, over a connection that exists only on your PC and only while the window is open.

Prefer not to use a browser window? Choose **Paste a cookie instead**. To get the account's cookie:

1. Open a private window (Ctrl+Shift+N in Chrome or Edge) and log into the account on roblox.com.
2. Press **F12**, open the **Application** tab (in Firefox, **Storage**), then **Cookies** > `https://www.roblox.com`.
3. Click `.ROBLOSECURITY`, copy its whole **Value**, and paste it into AltVault.
4. Close the private window. Don't click **Log Out**, because that makes the cookie stop working.

> **A Roblox login cookie is equivalent to your password.** Only give it to software you trust and have read.
> AltVault is a single readable file so that you can check exactly what it does with it.

## Anti-AFK

Tick **Anti-AFK** in the footer. Every 6–9 minutes AltVault:

1. waits until you've stopped using your mouse and keyboard for 2 seconds,
2. gives each Roblox window focus for a split second and holds Space (a jump),
3. returns focus to the app you were using.

Each window is tracked on its own. If one can't be pinged (for example while it's teleporting), only that window is retried, every 30 seconds.
If a window reaches 15 minutes without a ping, it's pinged straight away instead of waiting for you to pause, so no client gets near Roblox's 20-minute limit.
Every pass is written to `%LOCALAPPDATA%\AltVault\altvault.log`, and any missed window is listed by account with the reason.

Roblox ignores input sent to background windows, so a brief focus switch is the only reliable method.
Any Roblox window hidden behind your other apps is moved off-screen for that moment and put back exactly where it was, so you never see it flash.
The key is only pressed once a Roblox window is confirmed to be in focus, so it never types into your other apps.

- To exclude an account, right-click it and choose **Turn anti-AFK off for this account**.
- Don't minimise Roblox windows; Windows can't send them input. Leave them behind other windows instead.
- To change the key, set `"afk_key"` in `%LOCALAPPDATA%\AltVault\settings.json` to a letter, `"space"` or `"f13"`.

## Security and privacy

- **Encrypted at rest.** Accounts are stored in `%LOCALAPPDATA%\AltVault\accounts.dat`, encrypted with Windows DPAPI.
  Only your Windows user on your PC can decrypt the file; copying it to another user or machine makes it useless.
  AltVault also sets the file's permissions so that only you and SYSTEM can open it.
- **Sent only to Roblox.** Cookies are only ever sent to `https://*.roblox.com`, and the code enforces this.
  There's no telemetry, no update checks and no other servers.
- **Never logged.** Cookies and login tickets are never written to `altvault.log` or shown in error messages.
- **Cleared from the clipboard.** After you add an account, AltVault clears the cookie from your clipboard.
  Windows clipboard history (Win+V) may still have a copy, so clear that history if you have it turned on.
- **What DPAPI can't stop.** Any program running as *your* Windows user could decrypt the file, just as it could read your browser's cookies.
  Keep your PC free of malware.
- **Expired cookies.** If a cookie leaks, logging that account out on roblox.com makes it invalid.
- **Tested on every change.** The [tests](tests/test_altvault.py) check each claim above:
  - cookies are refused for any non-Roblox or non-HTTPS address, including lookalikes such as `evilroblox.com`;
  - a redirect away from Roblox is never followed with the cookie, tested against real local servers;
  - no log line or error message can include a cookie or login ticket;
  - the accounts file is encrypted, rejects tampering, and is locked to your Windows account.
- **Found a problem?** Please report it privately; see [SECURITY.md](SECURITY.md).

## Files

| Path | Contents |
| --- | --- |
| `%LOCALAPPDATA%\AltVault\accounts.dat` | Encrypted accounts. Never share this file. |
| `%LOCALAPPDATA%\AltVault\settings.json` | Window size, last game and options (no secrets) |
| `%LOCALAPPDATA%\AltVault\avatars\` | Cached avatar images |
| `%LOCALAPPDATA%\AltVault\altvault.log` | Errors and warnings (no secrets) |

## Building the download

Official releases are built by GitHub Actions; see [Verify your download](#verify-your-download).
To make a release, add a section to [CHANGELOG.md](CHANGELOG.md), bump `APP_VERSION` in `altvault.py`, then push a tag such as `v2.1.2`.
The release workflow builds, checks and attests the zip and attaches it to a draft release, which is published by hand.

To build it yourself:

```
py -3.13 build_release.py
py -3.13 tests/check_release.py dist/AltVault-<version>-win64.zip
```

This creates `dist/AltVault-<version>-win64.zip` from your python.org Python install.
It copies the official, signed binaries unchanged, leaves out unused parts of Python, and adds `altvault.py`.
A `AltVault._pth` file locks the bundled Python to its own folder, so `PYTHONPATH` and other installed packages can't inject code.

To run the tests: `py -3 -m unittest discover -s tests -v`. They use a temporary folder and never touch your own accounts.

## Disclaimer

AltVault isn't affiliated with Roblox Corporation. Automating input, including Anti-AFK, may break the rules of Roblox or of individual games.
Use it at your own risk.
