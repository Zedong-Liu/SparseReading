"""Pi's persistent JSONL transport, using the shared core protocol."""

import argparse

from sparseread.bridge.server import serve_bridge
from sparseread_agent_tools.runtime import HostBridge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead host JSONL bridge")
    parser.add_argument("--host", choices=["codex", "pi"], required=True)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--mode", choices=["auto", "advisory"], default="auto")
    args = parser.parse_args(argv)
    return serve_bridge(args, lambda workspace, mode: HostBridge(host=args.host, workspace=workspace, mode=mode))


if __name__ == "__main__":
    raise SystemExit(main())
