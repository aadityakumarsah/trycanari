import { useState, type ReactNode } from "react";

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

export function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export { words };
