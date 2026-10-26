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
- **Private servers:** paste a private server link and every selected account joins that private server.
- **FPS caps, lowest graphics and mute per account:** run alts at 15 FPS, minimum graphics and no sound to save power, while your main stays smooth.
- **Server browser:** see a game's servers with player counts, ping and FPS, and join one, or let AltVault pick the least busy server with room for all your accounts.
- **Join a player:** join the server a friend is in by typing their username.
- **Groups:** sort accounts into folders such as Mains and Alts, and join, cap or mute a whole group at once.
- **Favourite games:** star games so they stay at the top of the game list.
- **Import from Roblox Account Manager:** bring your accounts, groups and notes over from RAM in one step.
- **Multi-Roblox:** run several clients at once.
- **Auto-tile:** arrange any number of Roblox windows in an even grid.
- **Live status:** see who's online or in game, with avatars.
- **Anti-AFK:** stops Roblox's 20-minute idle kick for every open client, while you keep using your PC.
- **Live view:** every Roblox window as a live tile, with who's in a game, Roblox's CPU and RAM, and an egg hatch calculator.
- **Stream mode:** hides usernames and avatars, in AltVault and in Roblox's window titles, for screen sharing.
- **Hide from taskbar:** keeps a dozen Roblox windows out of the taskbar and Alt+Tab.
- **Export and import:** move your accounts to another PC in a password-protected file.

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
gh attestation verify .\AltVault-2.7.0-win64.zip --repo HECTICWINS/AltVault
```

The download includes its own copy of Python, so one or two lesser-known engines may flag it; that's a common false positive for Python apps.
You can always read `altvault.py` yourself, or run it directly with your own Python instead of the download.

`AltVault.exe` is the official, digitally signed Python runtime from python.org, renamed.
That's why Windows Smart App Control and SmartScreen let it run without warnings, unlike unsigned `.exe` builds.
It's also why the file itself shows the Python icon: changing it would break the signature.
The running window and taskbar show AltVault's icon. For a desktop shortcut with the same icon, right-click `AltVault.exe` > **Show more options** > **Create shortcut**, then point the shortcut's icon at `AltVault.ico`.
The download contains no compiled code of ours. The app itself is the readable `altvault.py` next to the exe.

**Already have Python 3.9 or newer?** You can run the source instead of the download. There's nothing to `pip install`; AltVault only uses Python's standard library.

1. Install Python from [python.org](https://www.python.org/downloads/windows/) (64-bit, 3.13 recommended), not the Microsoft Store version.
2. Right-click `altvault.py` > **Open with** > **Python**. Or run `pyw altvault.py` in that folder.

Double-clicking `altvault.py` only works if `.py` files open with Python on your PC. Code editors such as VS Code often take them over, so the file opens as text instead.
**Code > Download ZIP** on GitHub gives you this source version. For the ready-to-run `AltVault.exe`, use the [Releases](https://github.com/HECTICWINS/AltVault/releases) page.

**Upgrading from PyRAM?** Close PyRAM, then start AltVault. Your saved accounts, settings and avatars move over automatically.

## Finding your way around

- **Sidebar:** **Accounts**, **Profiles**, **Games**, **Live**, **Stats** and **Pulse** (Ctrl+1 to Ctrl+6), and **Settings** at the bottom (Ctrl+,).
- **Profiles page:** saved setups, a name plus some accounts and a game. **Launch** selects those accounts and joins them.
- **Games page:** your favourite and recent games with their icons. **Pick for joining**, **Servers...**, star or remove them, or paste a link to add one.
- **Stats page:** how the last 24 hours or 7 days went: time in a game, launches that worked, Anti-AFK pings and misses, and windows that closed without AltVault closing them (crashes, kicks or closed by hand). AltVault keeps only these numbers, per hour, for a week, in `history.json`.
- **Pulse page:** your PC's and Roblox's CPU and memory, live every 2 seconds while the page is open, and charted over the last hour, day or week from a reading AltVault takes once a minute.
- **Accounts page, top:** search the list.
- **Game box:** what to join. **★** pins a game; **Servers...** shows its servers. The **Server** box under it is optional.
- **Account list:** select accounts (Ctrl+click, Shift+click, Ctrl+A, or click a group's header). The **Join** button shows how many will launch.
- **Right-click** an account or group: **Join**, **Performance** (FPS cap, lowest graphics, mute, Anti-AFK), **Group**, **Account** (note, profile, log in again), and **Remove**.
- **Bottom (every page):** the **Anti-AFK** switch and what AltVault last did.
- **Live page:** a tile for every Roblox window. Click one to bring that window to the front; its **✕** closes it. The dot shows its state: green in a game, yellow still loading, red not responding. On the right: how many windows are in a game, Roblox's CPU and RAM use, your PC's free RAM, Anti-AFK's last and next ping, and the **egg hatch** calculator. Type one alt's speed (eggs per minute, eggs per hour or seconds per egg) and it multiplies it by the accounts in a game. Previews are drawn by Windows itself (like taskbar previews), only while the page is open, so they cost nothing the rest of the time.
- **Settings page:** things you set once: Multi-Roblox, same server, window arranging, the Anti-AFK key, **Stream mode**, **Hide Roblox from the taskbar**, and **Export / Import**.

## Moving your accounts to another PC

Updates keep your accounts by themselves: they live in `%LOCALAPPDATA%\AltVault`, not next to the app, so replacing the app folder with a new version changes nothing.

That data only opens for your Windows user on this PC. To move accounts elsewhere, open **Settings > Export...**, pick a file and a password (8 characters or more). On the other PC, **Settings > Import...** and type the password. Groups, notes, FPS caps, graphics and mute settings, Anti-AFK choices and favourite games come along; every login is checked with Roblox first.

The file is encrypted with AES-256-GCM using a key made from your password with scrypt, so it can't be read or changed without the password. Still, anyone with the file **and** the password can log into those accounts: keep both private, and delete the file once you've imported it.

## Adding an account

Click **+ Add account**, then **Log in with browser**.

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

## Joining a private server

Paste the private server link into the **Game** box, select the accounts and click **Join**. Both kinds of link work:

- share links, such as `https://www.roblox.com/share?code=...&type=Server`, which is what Roblox's **Copy link** button gives you now;
- older links, such as `https://www.roblox.com/games/123456/Game-Name?privateServerLinkCode=...`.

