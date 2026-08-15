# Security

Housewright runs against a family's email, calendar, finances, and home
devices. Take that seriously even though this is an as-is project.

## Reporting a vulnerability

Open a GitHub Security Advisory (Security tab, "Report a vulnerability")
rather than a public issue. There is no bug bounty and no response-time
promise, but privacy-affecting reports are the one category of issue the
maintainer treats as urgent.

## Design posture (what the system already assumes)

- **Secrets never live in the repo.** The Telegram token comes from the
  environment, the finance session from the OS keychain, and real configs
  plus all state are gitignored. If you find a code path that could write
  a secret to a tracked file, that is a vulnerability: report it.
- **Email content is untrusted input.** Scanners extract structured facts
  only; they never follow links, never reply, and scrub URLs from
  anything written to shared surfaces. A prompt-injection path from email
  content to an action beyond calendar/task filing is a vulnerability.
- **Read-only by default.** No money movement, no purchases, no device
  switching, no outbound mail. A code path that violates this is a bug
  even if intentional-looking.
- **Local network only.** The dashboard binds for LAN/VPN use and has no
  write path. Do not expose it to the public internet.

## Scope notes for self-hosters

You are running this against your own accounts with your own credentials.
The gog CLI holds OAuth tokens in its own keyring; protect the machine
accordingly (disk encryption, screen lock). The Telegram bot token grants
message-sending as your bot: if it leaks, revoke it with BotFather.
