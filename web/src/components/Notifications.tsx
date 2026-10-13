import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { pictureOf } from "../account.tsx";
import { api } from "../api.ts";
import { useInterval, useLoad } from "../hooks.ts";
import { formatZec, sym } from "../money.ts";
import type { Notification, Notifications } from "../types.ts";
import { Icon, type IconName } from "./Brand.tsx";
import { Ago, Avatar } from "./ui.tsx";

const POLL_MS = 30_000;

interface Told {
  icon: IconName;
  tone: "alert" | "good" | "wait";
  title: string;
  detail: string;
  to: string;
}

/** What a notification says, and where reading it leads. */
export function describe(note: Notification): Told {
  const amount = note.amount_zatoshi === null ? "The reward" : `${formatZec(note.amount_zatoshi)} ${sym()}`;
  const hunter = note.actor?.name ?? "A hunter";
  const dashboard = `/dashboard/b/${note.bounty_id}`;
  switch (note.kind) {
    case "bounty_broken":
      if (note.house) {
        return {
          icon: "shield",
          tone: "alert",
          title: `The house hunter broke ${note.bounty_title}`,
          detail: "Verified in at least 3 of 5 replays. Nothing is paid and your escrow is untouched. The bounty is paused until you patch it.",
          to: `${dashboard}?tab=findings`,
        };
      }
      return {
        icon: "zap",
        tone: "alert",
        title: `${hunter} broke ${note.bounty_title}`,
        detail: `Verified in at least 3 of 5 replays. The bounty is paused and ${amount} is being paid out.`,
        to: `${dashboard}?tab=findings`,
      };
    case "reward_paid":
      return {
        icon: "coin",
        tone: "good",
        title: `${amount} paid for ${note.bounty_title}`,
        detail: `Sent to ${note.actor?.name ?? "the hunter"} from the bounty's escrow.`,
        to: `${dashboard}?tab=ledger`,
      };
    case "break_verified":
      return {
        icon: "check",
        tone: "good",
        title: `You broke ${note.bounty_title}`,
        detail: `Verified. Your payout of ${amount} is under review and is sent next.`,
        to: "/attempts",
      };
    case "payout_held":
      return {
        icon: "clock",
        tone: "wait",
        title: `Payout on hold for ${note.bounty_title}`,
        detail: `${amount} is yours. It could not be sent automatically, so a person is looking at it.`,
        to: "/attempts",
      };
    case "payout_sent":
      return {
        icon: "coin",
        tone: "good",
        title: `You were paid ${amount}`,
        detail: `For your verified break of ${note.bounty_title}.`,
        to: "/attempts",
      };
  }
}

/** The bell in the navigation bar: a red mark while something is unread, and the list. */
export function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const loaded = useLoad((signal) => api<Notifications>("GET", "/v1/notifications", { signal }), "", "notifications");
  const [fresh, setFresh] = useState<Notifications | null>(null);
  const told = fresh ?? loaded.data;
  const { reload } = loaded;

  // A reload supersedes whatever marking something read answered with.
  useEffect(() => setFresh(null), [loaded.data]);
  useInterval(() => void reload(), POLL_MS, true);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close();
    const onClick = (event: MouseEvent) => {
      if (!(event.target as Element).closest(".bell")) close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick);
    };
  }, [open, close]);

  async function read(ids?: string[]) {
    try {
      setFresh(await api<Notifications>("POST", "/v1/notifications/read", { body: ids ? { ids } : {} }));
    } catch {
      // Still unread, then: the mark stays and the next poll tells the truth.
    }
  }

  const unread = told?.unread ?? 0;
  const items = told?.items ?? [];
  return (
    <div className="bell">
      <button
        type="button"
        className="icon-btn bell-button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls="notifications"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        onClick={() => {
          setOpen((shown) => !shown);
          if (!open) void reload();
        }}
      >
        <Icon name="bell" size={19} />
        {unread > 0 && <i className="bell-dot" />}
      </button>
      {open && (
        <div id="notifications" className="drop-menu bell-menu">
          <div className="bell-head">
            <b>Notifications</b>
            {unread > 0 && (
              <button type="button" className="text-button" onClick={() => void read()}>
                Mark all read
              </button>
            )}
          </div>
          {items.length === 0 ? (
            <p className="bell-empty">
              Nothing yet. You are told here when a bounty of yours is broken, and when an attack of yours is
              verified and paid.
            </p>
          ) : (
            <ul className="bell-list">
              {items.map((note) => {
                const said = describe(note);
                return (
                  <li key={note.id}>
                    <button
                      type="button"
                      className={note.read ? "bell-item" : "bell-item unread"}
                      onClick={() => {
                        if (!note.read) void read([note.id]);
                        close();
                        navigate(said.to);
                      }}
                    >
                      {note.actor ? (
                        <Avatar name={note.actor.name} picture={pictureOf(note.actor)} size={32} />
                      ) : (
                        <span className={`bell-icon ${said.tone}`}>
                          <Icon name={said.icon} size={16} />
                        </span>
                      )}
                      <span className="bell-text">
                        <strong>{said.title}</strong>
                        <small>{said.detail}</small>
                        <small className="bell-when">
                          <Ago iso={note.created_at} />
                        </small>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
