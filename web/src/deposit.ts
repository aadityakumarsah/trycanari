// Builds a bounty's deposit, has the sponsor's wallet sign it, and hands it to the
// network through this site's server. Loaded only when someone pays with a wallet, since
// the Solana library is large.
import { PublicKey, SystemProgram, Transaction, TransactionInstruction } from "@solana/web3.js";

import { api } from "./api.ts";
import type { Provider } from "./wallet.ts";

const MEMO_PROGRAM = new PublicKey("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr");

export interface Deposit {
  from: string;
  to: string;
  /** The amount in lamports. */
  lamports: number;
  /** The bounty's deposit code. Without it the payment cannot be matched to the bounty. */
  memo: string;
}

/** The unsigned transaction: a transfer to the escrow, and the deposit code as its memo. */
export function buildDeposit(deposit: Deposit, blockhash: string): Transaction {
  const payer = new PublicKey(deposit.from);
  const transaction = new Transaction({ feePayer: payer, recentBlockhash: blockhash });
  transaction.add(
    SystemProgram.transfer({ fromPubkey: payer, toPubkey: new PublicKey(deposit.to), lamports: BigInt(deposit.lamports) }),
    new TransactionInstruction({ programId: MEMO_PROGRAM, keys: [], data: new TextEncoder().encode(deposit.memo) as Buffer }),
  );
  return transaction;
}

function base64(bytes: Uint8Array): string {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text);
}

/** Pay a deposit from the connected wallet. Returns the transaction's signature. */
export async function payDeposit(provider: Provider, deposit: Deposit): Promise<string> {
  // The server asks the network this deployment pays on, whatever the wallet is set to.
  const { blockhash } = await api<{ blockhash: string }>("GET", "/v1/solana/blockhash");
  const signed = await provider.signTransaction(buildDeposit(deposit, blockhash));
  const sent = await api<{ signature: string }>("POST", "/v1/solana/send", {
    body: { transaction: base64(signed.serialize()) },
  });
  return sent.signature;
}
