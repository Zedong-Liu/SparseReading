# SparseRead for OpenClaw

OpenClaw plugin pilot for exposing SparseRead as tools plus optional runtime
hooks.

The plugin starts `python -m sparseread.bridge.openclaw` through stdio JSONL and
keeps one bridge per OpenClaw session key.  The Python bridge owns artifact ids,
ready state, and trace aggregation while delegating all reading logic to the
existing SparseRead core.

Default install policy is `auto`, and default `hookMode` is `off`. Production use
starts with `sro_preview`; `sro_card` is retained for compatibility/debugging
and `bench_protocol`. Keep `hookMode=off` on OpenClaw 2026.6.11 and Windows
unless you are deliberately testing lifecycle-hook behavior. Use
`hookMode=enforce` only for controlled high-confidence long-document/PDF or
compact audit-closure runs.

For normal source installs, run this from the repository root instead of
manually editing OpenClaw config:

```bash
python3 scripts/install_sparseread.py --platform openclaw --openclaw-hook-mode off --doctor
```
