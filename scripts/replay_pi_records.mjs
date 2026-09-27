// Local-only RPC harness using the actual Pi source-extension loader, no model.
import { pathToFileURL } from "node:url"
import { createInterface } from "node:readline"
import path from "node:path"

const [workspace, sdkRoot, agentDir] = process.argv.slice(2)
const sdkEntry = path.resolve(sdkRoot, "node_modules/@earendil-works/pi-coding-agent/dist/index.js")
const { discoverAndLoadExtensions, createReadTool } = await import(pathToFileURL(sdkEntry).href)
const loaded = await discoverAndLoadExtensions([path.join(workspace, ".sparseread/pi/sparseread-pi")], workspace, agentDir)
if (loaded.errors.length || loaded.extensions.length !== 1) throw new Error("Pi extension failed to load")
const extension = loaded.extensions[0]
const context = { cwd: workspace }
const emit = async (name, event) => {
  const results = []
  for (const handler of extension.handlers.get(name) ?? []) results.push(await handler(event, context))
  return results.find((result) => result !== undefined) ?? null
}
try {
  for await (const line of createInterface({ input: process.stdin })) {
    let response
    try {
      const request = JSON.parse(line)
      if (request.method === "native_read") {
        response = await emit("tool_call", { type: "tool_call", toolName: "read", toolCallId: request.id, input: request.params })
      } else if (request.method === "native_execute") {
        const result = await createReadTool(workspace).execute(String(request.id), request.params)
        response = { content_chars: result.content.filter((item) => item.type === "text").reduce((n, item) => n + item.text.length, 0) }
      } else if (request.method === "reset") {
        await emit("session_start", { type: "session_start", reason: "new" })
        response = { ok: true }
      } else {
        const tool = extension.tools.get(request.method)
        if (!tool) throw new Error("unknown tool")
        const result = await tool.definition.execute(request.id, request.params, undefined, undefined, context)
        response = JSON.parse(result.content[0].text)
      }
      process.stdout.write(JSON.stringify({ result: response }) + "\n")
    } catch (error) {
      process.stdout.write(JSON.stringify({ error: String(error) }) + "\n")
    }
  }
} finally {
  await emit("session_shutdown", { type: "session_shutdown", reason: "quit" })
}
