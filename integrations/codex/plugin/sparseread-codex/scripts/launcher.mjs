import { spawn, spawnSync } from "node:child_process"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const pluginRoot = path.resolve(scriptDirectory, "..")
const runtimePath = path.join(pluginRoot, ".sparseread-runtime.json")

function loadRuntime() {
  const runtime = JSON.parse(readFileSync(runtimePath, "utf8"))
  if (
    typeof runtime.python !== "string" || !path.isAbsolute(runtime.python) ||
    typeof runtime.workspace !== "string" || !path.isAbsolute(runtime.workspace) ||
    !["auto", "advisory"].includes(runtime.mode) || runtime.protocol !== "1.0"
  ) {
    throw new Error("invalid .sparseread-runtime.json; reinstall SparseRead for this workspace")
  }
  return runtime
}

function failOpen() {
  process.stdout.write("{}\n")
}

function sessionStart() {
  try {
    loadRuntime()
  } catch {
    failOpen()
    return
  }
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext:
        "SparseRead provides sro_decide, sro_preview, sro_read, sro_raw, sro_card, and sro_trace. For large inputs, preview first and read only the evidence needed.",
    },
  }) + "\n")
}

function runHook() {
  let runtime
  let input
  try {
    runtime = loadRuntime()
    input = readFileSync(0, "utf8")
  } catch {
    failOpen()
    return
  }
  const result = spawnSync(
    runtime.python,
    ["-m", "sparseread_agent_tools.codex_hook", "--workspace", runtime.workspace, "--mode", runtime.mode],
    {
      cwd: runtime.workspace,
      env: process.env,
      input,
      encoding: "utf8",
      maxBuffer: 1024 * 1024,
      timeout: 3000,
      windowsHide: true,
    },
  )
  if (result.error || result.status !== 0) {
    failOpen()
    return
  }
  if (result.stdout) process.stdout.write(result.stdout)
}

function runMcp() {
  let runtime
  try {
    runtime = loadRuntime()
  } catch (error) {
    process.stderr.write(`SparseRead MCP startup failed: ${error.message}\n`)
    process.exitCode = 1
    return
  }
  const child = spawn(
    runtime.python,
    ["-m", "sparseread_agent_tools.mcp", "--host", "codex", "--workspace", runtime.workspace, "--mode", runtime.mode],
    {
      cwd: runtime.workspace,
      env: process.env,
      stdio: "inherit",
      windowsHide: true,
    },
  )
  const forwardSignal = (signal) => {
    if (!child.killed) {
      try {
        child.kill(signal)
      } catch {
        child.kill()
      }
    }
  }
  process.on("SIGINT", () => forwardSignal("SIGINT"))
  process.on("SIGTERM", () => forwardSignal("SIGTERM"))
  child.on("error", (error) => {
    process.stderr.write(`SparseRead MCP startup failed: ${error.message}\n`)
    process.exitCode = 1
  })
  child.on("close", (code, signal) => {
    if (signal) process.exitCode = 1
    else process.exitCode = code ?? 1
  })
}

const command = process.argv[2]
if (command === "mcp") runMcp()
else if (command === "--hook") runHook()
else if (command === "--session-start") sessionStart()
else {
  process.stderr.write("usage: launcher.mjs mcp|--hook|--session-start\n")
  process.exitCode = 2
}
