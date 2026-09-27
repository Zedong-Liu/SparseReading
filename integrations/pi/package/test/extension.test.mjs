import assert from "node:assert/strict"
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import { execFileSync } from "node:child_process"
import os from "node:os"
import path from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { discoverAndLoadExtensions } from "@earendil-works/pi-coding-agent"
import { createSparseReadPiExtension } from "../dist/index.js"

const toolNames = ["sro_preview", "sro_read", "sro_raw", "sro_card", "sro_decide", "sro_trace"]
const runtime = {
  python: "/runtime/bin/python",
  workspace: "/workspace/project",
  mode: "auto",
  protocol: "1.0",
}

class FakePi {
  tools = new Map()
  handlers = new Map()

  registerTool(definition) {
    this.tools.set(definition.name, definition)
  }

  on(event, handler) {
    const handlers = this.handlers.get(event) ?? []
    handlers.push(handler)
    this.handlers.set(event, handlers)
    return () => {}
  }

  async emit(event, payload = {}, context = { signal: undefined }) {
    const results = []
    for (const handler of this.handlers.get(event) ?? []) results.push(await handler(payload, context))
    return results
  }
}

function setup({ mode = "auto", response, requestError, runtimeError, noRuntime = false, packageRoot } = {}) {
  const pi = new FakePi()
  const bridges = []
  let factoryCalls = 0
  const options = {
    runtime: runtimeError || noRuntime ? undefined : { ...runtime, mode },
    runtimeError,
    packageRoot,
    bridgeFactory: () => {
      factoryCalls += 1
      const bridge = {
        calls: [],
        shutdownCount: 0,
        async request(method, params, signal) {
          this.calls.push({ method, params, signal })
          if (requestError) throw requestError
          return response ? response(method, params) : { method, params }
        },
        async shutdown() {
          this.shutdownCount += 1
        },
      }
      bridges.push(bridge)
      return bridge
    },
  }
  createSparseReadPiExtension(pi, options)
  return { pi, bridges, get factoryCalls() { return factoryCalls } }
}

function readEvent(pathname, input = {}) {
  return { type: "tool_call", toolName: "read", toolCallId: "read-call", input: { path: pathname, ...input } }
}

function copySourcePackage(packageRoot) {
  cpSync(path.resolve("src"), path.join(packageRoot, "src"), { recursive: true })
  cpSync(path.resolve("package.json"), path.join(packageRoot, "package.json"))
  cpSync(path.resolve("README.md"), path.join(packageRoot, "README.md"))
}

test("registers and forwards all six tools with per-session request context", async () => {
  const testSetup = setup()
  const { pi, bridges } = testSetup
  assert.deepEqual([...pi.tools.keys()], toolNames)
  assert.equal(testSetup.factoryCalls, 0)

  const calls = [
    ["sro_preview", { path: "report.pdf" }, "preview"],
    ["sro_read", { target: { path: "report.pdf" }, mode: "scout", hint: { goal: "find the total" } }, "read"],
    ["sro_raw", { raw_ref: "raw-1" }, "raw"],
    ["sro_card", { path: "report.pdf" }, "card"],
    ["sro_decide", { path: "report.pdf" }, "decide"],
    ["sro_trace", {}, "trace"],
  ]
  const context = {}
  for (const [name, params] of calls) {
    const result = await pi.tools.get(name).execute(`${name}-call`, params, undefined, undefined, context)
    assert.equal(JSON.parse(result.content[0].text).method, name.replace("sro_", ""))
  }

  assert.equal(testSetup.factoryCalls, 1)
  assert.deepEqual(bridges[0].calls.map(({ method }) => method), calls.map(([, , method]) => method))
  assert.equal(bridges[0].calls[0].params.context.conversation_id.length > 0, true)
  assert.equal(bridges[0].calls[0].params.context.tool_call_id, "sro_preview-call")
})

test("passes bounded reads and redirects a force read only once per canonical path", async () => {
  const { pi, bridges } = setup({
    response: () => ({ host_gate: { block_native_read: true } }),
  })
  const handler = pi.handlers.get("tool_call")[0]

  assert.equal(await handler(readEvent("report.pdf", { limit: 30 }), { signal: undefined }), undefined)
  assert.equal(bridges.length, 0)

  const first = await handler(readEvent("report.pdf"), { signal: undefined })
  assert.equal(first.block, true)
  assert.match(first.reason, /explicitly retry this same read natively/)
  const retry = await handler(readEvent(path.join(runtime.workspace, "report.pdf")), { signal: undefined })
  assert.equal(retry, undefined)
  assert.equal(bridges[0].calls.filter(({ method }) => method === "decide").length, 1)
})

test("native read paths follow the host cwd when started inside a project subdirectory", async () => {
  const { pi, bridges } = setup({ response: () => ({ host_gate: { block_native_read: true } }) })
  const context = { cwd: path.join(runtime.workspace, "reports"), signal: undefined }
  const result = await pi.handlers.get("tool_call")[0](readEvent("incident.md"), context)
  assert.equal(result.block, true)
  assert.equal(bridges[0].calls[0].params.path, path.join(context.cwd, "incident.md"))
  assert.match(result.reason, /reports/)
})

test("an explicit line offset keeps a targeted native read on the native path", async () => {
  const { pi, bridges } = setup({ response: () => ({ host_gate: { block_native_read: true } }) })
  const result = await pi.handlers.get("tool_call")[0](readEvent("incident.md", { offset: 500 }), {})
  assert.equal(result, undefined)
  assert.equal(bridges.length, 0)
})

