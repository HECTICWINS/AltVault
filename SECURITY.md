# Security

AltVault stores Roblox login cookies, which work like passwords, so security reports are taken seriously.

## Reporting a vulnerability

**Please don't open a public issue for a security problem.** Report it privately instead:

1. Go to the [Security tab](../../security) of this repository.
2. Click **Report a vulnerability** and describe the problem and how to reproduce it.

Only the maintainer can see the report. You'll get a reply as soon as possible, normally within a week, and credit in the fix's release notes if you'd like it.

## Supported versions

Only the latest release gets security fixes. Please check you're on it before reporting.

## What's in scope

Anything that could expose a saved login or let someone act as a user, for example:

- a cookie or login ticket being sent anywhere other than `https://*.roblox.com`, including through redirects
- a cookie or login ticket appearing in `altvault.log`, an error message or a crash report
- `accounts.dat` being readable by another Windows account, or decryptable without the user's Windows login
- the browser login reading anything from the user's own browser profile, or leaving its temporary profile behind
- a release download that doesn't match the source, or isn't covered by its build attestation

Roblox bans, Anti-AFK not working in a particular game, and problems that need someone to already be running code as the user (which could read browser cookies just as easily) aren't vulnerabilities in AltVault. Please open a normal issue for those.

## How AltVault protects logins

The [README's security section](README.md#security-and-privacy) explains each protection. The automated tests in [`tests/`](tests) check them on every change.
