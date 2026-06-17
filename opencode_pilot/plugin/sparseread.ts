import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
import path from "node:path"
import { tool, type Plugin, type ToolResult } from "@opencode-ai/plugin"

type Json = any

type BridgeRequest = {
  id: string
  method: string
  params?: Record<string, Json>
}

type BridgeResponse = {
  id: string
  ok: boolean
  result?: Json
  error?: string
}

type NativeEvent = {
  time: number
  phase: "before" | "after" | "block" | "nudge"
  tool: string
  args: Record<string, Json>
  truncated?: boolean
  outputChars?: number
  reason?: string
}

type SparseReadPluginOptions = {
  policy?: "observe" | "advisory" | "nudge" | "enforce" | "block" | "replace_truncation_experimental"
  python?: string
  bridgeCommand?: string | string[]
  projectRoot?: string
  bridgeModule?: string
  mode?: "auto" | "bench_protocol" | "force" | "force_sro" | "native" | "advisory"
}

class SparseReadBridge {
  private process?: ChildProcessWithoutNullStreams
  private nextID = 1
  private buffer = ""
  private pending = new Map<string, { resolve: (value: Json) => void; reject: (error: Error) => void }>()

  constructor(
    private readonly command: string,
    private readonly args: string[],
    private readonly cwd: string,
  ) {}

  request(method: string, params: Record<string, Json> = {}): Promise<Json> {
    this.ensure()
    const id = String(this.nextID++)
    const payload: BridgeRequest = { id, method, params }
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.process!.stdin.write(JSON.stringify(payload) + "\n")
    })
  }

  shutdown() {
    if (!this.process) return
    try {
      this.process.stdin.write(JSON.stringify({ id: String(this.nextID++), method: "shutdown", params: {} }) + "\n")
    } catch {
      // ignore shutdown races
    }
    this.process.kill()
    this.process = undefined
  }

  private ensure() {
    if (this.process) return
    this.process = spawn(this.command, this.args, {
      cwd: this.cwd,
      env: { ...process.env },
      stdio: ["pipe", "pipe", "pipe"],
    })
    this.process.stdout.on("data", (chunk) => this.onData(String(chunk)))
    this.process.stderr.on("data", (chunk) => {
      const text = String(chunk).trim()
      if (text) console.error(`[sparseread bridge] ${text}`)
    })
    this.process.on("exit", (code, signal) => {
      const error = new Error(`SparseRead bridge exited code=${code ?? ""} signal=${signal ?? ""}`.trim())
      for (const pending of this.pending.values()) pending.reject(error)
      this.pending.clear()
      this.process = undefined
    })
  }

  private onData(text: string) {
    this.buffer += text
    while (true) {
      const index = this.buffer.indexOf("\n")
      if (index < 0) return
      const line = this.buffer.slice(0, index).trim()
      this.buffer = this.buffer.slice(index + 1)
      if (!line) continue
      let response: BridgeResponse
      try {
        response = JSON.parse(line)
      } catch (error) {
        console.error(`[sparseread bridge] invalid json: ${line}`)
        continue
      }
      const pending = this.pending.get(response.id)
      if (!pending) continue
      this.pending.delete(response.id)
      if (response.ok) pending.resolve(response.result)
      else pending.reject(new Error(response.error || "SparseRead bridge error"))
    }
  }
}

const nativeEvents: NativeEvent[] = []

function now() {
  return Math.round(Date.now() / 1000)
}

function isObject(value: unknown): value is Record<string, Json> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function normalizePolicy(value: unknown) {
  if (value === "replace_truncation_experimental" || value === "block") return "enforce"
  if (value === "nudge") return "advisory"
  if (value === "enforce" || value === "advisory" || value === "observe") return value
  return "observe"
}

function outputText(output: any): string {
  if (typeof output === "string") return output
  if (isObject(output) && typeof output.output === "string") return output.output
  return ""
}

function outputTruncated(output: any): boolean {
  if (!isObject(output)) return false
  if (output.metadata && typeof output.metadata.truncated === "boolean") return output.metadata.truncated
  return /Output capped|Results truncated|Showing .* of .* entries|Use offset=|output truncated|Full output saved/i.test(outputText(output))
}

function readPath(args: Record<string, Json>) {
  return typeof args.filePath === "string" ? args.filePath : typeof args.path === "string" ? args.path : undefined
}

