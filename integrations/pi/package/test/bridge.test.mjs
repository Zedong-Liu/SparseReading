import assert from "node:assert/strict"
import { EventEmitter } from "node:events"
import { Writable } from "node:stream"
import test from "node:test"
import { BridgeClient } from "../dist/bridge.js"

class FakeChild extends EventEmitter {
  requests = []
  stdout = new EventEmitter()
  stderr = new EventEmitter()
  killed = false
  input = ""

  constructor(handler) {
    super()
    this.handler = handler
    this.stdin = new Writable({
      write: (chunk, _encoding, callback) => {
        this.consume(String(chunk))
        callback()
      },
    })
  }

  consume(chunk) {
    this.input += chunk
    while (true) {
      const newline = this.input.indexOf("\n")
      if (newline < 0) return
      const line = this.input.slice(0, newline).trim()
      this.input = this.input.slice(newline + 1)
      if (!line) continue
      const request = JSON.parse(line)
      this.requests.push(request)
      this.handler(request, this)
    }
  }

  respond(request, result, ok = true, error = undefined) {
    this.stdout.emit("data", Buffer.from(JSON.stringify({ id: request.id, ok, result, error }) + "\n"))
  }

  kill() {
    this.killed = true
    queueMicrotask(() => this.emit("exit", null, "SIGTERM"))
    return true
  }
}

function fakeSpawner(handler) {
  const children = []
  const launches = []
  const spawnProcess = (command, args, options) => {
    const child = new FakeChild(handler)
    children.push(child)
    launches.push({ command, args, options })
    return child
  }
  return { children, launches, spawnProcess }
}

const runtime = {
  python: "/runtime/bin/python",
  workspace: "/workspace/project",
  mode: "auto",
  protocol: "1.0",
}

test("starts on first request, checks the Pi protocol, and unwraps JSONL results", async () => {
  const fake = fakeSpawner((request, child) => {
    if (request.method === "version") child.respond(request, { protocol_version: "1.0", platform: "Pi" })
    else child.respond(request, { method: request.method, params: request.params })
  })
  const bridge = new BridgeClient(runtime, { spawnProcess: fake.spawnProcess })

  assert.equal(fake.children.length, 0)
  const result = await bridge.request("preview", { path: "report.pdf" })

  assert.deepEqual(result, { method: "preview", params: { path: "report.pdf" } })
  assert.deepEqual(fake.children[0].requests.map(({ method }) => method), ["version", "preview"])
  assert.deepEqual(fake.launches[0], {
    command: runtime.python,
    args: ["-m", "sparseread_agent_tools.bridge", "--host", "pi", "--workspace", runtime.workspace, "--mode", "auto"],
    options: { cwd: runtime.workspace, stdio: ["pipe", "pipe", "pipe"] },
  })
  await bridge.shutdown()
})

test("preserves Chinese and emoji when JSONL chunks split inside UTF-8 code points", async () => {
  const expected = { content: "中文证据：负责人李明 🚀" }
  const fake = fakeSpawner((request, child) => {
    if (request.method === "version") child.respond(request, { protocol_version: "1.0", platform: "Pi" })
    else if (request.method === "shutdown") queueMicrotask(() => child.emit("exit", 0, null))
    else {
      const bytes = Buffer.from(JSON.stringify({ id: request.id, ok: true, result: expected }) + "\n")
      for (const byte of bytes) child.stdout.emit("data", Buffer.from([byte]))
    }
  })
  const bridge = new BridgeClient(runtime, { spawnProcess: fake.spawnProcess })
  try {
    assert.deepEqual(await bridge.request("preview", { path: "报告.md" }), expected)
  } finally {
    await bridge.shutdown()
  }
})

test("a broken stdin pipe rejects pending work without crashing the Pi host", async () => {
  const fake = fakeSpawner((request, child) => {
    if (request.method === "version") child.respond(request, { protocol_version: "1.0", platform: "Pi" })
    else queueMicrotask(() => child.stdin.emit("error", new Error("EPIPE: broken bridge pipe")))
  })
  const bridge = new BridgeClient(runtime, { spawnProcess: fake.spawnProcess, timeoutMs: 50 })
  await assert.rejects(bridge.request("preview", { path: "report.pdf" }), /EPIPE/)
  assert.equal(fake.children[0].killed, true)
  await bridge.shutdown()
})

test("rejects a version or platform mismatch and closes that bridge", async () => {
  for (const version of [
    { protocol_version: "2.0", platform: "Pi" },
    { protocol_version: "1.0", platform: "Codex" },
  ]) {
    const fake = fakeSpawner((request, child) => {
      if (request.method === "version") child.respond(request, version)
    })
    const bridge = new BridgeClient(runtime, { spawnProcess: fake.spawnProcess })
    await assert.rejects(bridge.request("trace"), /mismatch/)
    assert.equal(fake.children[0].killed, true)
  }
})

test("abort drops that request while the JSONL process remains usable", async () => {
  const fake = fakeSpawner((request, child) => {
    if (request.method === "version") child.respond(request, { protocol_version: "1.0", platform: "Pi" })
    else if (request.method === "read") setTimeout(() => child.respond(request, { late: true }), 30)
    else child.respond(request, { method: request.method })
  })
  const bridge = new BridgeClient(runtime, { spawnProcess: fake.spawnProcess })
  const controller = new AbortController()
  const aborted = bridge.request("read", {}, controller.signal)
  controller.abort()

  await assert.rejects(aborted, { name: "AbortError" })
  assert.deepEqual(await bridge.request("preview"), { method: "preview" })
  await new Promise((resolve) => setTimeout(resolve, 40))
  assert.equal(fake.children.length, 1)
  await bridge.shutdown()
})

test("times out a stuck bridge, then starts a fresh process for the next request", async () => {
  const fake = fakeSpawner((request, child) => {
    if (request.method === "version") child.respond(request, { protocol_version: "1.0", platform: "Pi" })
    else if (request.method !== "hang") child.respond(request, { method: request.method })
  })
  const bridge = new BridgeClient(runtime, { spawnProcess: fake.spawnProcess, timeoutMs: 20 })

  await assert.rejects(bridge.request("hang"), /timed out/)
  assert.equal(fake.children[0].killed, true)
  assert.deepEqual(await bridge.request("trace"), { method: "trace" })
  assert.equal(fake.children.length, 2)
  await bridge.shutdown()
})

test("shutdown sends the protocol cleanup request and is safe to repeat", async () => {
  const fake = fakeSpawner((request, child) => {
    if (request.method === "version") child.respond(request, { protocol_version: "1.0", platform: "Pi" })
    else if (request.method === "shutdown") queueMicrotask(() => child.emit("exit", 0, null))
    else child.respond(request, {})
  })
  const bridge = new BridgeClient(runtime, { spawnProcess: fake.spawnProcess })
  await bridge.request("trace")

  await bridge.shutdown()
  await bridge.shutdown()

  assert.equal(fake.children[0].requests.at(-1).method, "shutdown")
  assert.equal(fake.children[0].stdin.writableEnded, true)
})
