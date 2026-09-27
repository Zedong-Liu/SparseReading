import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
import { StringDecoder } from "node:string_decoder"
import type { RuntimeConfig } from "./runtime.js"

export interface BridgeClientOptions {
  timeoutMs?: number
  spawnProcess?: typeof spawn
}

interface BridgeEnvelope {
  id: string
  ok: boolean
  result?: unknown
  error?: string
}

interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: NodeJS.Timeout
  signal?: AbortSignal
  abortHandler?: () => void
}

const DEFAULT_TIMEOUT_MS = 30_000

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function abortError(): Error {
  const error = new Error("SparseRead bridge request was aborted")
  error.name = "AbortError"
  return error
}

function errorFrom(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value))
}

function awaitWithAbort<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise
  if (signal.aborted) return Promise.reject(abortError())
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      signal.removeEventListener("abort", onAbort)
      reject(abortError())
    }
    signal.addEventListener("abort", onAbort, { once: true })
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort)
        resolve(value)
      },
      (error) => {
        signal.removeEventListener("abort", onAbort)
        reject(error)
      },
    )
  })
}

export class BridgeClient {
  private child?: ChildProcessWithoutNullStreams
  private nextId = 1
  private buffer = ""
  private readonly pending = new Map<string, PendingRequest>()
  private versionCheck?: Promise<void>
  private shutdownPromise?: Promise<void>
  private readonly timeoutMs: number
  private readonly spawnProcess: typeof spawn

  constructor(private readonly config: RuntimeConfig, options: BridgeClientOptions = {}) {
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
    this.spawnProcess = options.spawnProcess ?? spawn
  }

  async request(method: string, params: Record<string, unknown> = {}, signal?: AbortSignal): Promise<unknown> {
    if (signal?.aborted) throw abortError()
    if (this.shutdownPromise) await awaitWithAbort(this.shutdownPromise, signal)
    if (signal?.aborted) throw abortError()

    const child = this.ensureChild()
    const versionCheck = this.versionCheck ??= this.requestRaw(child, "version", {}).then((result) => {
      if (!isObject(result) || result.protocol_version !== this.config.protocol) {
        throw new Error(
          `SparseRead bridge protocol mismatch: expected ${this.config.protocol}, got ${isObject(result) ? String(result.protocol_version ?? "missing") : "invalid response"}`,
        )
      }
      if (result.platform !== "Pi") {
        throw new Error(`SparseRead bridge platform mismatch: expected Pi, got ${isObject(result) ? String(result.platform ?? "missing") : "invalid response"}`)
      }
    }).catch((error) => {
      this.failChild(child, errorFrom(error))
      throw error
    })

    await awaitWithAbort(versionCheck, signal)
    if (this.child !== child) throw new Error("SparseRead bridge session was reset")
    return this.requestRaw(child, method, params, signal)
  }

  async shutdown(): Promise<void> {
    if (this.shutdownPromise) return this.shutdownPromise
    const child = this.child
    if (!child) {
      this.versionCheck = undefined
      return
    }

    this.child = undefined
    this.buffer = ""
    this.versionCheck = undefined
    this.rejectPending(new Error("SparseRead bridge session was reset"))

    const closing = new Promise<void>((resolve) => {
      let finished = false
      const finish = () => {
        if (finished) return
        finished = true
        clearTimeout(timer)
        resolve()
      }
      const timer = setTimeout(() => {
        try {
          child.kill()
        } catch {
          // Process cleanup is best-effort during Pi lifecycle changes.
        }
        finish()
      }, 750)
      // Keep the cleanup deadline alive until shutdown has actually completed.
      child.once("exit", finish)
      child.once("error", finish)

      try {
        child.stdin.end(JSON.stringify({ id: String(this.nextId++), method: "shutdown", params: {} }) + "\n")
      } catch {
        try {
          child.kill()
        } catch {
          // Process cleanup is best-effort during Pi lifecycle changes.
        }
        finish()
      }
    })
    this.shutdownPromise = closing
    void closing.finally(() => {
      if (this.shutdownPromise === closing) this.shutdownPromise = undefined
    })
    return closing
  }