function shouldInspectTool(toolName: string) {
  return toolName === "read" || toolName === "grep" || toolName === "bash" || toolName === "shell"
}

function bridgeCommandPrefix(options: SparseReadPluginOptions | undefined, python: string): string[] {
  const raw = options?.bridgeCommand || process.env.SPARSEREAD_BRIDGE_COMMAND
  if (Array.isArray(raw)) return raw
  if (typeof raw !== "string" || raw.trim() === "") return [python]
  const trimmed = raw.trim()
  if (trimmed.startsWith("[")) {
    const parsed = JSON.parse(trimmed)
    if (!Array.isArray(parsed) || parsed.some((part) => typeof part !== "string")) {
      throw new Error("SPARSEREAD_BRIDGE_COMMAND must be a JSON string array")
    }
    return parsed
  }
  return trimmed.split(/\s+/)
}

export const SparseReadOpenCodePlugin: Plugin = async ({ directory, worktree }, options?: SparseReadPluginOptions) => {
  const policy = normalizePolicy(options?.policy || process.env.SPARSEREAD_POLICY)
  const projectRoot = options?.projectRoot || process.env.SPARSEREAD_PROJECT_ROOT || process.cwd()
  const python = options?.python || process.env.SPARSEREAD_PYTHON || "python3"
  const bridgeModule = options?.bridgeModule || "sparseread.bridge.opencode"
  const commandPrefix = bridgeCommandPrefix(options, python)
  const [bridgeCommand, ...bridgeArgsPrefix] = commandPrefix
  const bridge = new SparseReadBridge(
    bridgeCommand,
    [
      ...bridgeArgsPrefix,
      "-m",
      bridgeModule,
      "--workspace",
      worktree || directory,
      "--mode",
      options?.mode || process.env.SPARSEREAD_MODE || "auto",
    ],
    projectRoot,
  )

  async function decideForPath(candidatePath: string | undefined) {
    if (!candidatePath) return undefined
    const absolute = path.isAbsolute(candidatePath) ? candidatePath : path.join(directory, candidatePath)
    try {
      return await bridge.request("decide", { path: absolute })
    } catch (error) {
      nativeEvents.push({
        time: now(),
        phase: "after",
        tool: "sro_decide",
        args: { path: absolute },
        reason: error instanceof Error ? error.message : String(error),
      })
      return undefined
    }
  }

  async function gateForPath(candidatePath: string | undefined) {
    const decision = await decideForPath(candidatePath)
    return { decision, gate: decision?.opencode_gate || {} }
  }

  async function maybeNudge(toolName: string, args: Record<string, Json>, output: any) {
    if (policy !== "advisory" && policy !== "enforce") return
    if (!isObject(output) || typeof output.output !== "string") return
    const truncated = outputTruncated(output)
    const isDirectoryRead =
      toolName === "read" && isObject(output.metadata) && output.metadata.display?.type === "directory"
    const candidate = readPath(args) || (typeof args.path === "string" ? args.path : undefined)
    const { decision, gate } = await gateForPath(candidate)
    const shouldNudge =
      truncated ||
      gate?.nudge_native === true ||
      (policy === "enforce" && gate?.block_native_read === true) ||
      (toolName === "grep" && gate?.mode === "advisory" && decision?.type === "collection") ||
      (isDirectoryRead && decision?.large === true)
    if (!shouldNudge) return
    const nudgeMessage = isDirectoryRead
      ? "SparseRead can preview this directory as a collection evidence bundle. Use sro_preview on this directory instead of listing individual files."
      : "SparseRead may be a better fit for this large or truncated evidence source. Use sro_preview first; call sro_read only if targeted evidence is needed."
    output.output +=
      "\n\n<system-reminder>\n" + nudgeMessage + "\n</system-reminder>"
    nativeEvents.push({
      time: now(),
      phase: "nudge",
      tool: toolName,
      args,
      truncated,
      reason: gate?.reason || "sparse_read_hint",
    })
  }

  return {
    tool: {
      sro_preview: tool({
        description: "Production SparseRead entrypoint. Return a no-HintSpec preview with embedded card metadata, samples, signals, raw_ref, and next-step guidance.",
        args: {
          target: tool.schema.object({}).passthrough().optional().describe("Use {path} or {artifact_id}; path shortcut is also accepted"),
          path: tool.schema.string().optional().describe("Path to preview"),
          artifact_id: tool.schema.string().optional().describe("Existing SparseRead artifact id to preview"),
        },
        async execute(args): Promise<ToolResult> {
          const result = await bridge.request("preview", args)
          return {
            title: "sro_preview",
            output: JSON.stringify(result),
            metadata: { sparseread: true, method: "preview" },
          }
        },
      }),
      sro_raw: tool({
        description: "Retrieve original content behind a raw_ref returned by sro_preview. Use only when preview is insufficient.",
        args: {
          raw_ref: tool.schema.string().describe("raw_ref returned by sro_preview"),
          range: tool.schema.object({}).passthrough().optional().describe("Optional byte range: {start,end}"),
          selector: tool.schema.string().optional().describe("Optional case-insensitive line selector"),
        },
        async execute(args): Promise<ToolResult> {
          const result = await bridge.request("raw", args)
          return {
            title: "sro_raw",
            output: JSON.stringify(result),
            metadata: { sparseread: true, method: "raw" },
          }
        },
      }),
      sro_card: tool({
        description: "Compatibility/debug tool. Return a SparseRead FileCard; production flows should start with sro_preview.",
        args: {
          path: tool.schema.string().describe("Path to the file, document, or directory to inspect"),
        },
        async execute(args): Promise<ToolResult> {
          const result = await bridge.request("card", { path: args.path })
          return {
            title: "sro_card",
            output: JSON.stringify(result),
            metadata: { sparseread: true, method: "card" },
          }
        },
      }),
      sro_read: tool({
        description:
          "Read sparse evidence using mode scout/focus/collect/refine/verify. If the result is ready, write the requested deliverable instead of rereading the source.",
        args: {
          target: tool.schema.object({}).passthrough().describe("Use {path} for first read or {artifact_id} for follow-up"),
          mode: tool.schema.enum(["scout", "focus", "collect", "refine", "verify"]),
          hint: tool.schema.object({}).passthrough().describe("HintSpec with goal, needles, slots, want, scope, type_hint"),
        },
        async execute(args): Promise<ToolResult> {
          const result = await bridge.request("read", args)
          return {
            title: "sro_read",
            output: JSON.stringify(result),
            metadata: { sparseread: true, method: "read" },
          }
        },
      }),
      sro_trace: tool({
        description: "Return SparseRead and native OpenCode read/grep/bash trace for this session.",
        args: {},
        async execute(): Promise<ToolResult> {
          const trace = await bridge.request("trace", {})
          return {
            title: "sro_trace",
            output: JSON.stringify({ ...trace, native_events: nativeEvents }),
            metadata: { sparseread: true, method: "trace" },
          }
        },
      }),
    },
    "tool.definition": async (input, output) => {
      if (input.toolID !== "read" && input.toolID !== "grep" && input.toolID !== "bash" && input.toolID !== "shell") return
      output.description +=
        "\nSparseRead: large supported evidence should start with sro_preview; use sro_read only for targeted evidence. Small setup reads and native code/data work are fine."
    },
    "tool.execute.before": async (input, output) => {
      if (!shouldInspectTool(input.tool)) return
      const args = isObject(output.args) ? output.args : {}
      nativeEvents.push({ time: now(), phase: "before", tool: input.tool, args })
      if (policy !== "enforce") return
      if (input.tool !== "read") return
      const candidate = readPath(args)
      const { gate } = await gateForPath(candidate)
      if (gate?.block_native_read !== true) return
      nativeEvents.push({
        time: now(),
        phase: "block",
        tool: input.tool,
        args,
        reason: gate?.reason || "SparseRead high-confidence handoff",
      })
      throw new Error(
        `SparseRead enforce: use sro_preview(path=${candidate}) first; call sro_read with the returned artifact_id only if targeted evidence is needed.`,
      )
    },
    "tool.execute.after": async (input, output) => {
      if (!shouldInspectTool(input.tool)) return
      const args = isObject(input.args) ? input.args : {}
      const text = outputText(output)
      const truncated = outputTruncated(output)
      nativeEvents.push({
        time: now(),
        phase: "after",
        tool: input.tool,
        args,
        truncated,
        outputChars: text.length,
      })
      await maybeNudge(input.tool, args, output)
    },
    dispose: async () => {
      bridge.shutdown()
    },
  }
}

export default SparseReadOpenCodePlugin
