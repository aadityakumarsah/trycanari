import { Icon, type IconName } from "./Brand.tsx";

type Cell = [IconName, string];

// What goes into the sandbox, and what comes out of it. Three columns a side.
const ATTACKS: Cell[][] = [
  [
    ["chat", "Prompt injection"],
    ["doc", "Poisoned document"],
    ["mask", "Role-play"],
    ["code", "Encoding tricks"],
  ],
  [
    ["bot", "Attacker agents"],
    ["layers", "Nested instructions"],
    ["zap", "Urgency and pressure"],
    ["box", "Tool misuse"],
    ["search", "Probing"],
  ],
  [
    ["link", "Smuggled links"],
    ["eye", "Prompt extraction"],
    ["target", "Goal hijacking"],
    ["key", "Secret fishing"],
  ],
];

const PROOF: Cell[][] = [
  [
    ["repeat", "Five replays"],
    ["ban", "Canary tripped"],
    ["check", "3-of-5 verdict"],
    ["coin", "Shielded payout"],
  ],
  [
    ["doc", "Full transcripts"],
    ["lock", "Pinned config"],
    ["shield", "Mock tools only"],
    ["key", "Encrypted at rest"],
    ["clock", "Paid in minutes"],
  ],
  [
    ["grid", "Append-only ledger"],
    ["repeat", "Re-arm check"],
    ["target", "Exact evidence"],
    ["check", "No triage"],
  ],
];

function Column({ cells, index }: { cells: Cell[]; index: number }) {
  // The list is laid out twice so the loop has no seam; the copy is hidden from readers.
  return (
    <div className={`flow-col flow-col-${index}`}>
      {[false, true].map((copy) => (
        <ul key={String(copy)} aria-hidden={copy || undefined}>
          {cells.map(([icon, label]) => (
            <li key={label} className="flow-cell">
              <Icon name={icon} size={26} />
              <span>{label}</span>
            </li>
          ))}
        </ul>
      ))}
    </div>
  );
}

function Side({ columns, side, label }: { columns: Cell[][]; side: "left" | "right"; label: string }) {
  return (
    <div className={`flow-wrap flow-wrap-${side}`} role="group" aria-label={label}>
      <div className="flow-grid">
        {columns.map((cells, index) => (
          <Column key={index} cells={cells} index={index} />
        ))}
      </div>
    </div>
  );
}

/** Attacks stream in on the left, the sandbox sits in the middle, proof streams out on the right. */
export function Flow() {
  return (
    <div className="flow">
      <div className="flow-bg" aria-hidden="true" />
      <Side columns={ATTACKS} side="left" label="What hunters try" />
      <span className="flow-line flow-line-left" aria-hidden="true" />
      <span className="flow-line flow-line-right" aria-hidden="true" />
      <div className="flow-hub">
        <img src="/app/brand/mascot.png" alt="The trycanari sandbox" width={76} height={76} />
      </div>
      <Side columns={PROOF} side="right" label="What comes out" />
    </div>
  );
}