  private ensureChild(): ChildProcessWithoutNullStreams {
    if (this.child) return this.child
    const child = this.spawnProcess(
      this.config.python,
      [
        "-m",
        "sparseread_agent_tools.bridge",
        "--host",
        "pi",
        "--workspace",
        this.config.workspace,
        "--mode",
        this.config.mode,
      ],
      { cwd: this.config.workspace, stdio: ["pipe", "pipe", "pipe"] },
    ) as ChildProcessWithoutNullStreams
    this.child = child
    child.stdin.on("error", (error: Error) => this.failChild(child, error))
    const stdoutDecoder = new StringDecoder("utf8")
    child.stdout.on("data", (chunk: Buffer | string) => {
      this.onData(child, typeof chunk === "string" ? chunk : stdoutDecoder.write(chunk))
    })
    child.stderr.on("data", (chunk: Buffer | string) => {
      const message = String(chunk).trim()
      if (message) console.error(`[sparseread pi bridge] ${message}`)
    })
    child.once("error", (error) => this.failChild(child, error))
    child.once("exit", (code, signal) => {
      this.failChild(child, new Error(`SparseRead bridge exited code=${code ?? ""} signal=${signal ?? ""}`.trim()), false)
    })
    return child
  }

  private requestRaw(
    child: ChildProcessWithoutNullStreams,
    method: string,
    params: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<unknown> {
    if (signal?.aborted) return Promise.reject(abortError())
    const id = String(this.nextId++)
    return new Promise((resolve, reject) => {
      let settled = false
      const finish = (error?: Error, value?: unknown) => {
        if (settled) return
        settled = true
        clearTimeout(pending.timer)
        if (pending.abortHandler && pending.signal) {
          pending.signal.removeEventListener("abort", pending.abortHandler)
        }
        this.pending.delete(id)
        if (error) reject(error)
        else resolve(value)
      }
      const pending: PendingRequest = {
        resolve: (value) => finish(undefined, value),
        reject: (error) => finish(error),
        timer: setTimeout(() => {
          const error = new Error(`SparseRead bridge request timed out after ${this.timeoutMs} ms (${method})`)
          finish(error)
          this.failChild(child, error)
        }, this.timeoutMs),
        signal,
      }
      // A pending request must settle even when no other event-loop handles remain.
      if (signal) {
        pending.abortHandler = () => finish(abortError())
        signal.addEventListener("abort", pending.abortHandler, { once: true })
      }
      this.pending.set(id, pending)
      try {
        child.stdin.write(JSON.stringify({ id, method, params }) + "\n")
      } catch (error) {
        const requestError = errorFrom(error)
        finish(requestError)
        this.failChild(child, requestError)
      }
    })
  }

  private onData(child: ChildProcessWithoutNullStreams, chunk: string): void {
    if (this.child !== child) return
    this.buffer += chunk
    while (true) {
      const newline = this.buffer.indexOf("\n")
      if (newline < 0) return
      const line = this.buffer.slice(0, newline).trim()
      this.buffer = this.buffer.slice(newline + 1)
      if (!line) continue

      let envelope: unknown
      try {
        envelope = JSON.parse(line)
      } catch {
        this.failChild(child, new Error("SparseRead bridge returned invalid JSONL"))
        return
      }
      if (!isObject(envelope) || typeof envelope.id !== "string" || typeof envelope.ok !== "boolean") {
        this.failChild(child, new Error("SparseRead bridge returned an invalid response envelope"))
        return
      }
      const request = this.pending.get(envelope.id)
      if (!request) continue
      if (envelope.ok) {
        request.resolve(envelope.result)
      } else {
        request.reject(new Error(typeof envelope.error === "string" ? envelope.error : "SparseRead bridge request failed"))
      }
    }
  }

  private failChild(child: ChildProcessWithoutNullStreams, error: Error, kill = true): void {
    if (this.child !== child) return
    this.child = undefined
    this.buffer = ""
    this.versionCheck = undefined
    this.rejectPending(error)
    if (kill) {
      try {
        child.kill()
      } catch {
        // The process may already be gone.
      }
    }
  }

  private rejectPending(error: Error): void {
    for (const request of this.pending.values()) request.reject(error)
    this.pending.clear()
  }
}
