import { useEffect, useRef, useState } from "react";

type FrameworkRow = {
  name: string;
  token: number;
  wall: number;
};

type Props = {
  flagship: {
    naiveTokens: number;
    srTokens: number;
    naiveScore: number;
    srScore: number;
    naiveWall: number;
    srWall: number;
    naiveReq: number;
    srReq: number;
    reduction: number;
  };
  frameworks: FrameworkRow[];
  opusTokens: string;
  opusWall: string;
  qualityHeld: number;
  qualityTotal: number;
  qualityDip: string;
};

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

function score(n: number): string {
  return n >= 1 ? "1.0" : n.toFixed(2);
}

export default function ResultsBoard(props: Props) {
  const root = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);
  const [reduced, setReduced] = useState(true);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setInView(true);
      },
      { threshold: 0.2 },
    );
    io.observe(node);
    return () => io.disconnect();
  }, []);

  const live = inView || reduced;
  const heldPct = Math.round((props.qualityHeld / props.qualityTotal) * 100);

  return (
    <div ref={root} className={`results-board${live ? " in" : ""}`} data-reduced={reduced}>
      <article className="result-card result-flagship">
        <p className="result-kicker">Flagship cell · long-context reading</p>
        <h3>Same answers. 86% fewer tokens.</h3>
        <p>
          DeepSeek-V4-Flash on a five-question long document. Score stayed{" "}
          {score(props.flagship.srScore)}. One recorded run, not an average.
        </p>
        <div className="shrink-plot" aria-hidden="true">
          <div className="shrink-row">
            <span>Full read</span>
            <i>
              <b className="naive" style={{ width: live ? "100%" : "0%" }} />
            </i>
            <em>{fmt(props.flagship.naiveTokens)}</em>
          </div>
          <div className="shrink-row">
            <span>SparseRead</span>
            <i>
              <b
                className="sr"
                style={{
                  width: live ? `${100 - props.flagship.reduction}%` : "0%",
                }}
              />
            </i>
            <em>{fmt(props.flagship.srTokens)}</em>
          </div>
        </div>
        <ul className="result-pills">
          <li>
            <b>−{props.flagship.reduction}%</b>
            <span>tokens</span>
          </li>
          <li>
            <b>
              {props.flagship.naiveReq} → {props.flagship.srReq}
            </b>
            <span>requests</span>
          </li>
          <li>
            <b>
              {props.flagship.naiveWall.toFixed(0)}s → {props.flagship.srWall.toFixed(0)}s
            </b>
            <span>wall time</span>
          </li>
          <li>
            <b>
              {score(props.flagship.naiveScore)} = {score(props.flagship.srScore)}
            </b>
            <span>score</span>
          </li>
        </ul>
      </article>

      <article className="result-card">
        <p className="result-kicker">Strongest model evaluated</p>
        <h3>Claude Opus 5 still saves most of the read.</h3>
        <p>
          On sparse-fit scenarios the strongest model still drops {props.opusTokens} of
          tokens and {props.opusWall} of wall time versus full reading.
        </p>
        <div className="opus-range" aria-hidden="true">
          <span>tokens saved</span>
          <div className="opus-track">
            <i style={{ left: "59.6%", right: `${100 - 89}%` }} />
          </div>
          <em>59.6% — 89.0%</em>
        </div>
        <div className="opus-range" aria-hidden="true">
          <span>wall time saved</span>
          <div className="opus-track">
            <i className="wall" style={{ left: "51.3%", right: `${100 - 78.1}%` }} />
          </div>
          <em>51.3% — 78.1%</em>
        </div>
      </article>

      <div className="result-twin">
        <article className="result-card">
          <p className="result-kicker">Same protocol, three harnesses</p>
          <h3>The saving ports. It is not uniform.</h3>
          <p>
            Median token cut across five models: NanoBot 69.0%, OpenCode 71.8%,
            OpenClaw 28.7%. Wall-time medians follow the same order.
          </p>
          <ul className="framework-bars">
            {props.frameworks.map((row) => (
              <li key={row.name}>
                <div>
                  <b>{row.name}</b>
                  <span>
                    {row.token.toFixed(1)}% tokens · {row.wall.toFixed(1)}% wall
                  </span>
                </div>
                <i>
                  <b style={{ width: live ? `${row.token}%` : "0%" }} />
                </i>
              </li>
            ))}
          </ul>
        </article>

        <article className="result-card result-compare">
          <p className="result-kicker">Against other reading cuts</p>
          <h3>Lowest tokens and time. Score never below full read.</h3>
          <p>
            Versus ACON and last-10 masking, lowest tokens and wall time in every
            setting. Only method whose score never fell below Naive. 63.4–86.3%
            tokens; 1.8–3.6× sooner.
          </p>
          <ol className="rank-lane">
            <li className="win">
              <b>SparseRead</b>
              <span>lowest tokens and time</span>
            </li>
            <li>
              <b>ACON</b>
              <span>compresses after the full read</span>
            </li>
            <li>
              <b>Last-10 mask</b>
              <span>drops old observations</span>
            </li>
          </ol>
        </article>
      </div>

      <article className="result-card result-quality">
        <p className="result-kicker">Quality usually holds</p>
        <div className="quality-body">
          <div className="quality-ring" aria-hidden="true">
            <svg viewBox="0 0 72 72">
              <circle cx="36" cy="36" r="28" />
              <circle
                cx="36"
                cy="36"
                r="28"
                className="held"
                strokeDasharray={`${(heldPct / 100) * 176} 176`}
              />
            </svg>
            <b>
              {props.qualityHeld}/{props.qualityTotal}
            </b>
          </div>
          <div>
            <h3>Quality held or improved on 26 of 30 settings.</h3>
            <p>
              Four cells dipped between {props.qualityDip}. Tokens and wall time
              still fell in all 30. Across the three paper harnesses, 12 of 15
              cells hold or improve; three regress between −0.031 and −0.013.
            </p>
          </div>
        </div>
      </article>
    </div>
  );
}
