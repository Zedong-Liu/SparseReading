import assert from "node:assert/strict"
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import test from "node:test"
import { discoverAndLoadExtensions } from "@earendil-works/pi-coding-agent"

const runtimeFile = process.env.SPARSEREAD_PI_RUNTIME_FILE
const smokePath = process.env.SPARSEREAD_PI_SMOKE_PATH ?? ".pi/settings.json"
const expectedTools = ["sro_preview", "sro_read", "sro_raw", "sro_card", "sro_decide", "sro_trace"]

function findRawRef(value) {
  if (!value || typeof value !== "object") return undefined
  if (typeof value.raw_ref === "string" && value.raw_ref) return value.raw_ref
  for (const child of Object.values(value)) {
    const found = findRawRef(child)
    if (found) return found
  }
  return undefined
}

test("installed source package loads in Pi and forwards preview/raw through the managed Python bridge", {
  skip: runtimeFile ? false : "set SPARSEREAD_PI_RUNTIME_FILE to a managed Pi package runtime file",
}, async () => {
  const runtime = JSON.parse(readFileSync(runtimeFile, "utf8"))
  const project = runtime.workspace
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "sparseread-pi-installed-smoke-"))
  const packageRoot = path.join(tempRoot, "sparseread-pi")
  const agentDir = path.join(tempRoot, "agent")
  const originalError = console.error
  try {
    cpSync(path.resolve("src"), path.join(packageRoot, "src"), { recursive: true })
    cpSync(path.resolve("package.json"), path.join(packageRoot, "package.json"))
    cpSync(path.resolve("README.md"), path.join(packageRoot, "README.md"))
    writeFileSync(path.join(packageRoot, ".sparseread-runtime.json"), JSON.stringify(runtime, null, 2))
    assert.equal(existsSync(path.join(packageRoot, "node_modules")), false)

    console.error = () => {}
    const loaded = await discoverAndLoadExtensions([packageRoot], project, agentDir)
    assert.deepEqual(loaded.errors, [])
    assert.equal(loaded.extensions.length, 1)
    const extension = loaded.extensions[0]
    assert.deepEqual([...extension.tools.keys()].sort(), [...expectedTools].sort())

    const previewResult = await extension.tools.get("sro_preview").definition.execute(
      "pi-smoke-preview",
      { path: smokePath },
      undefined,
      undefined,
      {},
    )
    const preview = JSON.parse(previewResult.content[0].text)
    const rawRef = findRawRef(preview)
    assert.ok(rawRef, "sro_preview should return a raw_ref for the smoke target")

    const rawResult = await extension.tools.get("sro_raw").definition.execute(
      "pi-smoke-raw",
      { raw_ref: rawRef },
      undefined,
      undefined,
      {},
    )
    const raw = JSON.parse(rawResult.content[0].text)
    assert.ok(Object.hasOwn(raw, "raw"), "sro_raw should return source content through the managed bridge")

    const shutdown = extension.handlers.get("session_shutdown")?.[0]
    if (shutdown) await shutdown({ type: "session_shutdown", reason: "quit" }, {})
  } finally {
    console.error = originalError
    rmSync(tempRoot, { recursive: true, force: true })
  }
})