test("reserves a redirect before awaiting decide so concurrent retries pass through", async () => {
  let releaseDecision
  const { pi, bridges } = setup({
    response: () => new Promise((resolve) => { releaseDecision = () => resolve({ host_gate: { block_native_read: true } }) }),
  })
  const handler = pi.handlers.get("tool_call")[0]
  const firstPromise = handler(readEvent("report.pdf"), { signal: undefined })
  await new Promise((resolve) => setImmediate(resolve))
  const concurrentRetry = await handler(readEvent("report.pdf"), { signal: undefined })
  assert.equal(concurrentRetry, undefined)
  releaseDecision()
  assert.equal((await firstPromise).block, true)
  assert.equal(bridges[0].calls.length, 1)
})

test("advisory mode never checks or blocks native reads", async () => {
  const testSetup = setup({
    mode: "advisory",
    response: () => ({ host_gate: { block_native_read: true } }),
  })
  const { pi, bridges } = testSetup
  const result = await pi.handlers.get("tool_call")[0](readEvent("report.pdf"), { signal: undefined })
  assert.equal(result, undefined)
  assert.equal(testSetup.factoryCalls, 0)
  assert.equal(bridges.length, 0)
})

test("core error payloads surface as failed tools, not successful evidence", async () => {
  const { pi } = setup({ response: () => ({ raw: { error: "unknown or stale raw_ref; call sro_preview again" } }) })
  await assert.rejects(
    pi.tools.get("sro_raw").execute("stale-call", { raw_ref: "previous-session-ref" }, undefined, undefined, {}),
    /stale raw_ref.*native tools/,
  )
})

test("bridge errors fail open and release the one-time redirect reservation", async () => {
  const { pi, bridges } = setup({ requestError: new Error("outside configured workspace") })
  const handler = pi.handlers.get("tool_call")[0]
  const originalError = console.error
  console.error = () => {}
  try {
    assert.equal(await handler(readEvent("../outside.txt"), { signal: undefined }), undefined)
    assert.equal(await handler(readEvent("../outside.txt"), { signal: undefined }), undefined)
  } finally {
    console.error = originalError
  }
  assert.equal(bridges[0].calls.filter(({ method }) => method === "decide").length, 2)
})

test("redirects at most 512 unique candidates and clears the set on session reset", async () => {
  const { pi, bridges } = setup({
    response: () => ({ host_gate: { block_native_read: true } }),
  })
  const handler = pi.handlers.get("tool_call")[0]
  for (let index = 0; index < 512; index += 1) {
    assert.equal((await handler(readEvent(`file-${index}.pdf`), { signal: undefined })).block, true)
  }
  assert.equal(await handler(readEvent("overflow.pdf"), { signal: undefined }), undefined)
  assert.equal(bridges[0].calls.length, 512)

  await pi.emit("session_start", { type: "session_start", reason: "new" })
  assert.equal((await handler(readEvent("file-0.pdf"), { signal: undefined })).block, true)
  assert.equal(bridges.length, 2)
  assert.equal(bridges[0].shutdownCount, 1)
  assert.equal(bridges[1].calls[0].params.context.conversation_id === bridges[0].calls[0].params.context.conversation_id, false)
})

test("runtime-less package leaves native reads available and reports setup instructions", async () => {
  const packageRoot = mkdtempSync(path.join(os.tmpdir(), "sparseread-pi-no-runtime-"))
  const originalError = console.error
  console.error = () => {}
  try {
    const testSetup = setup({ packageRoot, noRuntime: true })
    const { pi } = testSetup
    const trace = pi.tools.get("sro_trace")
    await assert.rejects(trace.execute("trace-call", {}, undefined, undefined, {}), /install_sparseread.py --platform pi --workspace/)
    assert.equal(await pi.handlers.get("tool_call")[0](readEvent("report.pdf"), { signal: undefined }), undefined)
    assert.equal(testSetup.factoryCalls, 0)
  } finally {
    console.error = originalError
    rmSync(packageRoot, { recursive: true, force: true })
  }
})

test("Pi 0.87.1 install and loader register the source package without package node_modules", async () => {
  const cwd = mkdtempSync(path.join(os.tmpdir(), "sparseread-pi-loader-"))
  const packageRoot = path.join(cwd, ".sparseread", "pi", "sparseread-pi")
  const agentDir = path.join(cwd, "agent")
  const cliPath = path.resolve(
    path.dirname(fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent"))),
    "bundle/cli.js",
  )
  const originalError = console.error
  mkdirSync(packageRoot, { recursive: true })
  copySourcePackage(packageRoot)
  assert.equal(existsSync(path.join(packageRoot, "node_modules")), false)

  execFileSync(process.execPath, [cliPath, "install", "--local", packageRoot], { cwd, stdio: "pipe" })
  const settings = JSON.parse(readFileSync(path.join(cwd, ".pi", "settings.json"), "utf8"))
  assert.ok(settings.packages.some((source) => typeof source === "string" && source.includes("sparseread-pi")))

  console.error = () => {}
  try {
    const result = await discoverAndLoadExtensions([packageRoot], cwd, agentDir)
    assert.deepEqual(result.errors, [])
    assert.equal(result.extensions.length, 1)
    assert.deepEqual([...result.extensions[0].tools.keys()].sort(), [...toolNames].sort())
  } finally {
    console.error = originalError
    rmSync(cwd, { recursive: true, force: true })
  }
})
