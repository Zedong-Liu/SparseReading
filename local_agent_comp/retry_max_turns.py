"""Re-run tasks that hit max-turns=12 with max-turns=20."""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO / "SRO_test/qwenclawbench/baseline"
AGG_PATH = REPO / "SRO_test/qwenclawbench/claude_sro_bench_results/aggregate.json"

RETRY = [
    "task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix",
    "task_00098_diagnose_scheduled_book_recommendation_failure",
    "task_00058_did_regression_on_simulated_panel_data",
    "task_spreadsheetbench_verified_49333_trimmed_vlookup",
]

API_KEY = "sk-bb54c365c4414c7a87c121f200ede632"
API_BASE = "https://api.deepseek.com/anthropic"
MODEL = "DeepSeek-V4-Flash"

agg = json.loads(open(AGG_PATH, encoding="utf-8").read())

for task_id in RETRY:
    src = BASELINE_DIR / task_id / "runtime"
    tasks_dir = src / "tasks"
    md_files = list(tasks_dir.glob("*.md")) if tasks_dir.exists() else []
    if not md_files:
        continue
    content = md_files[0].read_text(encoding="utf-8", errors="replace")
    prompt = content.split("## Prompt", 1)[1].split("## Expected", 1)[0].strip()

    ws = Path(tempfile.mkdtemp(prefix="retry_"))
    assets_dir = src / "assets"
    if assets_dir.exists():
        for item in assets_dir.iterdir():
            dest = ws / item.name
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=False)
            else:
                shutil.copy2(item, dest)

    (ws.parent / "settings.json").write_text("{}", encoding="utf-8")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "ANTHROPIC_MODEL": MODEL, "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODEL,
           "ANTHROPIC_AUTH_TOKEN": API_KEY, "ANTHROPIC_BASE_URL": API_BASE}

    print(f"{task_id}: running...", end=" ", flush=True)
    start = time.time()
    proc = subprocess.run(
        ["claude", "-p", prompt, "--max-turns", "20",
         "--dangerously-skip-permissions", "--add-dir", str(ws),
         "--settings", str(ws.parent / "settings.json"),
         "--output-format", "text"],
        capture_output=True, timeout=600, cwd=str(ws), env=env)
    elapsed = time.time() - start
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")

    # Grading
    grading_code = ""
    parts = content.split("```python")
    if len(parts) > 1:
        grading_code = parts[1].split("```", 1)[0]

    transcript = [
        {"type": "message", "message": {"role": "user",
         "content": [{"type": "text", "text": prompt}]}},
        {"type": "message", "message": {"role": "assistant",
         "content": [{"type": "text", "text": stdout}]}},
    ]

    score = 0.0
    if grading_code:
        try:
            ns = {}
            exec(grading_code, ns)
            gf = ns.get("grade")
            if gf:
                s = gf(transcript, str(ws))
                if isinstance(s, dict):
                    vals = [float(v) for v in s.values() if v > 0]
                    score = sum(s.values()) / max(len(s), 1) if s else 0.0
        except Exception as e:
            print(f"grade_err={e}", end=" ")

    hit_limit = len(stdout) < 80 and "Reached max turns" in stdout
    ok = proc.returncode == 0 and len(stdout) > 60 and not hit_limit
    print(f"score={score:.4f} [{elapsed:.0f}s] "
          f"{'OK' if ok else 'MAX_TURNS' if hit_limit else 'FAIL'}")

    # Update aggregate
    for r in agg:
        if r["task_id"] == task_id:
            r["score"] = round(score, 4)
            r["elapsed_s"] = elapsed
            r["success"] = ok
            r["error"] = "" if ok else ("max turns" if hit_limit else "error")
            break

    shutil.rmtree(ws.parent, ignore_errors=True)

agg_path = str(AGG_PATH)
open(agg_path, "w", encoding="utf-8").write(
    json.dumps(agg, indent=2, ensure_ascii=False))
print("Aggregate updated.")

# Print final summary
print("\n=== FINAL RESULTS ===")
cats = {}
for r in agg:
    c = r["category"]
    if c not in cats:
        cats[c] = {"scores": [], "passed": 0, "total": 0}
    cats[c]["scores"].append(r["score"])
    cats[c]["total"] += 1
    if r["success"]:
        cats[c]["passed"] += 1

for cat in ["long-context", "audit", "structured", "native-fit"]:
    if cat not in cats:
        continue
    d = cats[cat]
    avg = sum(d["scores"]) / d["total"]
    print(f"{cat:20s} {d['total']:>3d} tasks  passed={d['passed']}  avg={avg:.4f}")

all_s = [r["score"] for r in agg]
print(f"{'OVERALL':20s} {len(all_s):>3d} tasks  passed={sum(1 for r in agg if r['success'])}  avg={sum(all_s)/len(all_s):.4f}")
