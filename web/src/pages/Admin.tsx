import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { inviteLink, pictureOf, useAccount } from "../account.tsx";
import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { Ago, Avatar, ErrorNote, Skeleton } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { useToast } from "../toast.tsx";
import type { AdminCode, Person } from "../types.ts";

/** A switch for one thing about one person. It shows the change at once and takes it back if refused. */
function Toggle({
  label,
  on,
  locked,
  onChange,
}: {
  label: string;
  on: boolean;
  locked?: string | undefined;
  onChange: (next: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={locked ?? label}
      className={on ? "switch on" : "switch"}
      disabled={busy || locked !== undefined}
      onClick={() => {
        setBusy(true);
        void onChange(!on).finally(() => setBusy(false));
      }}
    >
      <i />
    </button>
  );
}

function People() {
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const people = useLoad(
    (signal) => api<Person[]>("GET", `/v1/admin/people${query ? `?q=${encodeURIComponent(query)}` : ""}`, { signal }),
    query,
    `admin:people:${query}`,
  );
  const me = useAccount().data;
  const toast = useToast();

  // Search a moment after typing stops, not on every letter.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(typed.trim()), 300);
    return () => clearTimeout(timer);
  }, [typed]);

  async function change(person: Person, body: Record<string, boolean>, said: string) {
    try {
      await api<Person>("PATCH", `/v1/admin/people/${person.id}`, { body });
      toast(said);
    } catch (error) {
      toast(error instanceof Error ? error.message : "That did not work", "bad");
    }
    await people.reload();
  }

  const list = people.data ?? [];
  const who = (person: Person) => person.name ?? person.email;
  return (
    <section>
      <div className="toolbar">
        <label className="search">
          <Icon name="search" size={16} />
          <input
            type="search"
            placeholder="Search by name, email, GitHub or company"
            aria-label="Search people"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
          />
        </label>
        <span className="muted count-note">
          {list.length} {list.length === 1 ? "person" : "people"}
        </span>
      </div>
      <ErrorNote>{people.error}</ErrorNote>
      {!people.data && !people.error && <Skeleton lines={4} />}
      {people.data && list.length === 0 && <p className="empty">Nobody matches “{query}”.</p>}
      {list.length > 0 && (
        <div className="scroll">
          <table className="people">
            <thead>
              <tr>
                <th>Person</th>
                <th>Joined</th>
                <th>Can hunt</th>
                <th>Ambassador</th>
                <th>Admin</th>
              </tr>
            </thead>
            <tbody>
              {list.map((person) => (
                <tr key={person.id}>
                  <td>
                    <span className="person">
                      <Avatar name={person.name} picture={pictureOf(person)} size={34} />
                      <span>
                        <b>
                          {person.name ?? "No name yet"}
                          {person.is_owner && <em className="role-tag owner">Owner</em>}
                        </b>
                        <small>
                          {person.email}
                          {person.company ? ` · ${person.company}` : ""}
                        </small>
                      </span>
                    </span>
                  </td>
                  <td>
                    <Ago iso={person.created_at} />
                  </td>
                  <td>
                    <Toggle
                      label={`Let ${who(person)} take part in bounties`}
                      on={person.can_hunt}
                      locked={person.can_hunt ? "Access, once given, stays" : undefined}
                      onChange={() => change(person, { can_hunt: true }, `${who(person)} can hunt now`)}
                    />
                  </td>
                  <td>
                    <Toggle
                      label={`Make ${who(person)} an ambassador`}
                      on={person.is_ambassador}
                      onChange={(next) =>
                        change(
                          person,
                          { is_ambassador: next },
                          next ? `${who(person)} is now an ambassador` : `${who(person)} is no longer an ambassador`,
                        )
                      }
                    />
                  </td>
                  <td>
                    <Toggle
                      label={`Make ${who(person)} an admin`}
                      on={person.is_admin}
                      locked={
                        person.is_owner
                          ? "An owner is always an admin"
                          : person.email === me?.email
                            ? "You cannot remove your own admin access"
                            : undefined
                      }
                      onChange={(next) =>
                        change(
                          person,
                          { is_admin: next },
                          next ? `${who(person)} is now an admin` : `${who(person)} is no longer an admin`,
                        )
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Codes() {
  const codes = useLoad((signal) => api<AdminCode[]>("GET", "/v1/admin/referral-codes", { signal }), "codes", "admin:codes");
  const [uses, setUses] = useState("10");
  const [custom, setCustom] = useState("");
  const making = useAction();
  const toast = useToast();

  async function make(event: FormEvent) {
    event.preventDefault();
    await making.run(async () => {
      const body = { max_uses: Number(uses), ...(custom.trim() ? { code: custom.trim() } : {}) };
      const made = await api<AdminCode>("POST", "/v1/admin/referral-codes", { body });
      await navigator.clipboard.writeText(inviteLink(made.code)).catch(() => undefined);
      setCustom("");
      await codes.reload();
      toast(`${made.code} created. Its invite link is copied.`);
    });
  }

  const list = codes.data ?? [];
  const joined = list.reduce((sum, code) => sum + code.used, 0);
  return (
    <section>
      <form className="panel code-maker" onSubmit={(event) => void make(event)}>
        <div>
          <h2>New referral code</h2>
          <p className="muted">
            Anyone who opens its invite link can take part in bounties, until its places run out.
          </p>
        </div>
        <label>
          Places
          <input
            required
            type="number"
            min={1}
            max={10000}
            inputMode="numeric"
            value={uses}
            onChange={(event) => setUses(event.target.value)}
          />
        </label>
        <label>
          Code <small>optional</small>
          <input
            className="code"
            placeholder="Generated if empty"
            minLength={4}
            maxLength={32}
            pattern="[A-Za-z0-9\-]+"
            spellCheck={false}
            value={custom}
            onChange={(event) => setCustom(event.target.value.toUpperCase())}
          />
        </label>
        <button className="accent" disabled={making.busy}>
          <Icon name="plus" size={15} /> {making.busy ? "Creating…" : "Create and copy link"}
        </button>
        <ErrorNote>{making.error}</ErrorNote>
      </form>

      <ErrorNote>{codes.error}</ErrorNote>
      {!codes.data && !codes.error && <Skeleton lines={4} />}
      {codes.data && list.length === 0 && <p className="empty">No referral code has been made yet.</p>}
      {list.length > 0 && (
        <>
          <p className="muted count-note">
            {list.length} {list.length === 1 ? "code" : "codes"}, {joined} {joined === 1 ? "person" : "people"} brought
            in.
          </p>
          <div className="scroll">
            <table className="people">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Places used</th>
                  <th>Made by</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((code) => (
                  <tr key={code.code}>
                    <td>
                      <code className="code-chip">{code.code}</code>
                    </td>
                    <td>
                      <span className="use">
                        <span className="meter" role="img" aria-label={`${code.used} of ${code.max_uses} places used`}>
                          <i className={`fill p${Math.min(100, Math.round((code.used / code.max_uses) * 10) * 10)}`} />
                        </span>
                        {code.used} / {code.max_uses}
                      </span>
                    </td>
                    <td>{code.owner_email ?? "The operator"}</td>
                    <td>
                      <Ago iso={code.created_at} />
                    </td>
                    <td className="num">
                      <button
                        type="button"
                        className="secondary small-button"
                        onClick={() => {
                          void navigator.clipboard.writeText(inviteLink(code.code)).then(() => toast("Invite link copied"));
                        }}
                      >
                        <Icon name="link" size={14} /> Copy link
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

const TABS = ["people", "codes"] as const;

export function Admin() {
  const account = useAccount();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "codes" ? "codes" : "people";

  if (!account.data) return account.error ? <ErrorNote>{account.error}</ErrorNote> : <Skeleton block lines={4} />;
  // Not an admin: as far as they are concerned there is no such page.
  if (!account.data.is_admin) return <NotFound />;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="eyebrow">Admin</p>
          <h1>Run the platform</h1>
        </div>
      </div>
      <p className="lede">
        Decide who can hunt, who can invite others, and who helps you run trycanari. Changes take effect at once.
      </p>
      <div className="tabs" role="tablist" aria-label="Admin">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "tab current" : "tab"}
            onClick={() => setParams(name === "people" ? {} : { tab: name }, { replace: true })}
          >
            {name === "people" ? "People" : "Referral codes"}
          </button>
        ))}
      </div>
      <div role="tabpanel">{tab === "people" ? <People /> : <Codes />}</div>
    </>
  );
}
