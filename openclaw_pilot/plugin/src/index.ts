import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
import path from "node:path"
import { Type } from "typebox"
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry"

type Json = unknown
type JsonObject = Record<string, Json>

type BridgeRequest = {
  id: string
  method: string
  params?: JsonObject
}

type BridgeResponse = {
  id: string
  ok: boolean
  result?: Json
  error?: string
}

type SparseReadConfig = {
  policy: "observe" | "advisory" | "enforce" | "native" | "auto"
  python: string
  bridgeCommand?: string
  projectRoot: string
  workspaceRoot: string
  bridgeModule: string
  mode: "auto" | "bench_protocol" | "force" | "force_sro" | "native" | "advisory"
}

class SparseReadBridge {
  private process?: ChildProcessWithoutNullStreams
  private nextID = 1
  private buffer = ""
  private idleTimer?: NodeJS.Timeout
  private pending = new Map<string, { resolve: (value: Json) => void; reject: (error: Error) => void }>()

  constructor(
    private readonly command: string,
    private readonly args: string[],
    private readonly cwd: string,
  ) {}

  request(method: string, params: JsonObject = {}): Promise<JsonObject> {
    this.ensure()
    this.clearIdleTimer()
    const id = String(this.nextID++)
    const payload: BridgeRequest = { id, method, params }
    return new Promise((resolve, reject) => {
      this.pending.set(id, {
        resolve: (value) => {
          this.scheduleIdleShutdown()
          resolve(asObject(value))
        },
        reject,
      })
      this.process!.stdin.write(JSON.stringify(payload) + "\n")
    })
  }

  shutdown() {
    if (!this.process) return
    this.clearIdleTimer()
    try {
      this.process.stdin.write(JSON.stringify({ id: String(this.nextID++), method: "shutdown", params: {} }) + "\n")
    } catch {
      // Ignore shutdown races.
    }
    this.process.kill()
    this.process = undefined
  }

  private clearIdleTimer() {
    if (!this.idleTimer) return
    clearTimeout(this.idleTimer)
    this.idleTimer = undefined
  }

  private scheduleIdleShutdown() {
    this.clearIdleTimer()
    this.idleTimer = setTimeout(() => this.shutdown(), 20_000)
    this.idleTimer.unref?.()
  }

