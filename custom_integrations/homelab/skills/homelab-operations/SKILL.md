---
name: homelab-operations
description: Use r1's safe local tools for home automation, cameras, media, books, food, finance, observability, and infrastructure diagnostics.
version: 2.0.0
author: local
metadata:
  hermes:
    tags: [homelab, home-assistant, frigate, books, media, finance, food, observability]
---

# r1 Homelab Operations

Use the `homelab` toolset rather than browser automation, raw terminal commands, arbitrary SQL, or direct database edits.

## Routing

- Home Assistant current entities and bounded REST history: `homelab_ha`. Search first if the entity is uncertain. Control only an entity already in `HASS_CONTROL_ALLOWLIST`, only with a fixed schema service, and only after an explicit request (`confirm: true`).
- Historical sensor calculations: `homelab_influx`. Use `metadata` first when names are uncertain; use Europe/Ljubljana boundaries.
- Frigate health, events, snapshots, recordings: `homelab_frigate`. This tool itself enforces the allowed Telegram-chat list.
- Grafana dashboards/panels/health: `homelab_grafana`.
- Plex streams/search/recent media: `homelab_plex`.
- qBittorrent progress/speed/ETA/stalls/errors: `homelab_qbittorrent`.
- Sonarr/Radarr reads: `homelab_arr`; explicit add requests: `homelab_radarr_add_movie` or `homelab_sonarr_add_series`.
- Lidarr reads: `homelab_lidarr`; explicit artist adds: `homelab_lidarr_add_artist`.
- Bazarr subtitle/health state: `homelab_bazarr`. This surface is read-only.
- Prowlarr indexer/application diagnostics: `homelab_prowlarr`.
- LazyLibrarian search/status/add: `homelab_lazylibrarian`; Calibre search/import/metadata/conversion: `homelab_calibre`.
- Sure accounts/balances/transactions/spending/net-worth data and guarded expense entry: `homelab_sure`.
- Grocy stock/shopping list: `homelab_grocy`.
- Aurboda health/nutrition/sleep/workouts: use the existing Aurboda MCP tools; never create parallel storage.
- Cooklang recipe search/creation: use the existing Cook MCP tools; Cooklang remains the recipe source of truth.
- TimescaleDB, Redis, Mosquitto, Homarr, and fixed container diagnostics: `homelab_infrastructure`.
- Aggregate status: `homelab_health`.

## Confirmation and data rules

Read-only calls do not require confirmation. Set `confirm: true` only when the user explicitly requested that exact write. Preview first for ambiguous media/books and for financial writes. Never use a title mention as approval. Never delete media, accounts, transactions, cameras, HA entities, indexers, or databases. Never expose tokens, cookies, passwords, authorization headers, tracker URLs, or raw secret-bearing configs.

Home Assistant never accepts arbitrary service names or payloads. Frigate never accepts arbitrary URLs or paths. Infrastructure never accepts SQL, Redis commands, MQTT topics, shell commands, URLs, or container names. Book imports are path-restricted; Calibre metadata fields and conversion formats are allowlisted. Expense creation checks duplicates and reads the created transaction back.

For Aurboda and Cooklang follow the personal-health skill: Cooklang defines recipes, Aurboda records actual consumption and health data, and recipe creation/cooking is not evidence that a meal was eaten.
