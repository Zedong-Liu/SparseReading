import { useEffect, useMemo, useState, type ReactNode } from "react";

export type TraceEvent = {
  lane: string;
  step: number;
  action: string;
  target: string;
  note: string;
  tokens_delta: number;
  request_delta: number;
};

export type ReplayProps = {
  label: string;
  claimIds: string[];
  humanTitle: string;
  oneLiner: string;
  task: string;
  model: string;
  framework: string;
  benchmark: string;
  questions: string[];
  naive: { score: number; tokens: number; wall_seconds: number; requests: number };
  sparseread: {
    score: number;
    tokens: number;
    wall_seconds: number;
    requests: number;
    reader_calls: number;
  };
  reductionPct: number;
  naiveTrace: TraceEvent[];
  srTrace: TraceEvent[];
  numRuns: number;
};

function fmtInt(n: number): string {
  return n.toLocaleString("en-US");
}

function fmtWall(seconds: number): string {
  return `${seconds.toFixed(1)} s`;
}

function upTo(trace: TraceEvent[], step: number) {
  return trace
    .filter((event) => event.step <= step)
    .reduce(
      (acc, event) => ({
        tokens: acc.tokens + event.tokens_delta,
        requests: acc.requests + event.request_delta,
      }),
      { tokens: 0, requests: 0 },
    );
}

const NAIVE_LINES = 28;
const SR_SLOTS = 5;
const STEP_MS = 1600;

export default function ReplayStage(props: ReplayProps) {
  const maxStep = Math.max(
    0,
    ...props.naiveTrace.map((event) => event.step),
    ...props.srTrace.map((event) => event.step),
  );

  const [step, setStep] = useState(maxStep);
  const [playing, setPlaying] = useState(false);
  const [reduced, setReduced] = useState(true);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      const preferStatic = media.matches;
      setReduced(preferStatic);
      setPlaying(!preferStatic);
      setStep(preferStatic ? maxStep : 0);
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [maxStep]);

  useEffect(() => {
    if (!playing || reduced) return;
    const id = window.setInterval(() => {
      setStep((current) => (current >= maxStep ? 0 : current + 1));
    }, STEP_MS);
    return () => window.clearInterval(id);
  }, [playing, reduced, maxStep]);

  const naiveLive = useMemo(() => {
    if (step >= maxStep) {
      return { tokens: props.naive.tokens, requests: props.naive.requests };
    }
    return upTo(props.naiveTrace, step);
  }, [props.naive, props.naiveTrace, step, maxStep]);

  const srLive = useMemo(() => {
    if (step >= maxStep) {
      return { tokens: props.sparseread.tokens, requests: props.sparseread.requests };
    }
    return upTo(props.srTrace, step);
  }, [props.sparseread, props.srTrace, step, maxStep]);

  const naiveRatio = props.naive.tokens ? Math.min(1, naiveLive.tokens / props.naive.tokens) : 0;
  const srRatio = props.naive.tokens ? Math.min(1, srLive.tokens / props.naive.tokens) : 0;
  const done = step >= maxStep;

  return (
    <div
      className="replay"
      data-claim-id={props.claimIds.join(" ")}
      data-replay="flagship"
    >
      <div className="replay-head">
        <span className="replay-badge">REPLAY</span>
        <p className="kicker">{props.label}</p>
      </div>
      <h3 className="replay-title">{props.humanTitle}</h3>
      <p className="replay-sub">
        {props.oneLiner} {props.benchmark} · {props.model} · {props.framework} ·{" "}
        {props.numRuns === 1 ? "single run" : `${props.numRuns} runs`} · Figure 8
      </p>

      <div className="replay-grid">
        <Lane
          title="Over-read / Full Read"
          tone="naive"
          tokens={naiveLive.tokens}
          requests={naiveLive.requests}
          wall={done ? fmtWall(props.naive.wall_seconds) : "—"}
          score={done ? props.naive.score.toFixed(1) : "—"}
          bar={naiveRatio * 100}
          claimId="case-flagship-loogle5q-flash"
          event={step >= 1 ? props.naiveTrace.find((item) => item.step === Math.min(step, maxStep)) : undefined}
        >
          <VolumeField tone="naive" ratio={naiveRatio} />
          <p className="volume-caption">Whole chapter stays in context</p>
        </Lane>
        <Lane
          title="SparseRead"
          tone="sr"
          tokens={srLive.tokens}
          requests={srLive.requests}
          wall={done ? fmtWall(props.sparseread.wall_seconds) : "—"}
          score={done ? props.sparseread.score.toFixed(1) : "—"}
          bar={srRatio * 100}
          claimId="case-flagship-loogle5q-flash"
          event={step >= 1 ? props.srTrace.find((item) => item.step === Math.min(step, maxStep)) : undefined}
        >
          <VolumeField tone="sr" ratio={Math.min(1, srLive.tokens / props.sparseread.tokens || 0)} />
          <p className="volume-caption">Five anchored slots, then stop</p>
        </Lane>
      </div>

      <p className="replay-foot" data-claim-id="case-flagship-loogle5q-flash-reduction">
        <span data-claim-id="case-flagship-loogle5q-flash">
          {fmtInt(props.naive.tokens)} → {fmtInt(props.sparseread.tokens)} tokens
        </span>
        {" · "}
        −{props.reductionPct}% · score {props.sparseread.score.toFixed(1)} /{" "}
        {props.naive.score.toFixed(1)} · five local questions, same answers
      </p>

      <div className="replay-controls">
        <button type="button" onClick={() => setPlaying((value) => !value)} disabled={reduced}>
          {reduced ? "Static poster" : playing ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          onClick={() => {
            setPlaying(false);
            setStep((current) => Math.max(0, current - 1));
          }}
        >
          Prev
        </button>
        <button
          type="button"
          onClick={() => {
            setPlaying(false);
            setStep((current) => Math.min(maxStep, current + 1));
          }}
        >
          Next
        </button>
        <span className="note">
          step {step}/{maxStep} · {props.task}
        </span>
      </div>
    </div>
  );
}

