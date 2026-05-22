# Remote Access

Verified on 2026-04-23.

## Login

```bash
ssh 6000p
```

Observed remote:

```text
host: HPDPS-5
user: gyd
home: /home/gyd
docker: Docker version 28.2.2
```

`6000p` is an SSH alias/config on this machine. Do not record key paths or credentials here.

## Real Project Locations

The active runtime is the remote Docker container `lzd-docker`.

Container paths:

```text
/data/lzd/agent-comp        bridge scripts and evaluation artifacts
/data/lzd/nanobot           main source repo; most code changes happen here
/data/lzd/pinchbench-skill  PinchBench benchmark scripts
```

Host paths:

```text
/data1/lzd/agent-comp
/data1/lzd/nanobot
/data1/lzd/nanobot-sro-v3
/data1/lzd/pinchbench-skill
```

Confirmed mount:

```text
/data1 -> /data (Docker bind mount)
```

Rule: inside the container use `/data/...`; outside the container use `/data1/...`.

## Repository State Snapshot

Checked inside `lzd-docker` on 2026-04-23.

`/data/lzd/nanobot`:

```text
repo: https://github.com/HKUDS/nanobot.git
branch: main...origin/main [behind 335]
HEAD: 7113ad34 Merge PR #2733: harden agent runtime for long-running tasks
status: dirty, with ContextLens-related local edits and untracked files
```

Use this form to avoid Git safe-directory failures as root in the container:

```bash
git -c safe.directory=/data/lzd/nanobot status --short --branch
```

`/data/lzd/agent-comp` is not a Git repository.

`/data/lzd/pinchbench-skill`:

```text
branch: main...origin/main [behind 125]
status: dirty, with local changes under scripts/ and untracked tests/
```

`/data/lzd/nanobot-sro-v3`:

```text
repo: https://github.com/HKUDS/nanobot.git
purpose: clean SRO v3 implementation branch/worktree
status: created 2026-04-23; local SRO v3 edits live here
```
