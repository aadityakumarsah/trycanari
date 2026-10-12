import { useEffect, useRef, useState, type ReactNode } from "react";

import { Icon, type IconName } from "./Brand.tsx";

/**
 * One bounty's day, drawn as a timeline: an attack that verifies and is paid, one
 * that fails, and the sponsor's patch. The geometry is fixed, in the SVG's own
 * units, so the lines always meet the cards; the whole drawing scales as one.
 */

const LINE = 270;
const MARKS = [290, 400, 540, 610, 680, 750, 890, 960, 1030, 1090, 1215];
const KEY_MARKS: { x: number; time: string; above: boolean }[] = [
  { x: 330, time: "09:45", above: true },
  { x: 470, time: "11:45", above: false },
  { x: 820, time: "16:45", above: true },
  { x: 1150, time: "18:45", above: false },
];

function Pill({
  x,
  y,
  width,
  tone,
  app,
  verb,
  icon,
  label,
}: {
  x: number;
  y: number;
  width: number;
  tone: string;
  app: IconName;
  verb: string;
  icon: IconName;
  label: string;
}) {
  return (
    <foreignObject x={x} y={y} width={width} height={46}>
      <div className={`tl-pill ${tone}`}>
        <span className="tl-app">
          <Icon name={app} size={15} />
        </span>
        <span className="tl-verb">{verb}</span>
        <Icon name={icon} size={16} />
        <span>{label}</span>
      </div>
    </foreignObject>
  );
}

function Card({
  x,
  y,
  width,
  badge,
  tone = "",
  children,
}: {
  x: number;
  y: number;
  width: number;
  badge: string;
  tone?: string;
  children: ReactNode;
}) {
  return (
    <foreignObject x={x} y={y} width={width} height={52}>
      <div className="tl-card">
        <b className={`tl-badge ${tone}`}>{badge}</b>
        <span>{children}</span>
      </div>
    </foreignObject>
  );
}

/** A status circle on a route, with its caption above or below. */
function Stop({
  x,
  y,
  tone,
  caption,
  below = false,
  order,
  children,
}: {
  x: number;
  y: number;
  tone: "good" | "bad";
  caption: string;
  below?: boolean;
  order: number;
  children: ReactNode;
}) {
  return (
    <g className={`tl-stop ${tone} d${order}`} transform={`translate(${x} ${y})`}>
      <circle r={24} />
      {children}
      <text y={below ? 50 : -38} textAnchor="middle">
        {caption}
      </text>
    </g>
  );
}

export function AttackTimeline() {
  const root = useRef<HTMLDivElement>(null);
  // The routes draw themselves the first time the section scrolls into view.
  const [seen, setSeen] = useState(typeof IntersectionObserver === "undefined");

  useEffect(() => {
    const element = root.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    const watcher = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setSeen(true);
          watcher.disconnect();
        }
      },
      { threshold: 0.3 },
    );
    watcher.observe(element);
    return () => watcher.disconnect();
  }, []);

  return (
    <div ref={root} className={seen ? "timeline seen" : "timeline"}>
      <svg
        viewBox="0 0 1280 540"
        role="img"
        aria-label="A live agent on a timeline. An attack through a document is replayed five times, verified and paid. An attack through the user message trips nothing and is not paid. The sponsor's patch is checked by replaying the paid attack."
      >
        {/* The timeline, and the quiet marks along it. */}
        <line className="tl-line" x1={0} y1={LINE} x2={1280} y2={LINE} />
        {MARKS.map((x) => (
          <circle key={x} className="tl-mark" cx={x} cy={LINE} r={4} />
        ))}

        {/* How each branch leaves the timeline. */}
        <g className="tl-dashed">
          <path d={`M470 ${LINE}V196`} />
          <path d="M470 150V84a12 12 0 0 1 12-12H560" />
          <path d={`M330 ${LINE}V330`} />
          <path d="M330 376V438a12 12 0 0 0 12 12H390" />
          <path d={`M820 ${LINE}V330`} />
          <path d="M820 376V438a12 12 0 0 0 12 12H900" />
        </g>

        {/* Verified: on to the payout, and back to the timeline. */}
        <path className="tl-route good" pathLength={1} d={`M792 72H1138a12 12 0 0 1 12 12V${LINE}`} />
        {/* Not verified: it ends here. */}
        <path className="tl-route bad" pathLength={1} d="M622 450H785" />

        {KEY_MARKS.map((mark) => (
          <g key={mark.x} className="tl-key" transform={`translate(${mark.x} ${LINE})`}>
            <circle r={9} />
            <circle className="dot" r={3.5} />
            <text y={mark.above ? -20 : 32} textAnchor="middle">
              {mark.time}
            </text>
          </g>
        ))}

        <Card x={40} y={244} width={210} badge="LIVE" tone="live">
          Treasury agent
        </Card>

        <Pill x={360} y={150} width={220} tone="violet" app="mask" verb="Attack via" icon="doc" label="Document" />
        <Card x={560} y={46} width={232} badge="REPLAY ×5">
          Forged-policy
        </Card>
        <Stop x={890} y={72} tone="good" caption="3 of 5 Tripped" order={1}>
          <path d="M-8 0l5.500 5.500L8-6" />
        </Stop>
        <Stop x={1020} y={72} tone="good" caption="Reward Paid" order={2}>
          <path d="M0 9V-9M-7-2l7-7 7 7" />
        </Stop>

        <Pill x={200} y={330} width={258} tone="orange" app="bot" verb="Attack via" icon="chat" label="User message" />
        <Card x={390} y={424} width={232} badge="REPLAY ×5">
          Direct-override
        </Card>
        <Stop x={675} y={450} tone="bad" caption="0 of 5 Tripped" below order={1}>
          <path d="M0-9V2M0 8v.500" />
        </Stop>
        <Stop x={785} y={450} tone="bad" caption="No Payout" below order={2}>
          <path d="M-6.500-6.500l13 13M6.500-6.500l-13 13" />
        </Stop>

        <Pill x={722} y={330} width={258} tone="green" app="shield" verb="Sponsor patched" icon="code" label="Config" />
        <Card x={900} y={424} width={206} badge="RE-ARM">
          Config-v2
        </Card>
      </svg>
    </div>
  );
}
