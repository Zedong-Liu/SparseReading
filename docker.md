# Docker Runtime

Verified on 2026-04-23.

## Container

```bash
ssh 6000p
docker exec -it lzd-docker bash
```

Observed container:

```text
name: lzd-docker
image: nvcr.io/nvidia/pytorch:25.09-py3
status when checked: Up 42 hours
mount: /data1 on host -> /data in container
```

Relevant ownership inside the container is `1001:1001` for `/data/lzd/...`; running Git as root may require `-c safe.directory=...`.

## Common Checks

```bash
ssh 6000p 'docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}"'
ssh 6000p 'docker inspect lzd-docker --format "{{range .Mounts}}{{printf \"%s -> %s (%s)\n\" .Source .Destination .Type}}{{end}}"'
ssh 6000p 'docker exec lzd-docker bash -lc "ls -ld /data/lzd/agent-comp /data/lzd/nanobot /data/lzd/pinchbench-skill"'
```

## Model Service

PinchBench/nanobot evaluations use a vLLM endpoint inside the container:

```text
host: 127.0.0.1
port: 8000
served model: qwen35-local
model path: /data/Qwen3.5-35B-A3B
python/env: /root/miniconda3/envs/kvserve-qwen35
```

Health check:

```bash
docker exec lzd-docker bash -lc 'curl -sf http://127.0.0.1:8000/health >/dev/null && echo up || echo down'
```

Observed on 2026-04-23: vLLM health was `down`; start it before online evaluation.

Practical rule for this host:

- Run `nvidia-smi` before startup.
- If only one GPU is actually free, use that card only and set `--tensor-parallel-size 1`.
- Keep one stable vLLM process alive and reuse port `8000` for repeated tests.

## SRO v3 Runtime

Use the clean v3 source path in benchmark runs:

```bash
docker exec -it lzd-docker bash
cd /data/lzd/nanobot-sro-v3
SRO_ENABLED=1 NANOBOT_SOURCE_PATH=/data/lzd/nanobot-sro-v3 /root/miniconda3/envs/kvserve-qwen35/bin/python -m pytest tests/sparse_reading/test_sro_protocol.py -q
```

Environment notes:

- `pytest-asyncio` and `ruff` are not installed in the checked container.
- `openpyxl` install via pip timed out; SRO v3 keeps `openpyxl` as the preferred XLSX reader and includes a stdlib fallback.
