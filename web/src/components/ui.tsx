import { useEffect, useState, type ReactNode } from "react";

import type { CanaryPublic } from "../types.ts";

const words = (value: string) => value.replaceAll("_", " ");

export function Badge({ state }: { state: string }) {
  return <span className={`badge badge-${state}`}>{words(state)}</span>;
}

export function Canaries({ items }: { items: CanaryPublic[] }) {
  return (
    <ul className="tags">
      {items.map((canary) => (
        <li key={`${canary.type}:${canary.public_label}`}>
          <span className="kind">{words(canary.type)}</span> {canary.public_label}
        </li>
      ))}
    </ul>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return children ? (
    <p className="error" role="alert">
      {children}
    </p>
  ) : null;
}

/** A value to copy exactly, such as an address or a deposit code. */
export function Copyable({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy">
      <div>
        <span className="label">{label}</span>
        <code>{value}</code>
      </div>
      <button
        type="button"
        className="secondary"
        onClick={() => {
          void navigator.clipboard.writeText(value).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/** Grey blocks in the shape of what is loading, so the page does not jump when it arrives. */
export function Skeleton({ lines = 3, block = false }: { lines?: number; block?: boolean }) {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading">
      {block ? <i className="bone block" /> : null}
      {Array.from({ length: lines }, (_, index) => (
        <i key={index} className={`bone line w${index % 3}`} />
      ))}
    </div>
  );
}

const UNITS: [number, Intl.RelativeTimeFormatUnit][] = [
  [60, "second"],
  [60, "minute"],
  [24, "hour"],
  [7, "day"],
  [4.35, "week"],
  [12, "month"],
  [Infinity, "year"],
];

/** "3 minutes ago". */
export function ago(iso: string, now = Date.now()): string {
  let amount = (new Date(iso).getTime() - now) / 1000;
  for (const [size, unit] of UNITS) {
    if (Math.abs(amount) < size) {
      return new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(Math.round(amount), unit);
    }
    amount /= size;
  }
  return "";
}

/** A time that stays true while the page is open: "just now" does not sit there for an hour. */
export function Ago({ iso }: { iso: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick((count) => count + 1), 30_000);
    return () => clearInterval(timer);
  }, []);
  return <time dateTime={iso} title={when(iso)}>{ago(iso)}</time>;
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export { words };