  private ensure() {
    if (this.process) return
    this.process = spawn(this.command, this.args, {
      cwd: this.cwd,
      env: { ...process.env },
      stdio: ["pipe", "pipe", "pipe"],
    })
    this.process.stdout.on("data", (chunk: Buffer) => this.onData(String(chunk)))
    this.process.stderr.on("data", (chunk: Buffer) => {
      const text = String(chunk).trim()
      if (text) console.error(`[sparseread bridge] ${text}`)
    })
    this.process.on("exit", (code: number | null, signal: NodeJS.Signals | null) => {
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
        response = JSON.parse(line) as BridgeResponse
      } catch {
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

const bridges = new Map<string, SparseReadBridge>()

function asObject(value: Json): JsonObject {
  return isObject(value) ? value : {}
}

function isObject(value: Json): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function config(raw: Json): SparseReadConfig {
  const obj = asObject(raw)
  return {
    policy: policy(obj.policy ?? process.env.SPARSEREAD_POLICY),
    python: stringValue(obj.python ?? process.env.SPARSEREAD_PYTHON, "python3"),
    bridgeCommand: stringValue(obj.bridgeCommand ?? process.env.SPARSEREAD_BRIDGE_COMMAND, ""),
    projectRoot: stringValue(obj.projectRoot ?? process.env.SPARSEREAD_PROJECT_ROOT, process.cwd()),
    workspaceRoot: stringValue(obj.workspaceRoot ?? process.env.SPARSEREAD_WORKSPACE_ROOT, ""),
    bridgeModule: stringValue(obj.bridgeModule ?? process.env.SPARSEREAD_BRIDGE_MODULE, "sparseread.bridge.openclaw"),
    mode: sparseMode(obj.mode ?? process.env.SPARSEREAD_MODE),
  }
}

function pluginConfig(ctx: Json, fallback: Json): SparseReadConfig {
  const obj = asObject(ctx)
  return config(obj.pluginConfig ?? fallback)
}

function policy(value: Json): SparseReadConfig["policy"] {
  if (value === "enforce" || value === "advisory" || value === "observe" || value === "native" || value === "auto") return value
  return "auto"
}

function sparseMode(value: Json): SparseReadConfig["mode"] {
  if (value === "bench_protocol") return value
  if (value === "force" || value === "force_sro" || value === "native" || value === "advisory") return value
  return "auto"
}

function stringValue(value: Json, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback
}

function splitCommand(raw: string, fallback: string): string[] {
  if (!raw) return [fallback]
  if (raw.trim().startsWith("[")) {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.some((part) => typeof part !== "string")) {
      throw new Error("SPARSEREAD_BRIDGE_COMMAND must be a JSON string array")
    }
    return parsed
  }
  return raw.trim().split(/\s+/)
}

function bridgeFor(ctx: Json, cfg: SparseReadConfig): SparseReadBridge {
  const workspace = workspaceOf(ctx, cfg)
  const key = `${workspace}:${cfg.bridgeModule}:${cfg.mode}`
  const existing = bridges.get(key)
  if (existing) return existing
  const prefix = splitCommand(cfg.bridgeCommand || "", cfg.python)
  const [command, ...argsPrefix] = prefix
  const bridge = new SparseReadBridge(
    command,
    [...argsPrefix, "-m", cfg.bridgeModule, "--workspace", workspace, "--mode", cfg.mode],
    cfg.projectRoot,
  )
  bridges.set(key, bridge)
  return bridge
}

function workspaceOf(ctx: Json, cfg?: SparseReadConfig): string {
  const obj = asObject(ctx)
  return stringValue(obj.workspaceDir ?? obj.workspace ?? obj.worktree ?? obj.cwd ?? cfg?.workspaceRoot ?? process.cwd(), process.cwd())
}

function runtimeContext(event: any, ctx: Json): JsonObject {
  return asObject(event?.context ?? ctx)
}

function toolText(result: JsonObject): string {
  return JSON.stringify(result)
}

function toolResult(result: JsonObject) {
  return {
    content: [{ type: "text", text: toolText(result) }],
    details: result,
  }
}

function paramsPath(params: JsonObject): string | undefined {
  for (const key of ["path", "filePath", "file_path", "target"]) {
    const value = params[key]
    if (typeof value === "string" && value.trim()) return value
  }
  return undefined
}

function absolutePath(candidate: string, ctx: Json, cfg?: SparseReadConfig): string {
  return path.isAbsolute(candidate) ? candidate : path.join(workspaceOf(ctx, cfg), candidate)
}

function normalizeTarget(target: Json, ctx: Json, cfg: SparseReadConfig): JsonObject {
  if (typeof target === "string" && target.trim()) {
    const value = target.trim()
    if (value.startsWith("{")) {
      try {
        return normalizeTarget(JSON.parse(value), ctx, cfg)
      } catch {
        // Fall through to path handling for non-JSON strings.
      }
    }
    if (value.startsWith("sro_")) return { artifact_id: value }
    return { path: absolutePath(value, ctx, cfg) }
  }
  const obj = asObject(target)
  if (typeof obj.path === "string" && obj.path.trim()) {
    return { ...obj, path: absolutePath(obj.path, ctx, cfg) }
  }
  return obj
}

function normalizeHint(hint: Json): JsonObject {
  let obj: JsonObject
  if (typeof hint === "string" && hint.trim()) {
    try {
      obj = asObject(JSON.parse(hint))
    } catch {
      obj = { goal: hint }
    }
  } else {
    obj = asObject(hint)
  }
  const slots = obj.slots
  const normalized: JsonObject = { ...obj }
  if (typeof normalized.scope === "string") {
    const scope = normalized.scope.trim().toLowerCase()
    if (scope === "entire" || scope === "full" || scope === "all") normalized.scope = "new"
  }
  if (typeof normalized.goal !== "string" || !normalized.goal.trim()) {
    const normalizedSlots = normalized.slots
    if (Array.isArray(normalizedSlots)) {
      const questions = normalizedSlots
        .map((slot) => (isObject(slot) ? String(slot.question ?? slot.id ?? "").trim() : ""))
        .filter(Boolean)
        .slice(0, 8)
      if (questions.length > 0) normalized.goal = `collect evidence for: ${questions.join("; ")}`.slice(0, 900)
    }
  }
  return normalized
}

function isBroadRead(params: JsonObject): boolean {
  const hasOffset = Number.isFinite(params.offset as number)
  const hasLimit = Number.isFinite(params.limit as number)
  const pages = typeof params.pages === "string" ? params.pages.trim() : ""
  if (hasLimit && Number(params.limit) <= 200) return false
  if (hasOffset && hasLimit) return false
  if (pages && /^[0-9]+(-[0-9]+)?$/.test(pages)) return false
  return true
}

function isBroadList(params: JsonObject): boolean {
  return params.recursive === true || Number(params.depth ?? 1) > 1 || params.path !== undefined
}

function looksLikeRawDump(command: string): boolean {
  return /\b(cat|less|more|head|tail|pdftotext|grep|rg|ripgrep)\b/.test(command)
    || /\b(read_csv|read_excel)\b/.test(command)
    || /\bopen\s*\(/.test(command)
    || /Path\([^)]*\)\.read_text\s*\(/.test(command)
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

function outputText(value: Json): string {
  if (typeof value === "string") return value
  if (!isObject(value)) return ""
  if (typeof value.output === "string") return value.output
  const content = value.content
  if (Array.isArray(content)) {
    return content
      .map((item) => (isObject(item) && typeof item.text === "string" ? item.text : ""))
      .filter(Boolean)
      .join("\n")
  }
  return ""
}

function outputTruncated(value: Json): boolean {
  if (!isObject(value)) return false
  const details = asObject(value.details)
  const metadata = asObject(value.metadata)
  if (details.truncated === true || metadata.truncated === true) return true
  return /Full output saved to:|Original size:|Output capped|Results truncated|Showing .* of .*|Use offset=|output truncated/i.test(outputText(value))
}

async function decideGate(bridge: SparseReadBridge, candidate: string | undefined, ctx: Json, cfg: SparseReadConfig): Promise<JsonObject | undefined> {
  if (!candidate) return undefined
  const result = await bridge.request("decide", { path: absolutePath(candidate, ctx, cfg) })
  return asObject(result.openclaw_gate)
}

async function recordNative(
  bridge: SparseReadBridge,
  phase: string,
  tool: string,
  params: JsonObject,
  output?: Json,
  reason = "",
) {
  await bridge.request("native_event", {
    phase,
    tool,
    params,
    truncated: output ? outputTruncated(output) : false,
    output_chars: output ? outputText(output).length : 0,
    reason,
  })
}

const sparseReadPlugin: any = definePluginEntry({
  id: "sparseread-openclaw",
  name: "SparseRead for OpenClaw",
  description: "Adds SparseRead tools and runtime-feature gate hooks.",
  register(api: any) {
    const registeredConfig = api.pluginConfig

    api.registerTool({
      name: "sro_preview",
      description: "Production SparseRead entrypoint. Return a no-HintSpec preview with embedded card metadata, samples, signals, raw_ref, and next-step guidance.",
      parameters: Type.Object({
        target: Type.Optional(Type.Record(Type.String(), Type.Unknown(), { description: "Use {path} or {artifact_id}." })),
        path: Type.Optional(Type.String({ description: "File, document, or directory path to preview." })),
        artifact_id: Type.Optional(Type.String({ description: "Existing SparseRead artifact id to preview." })),
      }),
      async execute(_id: string, params: JsonObject, ctx: Json) {
        const cfg = pluginConfig(ctx, registeredConfig)
        const normalizedParams = { ...params }
        if (typeof normalizedParams.path === "string") {
          normalizedParams.path = absolutePath(normalizedParams.path, ctx, cfg)
        }
        if (isObject(normalizedParams.target) && typeof normalizedParams.target.path === "string") {
          normalizedParams.target = normalizeTarget(normalizedParams.target, ctx, cfg)
        }
        const result = await bridgeFor(ctx, cfg).request("preview", normalizedParams)
        return toolResult(result)
      },
    })

    api.registerTool({
      name: "sro_raw",
      description: "Retrieve original content behind a raw_ref returned by sro_preview. Use only when preview is insufficient.",
      parameters: Type.Object({
        raw_ref: Type.String({ description: "raw_ref returned by sro_preview." }),
        range: Type.Optional(Type.Record(Type.String(), Type.Unknown(), { description: "Optional byte range: {start,end}." })),
        selector: Type.Optional(Type.String({ description: "Optional case-insensitive line selector." })),
      }),
      async execute(_id: string, params: JsonObject, ctx: Json) {
        const cfg = pluginConfig(ctx, registeredConfig)
        const result = await bridgeFor(ctx, cfg).request("raw", params)
        return toolResult(result)
      },
    })

    api.registerTool({
      name: "sro_card",
      description: "Compatibility/debug tool. Return a SparseRead FileCard and OpenClaw gate profile; production flows should start with sro_preview.",
      parameters: Type.Object({
        path: Type.String({ description: "File, document, or directory path to inspect." }),
      }),
      async execute(_id: string, params: JsonObject, ctx: Json) {
        const cfg = pluginConfig(ctx, registeredConfig)
        const result = await bridgeFor(ctx, cfg).request("card", { path: absolutePath(String(params.path), ctx, cfg) })
        return toolResult(result)
      },
    })

    api.registerTool({
      name: "sro_read",
      description: "Read sparse evidence with mode scout/focus/collect/refine/verify. hint.want must be one of fact/list/count/schema/table/verbatim. If collect returns ready, write the deliverable instead of rereading.",
      parameters: Type.Object({
        target: Type.Union([
          Type.Record(Type.String(), Type.Unknown(), { description: "Use {path} first or {artifact_id} for follow-up." }),
          Type.String({ description: "Compatibility shortcut: artifact_id such as sro_... or a path." }),
        ]),
        mode: Type.Union([
          Type.Literal("scout"),
          Type.Literal("focus"),
          Type.Literal("collect"),
          Type.Literal("refine"),
          Type.Literal("verify"),
        ]),
        hint: Type.Record(Type.String(), Type.Unknown(), { description: "HintSpec with goal, needles, slots, want, scope, type_hint." }),
      }),
      async execute(_id: string, params: JsonObject, ctx: Json) {
        const cfg = pluginConfig(ctx, registeredConfig)
        const result = await bridgeFor(ctx, cfg).request("read", {
          ...params,
          target: normalizeTarget(params.target, ctx, cfg),
          hint: normalizeHint(params.hint),
        })
        return toolResult(result)
      },
    })

    api.registerTool({
      name: "sro_decide",
      description: "Inspect a path and return the SparseRead core decision plus OpenClaw adapter gate.",
      parameters: Type.Object({
        path: Type.String({ description: "File, document, or directory path to inspect." }),
      }),
      async execute(_id: string, params: JsonObject, ctx: Json) {
        const cfg = pluginConfig(ctx, registeredConfig)
        const result = await bridgeFor(ctx, cfg).request("decide", { path: absolutePath(String(params.path), ctx, cfg) })
        return toolResult(result)
      },
    })

    api.registerTool({
      name: "sro_trace",
      description: "Return SparseRead, native tool, truncation, usage, ready-after-read, and gate trace for this session.",
      parameters: Type.Object({}),
      async execute(_id: string, _params: JsonObject, ctx: Json) {
        const cfg = pluginConfig(ctx, registeredConfig)
        const result = await bridgeFor(ctx, cfg).request("trace", {})
        return toolResult(result)
      },
    })

    api.on("before_tool_call", async (event: any, ctx: Json) => {
      const runCtx = runtimeContext(event, ctx)
      const cfg = pluginConfig(runCtx, registeredConfig)
      if (cfg.policy !== "enforce" && cfg.policy !== "auto") return
      const toolName = String(event?.toolName ?? "")
      const params = asObject(event?.params)
      const bridge = bridgeFor(runCtx, cfg)
      await recordNative(bridge, "before", toolName, params)

      if (toolName === "read" || toolName === "read_file") {
        const gate = await decideGate(bridge, paramsPath(params), runCtx, cfg)
        if (gate?.block_native_read !== true) return
        if (isBroadRead(params) || gate?.mode === "enforce") {
          const handoffPath = typeof gate.handoff_path === "string" ? gate.handoff_path : paramsPath(params)
          return {
            block: true,
            blockReason: `SparseRead enforce: use sro_preview(path=${handoffPath}) first; call sro_read with the returned artifact_id only if targeted evidence is needed.`,
          }
        }
      }

      if (toolName === "list" || toolName === "list_dir" || toolName === "dir_list") {
        if (!isBroadList(params)) return
        const gate = await decideGate(bridge, paramsPath(params), runCtx, cfg)
        if (gate?.block_native_read === true) {
          const handoffPath = typeof gate.handoff_path === "string" ? gate.handoff_path : paramsPath(params)
          return {
            block: true,
            blockReason: `SparseRead enforce: use sro_preview(path=${handoffPath}) for this evidence collection before broad listing.`,
          }
        }
      }

      if (toolName === "grep") {
        const gate = await decideGate(bridge, paramsPath(params), runCtx, cfg)
        if (gate?.block_native_search === true) {
          return {
            block: true,
            blockReason: `SparseRead enforce: use sro_preview first and sro_read only for targeted evidence instead of broad grep on this target.`,
          }
        }
      }

      if (toolName === "exec" || toolName === "bash" || toolName === "shell") {
        const command = String(params.command ?? params.cmd ?? "")
        if (!looksLikeRawDump(command)) return
        for (const candidate of commandPaths(command)) {
          const gate = await decideGate(bridge, candidate, runCtx, cfg)
          if (gate?.block_native_exec_dump === true) {
            return {
              block: true,
              blockReason: `SparseRead enforce: this command appears to dump ${candidate}. Use sro_preview first.`,
            }
          }
        }
      }
    }, { priority: 40, timeoutMs: 15000 })

    api.on("after_tool_call", async (event: any, ctx: Json) => {
      const runCtx = runtimeContext(event, ctx)
      const cfg = pluginConfig(runCtx, registeredConfig)
      const toolName = String(event?.toolName ?? event?.name ?? "")
      const params = asObject(event?.params)
      const result = event?.result ?? event?.toolResult ?? event?.output
      await recordNative(bridgeFor(runCtx, cfg), "after", toolName, params, result)
    }, { priority: 0, timeoutMs: 15000 })

    api.on("llm_output", async (event: any, ctx: Json) => {
      const runCtx = runtimeContext(event, ctx)
      const cfg = pluginConfig(runCtx, registeredConfig)
      const usage = event?.usage ?? event?.message?.usage
      if (!usage) return
      await bridgeFor(runCtx, cfg).request("usage_event", {
        provider: event?.provider,
        model: event?.model,
        usage,
        request_id: event?.callId ?? event?.requestId,
      })
    }, { priority: 0, timeoutMs: 10000 })

    api.on("before_prompt_build", async (event: any, ctx: Json) => {
      const runCtx = runtimeContext(event, ctx)
      const cfg = pluginConfig(runCtx, registeredConfig)
      if (cfg.policy === "native") return
      return {
        appendSystemContext:
          "SparseRead is available for long documents, PDFs, and compact evidence closures. Production SparseRead starts with sro_preview(path), which returns the minimal card, structure, samples, signals, raw_ref, and next action. Use native reads for small files, small logs, config edits, scripts, calculations, and full-table work. Call sro_read only after preview when targeted evidence is needed and provide a concrete HintSpec. For evidence collections: preview first, then at most one sro_read(mode=collect) when slots are explicit. Once slots are ready, write the deliverable immediately. Do not verify, refine, or re-read resolved slots. sro_card remains a compatibility/debug path, and bench_protocol keeps the older sro_card -> sro_read flow.",
      }
    }, { priority: -20, timeoutMs: 5000 })

    api.on("agent_end", async () => {
      for (const bridge of bridges.values()) bridge.shutdown()
      bridges.clear()
    }, { priority: 0, timeoutMs: 5000 })

    api.lifecycle?.registerRuntimeLifecycle?.({
      id: "sparseread-openclaw",
      async cleanup() {
        for (const bridge of bridges.values()) bridge.shutdown()
        bridges.clear()
      },
    })
  },
})

export default sparseReadPlugin
