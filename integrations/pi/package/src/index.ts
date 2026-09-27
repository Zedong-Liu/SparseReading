import { randomUUID } from "node:crypto"
import { realpathSync } from "node:fs"
import { fileURLToPath } from "node:url"
import path from "node:path"
import { Type, type TSchema } from "typebox"
import type { ExtensionAPI, ExtensionContext, ToolCallEvent } from "@earendil-works/pi-coding-agent"
import { BridgeClient } from "./bridge.js"
import { loadRuntimeConfig, runtimeSetupInstructions, type RuntimeConfig, type RuntimeConfigResult } from "./runtime.js"

export interface BridgeLike {
  request(method: string, params?: Record<string, unknown>, signal?: AbortSignal): Promise<unknown>
  shutdown(): Promise<void> | void
}

export interface PiExtensionOptions {
  packageRoot?: string
  runtime?: RuntimeConfig
  runtimeError?: string
  bridgeFactory?: (runtime: RuntimeConfig) => BridgeLike
}

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const stringPath = Type.String({
  minLength: 1,
  description: "Absolute or workspace-relative path. Targets outside the installed workspace use native tools.",
})
const episodeHint = Type.Object({
  relation: Type.Optional(Type.Union([
    Type.Literal("new"),
    Type.Literal("continue"),
    Type.Literal("switch"),
    Type.Literal("unknown"),
  ])),
  goal: Type.Optional(Type.Union([
    Type.Literal("selective_read"),
    Type.Literal("cross_file_evidence"),
    Type.Literal("structured_compute"),
    Type.Literal("edit_or_execute"),
    Type.Literal("full_fidelity"),
    Type.Literal("unknown"),
  ])),
  coverage: Type.Optional(Type.Union([
    Type.Literal("selective"),
    Type.Literal("exhaustive"),
    Type.Literal("unknown"),
  ])),
  summary: Type.Optional(Type.String()),
}, { additionalProperties: false })
const target = Type.Object({
  path: Type.Optional(stringPath),
  artifact_id: Type.Optional(Type.String()),
}, { additionalProperties: false, minProperties: 1 })
const hint = Type.Record(Type.String(), Type.Unknown())
const readRange = Type.Object({
  start: Type.Optional(Type.Integer({ minimum: 0 })),
  end: Type.Optional(Type.Integer({ minimum: 0 })),
}, { additionalProperties: false })
const MAX_REDIRECTS_PER_SESSION = 512

const toolDefinitions = [
  {
    name: "sro_preview",
    label: "SRO Preview",
    description: "Preview a large supported file, PDF, or directory before choosing targeted evidence to read.",
    method: "preview",
    parameters: Type.Object({
      path: Type.Optional(stringPath),
      artifact_id: Type.Optional(Type.String()),
      episode_hint: Type.Optional(episodeHint),
    }, { additionalProperties: false }),
  },
  {
    name: "sro_read",
    label: "SRO Read",
    description: "Read targeted evidence after preview with scout, focus, collect, refine, or verify mode.",
    method: "read",
    parameters: Type.Object({
      target,
      mode: Type.Union([
        Type.Literal("scout"),
        Type.Literal("focus"),
        Type.Literal("collect"),
        Type.Literal("refine"),
        Type.Literal("verify"),
      ]),
      hint,
      episode_hint: Type.Optional(episodeHint),
    }, { additionalProperties: false }),
  },
  {
    name: "sro_raw",
    label: "SRO Raw",
    description: "Retrieve exact source content behind a raw_ref when preview and targeted evidence are insufficient.",
    method: "raw",
    parameters: Type.Object({
      raw_ref: Type.String({ minLength: 1 }),
      range: Type.Optional(readRange),
      selector: Type.Optional(Type.String()),
    }, { additionalProperties: false }),
  },
  {
    name: "sro_card",
    label: "SRO Card",
    description: "Inspect compatibility and debug metadata for a file or directory.",
    method: "card",
    parameters: Type.Object({ path: stringPath }, { additionalProperties: false }),
  },
  {
    name: "sro_decide",
    label: "SRO Decide",
    description: "Inspect the unchanged SparseRead benefit gate for a workspace path without extracting its content.",
    method: "decide",
    parameters: Type.Object({
      path: stringPath,
      episode_hint: Type.Optional(episodeHint),
    }, { additionalProperties: false }),
  },
  {
    name: "sro_trace",
    label: "SRO Trace",
    description: "Inspect SparseRead decisions and read events for the current Pi session.",
    method: "trace",
    parameters: Type.Object({}, { additionalProperties: false }),
  },
] satisfies Array<{
  name: string
  label: string
  description: string
  method: string
  parameters: TSchema
}>

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function paramsRecord(value: unknown): Record<string, unknown> {
  return isObject(value) ? value : {}
}

