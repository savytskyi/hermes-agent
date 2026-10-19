---
title: Radarr and Sonarr
description: Search, add, monitor, and inspect movies and TV series through Radarr and Sonarr.
sidebar_label: Radarr and Sonarr
sidebar_position: 9
---

# Radarr and Sonarr Integration

Hermes can act as a conversational control layer for an existing Radarr/Sonarr installation. It uses the current `/api/v3` APIs with `X-Api-Key` authentication and delegates all release selection, download-client work, importing, naming, and library management to Radarr or Sonarr.

Hermes does **not** connect to torrent/Usenet clients, select individual releases, manipulate Plex files, or expose arbitrary media-server HTTP requests. The initial integration also has no delete, blacklist, or remove-from-disk operations.

## Configuration

Add the service URLs and API keys to `~/.hermes/.env`:

```bash
RADARR_URL=http://r1.local:7878
RADARR_API_KEY=your_radarr_api_key_here

SONARR_URL=http://r1.local:8989
SONARR_API_KEY=your_sonarr_api_key_here
```

Real keys belong only in `~/.hermes/.env`; do not add them to source control or documentation. Restart the gateway (or use `/reload` where supported) after changing the environment.

Hermes normally selects the root folder and quality profile used most often by the existing library. For an empty library it uses the first item returned by the service. You can set explicit name/path defaults without hard-coding numeric IDs:

```bash
RADARR_ROOT_FOLDER=/media/movies
RADARR_QUALITY_PROFILE=HD-1080p
SONARR_ROOT_FOLDER=/media/tv
SONARR_QUALITY_PROFILE=HD-1080p
```

A quality requested in conversation is matched against existing profile names. Common `4K`/`2160p`/`UHD` and `1080p` aliases are supported, but Hermes never invents profile IDs.

## Conversational commands

Examples include:

- `Download Dune Part Two`
- `Get Interstellar`
- `Download the newest Mission Impossible`
- `Find the 1999 version of The Mummy`
- `Add Oppenheimer, but don't download it yet`
- `Search for Interstellar now`
- `Is Dune downloaded yet?`
- `Do I already have Oppenheimer?`
- `Download all of Severance`
- `Download only season 2 of Severance`
- `Get The Great North season 6`
- `Download S02E04 of Severance`
- `How many episodes of The Great North are missing?`
- `What's downloading?`
- `How far along is Severance?`

When multiple materially different titles match, the media tool returns a short candidate list with year and TMDB/TVDB ID rather than adding one blindly. An explicit year strongly constrains matching; “newest” selects the latest plausible matching result.

## Tools

The `media` toolset provides:

- `media_search_movie` — Radarr lookup plus existing-library awareness
- `media_download_movie` — duplicate-safe add/download, with optional year, TMDB ID, quality name, and delayed search
- `media_search_movie_now` — trigger a search for an existing Radarr movie
- `media_movie_status` — library, queue, download, import, failure, and progress state
- `media_search_series` — Sonarr lookup plus seasons and existing-library awareness
- `media_download_series` — add/download all seasons or an exact list of seasons
- `media_download_episode` — monitor and search one exact episode
- `media_series_status` — series/season episode counts and queue state
- `media_download_queue` — combined Radarr/Sonarr queue
- `media_health` — lightweight read-only `/api/v3/system/status` checks

The `media` toolset is available in `hermes tools` and its individual tools appear only when the corresponding URL and API key are configured. Radarr and Sonarr being offline does not prevent Hermes from starting; calls return concise user-facing connection errors while detailed failures are logged internally.

## Season monitoring behavior

For a request such as “Download only season 3,” Hermes adds the series if needed, marks only season 3 as monitored, leaves specials and other seasons unmonitored, and sends Sonarr a `SeasonSearch` command for season 3. Existing series are updated with the same exclusive monitoring scope before the search starts.

An all-series request monitors all regular seasons and asks Sonarr to search for missing episodes. Episode-specific requests monitor the exact episode and send an `EpisodeSearch` command.
