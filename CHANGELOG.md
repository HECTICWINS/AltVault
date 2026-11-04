# Changelog

Every release on the [Releases](../../releases) page is built by GitHub from the tagged commit, comes with a build attestation, and is scanned by VirusTotal. See [Verify your download](README.md#verify-your-download).

## 3.0.0

- **Free for everyone:** no access key any more. Download, unzip and run.
- **A new, plainer look:** every page is redrawn in Microsoft's WebView2: a sidebar, a dense account table, plain status words and a one-line status bar. Windows draws the Live page's previews itself, so they still cost almost nothing. The window opens in under a second, and while it's hidden in the tray the page stops drawing and hands memory back.
- **Modes:** **Normal**, **Grind** and **Max alts**, one click at the top of Home. Grind runs every alt at 15 FPS, lowest graphics and muted, in Windows' efficiency mode; Max alts goes to 10 FPS and lets Windows move alts out of memory first when it runs short.
- **Main account:** the account you play on keeps your own Roblox settings and normal priority in every mode. Right-click it on the Accounts page and choose **Main account**, or pick it in **Settings > Modes**.
- **A server with room for everyone:** with Put accounts in the same server on, every account goes straight to a public server with space for all of them, instead of waiting for the first to arrive.
- **Stop and retry:** the Join button stops a running join, and accounts that didn't start are selected afterwards so Join tries them again.
- **Gentler launching:** an optional gap between launches, and a wait of up to a minute while memory is over 90% full.
- **Lighter rendering:** alts on lowest graphics also get the lowest textures, no anti-aliasing, a grey sky and no grass, through Roblox's allowed rendering flags. Your own flags file is always put back.
- **Minimise all** (Ctrl+M) and Restore all, on the Live page next to Tile.
- **Discord alerts:** Settings > Alerts sends disconnects, expired logins, crashes and low memory to your own Discord webhook.
- **Panic hotkey:** Ctrl+Alt+End closes every Roblox window from any app (off by default).
- **Upkeep:** Roblox logs older than 3 days are deleted once a day, and AltVault tells you when a newer version is out (it never installs anything by itself).
- **Copy diagnostics** in Settings > Data, for bug reports, with no logins, links or usernames.
- Dialogs (Add account, Edit profile, Servers, export and import, confirmations) and the notes in the corner match the new look.
- The low-memory warning suggests Max alts mode.
- Fixed a harmless error in the log when a background check finished just after AltVault closed.

## 2.8.0

- **Anti-AFK presses F13 by default,** so characters stay where they are instead of jumping. Settings still offers Space or a letter.
- **Doctor:** when an account drops, AltVault reads Roblox's own logs and tells you what happened, why and what to do, with a button for the fix. Idle kicks, rejected logins, game kicks, lost connections, crashes and server moves, kept for a week.
- **Home page:** status cards, quick actions, what needs attention, recent activity and a first-run checklist.
- **New sidebar:** page names in groups, collapsible to icons (Ctrl+B).
- **Settings in tabs:** General, Anti-AFK, Privacy, Appearance and Data, with a choice of accent colour and a settings backup that never holds logins or links.
- **Notes in the corner** instead of only the tray, tooltips, and keyboard shortcuts (F1 lists them).
- **Private server links are remembered:** the last link stays in the Game box and its list after a restart, encrypted to your Windows user. **Settings > Forget private link** deletes it.
- **Anti-AFK disturbs you less:** every 12–14 minutes instead of 6–9 while you're using your PC, and it stops and hands focus back as soon as you touch the mouse or keyboard.
- **Stays connected:** alerts when an account drops out of its game, is moved to another server, or its login expires, plus a low-memory warning. Waits longer before the key press when memory is nearly full, and never presses more than once.
- A login that still works is no longer reported as expired when Roblox refuses a launch ticket.
- **Removed:** the egg hatch calculator and the eggs hatched estimate.

## 2.7.1

- **New look with a sidebar:** **Accounts**, **Profiles**, **Games**, **Live**, **Stats**, **Pulse** and **Settings**, a bigger window, and the Anti-AFK switch on every page. Buttons, boxes, checkboxes and scrollbars are redrawn with rounded corners and hover, pressed and focus looks; lists highlight the row under the pointer.
- **Profiles:** save a setup (a name, some accounts and a game) and launch it with one click.
- **Games:** favourites and recent games in one list with their icons. Pick one for joining, browse its servers, star it or open it on roblox.com.
- **Stats:** time in a game, launches that worked, Anti-AFK pings and windows closed outside AltVault, over 24 hours or 7 days, with hourly charts (hover for values). Only numbers are kept, in `history.json`, for a week.
- **Pulse:** your PC's and Roblox's CPU and memory, live while the page is open and sampled once a minute otherwise, charted over the last hour, 24 hours or 7 days.
- **Live page:** every Roblox window as a live tile with a status dot (in a game, joining, not responding, or grey for a window AltVault didn't open). Click a tile to bring that window forward, or close it from its header. Tiles fill the space, no scrolling. Windows draws the previews itself, and only while the page is open.
- **Stats panel:** how many windows are in a game, Roblox's CPU and RAM, your PC's free RAM, and the last 24 hours at a glance. Sampled every 3 seconds, and only while you're looking.
- **Egg hatch calculator:** type one alt's speed in eggs/min, eggs/hour or seconds per egg and see the total per minute, hour and day for every account in a game.
- **Stream mode:** hides usernames and avatars in AltVault and in Roblox's window titles.
- **Hide Roblox from the taskbar:** also keeps them out of Alt+Tab. The taskbar buttons come back when you turn it off or quit.
- **Export and import accounts** as a password-protected `.altvault` file (scrypt + AES-256-GCM), with groups, notes and per-account settings.
- Settings is now a page instead of a separate window.
- **Eggs hatched (estimate):** type one alt's hatch speed once and AltVault keeps a running count: speed x accounts in a game x time, kept across restarts. See it on Stats (24 hours or 7 days, plus a total) and in the Live panel. It's an estimate, since the game's own counter can't be read; **Set total…** on Stats lines it up with the game's number and carries on from there.
- **Purple night theme:** near-black with a faint violet tint, and a dark violet accent on the selected page, main buttons, ticks and charts. New purple app icon.
- **Dark dialogs everywhere:** confirmations, warnings and text prompts match the app instead of flashing a bright Windows box. Destructive actions (Remove, Close all, Delete) use a red button.
- **Light on power:** pages only redraw what actually changed, so the Live page idles at about 0.2% of one CPU core (it was over 20% in testing). Roblox memory now matches Task Manager (the private working set).
- Game names with emoji (which Windows can't draw here) show cleanly, and group arrows are redrawn to match the theme.
- Scrollbars only appear when a list is longer than its box. Game and Server boxes sit side by side, and empty charts say so instead of showing blank axes.

## 2.6.0

- **Cleaner layout.** The game comes first, with the server box as a clearly optional second line. The **Join** button says how many accounts it will launch, and the footer holds only the Anti-AFK switch and status.
- **Settings window (⚙)** for things you set once: Multi-Roblox, same server, arranging windows, and the Anti-AFK key, which can now be chosen without editing files.
- **Right-click menu grouped** into **Performance** (FPS cap, lowest graphics, mute, Anti-AFK), **Group** and **Account**, with Join at the top and Remove at the bottom. Anti-AFK can now be switched off for several accounts at once.

## 2.5.0

- **Server browser:** click **Servers...** to see a game's open servers with player counts, ping and FPS. Join one, or click **Join least busy** to pick the emptiest server with room for all selected accounts. The list loads without sending any login.
- **Join a player's server:** right-click > **Join a player's server...** and type a username.
- **Groups:** sort accounts into folders, collapse them, and join, cap or mute a whole group from its header. Double-clicking a header never launches the group.
- **Favourite games:** the ☆ button pins a game to the top of the game list.
- **Lowest graphics and mute per account**, alongside FPS caps. Like FPS caps, they use Roblox's own settings for that account's launch, and your own settings are put back exactly.
- **Import from Roblox Account Manager:** reads RAM's `AccountData.json` (plain or encrypted on the same PC), checks each login, and keeps groups and aliases. Saved passwords are never read.

## 2.4.0

- **Anti-AFK tells you when it's blocked.** If it can't reach the Roblox windows for about 2 minutes, a tray alert names the accounts, what's in front (the lock screen, a UAC prompt, an administrator app such as an installer) and roughly how long until they'd be idle-kicked. A second alert says when it's working again.
- The log now records what was in front whenever a window refused focus. Only the program's file name is recorded, never window titles.

## 2.3.0

- **FPS caps per account:** right-click one or more accounts > **FPS cap** and pick 15, 20, 30, 45 or 60 FPS, or any custom number from 5 to 240. The cap applies from the account's next join, and the status column shows it.
- Caps use Roblox's own Maximum Frame Rate setting, written just before each launch; your own setting is put back once the client starts, and nothing is injected into Roblox.

## 2.2.0

- **Private servers:** paste a private server link into the Game box and every selected account joins that private server. Works with Roblox's share links (`roblox.com/share?code=...&type=Server`) and older `privateServerLinkCode` links. Before, pasting one joined a public server of that game instead.
- Expired or turned-off private server links get a clear message instead of a silent public join.
- Private server links are never saved to `settings.json` or written to the log, since they work like invites.

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
