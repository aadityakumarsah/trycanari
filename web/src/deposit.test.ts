import { describe, expect, test } from "bun:test";
import { Keypair, SystemInstruction, SystemProgram } from "@solana/web3.js";

import { buildDeposit } from "./deposit.ts";
import { shortAddress, walletProblem } from "./wallet.ts";

describe("a deposit paid from a wallet", () => {
  const sponsor = Keypair.generate();
  const escrow = Keypair.generate().publicKey;
  const blockhash = Keypair.generate().publicKey.toBase58();
  const deposit = { from: sponsor.publicKey.toBase58(), to: escrow.toBase58(), lamports: 55_000_000, memo: "dep-abc123" };

  test("is a transfer to the escrow with the deposit code as its memo", () => {
    const transaction = buildDeposit(deposit, blockhash);
    expect(transaction.feePayer?.toBase58()).toBe(deposit.from);
    expect(transaction.recentBlockhash).toBe(blockhash);
    const [pay, memo] = transaction.instructions;
    expect(pay?.programId.equals(SystemProgram.programId)).toBe(true);
    const moved = SystemInstruction.decodeTransfer(pay!);
    expect([moved.fromPubkey.toBase58(), moved.toPubkey.toBase58(), moved.lamports]).toEqual([deposit.from, deposit.to, 55_000_000n]);
    expect(memo?.programId.toBase58()).toBe("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr");
    expect(new TextDecoder().decode(memo?.data)).toBe("dep-abc123");
  });

  test("once signed by its owner, it is complete and can be sent as is", () => {
    const transaction = buildDeposit(deposit, blockhash);
    transaction.sign(sponsor);
    expect(transaction.verifySignatures()).toBe(true);
    expect(transaction.serialize().length).toBeLessThan(1232);
  });
});

describe("wallet helpers", () => {
  test("addresses are shortened for display and refusals are put plainly", () => {
    expect(shortAddress("9f67QkoogoB9rKED7gpjWJpDZUqS1s9QvnaNjnvQQTpy")).toBe("9f67…QTpy");
    expect(walletProblem(new Error("User rejected the request."))).toBe("You cancelled the request in your wallet.");
    expect(walletProblem(new Error("Wallet is locked"))).toBe("Wallet is locked");
  });
});
