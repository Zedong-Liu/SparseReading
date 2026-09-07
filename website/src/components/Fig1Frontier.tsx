import { useState } from "react";

/** Published Paper Figure 1 positions from latex/generate_fig1.py. */
const MODELS = [
  {
    label: "DeepSeek-V4-Pro",
    marker: "circle" as const,
    native: { cost: 1.025, score: 0.86 },
    sparse: { cost: 0.632, score: 0.91 },
    arrow: { x1: 1.01, y1: 0.865, x2: 0.66, y2: 0.908 },
    labelAt: { cost: 0.615, score: 0.945, ha: "start" as const },
  },
  {
    label: "Claude Opus 5",
    marker: "hex" as const,
    native: { cost: 0.97, score: 0.8675 },
    sparse: { cost: 0.34, score: 0.9077 },
    arrow: { x1: 0.954, y1: 0.8715, x2: 0.374, y2: 0.9067 },
    labelAt: { cost: 0.352, score: 0.9277, ha: "start" as const },
  },
  {
    label: "DeepSeek-V4-Flash",
    marker: "square" as const,
    native: { cost: 1.018, score: 0.795 },
    sparse: { cost: 0.485, score: 0.827 },
    arrow: { x1: 1.005, y1: 0.795, x2: 0.515, y2: 0.825 },
    labelAt: { cost: 0.475, score: 0.862, ha: "middle" as const },
  },
  {
    label: "Qwen3.5-35B",
    marker: "triangle" as const,
    native: { cost: 0.97, score: 0.533 },
    sparse: { cost: 0.72, score: 0.792 },
    arrow: { x1: 0.957, y1: 0.545, x2: 0.742, y2: 0.78 },
    labelAt: { cost: 0.69, score: 0.758, ha: "end" as const },
  },
];

const X0 = 0.3;
const X1 = 1.06;
const Y0 = 0.5;
const Y1 = 1.0;
const W = 720;
const H = 258;
const L = 58;
const R = 16;
const T = 22;
const B = 36;
const PW = W - L - R;
const PH = H - T - B;

function xOf(cost: number): number {
  return L + ((cost - X0) / (X1 - X0)) * PW;
}

function yOf(score: number): number {
  return T + (1 - (score - Y0) / (Y1 - Y0)) * PH;
}

function Marker({
  kind,
  cx,
  cy,
  fill,
  stroke,
  strokeWidth,
}: {
  kind: "hex" | "circle" | "square" | "triangle";
  cx: number;
  cy: number;
  fill: string;
  stroke: string;
  strokeWidth: number;
}) {
  const r = 8;
  if (kind === "circle") {
    return <circle cx={cx} cy={cy} r={r} fill={fill} stroke={stroke} strokeWidth={strokeWidth} />;
  }
  if (kind === "square") {
    return <rect x={cx - r} y={cy - r} width={r * 2} height={r * 2} fill={fill} stroke={stroke} strokeWidth={strokeWidth} />;
  }
  if (kind === "triangle") {
    const points = `${cx},${cy - r - 1} ${cx + r + 1},${cy + r} ${cx - r - 1},${cy + r}`;
    return <polygon points={points} fill={fill} stroke={stroke} strokeWidth={strokeWidth} />;
  }
  const pts = Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 180) * (60 * i - 30);
    return `${cx + (r + 1) * Math.cos(a)},${cy + (r + 1) * Math.sin(a)}`;
  }).join(" ");
  return <polygon points={pts} fill={fill} stroke={stroke} strokeWidth={strokeWidth} />;
}

function Arrow({ x1, y1, x2, y2 }: { x1: number; y1: number; x2: number; y2: number }) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const hx = x2 - ux * 9;
  const hy = y2 - uy * 9;
  const px = -uy;
  const py = ux;
  const points = `${x2},${y2} ${hx + px * 5},${hy + py * 5} ${hx - px * 5},${hy - py * 5}`;
  return (
    <g>
      <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#117f2b" strokeWidth="1.8" strokeDasharray="4 3" />
      <polygon points={points} fill="#117f2b" />
    </g>
  );
}

