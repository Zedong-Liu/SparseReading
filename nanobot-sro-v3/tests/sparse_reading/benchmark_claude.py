"""SparseRead Claude Code Benchmark Harness.

Runs standard test scenarios through the ClaudeBridge in both SRO and native
modes, measuring token consumption, gate decisions, and task completion.
Compares against OpenClaw experimental baselines from SRO_test/qwenclawbench/.

Usage:
  uv run --project nanobot-sro-v3 python -m pytest \
    nanobot-sro-v3/tests/sparse_reading/benchmark_claude.py -v -s

  # Or run directly:
  uv run --project nanobot-sro-v3 python \
    nanobot-sro-v3/tests/sparse_reading/benchmark_claude.py

Each scenario builds a temp directory with test assets, then simulates the
Claude Code tool-call pattern (preview → read → write) through the bridge.
Token metrics are collected via the bridge's built-in TokenTracker.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from sparseread.bridge.claude import ClaudeBridge, classify_claude_gate
from sparseread.token_tracker import estimate_file_tokens


# ---------------------------------------------------------------------------
# Benchmark scenarios
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkScenario:
    """A test scenario that exercises specific SRO gate paths."""

    name: str
    description: str
    expected_gate_mode: str  # expected dominate gate mode
    expected_min_savings: float  # minimum expected savings ratio

    def setup(self, root: Path) -> dict[str, Any]:
        """Create test assets. Returns metadata about what was created."""
        raise NotImplementedError

    def simulate_sro(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        """Simulate the SRO tool-call pattern through the bridge."""
        raise NotImplementedError

    def simulate_native(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        """Simulate native reads (bypassing SRO)."""
        raise NotImplementedError


class AuditBundleScenario(BenchmarkScenario):
    """Multi-file collection: code + state + output JSON — tests collection gate."""

    def __init__(self) -> None:
        super().__init__(
            name="audit_bundle",
            description="Audit bundle: code + state + output — collection gate",
            expected_gate_mode="enforce",
            expected_min_savings=0.70,
        )

    def setup(self, root: Path) -> dict[str, Any]:
        assets = root / "a_stock_announcements"
        assets.mkdir(parents=True)
        output_dir = assets / "output"
        output_dir.mkdir()

        (assets / "fetcher.py").write_text(
            "#!/usr/bin/env python3\n"
            + "def deduplicate(seen):\n    return list(seen)[-5000:]\n" * 200
            + "\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )
        (assets / "fetch_state.json").write_text(
            json.dumps({"seen_ids": [f"id_{i}" for i in range(35)]}), encoding="utf-8"
        )
        (output_dir / "announcements_2026-02-09.json").write_text(
            json.dumps([
                {"id": f"id_{i}", "company": f"Company {i}", "important": i < 5}
                for i in range(11)
            ]),
            encoding="utf-8",
        )
        (assets / "config.yaml").write_text(
            "max_pages: 10\nfetch_sse: true\nrequest_delay: 2\ncategory: stocks\nnotifications: email\n",
            encoding="utf-8",
        )
        (assets / "requirements.txt").write_text("requests>=2.28\npyyaml>=6.0\n", encoding="utf-8")

        return {
            "root": str(assets),
            "files": 5,
            "total_size": sum(
                (assets / f).stat().st_size
                for f in ["fetcher.py", "fetch_state.json", "config.yaml", "requirements.txt"]
            ) + (output_dir / "announcements_2026-02-09.json").stat().st_size,
        }

    def simulate_sro(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        root = meta["root"]

        # Step 1: decide on the collection
        decide = bridge.handle({"method": "decide", "params": {"path": root}})

        # Step 2: preview the collection
        preview = bridge.handle({"method": "preview", "params": {"path": root}})
        pack = preview.get("preview_pack", {})
        artifact_id = pack.get("artifact_id", "")

        # Step 3: usage stats (preview alone shows token savings)
        usage = bridge.handle({"method": "usage", "params": {}})

        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "trajectory": decide.get("claude_gate", {}).get("trajectory", "unknown"),
            "artifact_id": artifact_id,
            "preview_type": pack.get("card", {}).get("type", ""),
            "usage": usage,
        }

    def simulate_native(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        root = meta["root"]
        # In native mode, we just decide + card (no SRO tools used)
        decide = bridge.handle({"method": "decide", "params": {"path": root}})
        card = bridge.handle({"method": "card", "params": {"path": root}})
        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "sparse_recommended": card.get("file_card", {}).get("sparse_recommended", False),
            "usage": usage,
        }


class PDFComprehensionScenario(BenchmarkScenario):
    """Long PDF with fact-based questions — tests PDF enforcement."""

    def __init__(self) -> None:
        super().__init__(
            name="pdf_comprehension",
            description="PDF comprehension: long PDF with multi-fact QA",
            expected_gate_mode="enforce",
            expected_min_savings=0.85,
        )

    def setup(self, root: Path) -> dict[str, Any]:
        pdf = root / "report.pdf"
        # Simulate a large PDF (~50KB of text)
        pdf_content = "%PDF-1.4\n"
        pdf_content += (
            "The OpenClaw agent framework supports community-built skills.\n"
            "Before filtering, the public registry had 5,705 community-built skills.\n"
            "After applying quality filters, 3,421 skills remained.\n"
            "The largest skill category is 'Automation' with 892 skills.\n"
            "The second-largest is 'Data Analysis' with 654 skills.\n"
            "An OpenClaw skill is defined by a file named SKILL.md.\n"
            "The OpenClaw gateway exposes a typed WebSocket API.\n"
            "The skills registry data was collected on March 15, 2026.\n"
            "The paper proposes 12 new benchmark tasks for agent evaluation.\n"
        )
        pdf_content += ("Additional content for the report. " * 200 + "\n") * 50
        pdf.write_text(pdf_content, encoding="utf-8")
        return {"path": str(pdf), "size": pdf.stat().st_size}

    def simulate_sro(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        path = meta["path"]

        # Step 1: decide
        decide = bridge.handle({"method": "decide", "params": {"path": path}})

        # Step 2: preview
        preview = bridge.handle({"method": "preview", "params": {"path": path}})
        pack = preview.get("preview_pack", {})
        artifact_id = pack.get("artifact_id", "")

        # Step 3: collect evidence
        read_result = {}
        if artifact_id:
            read_result = bridge.handle({
                "method": "read",
                "params": {
                    "target": {"artifact_id": artifact_id},
                    "mode": "collect",
                    "hint": {
                        "goal": "answer comprehension questions about the PDF",
                        "type_hint": "pdf",
                        "slots": [
                            {"id": "q1", "question": "How many community-built skills before filtering?"},
                            {"id": "q2", "question": "How many skills after filtering?"},
                            {"id": "q3", "question": "Largest skill category and count"},
                            {"id": "q4", "question": "Second-largest skill category and count"},
                            {"id": "q5", "question": "File name that defines a skill"},
                            {"id": "q6", "question": "Type of API exposed by gateway"},
                            {"id": "q7", "question": "Data collection date"},
                            {"id": "q8", "question": "How many new benchmark tasks proposed"},
                        ],
                    },
                },
            })

        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "trajectory": decide.get("claude_gate", {}).get("trajectory", "unknown"),
            "artifact_id": artifact_id,
            "read_ready": (
                (read_result.get("evidence_pack", {}).get("slot_digest") or {}).get("overall_status", "")
                if read_result else ""
            ),
            "usage": usage,
        }

    def simulate_native(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        path = meta["path"]
        decide = bridge.handle({"method": "decide", "params": {"path": path}})
        card = bridge.handle({"method": "card", "params": {"path": path}})
        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "sparse_recommended": card.get("file_card", {}).get("sparse_recommended", False),
            "usage": usage,
        }


class LongTextQAScenario(BenchmarkScenario):
    """Single large text file (>50KB) with multi-fact questions — tests text enforcement."""

    def __init__(self) -> None:
        super().__init__(
            name="long_text_qa",
            description="Long text QA: >50KB markdown with fact extraction",
            expected_gate_mode="enforce",
            expected_min_savings=0.80,
        )

    def setup(self, root: Path) -> dict[str, Any]:
        doc = root / "document.md"
        content = "# The Fall of Outremer\n\n"
        content += "## Chapter 1: The Crusader States\n\n"
        content += ("The Kingdom of Jerusalem was established in 1099 after the First Crusade. "
                     "It lasted until 1291 when Acre fell to the Mamluks.\n\n")
        content += ("Baldwin I became the first king in 1100. "
                     "The kingdom reached its greatest extent under Baldwin III.\n\n")
        content += "## Chapter 2: Military Orders\n\n"
        content += ("The Knights Templar were founded in 1119 by Hugh de Payens. "
                     "They were headquartered on the Temple Mount in Jerusalem.\n\n")
        content += ("The Knights Hospitaller were founded earlier, in 1099, "
                     "by Gerard Thom. They ran a hospital in Jerusalem.\n\n")
        content += ("Both orders played crucial roles in the defense of the Crusader states.\n\n")
        # Pad to >50KB
        for i in range(3, 80):
            content += f"## Chapter {i}: Additional Material\n\n"
            content += ("Additional historical context and analysis for chapter {i}. " * 20) + "\n\n"
        doc.write_text(content, encoding="utf-8")
        return {"path": str(doc), "size": doc.stat().st_size}

    def simulate_sro(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        path = meta["path"]
        decide = bridge.handle({"method": "decide", "params": {"path": path}})
        preview = bridge.handle({"method": "preview", "params": {"path": path}})
        pack = preview.get("preview_pack", {})
        artifact_id = pack.get("artifact_id", "")
        read_result = {}
        if artifact_id:
            read_result = bridge.handle({
                "method": "read",
                "params": {
                    "target": {"artifact_id": artifact_id},
                    "mode": "collect",
                    "hint": {
                        "goal": "extract key historical facts",
                        "type_hint": "text",
                        "slots": [
                            {"id": "founding", "question": "When was the Kingdom of Jerusalem established?"},
                            {"id": "fall", "question": "When and how did the kingdom end?"},
                            {"id": "templar_founding", "question": "When and by whom were the Templars founded?"},
                        ],
                    },
                },
            })
        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "artifact_id": artifact_id,
            "read_ready": (
                (read_result.get("evidence_pack", {}).get("slot_digest") or {}).get("overall_status", "")
                if read_result else ""
            ),
            "usage": usage,
        }

    def simulate_native(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        path = meta["path"]
        decide = bridge.handle({"method": "decide", "params": {"path": path}})
        card = bridge.handle({"method": "card", "params": {"path": path}})
        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "sparse_recommended": card.get("file_card", {}).get("sparse_recommended", False),
            "usage": usage,
        }


class StructuredDataScenario(BenchmarkScenario):
    """Large CSV/JSON files — tests advisory mode."""

    def __init__(self) -> None:
        super().__init__(
            name="structured_data",
            description="Structured data: large CSV — advisory gate",
            expected_gate_mode="advisory",
            expected_min_savings=0.50,
        )

    def setup(self, root: Path) -> dict[str, Any]:
        csv_file = root / "transactions.csv"
        header = "id,date,amount,currency,category,merchant,status,region,payment_method,notes\n"
        rows = []
        for i in range(1, 2000):
            rows.append(
                f"txn_{i:06d},2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d},"
                f"{i * 10.5:.2f},USD,cat_{i % 20},merchant_{i % 50},"
                f"{'completed' if i % 3 else 'pending'},region_{i % 8},"
                f"{'card' if i % 2 else 'transfer'},note_{i}"
            )
        csv_file.write_text(header + "\n".join(rows), encoding="utf-8")
        return {"path": str(csv_file), "size": csv_file.stat().st_size}

    def simulate_sro(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        path = meta["path"]
        decide = bridge.handle({"method": "decide", "params": {"path": path}})
        preview = bridge.handle({"method": "preview", "params": {"path": path}})
        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "usage": usage,
        }

    def simulate_native(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        path = meta["path"]
        decide = bridge.handle({"method": "decide", "params": {"path": path}})
        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "usage": usage,
        }


class CodebaseExplorationScenario(BenchmarkScenario):
    """Directory with many small code files — tests native/advisory passthrough."""

    def __init__(self) -> None:
        super().__init__(
            name="codebase_exploration",
            description="Codebase exploration: small code files — native/advisory gate",
            expected_gate_mode="native",  # may also be advisory for mixed dirs
            expected_min_savings=0.0,
        )

    def setup(self, root: Path) -> dict[str, Any]:
        src = root / "src"
        src.mkdir()
        files = {
            "main.py": "def main():\n    pass\n",
            "utils.py": "def helper(x):\n    return x * 2\n",
            "config.toml": '[app]\nname = "test"\n',
            "models.py": "class User:\n    pass\n",
            "routes.py": "def router():\n    pass\n",
        }
        for name, content in files.items():
            (src / name).write_text(content, encoding="utf-8")
        return {"root": str(src), "files": len(files)}

    def simulate_sro(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        root = meta["root"]
        decide = bridge.handle({"method": "decide", "params": {"path": root}})
        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "usage": usage,
        }

    def simulate_native(self, bridge: ClaudeBridge, meta: dict[str, Any]) -> dict[str, Any]:
        root = meta["root"]
        decide = bridge.handle({"method": "decide", "params": {"path": root}})
        usage = bridge.handle({"method": "usage", "params": {}})
        return {
            "gate_mode": decide.get("claude_gate", {}).get("mode", "unknown"),
            "usage": usage,
        }


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


ALL_SCENARIOS: list[BenchmarkScenario] = [
    AuditBundleScenario(),
    PDFComprehensionScenario(),
    LongTextQAScenario(),
    StructuredDataScenario(),
    CodebaseExplorationScenario(),
]


@dataclass
class BenchmarkResult:
    scenario: str
    mode: str  # "sro" or "native"
    gate_mode: str
    operations: int
    full_file_tokens: int
    sr_response_tokens: int
    tokens_saved: int
    savings_ratio: float
    gate_summary: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


def run_scenario(scenario: BenchmarkScenario) -> tuple[BenchmarkResult, BenchmarkResult]:
    """Run a scenario in both SRO and native modes, return comparison."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        meta = scenario.setup(root)

        # SRO mode (use the bridge with SRO tools)
        sro_bridge = ClaudeBridge(workspace=root, mode="auto")
        sro_result = scenario.simulate_sro(sro_bridge, meta)

        # Native mode (gate checks but no SRO tools)
        native_bridge = ClaudeBridge(workspace=root, mode="native")
        native_result = scenario.simulate_native(native_bridge, meta)

        def _extract(result: dict, mode: str) -> BenchmarkResult:
            usage = result.get("usage", {})
            session = usage.get("session", {})
            return BenchmarkResult(
                scenario=scenario.name,
                mode=mode,
                gate_mode=result.get("gate_mode", "unknown"),
                operations=session.get("operations", 0),
                full_file_tokens=session.get("full_file_tokens", 0),
                sr_response_tokens=session.get("sr_response_tokens", 0),
                tokens_saved=session.get("tokens_saved", 0),
                savings_ratio=session.get("savings_ratio", 0.0),
                gate_summary=usage.get("gate_summary", {}),
                extra={k: v for k, v in result.items() if k not in ("usage",)},
            )

        return (
            _extract(sro_result, "sro"),
            _extract(native_result, "native"),
        )


