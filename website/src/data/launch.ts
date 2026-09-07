/**
 * Build-time loader for launch claims and the flagship replay packet.
 * Server/Astro only — do not import from client islands.
 * Numbers come from launch/claims.yaml (approved_for_publication: true)
 * and demo/cases/flagship/* — not a second handwritten table.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { load } from "js-yaml";

export type Claim = {
  id: string;
  claim: string;
  metric: string | null;
  value: unknown;
  unit: string | null;
  baseline: string | null;
  model: string | null;
  framework: string | null;
  dataset: string | null;
  task: string | null;
  num_runs: number | null;
  aggregation: string | null;
  source_file: string | null;
  paper_section: string | null;
  paper_figure_or_table: string | null;
  approved_for_publication: boolean;
  notes: string | null;
};

export type TraceEvent = {
  lane: string;
  step: number;
  action: string;
  target: string;
  note: string;
  tokens_delta: number;
  request_delta: number;
};

type ClaimsFile = {
  paper: {
    arxiv_id: string;
    version: string;
    title: string;
    url: string;
    github: string;
    submitted: string;
  };
  setup: {
    models: string[];
    frameworks: { name: string; version: string }[];
    task_sources: string[];
    n_tasks_general: number;
    scenarios: string[];
    baselines: string[];
  };
  claims: Claim[];
};

type Glossary = {
  product_name: string;
  paper_title: string;
  arxiv_id: string;
  arxiv_url: string;
  github_url: string;
  tagline: string;
  one_line_definition: string;
  terms: Record<string, { meaning?: string; values?: string[] }>;
};

type FlagshipCase = {
  id: string;
  human_title: string;
  one_liner: string;
  task_id: string;
  source_benchmark: string;
  model: string;
  framework: string;
  claim_ids: string[];
  naive: { score: number; tokens: number; wall_seconds: number; requests: number };
  sparseread: {
    score: number;
    tokens: number;
    wall_seconds: number;
    requests: number;
    reader_calls: number;
  };
  reduction_pct: number;
  replay_label: string;
  questions: string[];
  caveats: string[];
};

type FlagshipExpected = {
  case: string;
  label: string;
  claim_ids: string[];
  task: string;
  naive: { score: number; tokens: number; wall_seconds: number; requests: number };
  sparseread: {
    score: number;
    tokens: number;
    wall_seconds: number;
    requests: number;
    reader_calls: number;
  };
  reduction_pct: number;
  num_runs: number;
};

function findRoot(): string {
  const candidates = [
    resolve(process.cwd(), ".."),
    resolve(dirname(fileURLToPath(import.meta.url)), "../../.."),
  ];
  for (const dir of candidates) {
    if (existsSync(resolve(dir, "launch/claims.yaml"))) return dir;
  }
  throw new Error("Cannot locate repo root (launch/claims.yaml missing).");
}

export const ROOT = findRoot();

function readYaml<T>(rel: string): T {
  return load(readFileSync(resolve(ROOT, rel), "utf8")) as T;
}

function readJson<T>(rel: string): T {
  return JSON.parse(readFileSync(resolve(ROOT, rel), "utf8")) as T;
}

function readJsonl<T>(rel: string): T[] {
  return readFileSync(resolve(ROOT, rel), "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as T);
}

export const claimsFile = readYaml<ClaimsFile>("launch/claims.yaml");
export const glossary = readYaml<Glossary>("launch/glossary.yaml");
export const flagshipCase = readYaml<FlagshipCase>("launch/cases/flagship.yaml");
export const flagshipExpected = readJson<FlagshipExpected>("demo/cases/flagship/expected.json");
export const flagshipNaiveTrace = readJsonl<TraceEvent>("demo/cases/flagship/trace_naive.jsonl");
export const flagshipSrTrace = readJsonl<TraceEvent>("demo/cases/flagship/trace_sparseread.jsonl");
export const citationBib = readFileSync(resolve(ROOT, "launch/citation.bib"), "utf8").trim();

const byId = new Map(claimsFile.claims.map((item) => [item.id, item]));

export function claim(id: string): Claim {
  const item = byId.get(id);
  if (!item) throw new Error(`Unknown claim_id: ${id}`);
  if (item.approved_for_publication !== true) {
    throw new Error(`Claim ${id} is not approved_for_publication`);
  }
  return item;
}

function samePair(
  a: { tokens: number; score: number; requests: number },
  b: { tokens: number; score: number; requests: number },
) {
  return a.tokens === b.tokens && a.score === b.score && a.requests === b.requests;
}

if (!samePair(flagshipCase.naive, flagshipExpected.naive)) {
  throw new Error("flagship.yaml naive totals disagree with demo/cases/flagship/expected.json");
}
if (!samePair(flagshipCase.sparseread, flagshipExpected.sparseread)) {
  throw new Error("flagship.yaml SparseRead totals disagree with expected.json");
}
if (flagshipCase.reduction_pct !== flagshipExpected.reduction_pct) {
  throw new Error("flagship reduction_pct disagrees with expected.json");
}

export const authors = [
  "Zedong Liu",
  "Jiaan Wu",
  "Xinyang Ma",
  "Le Xu",
  "Kai Wang",
  "Yuanchao Hu",
  "Dingwen Tao",
  "Guangming Tan",
];

export const paper = claimsFile.paper;
export const setup = claimsFile.setup;

export const CANONICAL = "https://zedong-liu.github.io/SparseReading/";

export function fmtInt(n: number): string {
  return n.toLocaleString("en-US");
}

export function fmtWall(seconds: number): string {
  return `${seconds.toFixed(1)} s`;
}

export function fmtPct(value: unknown): string {
  if (typeof value !== "number") {
    throw new Error(`fmtPct expected a number, got ${typeof value}`);
  }
  return value.toFixed(1);
}

export type Fig8Pair = {
  scenario: string;
  model: string;
  naiveTokens: number;
  srTokens: number;
  naiveScore: number;
  srScore: number;
  hideWall: boolean;
};

const SCENARIO_NAME: Record<string, string> = {
  "LooGLE 5Q": "Long-context reading",
  "T12 audit": "Multi-file audit",
};

export function fig8Pairs(): Fig8Pair[] {
  const raw = readFileSync(
    resolve(ROOT, "results/reports/two_scenario_three_model_baseline_comparison_20260723.csv"),
    "utf8",
  );
  const rows = raw
    .split("\n")
    .slice(1)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(",");
      return {
        scenario: parts[1],
        model: parts[2],
        method: parts[4],
        score: Number(parts[5]),
        tokens: Number(parts[7]),
      };
    });
  const keys = [...new Set(rows.map((row) => `${row.scenario}::${row.model}`))];
  return keys.map((key) => {
    const [scenario, model] = key.split("::");
    const naive = rows.find((row) => row.scenario === scenario && row.model === model && row.method === "Naive");
    const sr = rows.find((row) => row.scenario === scenario && row.model === model && row.method === "SR");
    if (!naive || !sr) {
      throw new Error(`Figure 8 pair missing for ${key}`);
    }
    return {
      scenario: SCENARIO_NAME[scenario] ?? scenario,
      model,
      naiveTokens: naive.tokens,
      srTokens: sr.tokens,
      naiveScore: naive.score,
      srScore: sr.score,
      hideWall: scenario === "LooGLE 5Q" && model === "DeepSeek-V4-Pro",
    };
  });
}

export function replayProps() {
  return {
    label: flagshipExpected.label,
    claimIds: flagshipExpected.claim_ids,
    humanTitle: flagshipCase.human_title,
    oneLiner: flagshipCase.one_liner,
    task: "Long-context reading",
    model: flagshipCase.model,
    framework: flagshipCase.framework,
    benchmark: flagshipCase.source_benchmark,
    questions: flagshipCase.questions,
    naive: flagshipExpected.naive,
    sparseread: flagshipExpected.sparseread,
    reductionPct: flagshipExpected.reduction_pct,
    naiveTrace: flagshipNaiveTrace,
    srTrace: flagshipSrTrace,
    numRuns: flagshipExpected.num_runs,
  };
}
