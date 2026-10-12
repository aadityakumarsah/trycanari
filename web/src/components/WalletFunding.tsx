import { useState } from "react";

import { api, messageOf } from "../api.ts";
import { currency, formatZec, sym } from "../money.ts";
import { useToast } from "../toast.tsx";
import type { Funding, SponsorBountyDetail } from "../types.ts";
import {
  connectWallet,
  disconnectWallet,
  installedWallets,
  shortAddress,
  useWallet,
  walletProblem,
  type Connected,
} from "../wallet.ts";
import { Icon } from "./Brand.tsx";
import { ErrorNote } from "./ui.tsx";

/** Pick a wallet and share its address. Shown wherever a wallet can save typing. */
export function ConnectWallet({ onConnected }: { onConnected?: (wallet: Connected) => void }) {
  const wallet = useWallet();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const choices = installedWallets();

  if (wallet) {
    return (
      <p className="wallet-chip">
        <i /> {wallet.name} <code>{shortAddress(wallet.address)}</code>
        <button type="button" className="text-button quiet" onClick={disconnectWallet}>
          Disconnect
        </button>
      </p>
    );
  }
  if (choices.length === 0) {
    return (
      <p className="muted small">
        No Solana wallet found in this browser. Install{" "}
        <a href="https://phantom.com/download" target="_blank" rel="noreferrer">
          Phantom
        </a>{" "}
        or{" "}
        <a href="https://solflare.com/download" target="_blank" rel="noreferrer">
          Solflare
        </a>
        , then reload this page.
      </p>
    );
  }
  return (
    <div className="wallet-pick">
      {choices.map((choice) => (
        <button
          key={choice.name}
          type="button"
          className="secondary"
          disabled={busy !== ""}
          onClick={() => {
            setBusy(choice.name);
            setError("");
            connectWallet(choice)
              .then((now) => onConnected?.(now))
              .catch((problem: unknown) => setError(walletProblem(problem)))
              .finally(() => setBusy(""));
          }}
        >
          <Icon name="link" size={15} /> {busy === choice.name ? "Waiting for your wallet…" : `Connect ${choice.name}`}
        </button>
      ))}
      {error && <small className="photo-error">{error}</small>}
    </div>
  );
}

type Stage = "idle" | "preparing" | "signing" | "sending" | "sent";
const SAYS: Record<Stage, string> = {
  idle: "",
  preparing: "Preparing the deposit…",
  signing: "Approve it in your wallet…",
  sending: "Sending it to the network…",
  sent: "Sent",
};

/**
 * Fund a bounty from a connected wallet in one approval. The address, the amount and
 * the memo that ties the payment to this bounty are all filled in, so none can be wrong.
 */
export function WalletFunding({
  bounty,
  base,
  onMoved,
}: {
  bounty: SponsorBountyDetail;
  base: string;
  onMoved: () => Promise<void>;
}) {
  const wallet = useWallet();
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState("");
  const [signature, setSignature] = useState("");
  const toast = useToast();
  const money = currency();
  const waiting = bounty.state === "awaiting_deposit";
  const due = Math.max(bounty.required_zatoshi - bounty.balance_zatoshi, 0);
  const cluster = money.network && money.network !== "demo" ? `?cluster=${money.network}` : "";

  async function pay() {
    if (!wallet) return;
    setError("");
    try {
      setStage("preparing");
      // A draft has no deposit code yet. Asking to fund it makes one, with the wallet's
      // own address as where an unspent escrow would be returned.
      const funding = waiting
        ? { escrow_address: bounty.escrow_address, deposit_code: bounty.deposit_code, amount_due_zatoshi: due }
        : await api<Funding>("POST", `${base}/fund`, { body: { refund_address: wallet.address } });
      await onMoved();
      const { payDeposit } = await import("../deposit.ts");
      setStage("signing");
      const signed = await payDeposit(wallet.provider, {
        from: wallet.address,
        to: funding.escrow_address,
        lamports: funding.amount_due_zatoshi,
        memo: funding.deposit_code,
      });
      setSignature(signed);
      setStage("sent");
      toast("Deposit sent. The bounty goes live once the network confirms it.");
      await onMoved();
    } catch (problem) {
      setStage("idle");
      setError(messageOf(problem) || walletProblem(problem));
    }
  }

  const working = stage !== "idle" && stage !== "sent";
  return (
    <div className="wallet-fund">
      <ol className="wallet-steps">
        <li className={wallet ? "step-done" : "step-now"}>
          <b>Connect your wallet</b>
          <ConnectWallet />
        </li>
        <li className={stage === "sent" ? "step-done" : wallet ? "step-now" : undefined}>
          <b>
            Pay the deposit: {formatZec(due)} {sym()}
          </b>
          <span className="muted small">
            The reward plus the fee, sent to escrow with this bounty's code attached. The fee is charged only if
            a hunter is paid; close an unbroken bounty and all of it comes back to this wallet.
          </span>
          {stage !== "sent" && (
            <button type="button" className="accent" disabled={!wallet || working} onClick={() => void pay()}>
              {working ? SAYS[stage] : `Fund with wallet · ${formatZec(due)} ${sym()}`}
            </button>
          )}
          <ErrorNote>{error}</ErrorNote>
        </li>
        <li className={bounty.state === "live" ? "step-done" : stage === "sent" ? "step-now" : undefined}>
          <b>{bounty.state === "live" ? "Live" : "Goes live by itself"}</b>
          {stage === "sent" ? (
            <span className="muted small">
              <span className="spinner" /> Waiting for the network to finalize the deposit. This usually takes under a
              minute, and this page updates when it does.{" "}
              <a href={`https://explorer.solana.com/tx/${signature}${cluster}`} target="_blank" rel="noreferrer">
                View the transaction
              </a>
            </span>
          ) : (
            <span className="muted small">Nothing else to do once the deposit is confirmed.</span>
          )}
        </li>
      </ol>
      {money.network && money.network !== "demo" && (
        <p className="muted small">
          This is Solana {money.network}. Set your wallet to {money.network} and use test SOL, not real funds.
        </p>
      )}
    </div>
  );
}
