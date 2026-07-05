# SparseRead for OpenClaw

OpenClaw plugin pilot for exposing SparseRead as tools plus runtime gate hooks.

The plugin starts `python -m sparseread.bridge.openclaw` through stdio JSONL and
keeps one bridge per OpenClaw session key.  The Python bridge owns artifact ids,
ready state, and trace aggregation while delegating all reading logic to the
existing SparseRead core.

Default policy is `advisory`. Production use starts with `sro_preview`; `sro_card`
is retained for compatibility/debugging and `bench_protocol`. Use `enforce` only
for controlled high-confidence long-document/PDF or compact audit-closure runs.

For normal source installs, run this from the repository root instead of
manually editing OpenClaw config:

```bash
python3 scripts/install_sparseread.py --platform openclaw --doctor
```
