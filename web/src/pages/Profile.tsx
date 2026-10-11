import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { accountChanged, completeness, pictureOf, PROFILE_FIELDS, useAccount } from "../account.tsx";
import { api } from "../api.ts";
import { Icon, type IconName } from "../components/Brand.tsx";
import { ReferralGate } from "../components/ReferralGate.tsx";
import { Avatar, ErrorNote, Skeleton } from "../components/ui.tsx";
import { useAction } from "../hooks.ts";
import { useToast } from "../toast.tsx";
import type { Account } from "../types.ts";

type Field = (typeof PROFILE_FIELDS)[number];
type Draft = Record<Field, string>;

const BIO_LIMIT = 400;

/** The form's starting values: what the account has, with nothing as an empty box. */
export function draftOf(account: Account): Draft {
  return Object.fromEntries(PROFILE_FIELDS.map((field) => [field, account[field] ?? ""])) as Draft;
}

/** Only the fields that differ from what is saved, which is all the API needs to be sent. */
export function changes(draft: Draft, account: Account): Partial<Draft> {
  const saved = draftOf(account);
  return Object.fromEntries(PROFILE_FIELDS.filter((field) => draft[field].trim() !== saved[field]).map((field) => [field, draft[field].trim()]));
}

/** A GitHub username out of whatever was pasted: a profile address or an @handle. */
export function githubName(value: string): string {
  return value.trim().replace(/^(https?:\/\/)?(www\.)?(github\.com\/)?@?/i, "").replace(/\/+$/, "");
}

/** A ring that fills as the profile does. */
function Ring({ percent }: { percent: number }) {
  const radius = 26;
  const around = 2 * Math.PI * radius;
  return (
    <span className="ring" role="img" aria-label={`Profile ${percent}% complete`}>
      <svg viewBox="0 0 64 64" width="64" height="64">
        <circle className="ring-track" cx="32" cy="32" r={radius} />
        <circle
          className="ring-fill"
          cx="32"
          cy="32"
          r={radius}
          strokeDasharray={around}
          strokeDashoffset={around * (1 - percent / 100)}
        />
      </svg>
      <b>{percent}%</b>
    </span>
  );
}

function Row({ icon, children }: { icon: IconName; children: React.ReactNode }) {
  return (
    <li>
      <Icon name={icon} size={15} />
      <span>{children}</span>
    </li>
  );
}

