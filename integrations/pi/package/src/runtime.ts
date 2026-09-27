import { readFileSync } from "node:fs"
import path from "node:path"

export type SparseReadMode = "auto" | "advisory"

export interface RuntimeConfig {
  python: string
  workspace: string
  mode: SparseReadMode
  protocol: "1.0"
}

export interface RuntimeConfigResult {
  config?: RuntimeConfig
  error?: string
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function runtimeSetupInstructions(configPath: string): string {
  return [
    `SparseRead Pi runtime is not configured. Expected ${configPath}.`,
    "Install the workspace-managed Pi package from a SparseRead checkout:",
    "  uv run --project <sparse-reading-checkout> python <sparse-reading-checkout>/scripts/install_sparseread.py --platform pi --workspace <workspace>",
    "Then reload Pi. The installer writes the managed Python runtime settings beside this package.",
  ].join("\n")
}

export function loadRuntimeConfig(packageRoot: string): RuntimeConfigResult {
  const configPath = path.join(packageRoot, ".sparseread-runtime.json")
  let raw: string
  try {
    raw = readFileSync(configPath, "utf8")
  } catch (error) {
    if (isObject(error) && error.code === "ENOENT") {
      return { error: runtimeSetupInstructions(configPath) }
    }
    return {
      error: `Unable to read SparseRead Pi runtime settings at ${configPath}: ${error instanceof Error ? error.message : String(error)}`,
    }
  }

  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch (error) {
    return {
      error: `Invalid SparseRead Pi runtime JSON at ${configPath}: ${error instanceof Error ? error.message : String(error)}`,
    }
  }

  if (
    !isObject(value)
    || typeof value.python !== "string"
    || !path.isAbsolute(value.python)
    || typeof value.workspace !== "string"
    || !path.isAbsolute(value.workspace)
    || (value.mode !== "auto" && value.mode !== "advisory")
    || value.protocol !== "1.0"
  ) {
    return {
      error: `Invalid SparseRead Pi runtime settings at ${configPath}: expected absolute python/workspace paths, mode "auto" or "advisory", and protocol "1.0". Re-run the SparseRead workspace installer.`,
    }
  }

  return {
    config: {
      python: value.python,
      workspace: value.workspace,
      mode: value.mode,
      protocol: value.protocol,
    },
  }
}
