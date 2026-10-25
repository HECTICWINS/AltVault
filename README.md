# AltVault

A lightweight Roblox account manager for Windows, written as a single Python file with no dependencies.
Because it runs on the official, signed Python interpreter rather than an unsigned `.exe`, it works with Windows Smart App Control turned on.

## Features

- **Saved accounts:** your cookies are encrypted with Windows DPAPI.
- **Join:** launch one or many accounts into a game, optionally all into the same server or a specific Job ID.
- **Multi-Roblox:** run several clients at once.
- **Auto-tile:** arrange the Roblox windows in a grid.
- **Live status:** see who's online or in game, with avatars.
- **Anti-AFK:** stops Roblox's 20-minute idle kick for every open client, while you keep using your PC.
- **Low resource use:** idles at roughly 0.2% of one CPU core, and stops polling while minimised.
- **One window only:** opening AltVault again just brings the existing window to the front.

## Install

You don't need Python or anything else installed.

1. Download `AltVault-<version>-win64.zip` from the [Releases](../../releases) page (about 12 MB).
2. Unzip it anywhere, such as your Documents folder.
3. Double-click **AltVault.exe**.

`AltVault.exe` is the official, digitally signed Python runtime from python.org, renamed.
That's why Windows Smart App Control and SmartScreen let it run without warnings, unlike unsigned `.exe` builds.
It's also why the file itself shows the Python icon: changing it would break the signature.
The running window and taskbar show AltVault's icon. For a desktop shortcut with the same icon, right-click `AltVault.exe` > **Show more options** > **Create shortcut**, then point the shortcut's icon at `AltVault.ico`.
The download contains no compiled code of ours. The app itself is the readable `altvault.py` next to the exe.

**Already have Python 3.9 or newer?** You can just download `altvault.py` and double-click it, or run `pyw altvault.py`.
There's nothing to `pip install`; AltVault only uses Python's standard library.

**Upgrading from PyRAM?** Close PyRAM, then start AltVault. Your saved accounts, settings and avatars move over automatically.

## Adding an account

AltVault asks for an account's `.ROBLOSECURITY` cookie. The **Add account** dialog walks you through copying it from your browser.

> **Your cookie is equivalent to your password.** Only paste it into software you trust and have read.
> AltVault is a single readable file so that you can check exactly what it does with it.

## Anti-AFK

Tick **Anti-AFK** in the footer. Every 8–11 minutes AltVault:

1. waits until you've stopped using your mouse and keyboard for 2 seconds,
2. brings each Roblox window to the front for a moment and presses Space (a jump),
3. returns focus to the app you were using.

Roblox ignores input sent to background windows, so a brief focus switch is the only reliable method.
The key is only pressed once a Roblox window is confirmed to be in front, so it never types into your other apps.

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

## Files

| Path | Contents |
| --- | --- |
| `%LOCALAPPDATA%\AltVault\accounts.dat` | Encrypted accounts. Never share this file. |
| `%LOCALAPPDATA%\AltVault\settings.json` | Window size, last game and options (no secrets) |
| `%LOCALAPPDATA%\AltVault\avatars\` | Cached avatar images |
| `%LOCALAPPDATA%\AltVault\altvault.log` | Errors and warnings (no secrets) |

## Building the download

```
py -3.13 build_release.py
```

This creates `dist/AltVault-<version>-win64.zip` from your python.org Python install.
It copies the official, signed binaries unchanged, leaves out unused parts of Python, and adds `altvault.py`.
A `AltVault._pth` file locks the bundled Python to its own folder, so `PYTHONPATH` and other installed packages can't inject code.
Bump `APP_VERSION` in `altvault.py` before each release.

## Disclaimer

AltVault isn't affiliated with Roblox Corporation. Automating input, including Anti-AFK, may break the rules of Roblox or of individual games.
Use it at your own risk.
