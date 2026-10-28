"""LLM-visible schemas. Descriptions deliberately encode safety and routing rules."""
def schema(name, description, properties=None, required=None):
    return {"name": name, "description": description, "parameters": {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}

HA = schema("homelab_ha", "Home Assistant reads plus tightly allowlisted controls. Reads current entities and REST history. Control accepts only fixed services for entities listed in HASS_CONTROL_ALLOWLIST and requires confirm=true after an explicit user request; it never accepts an arbitrary service/domain/payload.", {
    "action": {"type":"string","enum":["list","search","get","weather","environment","history","control"]},
    "query": {"type":"string","description":"Friendly name, entity-id substring, area, or concept such as outside humidity"},
    "entity_id": {"type":"string"}, "domain":{"type":"string"}, "limit":{"type":"integer","minimum":1,"maximum":2000},
    "start":{"type":"string","description":"RFC3339 history start"},"end":{"type":"string","description":"RFC3339 history end"},
    "service":{"type":"string","enum":["turn_on","turn_off","toggle","open_cover","close_cover","stop_cover","set_temperature"]},
    "temperature":{"type":"number","minimum":5,"maximum":35},"confirm":{"type":"boolean"}}, ["action"])
INFLUX = schema("homelab_influx", "Safe READ-ONLY InfluxDB historical sensor query. Use for historical max/min/average/range, rainfall timestamps/totals, grouped days/months, and period comparisons. It generates bounded Flux; it does not accept arbitrary Flux/SQL.", {
    "action":{"type":"string","enum":["metadata","data_range","aggregate","series","last_rain","rain_totals","compare"]},
    "sensor":{"type":"string","description":"Actual entity/sensor/measurement name; use metadata first if uncertain"},
    "field":{"type":"string","default":"value"},
    "statistic":{"type":"string","enum":["max","min","mean","sum","count","last","range"]},
    "start":{"type":"string","description":"RFC3339 timestamp or safe Flux relative duration like -30d"},
    "stop":{"type":"string","description":"RFC3339 timestamp; default now"},
    "group_by":{"type":"string","enum":["hour","day","week","month"]},
    "start_b":{"type":"string"},"stop_b":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":1000}}, ["action"])
GRAFANA = schema("homelab_grafana", "READ-ONLY Grafana observability: health, list/search dashboards, and retrieve dashboard definitions/panels. Historical calculations should use homelab_influx instead.", {
    "action":{"type":"string","enum":["health","dashboards","dashboard"]},"query":{"type":"string"},"uid":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}}, ["action"])
PLEX = schema("homelab_plex", "READ-ONLY Plex server status, active sessions, transcode/direct-play details, bandwidth, libraries, search, recently added media, and scan status.", {
    "action":{"type":"string","enum":["status","sessions","libraries","search","recent","scan_status"]},"query":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}}, ["action"])
QBITTORRENT = schema("homelab_qbittorrent", "READ-ONLY qBittorrent transfer status and torrents. Use for what is downloading, speeds, progress, ETA, peers/seeds, stalled/error torrents, and diagnosing a stuck torrent.", {
    "action":{"type":"string","enum":["transfer","torrents","downloading","stalled","errors"]},"query":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":200}}, ["action"])
ARR = schema("homelab_arr", "READ-ONLY Sonarr/Radarr queues, wanted/missing items, upcoming calendar, recent history, health, and monitored library status.", {
    "service":{"type":"string","enum":["sonarr","radarr","both"]},"action":{"type":"string","enum":["queue","wanted","upcoming","history","health","library"]},"query":{"type":"string"},"days":{"type":"integer","minimum":1,"maximum":365},"limit":{"type":"integer","minimum":1,"maximum":200}}, ["service","action"])
RADARR_ADD = schema("homelab_radarr_add_movie", "Guarded Radarr action: look up/add one movie. Set confirm=false to preview candidates without writing. Set confirm=true only when the user explicitly requested the download/add; never infer confirmation from merely mentioning a title. Never manipulates qBittorrent directly and never deletes.", {
    "title":{"type":"string"},"year":{"type":"integer","minimum":1880,"maximum":2200},"tmdb_id":{"type":"integer"},"search_now":{"type":"boolean","default":True},"confirm":{"type":"boolean","description":"Must be true only after an explicit user add/download request; false performs lookup only"}}, ["title","confirm"])
SONARR_ADD = schema("homelab_sonarr_add_series", "Guarded Sonarr action: look up/add one TV series. Set confirm=false to preview candidates without writing. Set confirm=true only when the user explicitly requested the download/add; never infer confirmation from merely mentioning a title. Never deletes.", {
    "title":{"type":"string"},"year":{"type":"integer","minimum":1900,"maximum":2200},"tvdb_id":{"type":"integer"},"search_now":{"type":"boolean","default":True},"confirm":{"type":"boolean","description":"Must be true only after an explicit user add/download request; false performs lookup only"}}, ["title","confirm"])
HEALTH = schema("homelab_health", "Check concise r1 homelab health: Home Assistant, InfluxDB, Grafana, Plex, qBittorrent, Sonarr, Radarr, /Volumes/STORAGE, and relevant Docker containers.")

FRIGATE = schema("homelab_frigate", "Authorized-chat-only Frigate camera status, recent detection events, event filtering, snapshots, and recordings. Read-only: never changes cameras or retention. Media is returned as a local file path for delivery.", {
    "action":{"type":"string","enum":["status","events","snapshot","recording"]},"camera":{"type":"string"},"object":{"type":"string"},
    "after":{"type":"number","description":"Unix timestamp"},"before":{"type":"number","description":"Unix timestamp"},"event_id":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}}, ["action"])
LAZYLIBRARIAN = schema("homelab_lazylibrarian", "LazyLibrarian book search and download status plus guarded add. add requires an exact book_id from search and confirm=true after an explicit request.", {
    "action":{"type":"string","enum":["status","search","wanted","snatched","history","add"]},"query":{"type":"string"},"book_id":{"type":"string"},"confirm":{"type":"boolean"},"limit":{"type":"integer","minimum":1,"maximum":200}}, ["action"])
CALIBRE = schema("homelab_calibre", "Calibre library search/metadata plus guarded import, metadata edit, and conversion through calibredb/ebook-convert. Writes require confirm=true; imports are restricted to configured roots and metadata fields are allowlisted.", {
    "action":{"type":"string","enum":["list","search","metadata","import","edit_metadata","convert"]},"query":{"type":"string"},"book_id":{"type":"integer"},"path":{"type":"string"},
    "field":{"type":"string","enum":["title","authors","tags","publisher","series","series_index","comments","rating","languages","pubdate"]},"value":{"type":"string"},
    "format":{"type":"string","enum":["epub","mobi","azw3","pdf","txt"]},"confirm":{"type":"boolean"},"limit":{"type":"integer","minimum":1,"maximum":200}}, ["action"])
LIDARR = schema("homelab_lidarr", "READ-ONLY Lidarr status, health, queue, missing albums, artists, and albums.", {
    "action":{"type":"string","enum":["status","health","queue","missing","artists","albums"]},"query":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":200}}, ["action"])
LIDARR_ADD = schema("homelab_lidarr_add_artist", "Guarded Lidarr artist lookup/add. confirm=false previews candidates; confirm=true only after an explicit artist request. Never deletes.", {
    "artist":{"type":"string"},"foreign_artist_id":{"type":"string"},"search_now":{"type":"boolean","default":False},"confirm":{"type":"boolean"}}, ["artist","confirm"])
BAZARR = schema("homelab_bazarr", "READ-ONLY Bazarr status, health, provider state, subtitle history, and episode/movie subtitle status.", {
    "action":{"type":"string","enum":["status","health","providers","history","episodes","movies","wanted_episodes","wanted_movies","search_episode","search_movie"]},
    "episode_id":{"type":"integer"},"radarr_id":{"type":"integer"},"limit":{"type":"integer","minimum":1,"maximum":200}}, ["action"])
PROWLARR = schema("homelab_prowlarr", "READ-ONLY Prowlarr diagnostics: status, health, configured indexers, indexer statistics, and connected applications.", {
    "action":{"type":"string","enum":["status","health","indexers","stats","applications"]}}, ["action"])
SURE = schema("homelab_sure", "Sure Finance accounts, balances, transactions, spending, cash flow, insights, and guarded expense entry. add_expense requires confirm=true after preview. No deletion or bulk edits.", {
    "action":{"type":"string","enum":["accounts","balance_sheet","balances","cash_flow","insights","transactions","spending","add_expense"]},
    "account_id":{"type":"string"},"start_date":{"type":"string"},"end_date":{"type":"string"},"type":{"type":"string","enum":["income","expense"]},"query":{"type":"string"},
    "amount":{"type":"number","exclusiveMinimum":0},"name":{"type":"string"},"date":{"type":"string"},"currency":{"type":"string"},"category_id":{"type":"string"},"notes":{"type":"string"},"confirm":{"type":"boolean"},"limit":{"type":"integer","minimum":1,"maximum":1000}}, ["action"])
GROCY = schema("homelab_grocy", "Grocy inventory, products, locations, units, and shopping list. Writes use the established Grocy helper and require confirm=true after an explicit request; bulk destructive operations are not exposed.", {
    "action":{"type":"string","enum":["health","have","stock","products","list","list_add","list_done","stock_add","consume","undo","locations","units"]},
    "item":{"type":"string"},"items":{"type":"array","items":{"type":"string"}},"query":{"type":"string"},"location":{"type":"string"},"amount":{"type":"number","exclusiveMinimum":0},"note":{"type":"string"},
    "product_id":{"type":"integer"},"ids":{"type":"array","items":{"type":"integer"}},"delete":{"type":"boolean"},"best_before":{"type":"string"},"price":{"type":"number"},"unit":{"type":"string"},"create":{"type":"boolean"},"free_text":{"type":"boolean"},"spoiled":{"type":"boolean"},"transaction_id":{"type":"string"},"confirm":{"type":"boolean"}}, ["action"])
INFRASTRUCTURE = schema("homelab_infrastructure", "Safe fixed diagnostics for TimescaleDB/PostgreSQL, Sure Redis, Mosquitto, Homarr, and known Docker containers. Never accepts SQL, Redis commands, MQTT topics, shell commands, URLs, or arbitrary container names.", {
    "action":{"type":"string","enum":["containers","timescaledb","redis","mosquitto","homarr"]}}, ["action"])
