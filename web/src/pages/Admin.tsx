import { Fragment, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { inviteLink, pictureOf, useAccount } from "../account.tsx";
import { api, messageOf } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { Ago, Avatar, ErrorNote, Skeleton } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { useToast } from "../toast.tsx";
import { useConfirm } from "../confirm.tsx";
import type {
  AdminAction,
  AdminCode,
  AdminPilot,
  AdminPilotsPage,
  CodeKind,
  Page,
  Person,
  PilotStatus,
  ProviderSetting,
  ProviderTest,
} from "../types.ts";

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

/** What someone has left to spend, the limit an admin may give them in place of their
 * tier's, and a way to fill their allowance up again. */
function Tokens({
  person,
  onLimit,
  onReset,
}: {
  person: Person;
  onLimit: (limit: number | null) => Promise<void>;
  onReset: () => Promise<void>;
}) {
  const saved = person.credit_limit === null ? "" : String(person.credit_limit);
  const [typed, setTyped] = useState(saved);
  const [busy, setBusy] = useState(false);
  useEffect(() => setTyped(saved), [saved]);

  function save() {
    const wanted = typed.trim();
    if (wanted === saved) return;
    const limit = wanted === "" ? null : Number(wanted);
    if (limit !== null && (!Number.isInteger(limit) || limit < 0)) {
      setTyped(saved);
      return;
    }
    setBusy(true);
    void onLimit(limit).finally(() => setBusy(false));
  }

  return (
    <span className="tokens-cell">
      <span className="tokens-left" title={`${person.credits_balance} of ${person.credits_cap} tokens left for the last 24 hours`}>
        <b>{person.credits_balance}</b> / {person.credits_cap}
      </span>
      <input
        type="number"
        min={0}
        max={10000}
        inputMode="numeric"
        aria-label={`Token limit for ${person.name ?? person.email}`}
        title="Tokens in any 24 hours. Leave empty to use the tier's limit."
        placeholder="Tier"
        disabled={busy}
        value={typed}
        onChange={(event) => setTyped(event.target.value)}
        onBlur={save}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
      />
      <button
        type="button"
        className="secondary small-button"
        title="Forget what they have spent, so their allowance is full again"
        disabled={busy || person.credits_balance >= person.credits_cap}
        onClick={() => {
          setBusy(true);
          void onReset().finally(() => setBusy(false));
        }}
      >
        Refill
      </button>
    </span>
  );
}

/** Whether someone holds the Watch plan, and a way to give it to them or take it back. */
function PlanCell({ person, onDays }: { person: Person; onDays: (days: number) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const until = person.plan_until ? new Date(person.plan_until) : null;
  const active = until !== null && until.getTime() > Date.now();
  const give = (days: number) => {
    setBusy(true);
    void onDays(days).finally(() => setBusy(false));
  };
  return (
    <span className="tokens-cell">
      <span className="tokens-left" title={active && until ? `Until ${until.toLocaleDateString()}` : "No plan"}>
        {active && until ? `to ${until.toLocaleDateString(undefined, { day: "numeric", month: "short" })}` : "none"}
      </span>
      <button type="button" className="secondary small-button" title="Add 30 days of the Watch plan, without a payment" disabled={busy} onClick={() => give(30)}>
        +30 days
      </button>
      {active && (
        <button type="button" className="secondary small-button" disabled={busy} onClick={() => give(0)}>
          End
        </button>
      )}
    </span>
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
  const confirm = useConfirm();

  // Search a moment after typing stops, not on every letter, and from the first page.
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(typed.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [typed]);

  async function change(person: Person, body: Record<string, boolean | number | null>, said: string) {
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

  async function ban(person: Person) {
    if (person.banned) {
      await change(person, { banned: false }, `${who(person)} is no longer banned`);
      return;
    }
    const sure = await confirm({
      title: `Ban ${who(person)}?`,
      body: "They will not be able to attack bounties, list agents or make invites, whatever codes they hold. Their account and its history stay, and you can lift the ban at any time.",
      confirm: "Ban",
      danger: true,
    });
    if (sure) await change(person, { banned: true }, `${who(person)} is banned`);
  }

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
                <th>Can sponsor</th>
                <th>Ambassador</th>
                <th>Admin</th>
                <th>Tokens</th>
                <th>Watch plan</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((person) => (
                <tr key={person.id} className={person.banned ? "banned" : undefined}>
                  <td>
                    <span className="person">
                      <Avatar name={person.name} picture={pictureOf(person)} size={34} />
                      <span>
                        <b>
                          {person.name ?? "No name yet"}
                          {person.is_owner && <em className="role-tag owner">Owner</em>}
                          {person.banned && <em className="role-tag bad">Banned</em>}
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
                      locked={
                        person.banned
                          ? "Banned. Lift the ban first"
                          : person.referral_code
                            ? "Joined with an invite, which cannot be taken back"
                            : undefined
                      }
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
                      label={`Let ${who(person)} list agents`}
                      on={person.can_sponsor}
                      locked={
                        person.banned
                          ? "Banned. Lift the ban first"
                          : person.sponsor_code
                          ? "Joined with a sponsor invite, which cannot be taken back"
                          : person.is_admin
                            ? "An admin can always list agents"
                            : undefined
                      }
                      onChange={(next) =>
                        change(
                          person,
                          { sponsoring_granted: next },
                          next ? `${who(person)} can list agents now` : `${who(person)} can no longer list agents`,
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
                  <td>
                    <Tokens
                      person={person}
                      onLimit={(limit) =>
                        change(
                          person,
                          { credit_limit: limit },
                          limit === null
                            ? `${who(person)} is back on the tier's token limit`
                            : `${who(person)} now gets ${limit} ${limit === 1 ? "token" : "tokens"} a day`,
                        )
                      }
                      onReset={() => change(person, { reset_credits: true }, `${who(person)} has a full allowance again`)}
                    />
                  </td>
                  <td>
                    <PlanCell
                      person={person}
                      onDays={(days) =>
                        change(
                          person,
                          { plan_days: days },
                          days ? `${who(person)} has the Watch plan for ${days} more days` : `${who(person)} no longer has the Watch plan`,
                        )
                      }
                    />
                  </td>
                  <td className="num">
                    {!person.is_admin && (
                      <button
                        type="button"
                        className={person.banned ? "secondary small-button" : "danger small-button"}
                        onClick={() => void ban(person)}
                      >
                        <Icon name="ban" size={14} /> {person.banned ? "Lift ban" : "Ban"}
                      </button>
                    )}
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
  const [kind, setKind] = useState<CodeKind>("hunter");
  const [uses, setUses] = useState("1");
  const [open, setOpen] = useState<string | null>(null);
  const making = useAction();
  const removing = useAction();
  const toast = useToast();
  const confirm = useConfirm();
  const many = Number(count) > 1;
  const links = Math.max(1, Number(count) || 1);
  const people = Math.max(1, Number(uses) || 1);

  async function remove(code: AdminCode) {
    const sure = await confirm({
      title: `Delete ${code.code}?`,
      body:
        code.uses > 0
          ? `Its link stops working and it leaves this list. The ${code.uses === 1 ? "person" : `${code.uses} people`} who joined with it keep their access.`
          : "Its link stops working and it leaves this list.",
      confirm: "Delete invite",
      danger: true,
    });
    if (!sure) return;
    await removing.run(async () => {
      await api("DELETE", `/v1/admin/referral-codes/${encodeURIComponent(code.code)}`);
      await codes.reload();
      toast(`${code.code} deleted.`);
    });
  }

  async function clear(which: "used_up" | "unused") {
    const sure = await confirm({
      title: which === "used_up" ? "Delete every used-up invite?" : "Delete every unused invite?",
      body:
        which === "used_up"
          ? "Every invite with no use left is removed from the database. The people who joined with them keep their access."
          : "Every invite nobody has used yet is removed, and its link stops working.",
      confirm: "Delete them",
      danger: true,
    });
    if (!sure) return;
    await removing.run(async () => {
      const done = await api<{ deleted: number }>("DELETE", `/v1/admin/referral-codes?which=${which}`);
      setOffset(0);
      await codes.reload();
      toast(done.deleted === 0 ? "There was nothing to delete." : `${done.deleted} ${done.deleted === 1 ? "invite" : "invites"} deleted.`);
    });
  }

  async function make(event: FormEvent) {
    event.preventDefault();
    await making.run(async () => {
      const body = {
        kind,
        count: Number(count),
        max_uses: Number(uses),
        ...(custom.trim() && !many ? { code: custom.trim() } : {}),
      };
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
        <div className="code-maker-head">
          <h2>New invites</h2>
          <p className="muted">
            A hunter invite lets someone attack. A sponsor invite lets them list agents, and attack too. Make
            separate links that each work once, or one link that several people can use.
          </p>
        </div>
        <div className="code-maker-fields">
          <label>
            For a
            <select value={kind} onChange={(event) => setKind(event.target.value as CodeKind)}>
              <option value="hunter">Hunter</option>
              <option value="sponsor">Sponsor</option>
            </select>
          </label>
          <label>
            Links
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
            People per link
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
              placeholder={many ? "Generated" : "Generated if empty"}
              title={many ? "A code of your own can only be given to one link" : undefined}
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
        </div>
        <p className="muted code-maker-sum">
          {links === 1 ? "One link" : `${links} separate links`}
          {people === 1
            ? links === 1
              ? ", which works once."
              : ", each of which works once."
            : links === 1
              ? `, which ${people} people can use.`
              : `, each of which ${people} people can use.`}
        </p>
        <ErrorNote>{making.error}</ErrorNote>
      </form>

      <ErrorNote>{codes.error}</ErrorNote>
      {!codes.data && !codes.error && <Skeleton lines={4} />}
      {codes.data && total === 0 && <p className="empty">No invite has been made yet.</p>}
      {list.length > 0 && (
        <>
          <div className="codes-bar">
            <span className="muted count-note">
              {total} {total === 1 ? "invite" : "invites"} in all.
            </span>
            <button type="button" className="secondary small-button" disabled={removing.busy} onClick={() => void clear("used_up")}>
              <Icon name="trash" size={14} /> Delete used-up
            </button>
            <button type="button" className="secondary small-button" disabled={removing.busy} onClick={() => void clear("unused")}>
              <Icon name="trash" size={14} /> Delete unused
            </button>
          </div>
          <ErrorNote>{removing.error}</ErrorNote>
          <div className="scroll">
            <table className="people">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>For a</th>
                  <th>Status</th>
                  <th>Made by</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((code) => {
                  const left = code.max_uses - code.uses;
                  const shared = code.max_uses > 1;
                  const shown = open === code.code && code.uses > 0;
                  return (
                    <Fragment key={code.code}>
                      <tr className={left <= 0 ? "spent" : undefined}>
                        <td>
                          <code className="code-chip">{code.code}</code>
                        </td>
                        <td>
                          <span className={`kind-chip ${code.kind}`}>{code.kind}</span>
                        </td>
                        <td>
                          {shared ? (
                            <span className="use">
                              <span className="meter" aria-hidden="true">
                                <span className="fill" style={{ width: `${Math.min(100, (code.uses / code.max_uses) * 100)}%` }} />
                              </span>
                              {code.uses} of {code.max_uses} used
                              {code.uses > 0 && (
                                <button
                                  type="button"
                                  className="link"
                                  aria-expanded={shown}
                                  onClick={() => setOpen(shown ? null : code.code)}
                                >
                                  {shown ? "Hide" : "See who"}
                                </button>
                              )}
                            </span>
                          ) : code.used_by ? (
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
                          <span className="row-actions">
                            {left > 0 && (
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
                            <button
                              type="button"
                              className="danger small-button"
                              aria-label={`Delete ${code.code}`}
                              title="Delete this invite"
                              disabled={removing.busy}
                              onClick={() => void remove(code)}
                            >
                              <Icon name="trash" size={14} />
                            </button>
                          </span>
                        </td>
                      </tr>
                      {shown && (
                        <tr className="code-users">
                          <td colSpan={6}>
                            <ol>
                              {code.users.map((user) => (
                                <li key={user.email}>
                                  <b>{user.name ?? user.email}</b>
                                  {user.name && <span className="muted">{user.email}</span>}
                                  <small className="muted">
                                    joined <Ago iso={user.joined_at} />
                                  </small>
                                </li>
                              ))}
                            </ol>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
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
  "sponsoring_granted=true": "allowed to list agents",
  "sponsoring_granted=false": "no longer allowed to list agents",
  "banned=true": "banned",
  "banned=false": "no longer banned",
  credits_reset: "given a full token allowance again",
  plan_ended: "taken off the Watch plan",
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
                <Icon name={item.action.startsWith("codes_") ? "link" : "shield"} size={16} />
              </span>
              <span className="feed-main">
                <span>
                  {item.action === "codes_created" ? (
                    <>
                      <b>{item.admin_email}</b> created {item.detail}
                    </>
                  ) : item.action === "codes_deleted" ? (
                    <>
                      <b>{item.admin_email}</b> deleted {item.detail}
                    </>
                  ) : item.action === "plan_extended" ? (
                    <>
                      <b>{item.target_email ?? "Someone"}</b> was given the Watch plan until {item.detail}
                    </>
                  ) : item.action === "credit_limit" ? (
                    <>
                      <b>{item.target_email ?? "Someone"}</b> had their daily token limit set to {item.detail}
                    </>
                  ) : item.action === "model_provider=set" ? (
                    <>
                      The model provider was changed to <b>{item.detail}</b>
                    </>
                  ) : item.action === "model_provider=reset" ? (
                    <>The model provider went back to the server's settings</>
                  ) : (
                    <>
                      <b>{item.target_email ?? "Someone"}</b> was {WHAT[item.action] ?? item.action}
                    </>
                  )}
                </span>
                {!item.action.startsWith("codes_") && <small>by {item.admin_email}</small>}
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

const PILOT_STATUSES: PilotStatus[] = ["new", "contacted", "qualified", "pilot", "paid", "closed"];

function PilotLead({ pilot, onSaved }: { pilot: AdminPilot; onSaved: () => Promise<void> }) {
  const [status, setStatus] = useState<PilotStatus>(pilot.status);
  const [notes, setNotes] = useState(pilot.notes);
  const saving = useAction();
  const changed = status !== pilot.status || notes !== pilot.notes;

  useEffect(() => {
    setStatus(pilot.status);
    setNotes(pilot.notes);
  }, [pilot]);

  async function save() {
    const ok = await saving.run(async () => {
      await api<AdminPilot>("PATCH", `/v1/admin/pilots/${pilot.id}`, { body: { status, notes } });
    });
    if (ok) await onSaved();
  }

  return (
    <article className="pilot-lead">
      <header>
        <div>
          <span className={`badge badge-${pilot.status}`}>{pilot.status}</span>
          <h3>{pilot.company}</h3>
          <p>
            <b>{pilot.name}</b> · <a href={`mailto:${pilot.work_email}`}>{pilot.work_email}</a>
            {pilot.role ? ` · ${pilot.role}` : ""}
          </p>
        </div>
        <span className="pilot-lead-date">Applied <Ago iso={pilot.created_at} /></span>
      </header>
      <div className="pilot-lead-body">
        <div>
          <p className="label">Agent</p>
          <p>{pilot.use_case}</p>
          <div className="pilot-lead-tags">
            <span>{pilot.stage}</span>
            {pilot.goals.map((goal) => <span key={goal}>{goal}</span>)}
            <span>via {pilot.source}</span>
          </div>
          {pilot.company_website && <a className="pilot-site" href={pilot.company_website} target="_blank" rel="noreferrer">Open company site ↗</a>}
        </div>
        <div>
          <p className="label">Measured product activity</p>
          <dl className="pilot-outcomes">
            <div className={pilot.account_id ? "done" : undefined}><dt>Account</dt><dd>{pilot.account_id ? "yes" : "—"}</dd></div>
            <div className={pilot.bounties > 0 ? "done" : undefined}><dt>Agents listed</dt><dd>{pilot.bounties}</dd></div>
            <div className={pilot.scans_completed > 0 ? "done" : undefined}><dt>Scans run</dt><dd>{pilot.scans_completed}</dd></div>
            <div className={pilot.launched_bounties > 0 ? "done" : undefined}><dt>Bounties launched</dt><dd>{pilot.launched_bounties}</dd></div>
            <div className={pilot.verified_breaks > 0 ? "done" : undefined}><dt>Breaks found</dt><dd>{pilot.verified_breaks}</dd></div>
            <div className={pilot.paid_plan ? "done" : undefined}><dt>Paid Watch plan</dt><dd>{pilot.paid_plan ? "yes" : "—"}</dd></div>
          </dl>
        </div>
      </div>
      <footer>
        <label>
          Funnel stage
          <select value={status} onChange={(event) => setStatus(event.target.value as PilotStatus)}>
            {PILOT_STATUSES.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
        </label>
        <label className="pilot-notes">
          Private notes
          <textarea value={notes} rows={2} maxLength={10000} onChange={(event) => setNotes(event.target.value)} placeholder="Next step, owner, objections or call notes" />
        </label>
        <button type="button" disabled={!changed || saving.busy} onClick={() => void save()}>{saving.busy ? "Saving…" : "Save"}</button>
      </footer>
      <ErrorNote>{saving.error}</ErrorNote>
    </article>
  );
}

function Pilots() {
  const [offset, setOffset] = useState(0);
  const address = `/v1/admin/pilots?limit=${PAGE}&offset=${offset}`;
  const pilots = useLoad((signal) => api<AdminPilotsPage>("GET", address, { signal }), address);
  const funnel = pilots.data?.funnel;
  const traction = pilots.data?.traction;
  const steps = funnel ? [
    ["Applied", funnel.applications],
    ["Signed up", funnel.signed_up],
    ["Listed an agent", funnel.listed_agent],
    ["Ran a scan", funnel.ran_scan],
    ["Launched", funnel.launched_bounty],
    ["Paid", funnel.paid],
  ] as const : [];

  return (
    <section className="pilots-admin">
      {traction && (
        <div className="traction-cards" aria-label="Pilot traction">
          <div><small>Agents tested</small><b>{traction.tested_agents_7d}</b><span>last 7 days</span></div>
          <div><small>Regressions caught</small><b>{traction.regressions_caught}</b><span>paid attacks still worked</span></div>
          <div><small>Median patch time</small><b>{traction.median_patch_hours === null ? "—" : `${traction.median_patch_hours}h`}</b><span>finding to passed re-test</span></div>
          <div><small>Paid conversion</small><b>{traction.paid_conversion_percent}%</b><span>{funnel?.paid ?? 0} of {traction.active_pilots} active pilots</span></div>
        </div>
      )}
      <div className="pilot-funnel" aria-label="Design partner funnel">
        {steps.map(([label, value], index) => (
          <div key={label}>
            <small>{index + 1}. {label}</small>
            <b>{value}</b>
            {index > 0 && funnel && <span>{funnel.applications ? Math.round((value / funnel.applications) * 100) : 0}% of applications</span>}
          </div>
        ))}
      </div>
      <div className="pilots-admin-head">
        <div>
          <h2>Design partner pipeline</h2>
          <p>Operator notes are manual. Signup, listing, scans, launches, breaks and payments come from real product activity.</p>
        </div>
        <span className="muted count-note">{pilots.data?.total ?? 0} applications</span>
      </div>
      <ErrorNote>{pilots.error}</ErrorNote>
      {!pilots.data && !pilots.error && <Skeleton block lines={5} />}
      {pilots.data?.total === 0 && <p className="empty">No design partner has applied yet.</p>}
      <div className="pilot-leads">
        {pilots.data?.items.map((pilot) => <PilotLead key={pilot.id} pilot={pilot} onSaved={pilots.reload} />)}
      </div>
      <Pager offset={offset} total={pilots.data?.total ?? 0} onMove={setOffset} />
    </section>
  );
}

/** Providers that speak the OpenAI chat API, with the address each one uses. */
const PROVIDERS: [string, string][] = [
  ["Groq", "https://api.groq.com/openai/v1"],
  ["OpenRouter", "https://openrouter.ai/api/v1"],
  ["OpenAI", "https://api.openai.com/v1"],
  ["Together", "https://api.together.xyz/v1"],
];

const SOURCE = {
  admin: "Set here, by you",
  environment: "From the server's settings",
  none: "Built-in test model",
} as const;

/** Which model provider every agent runs on, and the key it is called with. Owner only. */
function Provider() {
  const setting = useLoad((signal) => api<ProviderSetting>("GET", "/v1/admin/model", { signal }), "provider");
  const [base, setBase] = useState("");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [tested, setTested] = useState<ProviderTest | null>(null);
  const [testing, setTesting] = useState(false);
  const saving = useAction();
  const toast = useToast();
  const confirm = useConfirm();

  const current = setting.data;
  // Start from the address in use, so changing only the key is one field.
  useEffect(() => {
    if (current?.api_base) setBase((typed) => typed || (current.api_base ?? ""));
  }, [current?.api_base]);

  if (!current) return setting.error ? <ErrorNote>{setting.error}</ErrorNote> : <Skeleton block lines={4} />;

  const address = base.trim();
  // The saved key goes on being used while the address it belongs to is unchanged.
  const keyKept = current.source !== "none" && address === current.api_base;
  const ready = Boolean(address) && (Boolean(key) || keyKept);
  const body = { api_base: address, ...(key ? { api_key: key } : {}) };

  function changed() {
    setTested(null);
    saving.clear();
  }

  async function test() {
    setTesting(true);
    setTested(null);
    try {
      const wanted = model.trim();
      setTested(await api<ProviderTest>("POST", "/v1/admin/model/test", { body: wanted ? { ...body, model: wanted } : body }));
    } catch (caught) {
      setTested({ ok: false, detail: messageOf(caught) || "The test could not be run.", milliseconds: 0, models: [], missing_models: [] });
    } finally {
      setTesting(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const lacking = tested?.missing_models ?? [];
    const sure = await confirm({
      title: "Switch the model provider?",
      body: lacking.length
        ? `Every agent without its own endpoint will run here from now on. This provider does not list ${lacking.join(", ")}, which open bounties use: their attacks will fail until those bounties change model.`
        : "Every agent without its own endpoint will run here from now on, on this key. It takes effect within a few seconds.",
      confirm: "Switch provider",
      danger: lacking.length > 0,
    });
    if (!sure) return;
    await saving.run(async () => {
      await api<ProviderSetting>("PUT", "/v1/admin/model", { body });
      setKey("");
      setTested(null);
      await setting.reload();
      toast("Model provider changed.");
    });
  }

  async function reset() {
    const sure = await confirm({
      title: "Go back to the server's settings?",
      body: "The provider and key set here are removed, and the ones in the server's own settings are used again.",
      confirm: "Use the server's settings",
    });
    if (!sure) return;
    await saving.run(async () => {
      await api<ProviderSetting>("DELETE", "/v1/admin/model");
      setBase("");
      setKey("");
      setTested(null);
      await setting.reload();
      toast("Back to the server's settings.");
    });
  }

  return (
    <>
      <section>
        <h2>Model provider in use</h2>
        <dl className="facts">
          <div>
            <dt>Provider</dt>
            <dd>{current.api_base ? new URL(current.api_base).hostname : "None"}</dd>
          </div>
          <div>
            <dt>Key</dt>
            <dd>
              <code>{current.key_hint ?? "none"}</code>
            </dd>
          </div>
          <div>
            <dt>Chosen</dt>
            <dd>
              <span className={`src-pill ${current.source}`}>{SOURCE[current.source]}</span>
            </dd>
          </div>
          <div>
            <dt>Models open bounties use</dt>
            <dd>
              {current.models_in_use.length ? (
                <span className="model-tags">
                  {current.models_in_use.map((name) => (
                    <span key={name} className="model-tag">
                      {name}
                    </span>
                  ))}
                </span>
              ) : (
                "None yet"
              )}
            </dd>
          </div>
        </dl>
        {current.source === "admin" && (
          <p className="muted provider-reset">
            {current.updated_at && (
              <>
                Changed <Ago iso={current.updated_at} />.{" "}
              </>
            )}
            <button type="button" className="link" disabled={saving.busy} onClick={() => void reset()}>
              Go back to the server's settings
            </button>
          </p>
        )}
      </section>

      <section>
        <h2>Change provider or key</h2>
        <p className="muted">
          Any provider that speaks the OpenAI chat API works. Agents with their own endpoint are not affected. The
          key is stored encrypted and never shown again.
        </p>
        <form className="provider-form" onSubmit={(event) => void save(event)}>
          <div className="tool-add">
            <span className="muted small">Start from:</span>
            {PROVIDERS.map(([name, url]) => (
              <button
                key={name}
                type="button"
                className={address === url ? "chip on" : "chip"}
                onClick={() => {
                  setBase(url);
                  changed();
                }}
              >
                {name}
              </button>
            ))}
          </div>
          <label>
            API address
            <input
              required
              type="url"
              spellCheck={false}
              className="code"
              placeholder="https://api.provider.com/v1"
              value={base}
              onChange={(event) => {
                setBase(event.target.value);
                changed();
              }}
            />
          </label>
          <label>
            API key
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              className="code"
              required={!keyKept}
              placeholder={keyKept ? `Saved (${current.key_hint}). Leave blank to keep it` : "The provider's secret key"}
              value={key}
              onChange={(event) => {
                setKey(event.target.value);
                changed();
              }}
            />
          </label>
          <div className="field-block">
            <label htmlFor="provider-model">A model to try (optional)</label>
            <span className="with-button">
              <input
                id="provider-model"
                spellCheck={false}
                className="code"
                list="provider-models"
                placeholder="Leave blank to only list the models"
                value={model}
                onChange={(event) => setModel(event.target.value)}
              />
              <button type="button" className="secondary" disabled={testing || !ready} onClick={() => void test()}>
                {testing ? "Testing…" : "Test"}
              </button>
            </span>
            <datalist id="provider-models">
              {(tested?.models ?? []).map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </div>
          {tested && (
            <div className={tested.ok ? "test-card good" : "test-card bad"} role="status">
              <span className="test-mark">
                <Icon name={tested.ok ? "check" : "ban"} size={18} />
              </span>
              <div>
                <b>{tested.ok ? "Connection works" : "It did not work"}</b>
                <p>{tested.detail}</p>
                {tested.ok && tested.models.length > 0 && (
                  <>
                    <p className="muted small">
                      {tested.models.length} models available{model.trim() ? "" : " — pick one to send it a test message"}:
                    </p>
                    <div className="model-chips">
                      {tested.models.slice(0, 16).map((name) => (
                        <button
                          key={name}
                          type="button"
                          className={model === name ? "chip on" : "chip"}
                          onClick={() => {
                            setModel(name);
                            setTested(null);
                          }}
                        >
                          {name}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
          {tested && tested.missing_models.length > 0 && (
            <p className="test-line bad" role="alert">
              <Icon name="ban" size={16} />
              <span>
                Open bounties use <b>{tested.missing_models.join(", ")}</b>, which this provider does not list.
                Their attacks will fail after the switch until those bounties change model.
              </span>
            </p>
          )}
          <ErrorNote>{saving.error}</ErrorNote>
          <button disabled={saving.busy || !ready}>{saving.busy ? "Saving…" : "Save and switch"}</button>
        </form>
      </section>
    </>
  );
}

const TABS = ["people", "pilots", "codes", "activity", "model"] as const;
const TAB_NAMES = { people: "People", pilots: "Pilots", codes: "Invites", activity: "Activity", model: "Model" } as const;

/** The admin area. It is loaded only for an admin: nobody else's browser is sent this code. */
export default function Admin() {
  const account = useAccount();
  const [params, setParams] = useSearchParams();
  // Where every agent runs is the owner's decision alone, so only the owner sees that tab.
  const tabs = TABS.filter((name) => name !== "model" || account.data?.is_owner);
  const tab = tabs.find((name) => name === params.get("tab")) ?? "people";

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
        Decide who can hunt, who can invite others, how many tokens each person gets, and who helps you run
        trycanari. Changes take effect at once and are logged.
      </p>
      <div className="tabs" role="tablist" aria-label="Admin">
        {tabs.map((name) => (
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
        {tab === "pilots" && <Pilots />}
        {tab === "codes" && <Codes />}
        {tab === "activity" && <Activity />}
        {tab === "model" && <Provider />}
      </div>
    </>
  );
}
