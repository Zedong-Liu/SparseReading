import { useEffect, useState } from "react";
import Fig1Frontier from "./Fig1Frontier";

export type Fig8Pair = {
  scenario: string;
  model: string;
  naiveTokens: number;
  srTokens: number;
  naiveScore: number;
  srScore: number;
  hideWall: boolean;
};

const SLIDE_MS = 4000;

export type HeroCarouselProps = {
  fig8: Fig8Pair[];
  flagship: {
    title: string;
    model: string;
    framework: string;
    naiveTokens: number;
    srTokens: number;
    naiveReq: number;
    srReq: number;
    naiveWall: number;
    srWall: number;
    naiveScore: number;
    srScore: number;
    reduction: number;
    questions: string[];
  };
  matrix: {
    tokenMax: string;
    wallMax: string;
    tokenMedian: string;
    wallMedian: string;
    scenarios: string;
    files: string;
    quality: string;
    opus: string;
  };
};

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

function score(n: number): string {
  return n >= 1 ? "1.0" : n.toFixed(2);
}

export default function HeroCarousel(props: HeroCarouselProps) {
  const [slide, setSlide] = useState(0);
  const [hoverPaused, setHoverPaused] = useState(false);
  const [reduced, setReduced] = useState(true);
  const last = 2;
  const paused = hoverPaused || reduced;

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    if (paused) return;
    const id = window.setInterval(() => {
      setSlide((current) => (current >= last ? 0 : current + 1));
    }, SLIDE_MS);
    return () => window.clearInterval(id);
  }, [paused]);

  const maxTokens = Math.max(...props.fig8.map((row) => row.naiveTokens));

  return (
    <figure
      className="hero-carousel"
      onMouseEnter={() => setHoverPaused(true)}
      onMouseLeave={() => setHoverPaused(false)}
    >
      <div className="carousel-frame">
        {slide === 0 && (
          <div className="slide compare-slide" data-claim-id="case-flagship-loogle5q-flash case-flagship-loogle5q-flash-reduction">
            <p className="kicker">Flagship · {props.flagship.model} · {props.flagship.framework} · single run</p>
            <h3>{props.flagship.title}</h3>
            <div className="compare-grid">
              <article className="compare-lane naive">
                <p className="replay-lane-kicker">Over-read / Full Read</p>
                <p className="replay-metric">
                  {fmt(props.flagship.naiveTokens)}
                  <span> tokens</span>
                </p>
                <p className="compare-meta">
                  <b>{props.flagship.naiveReq}</b> req · <b>{props.flagship.naiveWall.toFixed(1)}</b> s · score <b>{score(props.flagship.naiveScore)}</b>
                </p>
                <div className="volume naive" aria-hidden="true">
                  {Array.from({ length: 12 }, (_, index) => (
                    <span key={index} className="on" />
                  ))}
                </div>
                <p className="volume-caption">Whole ~100k-character chapter stays in context</p>
              </article>
              <article className="compare-lane sr">
                <p className="replay-lane-kicker">SparseRead</p>
                <p className="replay-metric">
                  {fmt(props.flagship.srTokens)}
                  <span> tokens</span>
                </p>
                <p className="compare-meta">
                  <b>{props.flagship.srReq}</b> req · <b>{props.flagship.srWall.toFixed(1)}</b> s · score <b>{score(props.flagship.srScore)}</b>
                </p>
                <div className="volume sr" aria-hidden="true">
                  {Array.from({ length: 5 }, (_, index) => (
                    <span key={index} className="on" style={{ width: `${44 + (index % 3) * 10}%` }} />
                  ))}
                </div>
                <p className="volume-caption">Five anchored slots · <b>−{props.flagship.reduction}%</b> tokens</p>
              </article>
            </div>
            <ul className="question-row">
              {props.flagship.questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          </div>
        )}

        {slide === 1 && <Fig1Frontier />}

        {slide === 2 && (
          <div className="slide chart-slide" data-claim-id="paper-s63-vs-baselines paper-s63-token-range paper-abs-token-max paper-abs-quality-preserve">
            <p className="kicker">Figure 8 · Naive vs SparseRead · n=1</p>
            <h3>Lower cost. Score holds or improves.</h3>
            <div className="chart-chips">
              <span data-claim-id="paper-abs-token-max paper-tab1-framework-medians">
                <b>up to {props.matrix.tokenMax}% tokens</b>
                <em>median {props.matrix.tokenMedian}%</em>
              </span>
              <span data-claim-id="paper-abs-wall-max paper-tab1-framework-medians">
                <b>up to {props.matrix.wallMax}% wall</b>
                <em>median {props.matrix.wallMedian}%</em>
              </span>
              <span>
                <b>{props.matrix.scenarios}</b>
                <em>{props.matrix.files}</em>
              </span>
              <span data-claim-id="paper-abs-quality-preserve">
                <b>{props.matrix.quality}</b>
                <em>26 of 30 settings</em>
              </span>
            </div>
            <ol className="lb">
              {props.fig8.map((row) => {
                const naiveW = (row.naiveTokens / maxTokens) * 100;
                const srW = (row.srTokens / maxTokens) * 100;
                const cut = ((row.naiveTokens - row.srTokens) / row.naiveTokens) * 100;
                return (
                  <li key={`${row.scenario}-${row.model}`}>
                    <div className="lb-meta">
                      <b>
                        {row.scenario} · {row.model}
                      </b>
                      <span>
                        {fmt(row.naiveTokens)} → {fmt(row.srTokens)} · −{cut.toFixed(1)}% · score {score(row.naiveScore)} → {score(row.srScore)}
                      </span>
                    </div>
                    <div className="lb-bars">
                      <i className="naive" style={{ width: `${naiveW}%` }} />
                      <i className="sr" style={{ width: `${srW}%` }} />
                    </div>
                  </li>
                );
              })}
            </ol>
            <p className="note">One run each · {props.matrix.opus}</p>
          </div>
        )}
      </div>
      <div className="carousel-controls">
        <button
          type="button"
          onClick={() => setSlide((current) => (current + last) % 3)}
        >
          Prev
        </button>
        {[0, 1, 2].map((index) => (
          <button
            key={index}
            type="button"
            className={slide === index ? "dot on" : "dot"}
            aria-label={`Slide ${index + 1}`}
            onClick={() => setSlide(index)}
          />
        ))}
        <figcaption className="note">
          {slide === 0 ? "01  Flagship over-read" : slide === 1 ? "02  Paper Figure 1" : "03  Figure 8 bars"}
          {" · hover pauses · REPLAY labeled"}
        </figcaption>
      </div>
    </figure>
  );
}
