# Security Policy

Lumina is a local-only Krita plugin: it renders a sphere, reads the brush
color, and stores its own settings. It makes no network connections, runs
no services, and sends nothing anywhere — so its attack surface is small.
The realistic risks are a malicious settings file or a crafted input
crashing/freezing Krita, and those reports are welcome.

## Supported Versions

| Version | Supported            |
| ------- | -------------------- |
| 2.5.x   | :white_check_mark:   |
| < 2.5   | :x: (please upgrade) |

Only the latest release receives fixes. If you are on an older version,
reproduce the issue on the latest release first.

## Reporting a Vulnerability

- **GitHub:** use
  [private vulnerability reporting](../../security/advisories/new)
  on this repo (preferred — it stays confidential until fixed), or open a
  regular issue if you are comfortable with it being public.
- **What to include:** Lumina + Krita versions, install type (flatpak
  matters), steps to reproduce, and any `lumina_log.txt` output.
- **What to expect:** confirmation within a week, a fix in the next
  release for anything verified, and credit in the release notes unless
  you prefer otherwise.

Out of scope: Krita itself (report those to the Krita project), and
anything requiring the attacker to already run code on your machine —
at that point a plugin is the least of your problems.
