# Documentation

Reference material for the Lumina Krita plugin. For the user-facing
manual, see [`../src/Lumina/MANUAL.md`](../src/Lumina/MANUAL.md)
(or `MANUAL.html` in the same folder, which is the page Krita shows under
**Help → Plugin Help**).

## About the plugin

| Document | What it covers |
|---|---|
| [GettingStarted.md](GettingStarted.md) | Install, enable, and a walkthrough with screenshots. Start here. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Module layout, data flow, design decisions, deploy commands. |
| [TestingGuide.md](TestingGuide.md) | The test suites and how to run them. |
| [Lumina.md](Lumina.md) | The original design brief, kept as written. |

## Krita and Qt reference

Background on the APIs this plugin is built on. These are not specific to
Lumina.

| Document | What it covers |
|---|---|
| [KritaAPIDocs.md](KritaAPIDocs.md) | Links and notes on the Krita scripting API. |
| [KritaPluginAPI.md](KritaPluginAPI.md) | Plugin API best practices and pitfalls. |
| [KritaPluginPatterns.md](KritaPluginPatterns.md) | Patterns for docker and widget structure. |
| [KritaFlatpak.md](KritaFlatpak.md) | Flatpak paths and deployment. |

## Troubleshooting

| Document | What it covers |
|---|---|
| [Bugs.md](Bugs.md) | Bugs hit in this plugin, with the real crash logs and the fixes. Start here when something breaks. |
| [CommonIssues.md](CommonIssues.md) | Wider set of issues and their verified solutions. |

`Bugs.md` keeps its original tracebacks, with the plugin's directory name
normalised to `Lumina` for consistency. They are historical records, not
instructions — the line numbers and paths inside them describe past states of
the code.
