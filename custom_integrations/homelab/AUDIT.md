# r1 Homelab Integration Audit

Validated 2026-10-28 on `r1.local`. No test performed a production write, download, device action, financial transaction, or database mutation.

## Integration matrix

| Service | Authentication | Hermes surface | Read operations | Guarded write operations | Live result | Remaining action |
|---|---|---|---|---|---|---|
| Home Assistant | Long-lived token configured and accepted | `homelab_ha` | Entity list/search/get, weather/environment summaries, bounded REST history | Fixed `turn_on`, `turn_off`, `toggle`, cover and thermostat actions; entity must be allowlisted and `confirm=true` | Entity search and history passed | `HASS_CONTROL_ALLOWLIST` is intentionally empty; owner must choose device entity IDs before control is possible |
| Frigate | Chat authorization configured; local API needs no separate token | `homelab_frigate` | Camera/detector status, event search by camera/object/time, snapshot and clip retrieval | None | Blocked: container is stopped after an OOM kill and has no active port binding | Review camera/config memory use and explicitly start Frigate; then rerun read-only tests |
| Plex | Existing local token is present but the server rejects it | `homelab_plex` | Identity/status, sessions/transcodes, bandwidth, libraries, search, recent, scan status | None | Server identity passed; authenticated library read returned HTTP 401 | Reclaim/sign in to the native Plex server so it issues a working token; do not disable Plex authentication |
| Sonarr | Existing API key accepted | `homelab_arr`, `homelab_sonarr_add_series` | Queue, wanted, calendar, history, health, monitored library | Exact series add/search only; preview and explicit confirmation; no delete | Queue and health passed; add preview passed without writing | None |
| Radarr | Existing API key accepted | `homelab_arr`, `homelab_radarr_add_movie` | Queue, wanted, calendar, history, health, monitored library | Exact movie add/search only; preview and explicit confirmation; no delete | Queue and health passed; add preview passed without writing | None |
| Lidarr | Existing API key accepted | `homelab_lidarr`, `homelab_lidarr_add_artist` | Status, health, queue, missing albums, artists, albums | Exact artist add; preview and explicit confirmation; no delete | Health and artist reads passed; add guard unit-tested | None |
| Bazarr | Existing API key accepted | `homelab_bazarr` | Status, health, providers, history, episode/movie subtitle status, wanted subtitles, candidate search | None | Status and providers passed | None |
| Prowlarr | Existing API key accepted | `homelab_prowlarr` | Status, health, indexers, indexer statistics, connected applications | None | Health and indexer statistics passed | None |
| qBittorrent | Existing Web API session works | `homelab_qbittorrent` | Transfer rates, torrents, downloading, stalled and error state | None | Transfer and downloading reads passed | None |
| LazyLibrarian | Existing API key accepted | `homelab_lazylibrarian` | Version/status, search, wanted, snatched, history | Add exact search result by ID with explicit confirmation | Status and search passed | None |
| Calibre / Calibre-Web | Local `calibredb`/`ebook-convert` CLI in the existing container | `homelab_calibre` | List/search and metadata | Path-restricted import, allowlisted metadata edit, allowlisted format conversion; explicit confirmation | Library search passed; CLI availability verified | None |
| Grafana | Local read API/DB integration | `homelab_grafana` | Health, dashboard search/list, sanitized dashboard/panel definitions | None | Health and dashboard read passed | None |
| InfluxDB | Existing token accepted | `homelab_influx` | Metadata, data range, bounded aggregate/series, rain totals, period comparison | None | Metadata and historical query passed | None |
| Aurboda fork | Existing Aurboda MCP token accepted | Existing `mcp__aurboda__*` tools | Health overview, metrics, sleep, workouts, activities, meals/nutrition, trends, correlations and reports | Existing Aurboda APIs for meals, metrics, workouts, notes and supported records | Health, sleep and meal reads passed | None |
| Sure fork | Existing API key accepted | `homelab_sure` | Accounts, balances, balance sheet/net worth, transactions, spending, cash flow, insights | Single expense creation with preview, duplicate check, explicit confirmation, and read-back; no delete/bulk edit | Accounts, balance sheet and spending passed | None |
| Grocy | Existing API key accepted | `homelab_grocy` | Health, stock, products, locations, units, shopping list | Add/consume stock and shopping-list updates through existing helper; explicit confirmation | Health and stock passed | None |
| Cooklang | Local Cook MCP works without login for recipe features | Existing `mcp__cook__*` tools | List/read/search/validate recipes, menus, pantry and shopping list | Recipe/menu/config creation through existing Cook MCP | Recipe listing passed (15 recipes) | Optional Cook account/token only for paid nutrition/import features; not needed for recipe workflows |
| TimescaleDB/PostgreSQL | Local container-only diagnostic | `homelab_infrastructure` | Readiness, server version, connection count and database sizes using fixed query | None | Passed | None |
| Sure Redis | Local container-only diagnostic | `homelab_infrastructure` | PING and selected memory counters | None | Passed | None |
| Mosquitto | Local TCP/container diagnostic | `homelab_infrastructure` | Reachability and container state | None | Passed | None |
| Homarr | Local HTTP diagnostic | `homelab_infrastructure` | Reachability/status | None | Passed | None |
| Docker containers | Docker CLI from host; fixed name allowlist | `homelab_infrastructure`, `homelab_health` | Status, health and restart count for known services | None | 23 known containers inspected | Frigate is stopped; aggregate health remains degraded until it runs |
| Nginx | Container status only | `homelab_infrastructure`, `homelab_health` | Container health/status | None | Running | None |
| Honcho local stack | Existing Hermes Honcho plugin | `honcho_*` tools | Memory/profile/search/reasoning through the plugin | Plugin-scoped profile/conclusion writes | API, database and Redis containers healthy; deriver running | No duplicate homelab integration needed |

## Security and persistence

- The plugin exposes fixed JSON schemas, not arbitrary URLs, SQL, Redis commands, MQTT topics, shell commands, Home Assistant service names, or container names.
- Frigate checks the originating Hermes session and allows camera data only for configured Telegram chat IDs (plus local CLI sessions).
- Home Assistant controls require both an explicit allowlist and `confirm=true`; the allowlist is currently empty, so control fails closed.
- Media/book/finance/food writes use previews or explicit confirmation. Destructive account, transaction, media, camera, database, and bulk-edit operations are absent.
- Secrets stay in the existing Hermes profile `.env`; tools redact error details that could contain credentials. No secret values are committed.
- Runtime plugin path `~/.hermes/plugins/homelab` is a symlink to this version-controlled tree, so code survives Hermes application updates and is covered with the development repository/host backups.
- No Docker socket was added to any service, no authentication was weakened, and no new public port was created.

## Validation

- Unit/safety suite: 14/14 passed.
- Read-only live checks passed for Home Assistant, InfluxDB, Grafana, qBittorrent, Sonarr, Radarr, Lidarr, Bazarr, Prowlarr, LazyLibrarian, Calibre, Sure, Grocy, TimescaleDB, Redis, Mosquitto, Homarr, Aurboda and Cooklang.
- Write paths were validated only through guards/previews and unit tests.
- Known live failures are Frigate availability (stopped after OOM) and Plex authenticated access (401 with the server's current stored token).
