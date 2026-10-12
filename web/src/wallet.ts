// Browser wallets (Phantom, Solflare, Backpack) put an object on the page that can say
// which address is theirs and sign a transaction when its owner approves. Nothing here
// can move money by itself: every signature is one the person approved in their wallet.
import { useSyncExternalStore } from "react";

/** The part of a wallet's object this app uses. All three wallets share it. */
export interface Provider {
  publicKey?: { toString(): string } | null;
  connect(): Promise<unknown>;
  disconnect?(): Promise<void>;
  signTransaction<T>(transaction: T): Promise<T>;
}

export interface WalletChoice {
  name: string;
  provider: Provider;
}

interface WalletWindow {
  phantom?: { solana?: Provider };
  solflare?: Provider;
  backpack?: Provider;
}

/** The wallets installed in this browser. */
export function installedWallets(): WalletChoice[] {
  if (typeof window === "undefined") return [];
  const page = window as unknown as WalletWindow;
  const found: [string, Provider | undefined][] = [
    ["Phantom", page.phantom?.solana],
    ["Solflare", page.solflare],
    ["Backpack", page.backpack],
  ];
  return found.flatMap(([name, provider]) => (provider ? [{ name, provider }] : []));
}

export interface Connected extends WalletChoice {
  address: string;
}

// One connection for the whole page, so the funding page and the bounty page agree.
let connected: Connected | null = null;
const listeners = new Set<() => void>();
function announce(): void {
  for (const listener of listeners) listener();
}

/** Ask a wallet for its address. The wallet shows its owner a prompt the first time. */
export async function connectWallet(choice: WalletChoice): Promise<Connected> {
  const answer = (await choice.provider.connect()) as { publicKey?: { toString(): string } } | undefined;
  const key = answer?.publicKey ?? choice.provider.publicKey;
  if (!key) throw new Error(`${choice.name} did not share an address.`);
  connected = { ...choice, address: key.toString() };
  announce();
  return connected;
}

export function disconnectWallet(): void {
  void connected?.provider.disconnect?.().catch(() => undefined);
  connected = null;
  announce();
}

/** The connected wallet, or null. Re-renders whoever uses it when that changes. */
export function useWallet(): Connected | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => connected,
    () => null,
  );
}

/** "9f67…QTpy". */
export function shortAddress(address: string): string {
  return address.length > 12 ? `${address.slice(0, 4)}…${address.slice(-4)}` : address;
}

/** What went wrong, in words, when a wallet refuses or its owner says no. */
export function walletProblem(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (/reject|denied|cancel|declin/i.test(text)) return "You cancelled the request in your wallet.";
  return text || "The wallet did not respond.";
}