export default function Fig1Frontier() {
  const [hover, setHover] = useState<string | null>(null);
  const active = MODELS.find((model) => model.label === hover);

  return (
    <div className="fig1">
      <p className="kicker">Paper Figure 1</p>
      <h3>SparseRead shifts execution toward lower-cost frontier</h3>
      <div className="fig1-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Paper Figure 1 cost-quality frontier">
          <rect x={xOf(0.302)} y={yOf(1.0)} width={xOf(0.55) - xOf(0.302)} height={yOf(0.895) - yOf(1.0)} fill="#eef7ef" />
          <text x={xOf(0.318)} y={yOf(0.966)} fill="#117f2b" fontSize="11" fontWeight="700">
            desirable frontier
          </text>
          {[0.5, 0.6, 0.7, 0.8, 0.9, 1.0].map((tick) => (
            <g key={tick}>
              <line x1={L} x2={W - R} y1={yOf(tick)} y2={yOf(tick)} stroke="#d8dcdd" strokeDasharray="4 3" />
              <text x={L - 8} y={yOf(tick) + 4} textAnchor="end" fill="#202020" fontSize="11">
                {tick.toFixed(1)}
              </text>
            </g>
          ))}
          {[0.3, 0.5, 0.7, 0.9, 1.0].map((tick) => (
            <text key={tick} x={xOf(tick)} y={H - 12} textAnchor="middle" fill="#202020" fontSize="11">
              {tick.toFixed(1)}
            </text>
          ))}
          <line x1={L} x2={L} y1={T} y2={H - B} stroke="#202020" />
          <line x1={L} x2={W - R} y1={H - B} y2={H - B} stroke="#202020" />
          <text x={L + PW / 2} y={H - 2} textAnchor="middle" fill="#202020" fontSize="12" fontWeight="700">
            Normalized model-token cost (lower is better)
          </text>
          <text
            x={14}
            y={T + PH / 2}
            textAnchor="middle"
            fill="#202020"
            fontSize="12"
            fontWeight="700"
            transform={`rotate(-90 14 ${T + PH / 2})`}
          >
            Mean task score
          </text>
          {MODELS.map((model) => {
            const x1 = xOf(model.native.cost);
            const y1 = yOf(model.native.score);
            const x2 = xOf(model.sparse.cost);
            const y2 = yOf(model.sparse.score);
            const on = hover === model.label;
            return (
              <g
                key={model.label}
                className="fig1-series"
                opacity={hover && !on ? 0.35 : 1}
                onMouseEnter={() => setHover(model.label)}
                onMouseLeave={() => setHover(null)}
              >
                <Arrow x1={xOf(model.arrow.x1)} y1={yOf(model.arrow.y1)} x2={xOf(model.arrow.x2)} y2={yOf(model.arrow.y2)} />
                <circle cx={x1} cy={y1} r={16} fill="transparent" />
                <circle cx={x2} cy={y2} r={16} fill="transparent" />
                <Marker kind={model.marker} cx={x1} cy={y1} fill="#ffffff" stroke="#6c6e70" strokeWidth={on ? 2.2 : 1.6} />
                <Marker kind={model.marker} cx={x2} cy={y2} fill="#1fa43e" stroke="#117f2b" strokeWidth={on ? 1.4 : 0.7} />
                <text
                  x={xOf(model.labelAt.cost)}
                  y={yOf(model.labelAt.score)}
                  textAnchor={model.labelAt.ha}
                  fill="#161616"
                  fontSize="11"
                  fontWeight="700"
                >
                  {model.label}
                </text>
              </g>
            );
          })}
          <text x={xOf(0.53)} y={yOf(0.66)} fill="#b75e17" fontSize="12" fontWeight="700">
            Stronger models save more tokens;
          </text>
          <text x={xOf(0.53)} y={yOf(0.62)} fill="#b75e17" fontSize="12" fontWeight="700">
            the smaller model gains more accuracy.
          </text>
          <g transform={`translate(${L + 10}, ${H - B - 34})`}>
            <rect x="0" y="0" width="168" height="26" fill="#fff" stroke="#202020" />
            <rect x="8" y="8" width="10" height="10" fill="#fff" stroke="#6c6e70" strokeWidth="1.5" />
            <text x="22" y="17" fill="#161616" fontSize="11" fontWeight="700">
              Native
            </text>
            <rect x="78" y="8" width="10" height="10" fill="#1fa43e" stroke="#117f2b" strokeWidth="1.5" />
            <text x="92" y="17" fill="#161616" fontSize="11" fontWeight="700">
              SparseRead
            </text>
          </g>
        </svg>
        {active && (
          <div className="fig1-tip">
            <b>{active.label}</b>
            <span>
              Native · cost {active.native.cost.toFixed(2)} · score {active.native.score.toFixed(2)}
            </span>
            <span>
              SparseRead · cost {active.sparse.cost.toFixed(2)} · score {active.sparse.score.toFixed(2)}
            </span>
          </div>
        )}
      </div>
      <p className="note">
        Paper Figure 1. Qwen3.5-35B appears in this motivation figure, not the six-model NanoBot matrix.
      </p>
    </div>
  );
}