/** How the profile reads to someone else, updating as the form is typed into. */
function Card({ account, draft, github }: { account: Account; draft: Draft; github: string }) {
  const shown = { ...account, ...Object.fromEntries(PROFILE_FIELDS.map((f) => [f, draft[f].trim() || null])) } as Account;
  const profile = completeness(shown);
  const steps: [string, boolean][] = [
    ["Name", Boolean(shown.name)],
    ["GitHub", Boolean(shown.github)],
    ["Company", Boolean(shown.company)],
    ["Role", Boolean(shown.role)],
    ["Location", Boolean(shown.location)],
    ["Website", Boolean(shown.website)],
    ["Bio", Boolean(shown.bio)],
  ];
  return (
    <aside className="profile-card">
      <div className="profile-cover" aria-hidden="true" />
      <div className="profile-face">
        <span className="halo">
          <Avatar name={shown.name} picture={pictureOf({ picture_url: account.picture_url, github: github || null })} size={96} />
        </span>
        <span className={`tier-pill ${account.credits.tier.toLowerCase()}`}>
          <Icon name="shield" size={12} /> {account.credits.tier}
        </span>
      </div>
      <h2>{shown.name ?? "Your name"}</h2>
      <p className="profile-line">
        {shown.role || shown.company
          ? [shown.role, shown.company].filter(Boolean).join(" at ")
          : "Your role and company appear here"}
      </p>
      {shown.bio && <p className="profile-bio">{shown.bio}</p>}
      <ul className="profile-links">
        {shown.location && <Row icon="pin">{shown.location}</Row>}
        {github && (
          <Row icon="github">
            <a href={`https://github.com/${github}`} target="_blank" rel="noreferrer">
              github.com/{github}
            </a>
          </Row>
        )}
        {shown.website && (
          <Row icon="globe">
            <a href={shown.website} target="_blank" rel="noreferrer">
              {shown.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}
            </a>
          </Row>
        )}
        <Row icon="chat">{account.email}</Row>
      </ul>
      <dl className="profile-stats">
        <div>
          <dt>Breaks</dt>
          <dd>{account.verified_breaks}</dd>
        </div>
        <div>
          <dt>Credits</dt>
          <dd>
            {account.credits.balance}
            <small> / {account.credits.cap}</small>
          </dd>
        </div>
        <div>
          <dt>Hunting</dt>
          <dd className={account.can_hunt ? "yes" : "no"}>{account.can_hunt ? "Open" : "Locked"}</dd>
        </div>
      </dl>
      <div className="profile-progress">
        <Ring percent={profile.percent} />
        <div>
          <b>{profile.percent === 100 ? "Profile complete" : "Complete your profile"}</b>
          <ul className="ticks">
            {steps.map(([label, done]) => (
              <li key={label} className={done ? "done" : undefined}>
                {label}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </aside>
  );
}

function Input({
  label,
  icon,
  hint,
  required = false,
  prefix,
  ...input
}: {
  label: string;
  icon: IconName;
  hint?: string;
  prefix?: string;
  required?: boolean;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {required && <em>required</em>}
      </span>
      <span className={prefix ? "field-box with-prefix" : "field-box"}>
        <Icon name={icon} size={16} />
        {prefix && <span className="field-prefix">{prefix}</span>}
        <input required={required} autoComplete="off" {...input} />
      </span>
      {hint && <small>{hint}</small>}
    </label>
  );
}

/** A key for scripts and agents. It is shown once, and making one retires the last. */
function ApiKey() {
  const [key, setKey] = useState("");
  const minting = useAction();
  const toast = useToast();

  async function mint() {
    if (!window.confirm("Make a new API key? Any key this account already has will stop working at once.")) return;
    await minting.run(async () => {
      const made = await api<{ api_key: string }>("POST", "/v1/account/api-key");
      setKey(made.api_key);
    });
  }

  return (
    <section className="panel form-section">
      <h2>
        <Icon name="key" size={17} /> API key
      </h2>
      <p className="muted">
        For scripts and AI agents that act as you. Only one key works at a time, and it is shown once: making a
        new one retires the old.
      </p>
      {key ? (
        <>
          <p className="notice">Copy this now. It will not be shown again.</p>
          <pre className="secret">{key}</pre>
          <div className="form-foot">
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(key).then(() => toast("API key copied"));
              }}
            >
              Copy key
            </button>
            <button type="button" className="secondary" onClick={() => setKey("")}>
              I have saved it
            </button>
          </div>
        </>
      ) : (
        <button type="button" className="secondary" disabled={minting.busy} onClick={() => void mint()}>
          {minting.busy ? "Making…" : "Make a new API key"}
        </button>
      )}
      <ErrorNote>{minting.error}</ErrorNote>
    </section>
  );
}

export function Profile() {
  const loaded = useAccount();
  const account = loaded.data;
  const [draft, setDraft] = useState<Draft | null>(null);
  const saving = useAction();
  const toast = useToast();
  // The username whose picture the card shows. It follows the box only once the box is
  // left, so a picture is not fetched for every letter typed.
  const [previewGithub, setPreviewGithub] = useState<string | null>(null);

  // Start from what is saved, once it has arrived. After that the form is the person's.
  useEffect(() => {
    if (account && draft === null) setDraft(draftOf(account));
  }, [account, draft]);

  const pending = useMemo(() => (account && draft ? changes(draft, account) : {}), [account, draft]);
  const dirty = Object.keys(pending).length > 0;

  // Leaving with unsaved changes asks first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (!account || !draft) return loaded.error ? <ErrorNote>{loaded.error}</ErrorNote> : <Skeleton block lines={6} />;

  const set = (field: Field) => (event: { target: { value: string } }) =>
    setDraft((current) => (current ? { ...current, [field]: event.target.value } : current));

  async function save(event: FormEvent) {
    event.preventDefault();
    const done = await saving.run(async () => {
      const body = { ...pending, ...(pending.github === undefined ? {} : { github: githubName(pending.github) }) };
      const saved = await api<Account>("PATCH", "/v1/account", { body });
      setDraft(draftOf(saved));
      accountChanged();
    });
    if (done) toast("Profile saved");
  }

  return (
    <>
      <p className="crumb">
        <Link to="/dashboard">← Dashboard</Link>
      </p>
      <div className="profile-layout">
        <Card account={account} draft={draft} github={previewGithub ?? account.github ?? ""} />

        <div className="profile-main">
          <header className="profile-head">
            <h1>Your profile</h1>
            <p className="muted">
              Who you are on trycanari. Sponsors see this when you break their agent, and it is how people know
              whose work they are looking at.
            </p>
          </header>

          {account.invite_only && !account.can_hunt && <ReferralGate compact />}

          <form id="profile-form" onSubmit={(event) => void save(event)}>
            <section className="panel form-section">
              <h2>
                <Icon name="user" size={17} /> About you
              </h2>
              <div className="field-grid">
                <Input label="Name" icon="user" required maxLength={60} placeholder="Ada Lovelace" value={draft.name} onChange={set("name")} />
                <Input label="Role" icon="briefcase" maxLength={80} placeholder="Security engineer" value={draft.role} onChange={set("role")} />
                <Input label="Current company" icon="grid" required maxLength={80} placeholder="Where you work, or Independent" value={draft.company} onChange={set("company")} />
                <Input label="Location" icon="pin" maxLength={80} placeholder="City, country" value={draft.location} onChange={set("location")} />
              </div>
            </section>

            <section className="panel form-section">
              <h2>
                <Icon name="link" size={17} /> Links
              </h2>
              <div className="field-grid">
                <Input
                  label="GitHub"
                  icon="github"
                  required
                  prefix="github.com/"
                  maxLength={80}
                  placeholder="username"
                  spellCheck={false}
                  hint="Your username, or paste your profile address. Your GitHub picture is used if you have no other."
                  value={draft.github}
                  onChange={set("github")}
                  onBlur={() => {
                    const name = githubName(draft.github);
                    setDraft((current) => (current ? { ...current, github: name } : current));
                    setPreviewGithub(name);
                  }}
                />
                <Input
                  label="Website"
                  icon="globe"
                  type="url"
                  maxLength={200}
                  placeholder="https://"
                  hint="A blog, portfolio or company page, starting with https://"
                  value={draft.website}
                  onChange={set("website")}
                />
              </div>
            </section>

            <section className="panel form-section">
              <h2>
                <Icon name="doc" size={17} /> Bio
              </h2>
              <label className="field">
                <span className="field-label">
                  A few lines about you
                  <span className={draft.bio.length > BIO_LIMIT - 40 ? "counter near" : "counter"}>
                    {draft.bio.length} / {BIO_LIMIT}
                  </span>
                </span>
                <textarea
                  rows={4}
                  maxLength={BIO_LIMIT}
                  placeholder="What you work on, what you like breaking, what you have found before."
                  value={draft.bio}
                  onChange={set("bio")}
                />
              </label>
            </section>
            <ErrorNote>{saving.error}</ErrorNote>
          </form>

          <ApiKey />
        </div>
      </div>

      <div className={dirty ? "save-bar shown" : "save-bar"} aria-hidden={!dirty}>
        <span>
          <i /> You have unsaved changes
        </span>
        <div>
          <button
            type="button"
            className="secondary"
            tabIndex={dirty ? 0 : -1}
            onClick={() => {
              setDraft(draftOf(account));
              setPreviewGithub(null);
            }}
          >
            Discard
          </button>
          <button form="profile-form" className="accent" tabIndex={dirty ? 0 : -1} disabled={saving.busy || !dirty}>
            {saving.busy ? "Saving…" : "Save profile"}
          </button>
        </div>
      </div>
    </>
  );
}