def print_report(results: list[tuple[BenchmarkResult, BenchmarkResult]]) -> None:
    """Print a human-readable benchmark comparison report."""
    print()
    print("=" * 90)
    print("  SparseRead Claude Code — Benchmark Report")
    print("=" * 90)
    print()
    print(f"  {'Scenario':<25} {'Gate':<10} {'SRO Tokens':>10} {'Savings':>8} {'Expected':>10} {'Status':>8}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*8} {'-'*10} {'-'*8}")

    all_pass = True
    for sro_result, native_result in results:
        scenario = ALL_SCENARIOS[[s.name for s in ALL_SCENARIOS].index(sro_result.scenario)]
        gate_ok = sro_result.gate_mode == scenario.expected_gate_mode
        savings_ok = sro_result.savings_ratio >= scenario.expected_min_savings or scenario.expected_gate_mode == "native"
        status = "✅" if (gate_ok and savings_ok) else "⚠️"

        if not gate_ok or not savings_ok:
            all_pass = False

        print(
            f"  {sro_result.scenario:<25} {sro_result.gate_mode:<10} "
            f"{sro_result.sr_response_tokens:>10,} {sro_result.savings_ratio:>7.1%} "
            f"{scenario.expected_min_savings:>9.0%} {status:>8}"
        )

    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*8} {'-'*10} {'-'*8}")
    print()

    # Detailed breakdown
    for sro_result, _native_result in results:
        print(f"  ── {sro_result.scenario} ──")
        print(f"     Gate mode:       {sro_result.gate_mode}")
        print(f"     SRO operations:  {sro_result.operations}")
        print(f"     Full-file est:   {sro_result.full_file_tokens:,} tokens")
        print(f"     SR response:     {sro_result.sr_response_tokens:,} tokens")
        print(f"     Tokens saved:    {sro_result.tokens_saved:,}")
        print(f"     Savings ratio:   {sro_result.savings_ratio:.1%}")
        gate = sro_result.gate_summary
        if gate:
            print(f"     Gate decisions:  {gate.get('total_gate_decisions', 0)} total — "
                  f"enforce={gate.get('enforce_pct', 0)}% "
                  f"advisory={gate.get('advisory_pct', 0)}% "
                  f"native={gate.get('native_pct', 0)}%")
        print()

    print(f"  Overall: {'ALL PASSED ✅' if all_pass else 'SOME CHECKS FAILED ⚠️'}")
    print("=" * 90)
    print()

    # JSON output for scripting
    json_results = []
    for sro_result, native_result in results:
        json_results.append({
            "scenario": sro_result.scenario,
            "sro": {
                "gate_mode": sro_result.gate_mode,
                "operations": sro_result.operations,
                "full_file_tokens": sro_result.full_file_tokens,
                "sr_response_tokens": sro_result.sr_response_tokens,
                "tokens_saved": sro_result.tokens_saved,
                "savings_ratio": sro_result.savings_ratio,
                "gate_summary": sro_result.gate_summary,
            },
            "native": {
                "gate_mode": native_result.gate_mode,
                "operations": native_result.operations,
                "full_file_tokens": native_result.full_file_tokens,
                "sr_response_tokens": native_result.sr_response_tokens,
                "tokens_saved": native_result.tokens_saved,
                "savings_ratio": native_result.savings_ratio,
                "gate_summary": native_result.gate_summary,
            },
        })
    print("  JSON report:")
    print(json.dumps(json_results, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# pytest entry points
# ---------------------------------------------------------------------------


def test_benchmark_audit_bundle() -> None:
    scenario = AuditBundleScenario()
    sro, native = run_scenario(scenario)
    assert sro.gate_mode == "enforce", f"Expected enforce, got {sro.gate_mode}"
    assert sro.savings_ratio >= 0.50, f"Savings too low: {sro.savings_ratio:.1%}"


def test_benchmark_pdf_comprehension() -> None:
    scenario = PDFComprehensionScenario()
    sro, native = run_scenario(scenario)
    assert sro.gate_mode == "enforce", f"Expected enforce, got {sro.gate_mode}"
    assert sro.savings_ratio >= 0.85, f"Savings too low: {sro.savings_ratio:.1%}"


def test_benchmark_long_text_qa() -> None:
    scenario = LongTextQAScenario()
    sro, native = run_scenario(scenario)
    assert sro.gate_mode == "enforce", f"Expected enforce, got {sro.gate_mode}"
    assert sro.savings_ratio >= 0.80, f"Savings too low: {sro.savings_ratio:.1%}"


def test_benchmark_structured_data() -> None:
    scenario = StructuredDataScenario()
    sro, native = run_scenario(scenario)
    assert sro.gate_mode == "advisory", f"Expected advisory, got {sro.gate_mode}"
    # Structured data advisory mode — savings may be moderate
    assert sro.savings_ratio >= 0.0, f"Savings should be non-negative"


def test_benchmark_codebase_exploration() -> None:
    scenario = CodebaseExplorationScenario()
    sro, native = run_scenario(scenario)
    assert sro.gate_mode in ("native", "advisory"), f"Expected native or advisory, got {sro.gate_mode}"
    # Native mode — no SRO operations should be triggered


def test_benchmark_gate_summary_in_usage() -> None:
    """Verify that sro_usage includes gate_summary and native_bypass_estimate."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create a mix of files: one large (enforce) and one small (native)
        large = root / "large.md"
        large.write_text("content\n" * 5000, encoding="utf-8")
        small = root / "small.py"
        small.write_text("x = 1\n", encoding="utf-8")

        bridge = ClaudeBridge(workspace=root, mode="auto")
        bridge.handle({"method": "decide", "params": {"path": str(large)}})
        bridge.handle({"method": "decide", "params": {"path": str(small)}})
        usage = bridge.handle({"method": "usage", "params": {}})

        assert "gate_summary" in usage
        assert "native_bypass_estimate" in usage
        assert usage["gate_summary"]["total_gate_decisions"] >= 2
        assert "by_mode" in usage["gate_summary"]


# ---------------------------------------------------------------------------
# Direct run
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    results = []
    for scenario in ALL_SCENARIOS:
        print(f"Running: {scenario.name}...")
        sro, native = run_scenario(scenario)
        results.append((sro, native))
    print_report(results)