function VolumeField({ tone, ratio }: { tone: "naive" | "sr"; ratio: number }) {
  const count = tone === "naive" ? NAIVE_LINES : SR_SLOTS;
  const lit = Math.max(ratio > 0 ? 1 : 0, Math.round(count * ratio));
  return (
    <div className={`volume ${tone}`} aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <span
          key={index}
          className={index < lit ? "on" : ""}
          style={tone === "sr" ? { width: `${42 + (index % 3) * 10}%` } : undefined}
        />
      ))}
    </div>
  );
}

function Lane({
  title,
  tone,
  tokens,
  requests,
  wall,
  score,
  bar,
  claimId,
  event,
  children,
}: {
  title: string;
  tone: "naive" | "sr";
  tokens: number;
  requests: number;
  wall: string;
  score: string;
  bar: number;
  claimId: string;
  event?: TraceEvent;
  children: ReactNode;
}) {
  return (
    <article className={`replay-lane ${tone}`}>
      <p className="replay-lane-kicker">{title}</p>
      <p className="replay-metric" data-claim-id={claimId}>
        {fmtInt(tokens)}
        <span> tokens</span>
      </p>
      <div className="meter" aria-hidden="true">
        <div className={`meter-fill ${tone}`} style={{ width: `${bar}%` }} />
      </div>
      <p className="replay-meta">
        <b>{requests}</b> req · <b>{wall}</b> · score <b>{score}</b>
      </p>
      {children}
      {event ? (
        <p className="replay-step-note">
          <code>
            {String(event.step).padStart(2, "0")} {event.action}
          </code>
          {event.note}
        </p>
      ) : null}
    </article>
  );
}
