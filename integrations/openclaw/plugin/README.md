# SparseRead for OpenClaw

OpenClaw plugin pilot for exposing SparseRead as tools plus optional runtime
hooks.

The plugin starts `python -m sparseread.bridge.openclaw` through stdio JSONL and
keeps one bridge per OpenClaw session key.  The Python bridge owns artifact ids,
ready state, and trace aggregation while delegating all reading logic to the
existing SparseRead core.

The source installer exposes one public mode. Default `--sparseread-mode auto`
uses gate-controlled interception: high-confidence long-document/PDF/log and
compact audit-closure reads are redirected to `sro_preview`, while low-benefit
cases keep OpenClaw tools. `--sparseread-mode advisory` registers guidance only
and does not intercept native reads. `sro_card` is retained for
compatibility/debugging and `bench_protocol`.

For normal source installs, run this from the repository root instead of
manually editing OpenClaw config:

```bash
python3 scripts/install_sparseread.py --platform openclaw --doctor
```
