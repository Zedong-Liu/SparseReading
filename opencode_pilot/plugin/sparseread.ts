import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
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
  phase: "before" | "after" | "block" | "nudge" | "rewrite"
  tool: string
  args: Record<string, Json>
  truncated?: boolean
  outputChars?: number
  reason?: string
}

type HandoffTarget = {
  absolutePath: string
  relativePath: string
  reason: string
}

type SparseReadPluginOptions = {
  policy?: "auto" | "native" | "observe" | "advisory" | "nudge" | "enforce" | "block" | "replace_truncation_experimental"
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
const debugLogPath = process.env.SPARSEREAD_DEBUG_LOG

function now() {
  return Math.round(Date.now() / 1000)
}

function debugLog(event: Record<string, Json>) {
  if (!debugLogPath) return
  try {
    appendFileSync(debugLogPath, JSON.stringify({ time: Date.now(), ...event }) + "\n", "utf8")
  } catch {
    // keep diagnostics best-effort only
  }
}

function isObject(value: unknown): value is Record<string, Json> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function normalizePolicy(value: unknown) {
  if (value === "replace_truncation_experimental" || value === "block") return "enforce"
  if (value === "nudge") return "advisory"
  if (value === "auto" || value === "native" || value === "enforce" || value === "advisory" || value === "observe") return value
  return "observe"
}

function allowsNativeBlocking(policy: ReturnType<typeof normalizePolicy>) {
  return policy === "auto" || policy === "enforce"
}

function allowsNudge(policy: ReturnType<typeof normalizePolicy>) {
  return policy === "auto" || policy === "advisory" || policy === "enforce"
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

function looksLikeRawDump(command: string) {
  return /\b(cat|less|more|head|tail|pdftotext|grep|rg|ripgrep)\b/.test(command)
    || /\b(read_csv|read_excel)\b/.test(command)
    || /\bopen\s*\(/.test(command)
    || /Path\([^)]*\)\.read_text\s*\(/.test(command)
}

function looksLikeRawCopy(command: string) {
  return /\b(cp|install|rsync)\b/.test(command)
}

function commandPaths(command: string): string[] {
  const suffix = String.raw`(?:pdf|txt|md|csv|tsv|xlsx|json|yaml|yml|xml|log|py|sh)`
  const quoted = new RegExp(String.raw`["']([^"']+\.${suffix})["']`, "gi")
  const bare = new RegExp(String.raw`(?:^|\s)([./A-Za-z0-9_\-][^\s'"|;&<>]+\.${suffix})(?=\s|$)`, "gi")
  const out = new Set<string>()
  for (const match of command.matchAll(quoted)) out.add(match[1])
  for (const match of command.matchAll(bare)) out.add(match[1])
  return Array.from(out)
}

function previewToolCall(candidate: string) {
  return `the sro_preview tool with {"path": ${JSON.stringify(candidate)}}`
}

function bridgeCommandPrefix(raw: string | string[] | undefined, python: string): string[] {
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

function windowsShellShim(command: string): boolean {
  return process.platform === "win32" && /\.(cmd|bat)$/i.test(command)
}

function spawnCommand(prefix: string[]): [string, string[]] {
  const [command, ...args] = prefix
  if (windowsShellShim(command)) {
    return [process.env.COMSPEC || "cmd.exe", ["/d", "/s", "/c", [command, ...args].map(windowsQuote).join(" ")]]
  }
  return [command, args]
}

function windowsQuote(value: string): string {
  if (!/[\s"]/u.test(value)) return value
  return `"${value.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/g, "$1$1")}"`
}

function shellQuote(value: string): string {
  if (process.platform === "win32") return windowsQuote(value)
  return `'${value.replace(/'/g, `'\"'\"'`)}'`
}

function readWorkspaceConfig(workspaceRoot: string): SparseReadPluginOptions | undefined {
  const configPath = path.join(workspaceRoot, ".opencode", "sparseread.json")
  try {
    const payload = JSON.parse(readFileSync(configPath, "utf8"))
    if (!isObject(payload)) throw new Error("config must be a JSON object")
    return payload as SparseReadPluginOptions
  } catch (error: any) {
    if (error?.code === "ENOENT") return undefined
    throw new Error(
      `Invalid SparseRead OpenCode config at ${configPath}: ${error instanceof Error ? error.message : String(error)}`,
    )
  }
}

function parentChain(start: string): string[] {
  const roots: string[] = []
  let current = path.resolve(start)
  while (true) {
    roots.push(current)
    const parent = path.dirname(current)
    if (parent === current) return roots
    current = parent
  }
}

function resolveWorkspaceRoot(worktree: string, directory: string): string {
  const explicit = process.env.SPARSEREAD_WORKSPACE_ROOT?.trim()
  if (explicit) return path.resolve(explicit)
  const seen = new Set<string>()
  const candidates = [directory, worktree, process.cwd()]
  for (const candidate of candidates) {
    if (!candidate) continue
    for (const current of parentChain(candidate)) {
      if (seen.has(current)) continue
      seen.add(current)
      if (existsSync(path.join(current, ".opencode", "sparseread.json"))) return current
    }
  }
  return worktree || process.cwd() || directory
}

export const SparseReadOpenCodePlugin: Plugin = async ({ directory, worktree }, options?: SparseReadPluginOptions) => {
  const workspaceRoot = resolveWorkspaceRoot(worktree, directory)
  debugLog({ event: "workspace_root", directory, worktree, workspaceRoot })
  const installed = readWorkspaceConfig(workspaceRoot)
  const policy = normalizePolicy(options?.policy ?? installed?.policy ?? process.env.SPARSEREAD_POLICY)
  const projectRoot = options?.projectRoot ?? installed?.projectRoot ?? process.env.SPARSEREAD_PROJECT_ROOT ?? process.cwd()
  const python = options?.python ?? installed?.python ?? process.env.SPARSEREAD_PYTHON ?? "python3"
  const bridgeModule = options?.bridgeModule ?? installed?.bridgeModule ?? "sparseread.bridge.opencode"
  const mode = options?.mode ?? installed?.mode ?? process.env.SPARSEREAD_MODE ?? "auto"
  const bridgeCommandRaw = options?.bridgeCommand ?? installed?.bridgeCommand ?? process.env.SPARSEREAD_BRIDGE_COMMAND
  const commandPrefix = bridgeCommandPrefix(bridgeCommandRaw, python)
  const targetSchema = tool.schema
    .object({
      path: tool.schema.string().optional().describe("Absolute or workspace-relative path for the first SparseRead step"),
      artifact_id: tool.schema.string().optional().describe("Artifact id returned by sro_preview for follow-up reads"),
    })
    .refine((value) => Boolean(value.path || value.artifact_id), {
      message: "target requires path or artifact_id",
    })
  const hintSchema = tool.schema
    .object({
      goal: tool.schema.string().optional().describe("One-sentence reading goal"),
      needles: tool.schema.array(tool.schema.string()).optional().describe("1-6 short keywords or phrases"),
      want: tool.schema.enum(["fact", "count", "verbatim", "table", "schema", "list"]).optional(),
      scope: tool.schema.enum(["new", "narrow", "expand", "verify"]).optional(),
      artifact: tool.schema.string().optional().describe("Artifact id when refine/verify continues a prior read"),
      type_hint: tool.schema
        .enum(["auto", "pdf", "text", "csv", "xlsx", "json", "yaml", "xml", "mixed", "collection"])
        .optional(),
      must_keep: tool.schema.array(tool.schema.string()).optional(),
      slots: tool.schema
        .array(
          tool.schema.object({
            id: tool.schema.string().describe("Stable slot id"),
            question: tool.schema.string().describe("Question or fact to extract"),
          }),
        )
        .optional(),
    })
    .passthrough()
  const [bridgeCommand, bridgeArgsPrefix] = spawnCommand(commandPrefix)
  const bridge = new SparseReadBridge(
    bridgeCommand,
    [
      ...bridgeArgsPrefix,
      "-m",
      bridgeModule,
      "--workspace",
      workspaceRoot,
      "--mode",
      mode,
    ],
    projectRoot,
  )
  let pendingTargets: HandoffTarget[] = []
  const readySessions = new Map<string, string>()
  const sessionsWithWrites = new Set<string>()
  const sessionsWithSro = new Set<string>()

  function readyStatus(value: Json): string {
    if (Array.isArray(value)) {
      for (const item of value) {
        const status = readyStatus(item)
        if (status) return status
      }
      return ""
    }
    if (!isObject(value)) return ""
    if (["ready", "ready_for_write", "ready_for_compute"].includes(String(value.overall_status || ""))) {
      return String(value.overall_status)
    }
    for (const item of Object.values(value)) {
      const status = readyStatus(item)
      if (status) return status
    }
    return ""
  }

  function rememberReady(context: any, result: Json) {
    const status = readyStatus(result)
    if (status && context?.sessionID) readySessions.set(context.sessionID, status)
  }

  function readyGuard(context: any, method: string): ToolResult | undefined {
    const status = context?.sessionID ? readySessions.get(context.sessionID) : undefined
    if (!status) return undefined
    return {
      title: `sro_${method}`,
      output: JSON.stringify({
        sro_guard: true,
        overall_status: status,
        allowed_next: ["write/edit deliverable", "run one required local calculation", "final response"],
        instruction: "SparseRead evidence is already terminal for this session. Do not call any sro_* tool again.",
      }),
      metadata: { sparseread: true, method, terminal: true },
    }
  }

  function lateSroGuard(context: any): ToolResult | undefined {
    const sessionID = context?.sessionID
    if (!sessionID || !sessionsWithWrites.has(sessionID) || sessionsWithSro.has(sessionID)) return undefined
    readySessions.set(sessionID, "deliverable_write_started")
    return readyGuard(context, "preview")
  }

  function reminderRoot() {
    return path.join(workspaceRoot, ".opencode", ".sparseread", "reminders")
  }

  function absoluteCandidate(candidatePath: string) {
    return path.resolve(workspaceRoot, candidatePath)
  }

  function normalizeTarget(target: Json): Json {
    if (typeof target === "string") {
      return target.startsWith("sro_") ? target : absoluteCandidate(target)
    }
    if (!isObject(target) || typeof target.path !== "string") return target
    return { ...target, path: absoluteCandidate(target.path) }
  }

  function isSameOrDescendant(base: string, candidate: string) {
    const relative = path.relative(base, candidate)
    return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))
  }

  function handoffForCandidate(candidatePath: string | undefined) {
    if (!candidatePath) return undefined
    const absolute = absoluteCandidate(candidatePath)
    let ancestorMatch: HandoffTarget | undefined
    let descendantMatch: HandoffTarget | undefined
    for (const target of pendingTargets) {
      if (target.absolutePath === absolute) return target
      if (!ancestorMatch && isSameOrDescendant(absolute, target.absolutePath)) ancestorMatch = target
      if (!descendantMatch && isSameOrDescendant(target.absolutePath, absolute)) descendantMatch = target
    }
    return descendantMatch || ancestorMatch
  }

  function reminderText(target: string, action: string, reason: string) {
    const relativeTarget = path.isAbsolute(target) ? path.relative(workspaceRoot, target) || "." : target
    const displayTarget = relativeTarget && !relativeTarget.startsWith("..") ? relativeTarget : target
    return [
      "SparseRead enforce:",
      `- first action: call ${previewToolCall(displayTarget)}`,
      `- target: ${displayTarget}`,
      "- do not run sro_preview inside bash or shell",
      `- after preview, use one targeted sro_read if needed, then write`,
      `- blocked native ${action}: high-confidence SparseRead target`,
      "",
    ].join("\n")
  }

  function reminderArtifact(target: string, action: string, reason: string) {
    const root = reminderRoot()
    mkdirSync(root, { recursive: true })
    const key = Buffer.from(`${target}\n${action}\n${reason}`, "utf8").toString("hex").slice(0, 32)
    const reminderPath = path.join(root, `${key}.txt`)
    writeFileSync(reminderPath, reminderText(target, action, reason), "utf8")
    const program = "process.stdout.write(require('fs').readFileSync(process.argv[1], 'utf8'))"
    const command = `node -e ${shellQuote(program)} ${shellQuote(reminderPath)}`
    return { reminderPath, command }
  }

  function rewriteReadArgs(args: Record<string, Json>, reminderPath: string) {
    args.filePath = reminderPath
    args.path = reminderPath
    return args
  }

  function rewriteGrepArgs(args: Record<string, Json>, reminderPath: string) {
    args.path = reminderPath
    args.pattern = "SparseRead|sro_preview|target"
    delete args.include
    return args
  }

  function rewriteCommandArgs(args: Record<string, Json>, command: string) {
    args.command = command
    args.cmd = command
    return args
  }

  function rewriteNativeTool(args: Record<string, Json>, target: string, action: string, reason: string, toolName: string) {
    const artifact = reminderArtifact(target, action, reason)
    if (toolName === "read") return rewriteReadArgs(args, artifact.reminderPath)
    if (toolName === "grep") return rewriteGrepArgs(args, artifact.reminderPath)
    return rewriteCommandArgs(args, artifact.command)
  }

  function loadPreflightTargets(result: any) {
    const handoffs = Array.isArray(result?.handoffs) ? result.handoffs.filter(isObject) : []
    pendingTargets = handoffs
      .map((item: Record<string, Json>) => {
        const relativePath = String(item.relative_path || item.path || "").trim()
        if (!relativePath) return undefined
        return {
          absolutePath: absoluteCandidate(relativePath),
          relativePath,
          reason: String(item.reason || "SparseRead high-confidence handoff"),
        }
      })
      .filter(Boolean) as HandoffTarget[]
    debugLog({ event: "preflight", targets: pendingTargets })
    return pendingTargets
  }

  async function refreshPreflightTargets() {
    try {
      return loadPreflightTargets(await bridge.request("preflight", { max_candidates: 24, max_results: 3 }))
    } catch (error) {
      pendingTargets = []
      debugLog({ event: "preflight_error", error: error instanceof Error ? error.message : String(error) })
      return pendingTargets
    }
  }

  async function decideForPath(candidatePath: string | undefined) {
    if (!candidatePath) return undefined
    const absolute = path.isAbsolute(candidatePath) ? candidatePath : path.join(workspaceRoot, candidatePath)
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

  function sparseReadBlockReason(candidate: string | undefined, action: string) {
    const target = candidate || "this source"
    return `SparseRead enforce: call ${previewToolCall(target)} first. Do not run sro_preview inside bash or shell. After preview, call sro_read with the returned artifact_id only if targeted evidence is needed instead of native ${action}.`
  }

  function preflightPrompt(result: any) {
    const handoffs = loadPreflightTargets(result)
    if (handoffs.length === 0) return ""
    const first = handoffs[0]
    const firstPath = String(first.relativePath || "").trim()
    if (!firstPath) return ""
    if (handoffs.length === 1) {
      return ` High-confidence evidence target detected: ${previewToolCall(firstPath)} before broad read, grep, or bash inspection.`
    }
    const targets = handoffs
      .map((item: HandoffTarget) => String(item.relativePath || "").trim())
      .filter(Boolean)
      .slice(0, 3)
      .map((item: string) => previewToolCall(item))
      .join("; ")
    if (!targets) return ""
    return ` High-confidence evidence targets detected: start with one of these tool calls: ${targets}.`
  }

  async function maybeNudge(toolName: string, args: Record<string, Json>, output: any) {
    if (!allowsNudge(policy)) return
    if (!isObject(output) || typeof output.output !== "string") return
    const truncated = outputTruncated(output)
    const isDirectoryRead =
      toolName === "read" && isObject(output.metadata) && output.metadata.display?.type === "directory"
    const candidate = readPath(args) || (typeof args.path === "string" ? args.path : undefined)
    const { decision, gate } = await gateForPath(candidate)
    const shouldNudge =
      truncated ||
      gate?.nudge_native === true ||
      (allowsNativeBlocking(policy) && gate?.block_native_read === true) ||
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
          target: targetSchema.optional().describe("Use {path} or {artifact_id}; path shortcut is also accepted"),
          path: tool.schema.string().optional().describe("Path to preview"),
          artifact_id: tool.schema.string().optional().describe("Existing SparseRead artifact id to preview"),
        },
        async execute(args, context): Promise<ToolResult> {
          const terminal = readyGuard(context, "preview")
          if (terminal) return terminal
          const late = lateSroGuard(context)
          if (late) return late
          if (context?.sessionID) sessionsWithSro.add(context.sessionID)
          const result = await bridge.request("preview", {
            ...args,
            ...(typeof args.path === "string" ? { path: absoluteCandidate(args.path) } : {}),
            ...(args.target ? { target: normalizeTarget(args.target) } : {}),
          })
          rememberReady(context, result)
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
        async execute(args, context): Promise<ToolResult> {
          const terminal = readyGuard(context, "raw")
          if (terminal) return terminal
          const result = await bridge.request("raw", args)
          rememberReady(context, result)
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
        async execute(args, context): Promise<ToolResult> {
          const terminal = readyGuard(context, "card")
          if (terminal) return terminal
          const result = await bridge.request("card", { path: absoluteCandidate(args.path) })
          rememberReady(context, result)
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
          target: targetSchema.describe("Use {path} for first read or {artifact_id} for follow-up"),
          mode: tool.schema.enum(["scout", "focus", "collect", "refine", "verify"]),
          hint: hintSchema.describe("HintSpec with goal, needles, slots, want, scope, type_hint"),
        },
        async execute(args, context): Promise<ToolResult> {
          const terminal = readyGuard(context, "read")
          if (terminal) return terminal
          const result = await bridge.request("read", { ...args, target: normalizeTarget(args.target) })
          rememberReady(context, result)
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
        "\nSparseRead: for large supported evidence, call the tool sro_preview with JSON args {\"path\": ...}; do not run sro_preview inside bash or shell. Use sro_read only for targeted evidence. Small setup reads and native code/data work are fine."
    },
    "experimental.chat.system.transform": async (_input, output) => {
      if (policy === "native") return
      const preflight = preflightPrompt(await bridge.request("preflight", { max_candidates: 24, max_results: 3 }).catch(() => ({})))
      output.system.push(
        "SparseRead is available for long documents, PDFs, and compact evidence closures. When SparseRead is the right path, call the tool sro_preview with JSON args {\"path\": ...}. Do not run sro_preview inside bash or shell. After preview, call sro_read only for targeted evidence. Once any SRO response reports overall_status ready, ready_for_write, or ready_for_compute, do not call any sro_* tool again; write the deliverable or run the single required calculation immediately." + preflight,
      )
    },
    event: async ({ event }) => {
      if (event.type !== "session.deleted") return
      const properties = event.properties as any
      const sessionID = properties?.sessionID ?? properties?.info?.id
      if (typeof sessionID === "string") {
        readySessions.delete(sessionID)
        sessionsWithWrites.delete(sessionID)
        sessionsWithSro.delete(sessionID)
      }
    },
    "tool.execute.before": async (input, output) => {
      if (!shouldInspectTool(input.tool)) return
      const args = isObject(output.args) ? output.args : {}
      nativeEvents.push({ time: now(), phase: "before", tool: input.tool, args })
      debugLog({ event: "before", tool: input.tool, args })
      if (!allowsNativeBlocking(policy)) return
      if (pendingTargets.length === 0) await refreshPreflightTargets()
      if (input.tool === "read") {
        const candidate = readPath(args)
        const { gate } = await gateForPath(candidate)
        const handoff = handoffForCandidate(candidate)
        const target = typeof gate?.handoff_path === "string" ? gate.handoff_path : handoff?.relativePath || candidate
        if (gate?.block_native_read !== true && !handoff) return
        const reason = String(gate?.reason || handoff?.reason || "SparseRead high-confidence handoff")
        nativeEvents.push({
          time: now(),
          phase: "rewrite",
          tool: input.tool,
          args,
          reason,
        })
        rewriteNativeTool(args, target || "this source", "read", reason, input.tool)
        debugLog({ event: "rewrite", tool: input.tool, candidate, target, args, reason })
        return
      }
      if (input.tool === "grep") {
        const candidate = typeof args.path === "string" ? args.path : readPath(args)
        const { gate } = await gateForPath(candidate)
        const handoff = handoffForCandidate(candidate)
        const target = typeof gate?.handoff_path === "string" ? gate.handoff_path : handoff?.relativePath || candidate
        if (gate?.block_native_search !== true && !handoff) return
        const reason = String(gate?.reason || handoff?.reason || "SparseRead high-confidence grep handoff")
        nativeEvents.push({
          time: now(),
          phase: "rewrite",
          tool: input.tool,
          args,
          reason,
        })
        rewriteNativeTool(args, target || "this source", "grep", reason, input.tool)
        debugLog({ event: "rewrite", tool: input.tool, candidate, target, args, reason })
        return
      }
      const command = String(args.command ?? args.cmd ?? "")
      const rawDump = looksLikeRawDump(command)
      const rawCopy = looksLikeRawCopy(command)
      if (!rawDump && !rawCopy) return
      for (const candidate of commandPaths(command)) {
        const { gate } = await gateForPath(candidate)
        const handoff = handoffForCandidate(candidate)
        const target = typeof gate?.handoff_path === "string" ? gate.handoff_path : handoff?.relativePath || candidate
        if (gate?.block_native_exec_dump !== true && !handoff) continue
        const reason = String(gate?.reason || handoff?.reason || "SparseRead high-confidence exec handoff")
        nativeEvents.push({
          time: now(),
          phase: "rewrite",
          tool: input.tool,
          args,
          reason,
        })
        rewriteNativeTool(args, target || "this source", rawCopy ? "copy" : "dump", reason, input.tool)
        debugLog({ event: "rewrite", tool: input.tool, candidate, target, args, reason })
        return
      }
    },
    "tool.execute.after": async (input, output) => {
      if (["write", "edit", "patch", "apply_patch"].includes(input.tool)) {
        sessionsWithWrites.add(input.sessionID)
      }
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