function requestContext(
  conversationId: string,
  turnId: string,
  toolCallId: string,
): Record<string, string> {
  return {
    conversation_id: conversationId,
    turn_id: turnId,
    message_id: turnId,
    tool_call_id: toolCallId,
  }
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function isBoundedRead(event: ToolCallEvent): boolean {
  if (event.toolName !== "read" || !isObject(event.input)) return false
  const limit = event.input.limit
  const offset = event.input.offset
  return (typeof limit === "number" && Number.isFinite(limit) && limit >= 0)
    || (typeof offset === "number" && Number.isFinite(offset) && offset > 1)
}

function readPath(event: ToolCallEvent): string | undefined {
  if (event.toolName !== "read" || !isObject(event.input)) return undefined
  return typeof event.input.path === "string" && event.input.path.trim() ? event.input.path : undefined
}

function canonicalCandidate(workspace: string, candidate: string): string {
  const resolved = path.resolve(workspace, candidate)
  try {
    return realpathSync.native(resolved)
  } catch {
    return resolved
  }
}

function shouldGateNativeRead(event: ToolCallEvent, mode: RuntimeConfig["mode"]): boolean {
  return mode === "auto" && event.toolName === "read" && !isBoundedRead(event) && readPath(event) !== undefined
}

function sparseReadRedirect(candidate: string): string {
  return `SparseRead force: call sro_preview with {"path": ${JSON.stringify(candidate)}} before this broad read. Follow preview.next_action; use sro_read only if targeted evidence is still needed. If SparseRead fails, explicitly retry this same read natively; the retry will be allowed.`
}

function sessionReset(pi: ExtensionAPI, reset: () => Promise<void>): void {
  pi.on("session_shutdown", async () => reset())
  pi.on("session_before_switch", async () => reset())
  pi.on("session_before_fork", async () => reset())
}

export function createSparseReadPiExtension(pi: ExtensionAPI, options: PiExtensionOptions = {}): void {
  const configResult: RuntimeConfigResult = options.runtime
    ? { config: options.runtime }
    : options.runtimeError
      ? { error: options.runtimeError }
      : loadRuntimeConfig(options.packageRoot ?? packageRoot)
  const runtime = configResult.config
  const configError = configResult.error
    ?? (!runtime ? runtimeSetupInstructions(path.join(options.packageRoot ?? packageRoot, ".sparseread-runtime.json")) : undefined)
  let bridge: BridgeLike | undefined
  let conversationId = randomUUID()
  let turnId = randomUUID()
  const redirectedReads = new Set<string>()

  const getBridge = (): BridgeLike => {
    if (!runtime) throw new Error(configError || "SparseRead Pi runtime is not configured. See the package README for setup.")
    bridge ??= (options.bridgeFactory ?? ((config) => new BridgeClient(config)))(runtime)
    return bridge
  }

  const reset = async () => {
    redirectedReads.clear()
    const current = bridge
    bridge = undefined
    if (current) {
      try {
        await current.shutdown()
      } catch (error) {
        console.error(`[sparseread pi] bridge cleanup failed: ${errorText(error)}`)
      }
    }
  }

  if (configError) console.error(`[sparseread pi] ${configError}`)

  for (const definition of toolDefinitions) {
    registerTool(pi, definition, getBridge, () => ({ conversationId, turnId }))
  }

  pi.on("turn_start", () => {
    turnId = randomUUID()
  })
  pi.on("session_start", async () => {
    await reset()
    conversationId = randomUUID()
    turnId = randomUUID()
  })
  sessionReset(pi, reset)

  pi.on("tool_call", async (event, context) => {
    if (!runtime || !shouldGateNativeRead(event, runtime.mode)) return
    const nativePath = readPath(event)
    if (!nativePath) return
    const candidate = path.resolve(context.cwd || runtime.workspace, nativePath)
    const requestConversationId = conversationId
    const redirectKey = JSON.stringify([requestConversationId, canonicalCandidate(runtime.workspace, candidate)])
    if (redirectedReads.has(redirectKey) || redirectedReads.size >= MAX_REDIRECTS_PER_SESSION) return
    // Reserve before awaiting the bridge so concurrent calls for one path cannot
    // both block; the second call passes through while the first is checked.
    redirectedReads.add(redirectKey)
    try {
      const result = await getBridge().request("decide", {
        path: candidate,
        context: requestContext(requestConversationId, turnId, event.toolCallId),
      }, context.signal)
      if (conversationId !== requestConversationId) {
        redirectedReads.delete(redirectKey)
        return
      }
      if (!isObject(result) || !isObject(result.host_gate) || result.host_gate.block_native_read !== true) {
        redirectedReads.delete(redirectKey)
        return
      }
      return { block: true, reason: sparseReadRedirect(candidate) }
    } catch (error) {
      redirectedReads.delete(redirectKey)
      console.error(`[sparseread pi] native read check failed open: ${errorText(error)}`)
      return
    }
  })
}

function registerTool(
  pi: ExtensionAPI,
  definition: (typeof toolDefinitions)[number],
  getBridge: () => BridgeLike,
  getSession: () => { conversationId: string; turnId: string },
): void {
  pi.registerTool({
    name: definition.name,
    label: definition.label,
    description: definition.description,
    parameters: definition.parameters,
    execute: async (
      toolCallId: string,
      params,
      signal: AbortSignal | undefined,
      _onUpdate,
      _context: ExtensionContext,
    ) => {
      const session = getSession()
      try {
        const result = await getBridge().request(
          definition.method,
          {
            ...paramsRecord(params),
            context: requestContext(session.conversationId, session.turnId, toolCallId),
          },
          signal,
        )
        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
          details: undefined,
        }
      } catch (error) {
        throw new Error(`SparseRead ${definition.name} failed: ${errorText(error)}. Continue with native tools if needed.`)
      }
    },
  })
}

export default function sparsereadPiExtension(pi: ExtensionAPI): void {
  createSparseReadPiExtension(pi)
}
