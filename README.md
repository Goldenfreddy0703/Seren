# Prism

[![Kodi version](https://img.shields.io/badge/Kodi%2020%2B%2F21%2F22-blue?style=for-the-badge)](https://kodi.tv/)
[![Early Release](https://img.shields.io/badge/status-early%20release-orange?style=for-the-badge)](https://github.com/Goldenfreddy0703/Prism/issues)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL3-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/GPL-3.0)
[![GitHub Wiki](https://img.shields.io/badge/docs-wiki-blue?style=for-the-badge)](https://github.com/Goldenfreddy0703/Prism/wiki)

**Prism** is an all-in-one Kodi addon for **Movies**, **TV Shows**, and **Anime** — a community-maintained fork of the original [Seren](https://github.com/nixgates/plugin.video.seren) addon by **Nixgates**, rebuilt with anime support, [Simkl](https://simkl.com/) integration, themes, and a modular provider package system.

Browse, discover, track, and play from a single menu. Playback is flexible — use **local files**, optional **debrid services**, or install **third-party provider packages** of your choice.

> **Credit where it's due:** All original Seren work belongs to [Nixgates](https://github.com/nixgates). Prism is maintained by volunteers who want to keep the project alive and expand it for the community.

> **Early release:** The Prism Repository is available for testing. Some devices (e.g. Apple TV, Xbox) may have edge-case bugs — please report them via [GitHub Issues](https://github.com/Goldenfreddy0703/Prism/issues).

---

## Features

### Discover & library

- **All-in-one media hub** — Movies, TV Shows, and Anime from one addon
- **Discover** — Curated lists for movies, TV, and anime (trending, popular, anticipated, top-rated, and more)
- **Simkl integration** — Watchlists, progress, ratings, Next Up, personal libraries, and calendar sync
- **Search** — Movies, TV shows, anime, and actor search in one place
- **My Files** — Browse and play local media or files on your debrid cloud

### Playback & quality

- **Local file playback** — Play from folders on your device or network
- **Debrid support** *(optional)* — Real-Debrid, Premiumize, AllDebrid, TorBox, and Offcloud
- **Smart Play** — Resume, shuffle, play from random, and chapter-aware playback
- **Skip intro & outro** — Automatic segment skipping where available
- **Playing Next dialog** — Seamless episode transitions
- **Audio & subtitle settings** — Customize audio tracks and subtitles per playback
- **Advanced sort & filter** — Quality, 3D, 60fps, internet speed, resolver hide, and more

### Customization

- **Language settings** — Control metadata and UI language preferences
- **Theme Manager** — Install third-party themes that match your Kodi skin
- **Provider package system** — Install and manage third-party provider packages; developers can build their own ([Custom Providers wiki](https://github.com/Goldenfreddy0703/Prism/wiki/Custom-Providers))
- **Built-in tools** — Cache management, database rebuild, provider manager, and download manager

### Platform

- **Kodi 20–22** — Tested on Nexus, Omega, and Pulsar
- **Context menus** — Quick actions via the companion Context Prism addon

For full setup and configuration guides, see the **[Prism Wiki](https://github.com/Goldenfreddy0703/Prism/wiki)**.

---

## Screenshots

Screenshots and theme previews are available on the individual [theme repositories](#ecosystem) and in the [Prism Wiki](https://github.com/Goldenfreddy0703/Prism/wiki/Themes).

---

## Requirements

- **Kodi 20 Nexus or later** (Kodi 19 and earlier are not supported)
- A **Simkl account** (recommended) for full library and sync features

### Playback options

Prism does **not** require a debrid account. How you play content is up to you:

- **Local files** — Point Prism at a folder on your device or network and play from your own library
- **Debrid services** *(optional)* — Real-Debrid, Premiumize, AllDebrid, TorBox, or Offcloud for cached torrent and cloud playback
- **Third-party provider packages** *(optional)* — Install packages via Provider Manager at your own discretion (not bundled with Prism)

---

## Quick Start

After installing Prism, follow these steps to get up and running:

1. **Install from the repository** — Context Prism first, then Prism (see [Installation](#installation))
2. **Sign in to Simkl** — Prism → Settings → Accounts → Simkl
3. **(Optional) Install provider packages** — Prism → Tools → Provider Manager → Install Package (third-party packages of your choice; not included with Prism)
4. **(Optional) Add a debrid account** — Prism → Settings → Accounts → your preferred service
5. **(Optional) Install a theme** — Prism → Tools → Theme Manager → Install Theme (see [Ecosystem](#ecosystem) for available themes)

---

## Installation

The recommended way to install Prism is through the **Prism Repository**, which enables automatic updates.

| Resource | URL |
|----------|-----|
| **Repository source** | `https://goldenfreddy0703.github.io/repository.prism` |
| **Repository repo** | [github.com/Goldenfreddy0703/repository.prism](https://github.com/Goldenfreddy0703/repository.prism) |
| **Addon source** | [github.com/Goldenfreddy0703/Prism](https://github.com/Goldenfreddy0703/Prism) |

### Install via Repository (Recommended)

1. In Kodi, go to **Settings → File Manager → Add source**
2. Enter this URL as the source:
   ```
   https://goldenfreddy0703.github.io/repository.prism
   ```
3. Name it something like `Prism` and confirm
4. Go to **Add-ons → Install from zip file**, select your new source, and install `repository.prism`
5. Go to **Add-ons → Install from repository → Prism Repository** and install **in this order**:
   - **Context Prism** (required dependency)
   - **Prism**

After installation, open Prism settings to configure Simkl, playback options, and preferences. See the [Wiki](https://github.com/Goldenfreddy0703/Prism/wiki/Installation-&-Updates) for a full walkthrough.

### Manual Installation

Only use this if you cannot install from the repository. Future updates should always come from the repository.

1. Install dependencies **in this order**:
   - Context Menu Addon (`context.prism`)
   - Prism Addon (`plugin.video.prism`)
2. After each update, **clear cache and rebuild the database** (Prism → Tools) so changes take effect properly

Pre-built zip packages are hosted in [repository.prism](https://github.com/Goldenfreddy0703/repository.prism).

---

## Roadmap

Prism is under active development. Planned improvements include expanded adaptive provider support for users without debrid accounts. Follow [GitHub Issues](https://github.com/Goldenfreddy0703/Prism/issues) for progress and feature requests.

---

## Ecosystem

Prism is part of a larger ecosystem of community-maintained addons and themes.

| Project | Description |
|---------|-------------|
| [repository.prism](https://github.com/Goldenfreddy0703/repository.prism) | Official Kodi repository for Prism and dependencies |
| [prism.theme.az](https://github.com/Goldenfreddy0703/prism.theme.az) | Theme for Artic Zephyr users |
| [prism.theme.auramod](https://github.com/Goldenfreddy0703/prism.theme.auramod) | Theme for AuraMod users |
| [prism.theme.ah2](https://github.com/Goldenfreddy0703/prism.theme.ah2) | Theme for Artic Horizon 2 users |
| [prism.theme.af](https://github.com/Goldenfreddy0703/prism.theme.af) | Theme for Artic Fuse users |
| [prism.theme.af2](https://github.com/Goldenfreddy0703/prism.theme.af2) | Theme for Artic Fuse 2 users |
| [prism.theme.af3](https://github.com/Goldenfreddy0703/prism.theme.af3) | Theme for Artic Fuse 3 users |

Install themes via **Prism → Tools → Theme Manager**. See the [Themes wiki](https://github.com/Goldenfreddy0703/Prism/wiki/Themes) for details.

---

## Troubleshooting

Run into issues? Start here:

- **[Prism Wiki](https://github.com/Goldenfreddy0703/Prism/wiki/Troubleshooting)** — Setup, configuration, and common fixes
- **[GitHub Issues](https://github.com/Goldenfreddy0703/Prism/issues)** — Bug reports and troubleshooting
- **[Addons4Kodi Discord](https://discord.gg/SqX7buB)** — Community support

---

## Contributing

Prism is a **community-driven** project. Seren was originally created by Nixgates, and this fork is maintained by volunteers.

Contributions are welcome:

- **Bug reports & feature requests** — Open an [issue](https://github.com/Goldenfreddy0703/Prism/issues)
- **Code contributions** — Submit a pull request
- **Community support** — Help others in issues or on Discord
- **Provider & theme developers** — See the [Custom Providers](https://github.com/Goldenfreddy0703/Prism/wiki/Custom-Providers) and [Themes](https://github.com/Goldenfreddy0703/Prism/wiki/Themes) wiki pages

If you'd like to take an active role in development, reach out via the contact methods below.

---

## Contact

- **Discord:** The Steampunk Owl
- **Keybase:** [Goldenfreddy0703](https://keybase.io/goldenfreddy0703)
- **Bug Reports:** [GitHub Issues](https://github.com/Goldenfreddy0703/Prism/issues)
- **Community Support:** [Addons4Kodi Discord](https://discord.gg/SqX7buB)

---

## Disclaimer

Prism is and always will be **free and open-source**. None of its code or resources may be sold or redistributed for commercial purposes.

Prism is a **media hub and playback platform**. It does **not** include, host, or distribute scrapers or streaming content. Users may optionally install **third-party provider packages** at their own discretion. Prism and its developers do **not** endorse, maintain, or take responsibility for any third-party provider package.

Users are responsible for complying with all applicable laws and regulations in their country.

Prism and its developers are not affiliated with Team Kodi, Simkl, or any third-party services used with the addon.

---

## License

Prism is licensed under the **[GPL-3.0 License](https://opensource.org/licenses/GPL-3.0)**.
