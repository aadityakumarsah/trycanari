import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { inviteLink, pictureOf, useAccount } from "../account.tsx";
import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { Ago, Avatar, ErrorNote, Skeleton } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { useToast } from "../toast.tsx";
import type { AdminAction, AdminCode, Page, Person } from "../types.ts";

const PAGE = 25;

/** A switch for one thing about one person. */
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

/** Previous and next, with where you are, for a list too long to show at once. */
function Pager({ offset, total, onMove }: { offset: number; total: number; onMove: (offset: number) => void }) {
  if (total <= PAGE) return null;
  const last = Math.min(offset + PAGE, total);
  return (
    <nav className="pager" aria-label="Pages">
      <span className="muted">
        {offset + 1}–{last} of {total}
      </span>
      <button type="button" className="secondary small-button" disabled={offset === 0} onClick={() => onMove(Math.max(0, offset - PAGE))}>
        Previous
      </button>
      <button type="button" className="secondary small-button" disabled={last >= total} onClick={() => onMove(offset + PAGE)}>
        Next
      </button>
    </nav>
  );
}

function People() {
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const address = `/v1/admin/people?limit=${PAGE}&offset=${offset}${query ? `&q=${encodeURIComponent(query)}` : ""}`;
  const people = useLoad((signal) => api<Page<Person>>("GET", address, { signal }), address);
  const me = useAccount().data;
  const toast = useToast();

  // Search a moment after typing stops, not on every letter, and from the first page.
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(typed.trim());
      setOffset(0);
    }, 300);
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

  const list = people.data?.items ?? [];
  const total = people.data?.total ?? 0;
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
          {total} {total === 1 ? "person" : "people"}
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
                      locked={person.referral_code ? "Joined with an invite, which cannot be taken back" : undefined}
                      onChange={(next) =>
                        change(
                          person,
                          { hunting_granted: next },
                          next ? `${who(person)} can hunt now` : `${who(person)} can no longer hunt`,
                        )
                      }
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
                          ? "The owner is always an admin"
                          : me?.is_owner
                            ? undefined
                            : "Only the owner can make or remove admins"
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
      <Pager offset={offset} total={total} onMove={setOffset} />
    </section>
  );
}