Every selected account joins that private server, so the **Server** box and the same-server setting are ignored.
Each account must be allowed in: for a friends-only private server, every alt needs to be friends with the owner.
A private server link works like an invite, so AltVault never saves it to `settings.json` or the log.

## Servers and joining a player

Click **Servers...** next to the Game box to see that game's open servers, with player counts, ping and FPS.
Pick one and click **Join this server**, or click **Join least busy** to send your selected accounts to the emptiest server that has room for all of them.
The server list is public, so AltVault loads it without sending any login.

To join a friend, right-click your accounts > **Join a player's server...** and type their username.
This only works when Roblox lets your account see which server they're in; usually that means being friends with them.

## Groups and favourites

Right-click an account > **Group** > **New group...** to sort accounts into folders such as Mains and Alts.
Right-click a group's header to join, cap or mute everyone in it at once, or to rename it.
Double-clicking a header only opens or closes it, so it never launches a whole group by accident.

Click **☆** next to the Game box to add the current game to your favourites. Favourites stay at the top of the game list with a ★.

## FPS caps, lowest graphics and mute

Right-click an account (or several, or a group) and choose:

- **FPS cap**: 15, 20, 30, 45 or 60 FPS, or **Custom...** for any number from 5 to 240;
- **Lowest graphics**: Roblox's lowest graphics quality;
- **Mute**: no sound.

Alts you're only AFK grinding on rarely need more than 15–30 FPS or any graphics, and these settings save a lot of CPU, GPU and power.

Each setting applies from that account's **next join**; windows that are already open aren't touched.
They use Roblox's own settings: just before launching an account, AltVault writes that account's values into Roblox's `GlobalBasicSettings_13.xml`, and once the client has started it puts your own settings back exactly.
Only those numbers are changed, and nothing is injected into Roblox.
To check an FPS cap, press **Shift+F5** in that Roblox window to show its frame rate.

## Importing from Roblox Account Manager

Click **+ Add account** > **Import from Roblox Account Manager...** and choose RAM's `AccountData.json`, which is in the folder RAM runs from.
AltVault checks every login with Roblox and adds the ones that still work, along with their RAM groups and aliases. Saved passwords are never read.
It works with RAM's normal encrypted file on the same PC and Windows account. If you set a password in RAM, turn that off in RAM first.

## Anti-AFK

Tick **Anti-AFK** at the bottom of the window. Every 6–9 minutes AltVault:

1. waits until you've stopped using your mouse and keyboard for 2 seconds,
2. gives each Roblox window focus for a split second and holds Space (a jump),
3. returns focus to the app you were using.

Each window is tracked on its own. If one can't be pinged (for example while it's teleporting), only that window is retried, every 30 seconds.
If a window reaches 15 minutes without a ping, it's pinged straight away instead of waiting for you to pause, so no client gets near Roblox's 20-minute limit.
Every pass is written to `%LOCALAPPDATA%\AltVault\altvault.log`, and any missed window is listed by account with the reason.

If something keeps Anti-AFK from reaching the Roblox windows for about 2 minutes, AltVault shows a **tray alert**.
It says which accounts are affected, what's in the way and roughly how long until they'd be idle-kicked.
Usual causes are the lock screen, a Windows permission (UAC) prompt, or an app running as administrator that's in front, such as an installer or updater.
Once pings get through again, a second alert says Anti-AFK is working again.
Only the blocking program's file name is recorded, never window titles, which can contain private text.

Roblox ignores input sent to background windows, so a brief focus switch is the only reliable method.
Any Roblox window hidden behind your other apps is moved off-screen for that moment and put back exactly where it was, so you never see it flash.
The key is only pressed once a Roblox window is confirmed to be in focus, so it never types into your other apps.

- To exclude accounts, select them, right-click and untick **Performance** > **Anti-AFK**.
- Don't minimise Roblox windows; Windows can't send them input. Leave them behind other windows instead.
- To change the key it presses, open **Settings** and pick a letter, Space or F13.

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
