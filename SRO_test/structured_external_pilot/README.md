# External structured local-fact pilot

This pilot screens structured-file tasks whose answer is a small, exact subset of one large official object. It excludes full-table aggregation, joins, regression, formula recalculation, and task-specific hints.

Snapshot provenance (downloaded 2026-07-15):

- `nasa_pscomppars_2026-07-15.csv`: NASA Exoplanet Archive TAP, `select * from pscomppars`, CSV format.
- `usgs_earthquakes_2024-01.csv`: USGS FDSN Event Web Service, all January 2024 events ordered by time, CSV format.
- `noaa_storm_events_2023.csv`: NOAA NCEI Storm Events bulk details file for 2023, revision `c20260323`.
- `nyc_311_2024-01-01.csv`: NYC Open Data dataset `erm2-nwe9`, requests created from 2024-01-01 00:00 through 2024-01-02 00:00, CSV format.
- `sec_msft_submissions_2026-07-15.json`: SEC EDGAR submissions API for CIK `0000789019`.

The task runtimes live under `SRO_test/qwenclawbench/{baseline,sro_v3}/task_external_*` only because they reuse the existing trusted runner layout. The task sources and prompts are external and are not QwenClawBench tasks.

SHA-256 checksums:

| Snapshot | SHA-256 |
| --- | --- |
| `nasa_pscomppars_2026-07-15.csv` | `35886dde44d13490d9cec4f3c87299e6e8dfc4d59f3b070e68bd4b2e2312d103` |
| `noaa_storm_events_2023.csv` | `2e5acf39bec00352828ce59bd5927a9eee942452080b327c175347a5bc44ec7f` |
| `nyc_311_2024-01-01.csv` | `f15aa2af336c67a1f3ba8fc58b9989cb26e13acf5c51224c1a97d80ab4b0f048` |
| `sec_msft_submissions_2026-07-15.json` | `a33237dbec83fc5a6529a3ec92990322400b2cb0f386993a44d0d0be69ea5804` |
| `usgs_earthquakes_2024-01.csv` | `4af793b178662edd23e83ae43b32251584d7d57f0572e663ae47be9d2fbbdc12` |