function Codes() {
  const [offset, setOffset] = useState(0);
  const address = `/v1/admin/referral-codes?limit=${PAGE}&offset=${offset}`;
  const codes = useLoad((signal) => api<Page<AdminCode>>("GET", address, { signal }), address);
  const [count, setCount] = useState("1");
  const [custom, setCustom] = useState("");
  const making = useAction();
  const toast = useToast();
  const many = Number(count) > 1;

  async function make(event: FormEvent) {
    event.preventDefault();
    await making.run(async () => {
      const body = { count: Number(count), ...(custom.trim() && !many ? { code: custom.trim() } : {}) };
      const made = await api<AdminCode[]>("POST", "/v1/admin/referral-codes", { body });
      await navigator.clipboard.writeText(made.map((item) => inviteLink(item.code)).join("\n")).catch(() => undefined);
      setCustom("");
      setOffset(0);
      await codes.reload();
      toast(made.length === 1 ? `${made[0]?.code} created. Its link is copied.` : `${made.length} invites created. Their links are copied.`);
    });
  }

  const list = codes.data?.items ?? [];
  const total = codes.data?.total ?? 0;
  return (
    <section>
      <form className="panel code-maker" onSubmit={(event) => void make(event)}>
        <div>
          <h2>New invites</h2>
          <p className="muted">Each invite link lets one person take part in bounties, and works once.</p>
        </div>
        <label>
          How many
          <input
            required
            type="number"
            min={1}
            max={500}
            inputMode="numeric"
            value={count}
            onChange={(event) => setCount(event.target.value)}
          />
        </label>
        <label>
          Code <small>optional, for one</small>
          <input
            className="code"
            placeholder={many ? "Generated" : "Generated if empty"}
            minLength={6}
            maxLength={32}
            pattern="[A-Za-z0-9\-]+"
            spellCheck={false}
            disabled={many}
            value={many ? "" : custom}
            onChange={(event) => setCustom(event.target.value.toUpperCase())}
          />
        </label>
        <button className="accent" disabled={making.busy}>
          <Icon name="plus" size={15} /> {making.busy ? "Creating…" : "Create and copy"}
        </button>
        <ErrorNote>{making.error}</ErrorNote>
      </form>

      <ErrorNote>{codes.error}</ErrorNote>
      {!codes.data && !codes.error && <Skeleton lines={4} />}
      {codes.data && total === 0 && <p className="empty">No invite has been made yet.</p>}
      {list.length > 0 && (
        <>
          <p className="muted count-note">
            {total} {total === 1 ? "invite" : "invites"} in all.
          </p>
          <div className="scroll">
            <table className="people">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Status</th>
                  <th>Made by</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((code) => (
                  <tr key={code.code} className={code.used_by ? "spent" : undefined}>
                    <td>
                      <code className="code-chip">{code.code}</code>
                    </td>
                    <td>
                      {code.used_by ? (
                        <span className="muted">Used by {code.used_by}</span>
                      ) : (
                        <span className="badge badge-live">available</span>
                      )}
                    </td>
                    <td>{code.owner_email ?? "The operator"}</td>
                    <td>
                      <Ago iso={code.created_at} />
                    </td>
                    <td className="num">
                      {!code.used_by && (
                        <button
                          type="button"
                          className="secondary small-button"
                          onClick={() => {
                            void navigator.clipboard.writeText(inviteLink(code.code)).then(() => toast("Invite link copied"));
                          }}
                        >
                          <Icon name="link" size={14} /> Copy link
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <Pager offset={offset} total={total} onMove={setOffset} />
    </section>
  );
}

const WHAT: Record<string, string> = {
  "is_admin=true": "made an admin",
  "is_admin=false": "removed as an admin",
  "is_ambassador=true": "made an ambassador",
  "is_ambassador=false": "removed as an ambassador",
  "hunting_granted=true": "given access to the bounties",
  "hunting_granted=false": "had access to the bounties taken back",
};

/** What admins have done. It is written once and cannot be changed by anyone. */
function Activity() {
  const [offset, setOffset] = useState(0);
  const address = `/v1/admin/actions?limit=${PAGE}&offset=${offset}`;
  const log = useLoad((signal) => api<Page<AdminAction>>("GET", address, { signal }), address);
  const list = log.data?.items ?? [];
  const total = log.data?.total ?? 0;
  return (
    <section>
      <ErrorNote>{log.error}</ErrorNote>
      {!log.data && !log.error && <Skeleton lines={4} />}
      {log.data && total === 0 && <p className="empty">Nothing has been done here yet.</p>}
      {list.length > 0 && (
        <ol className="feed log">
          {list.map((item, index) => (
            <li key={`${item.created_at}:${index}`}>
              <span className="feed-icon">
                <Icon name={item.action === "codes_created" ? "link" : "shield"} size={16} />
              </span>
              <span className="feed-main">
                <span>
                  {item.action === "codes_created" ? (
                    <>
                      <b>{item.admin_email}</b> created {item.detail}
                    </>
                  ) : (
                    <>
                      <b>{item.target_email ?? "Someone"}</b> was {WHAT[item.action] ?? item.action}
                    </>
                  )}
                </span>
                {item.action !== "codes_created" && <small>by {item.admin_email}</small>}
              </span>
              <span className="feed-side">
                <small>
                  <Ago iso={item.created_at} />
                </small>
              </span>
            </li>
          ))}
        </ol>
      )}
      <Pager offset={offset} total={total} onMove={setOffset} />
    </section>
  );
}

const TABS = ["people", "codes", "activity"] as const;
const TAB_NAMES = { people: "People", codes: "Invites", activity: "Activity" } as const;

/** The admin area. It is loaded only for an admin: nobody else's browser is sent this code. */
export default function Admin() {
  const account = useAccount();
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((name) => name === params.get("tab")) ?? "people";

  if (!account.data) return account.error ? <ErrorNote>{account.error}</ErrorNote> : <Skeleton block lines={4} />;
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
        Decide who can hunt, who can invite others, and who helps you run trycanari. Changes take effect at once
        and are logged.
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
            {TAB_NAMES[name]}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "people" && <People />}
        {tab === "codes" && <Codes />}
        {tab === "activity" && <Activity />}
      </div>
    </>
  );
}
