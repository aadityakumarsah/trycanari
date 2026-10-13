// Money as text. Amounts are integers of the currency's smallest unit (lamports for SOL);
// nothing here does float arithmetic on them. Which currency it is comes from the server,
// since it follows from the wallet the deployment uses. The platform pays in SOL; ZEC is
// what the in-memory wallet of tests and local development speaks.

export interface Currency {
  symbol: string;
  decimals: number;
  unit: string;
  /** Whether payments hide who paid whom and how much. */
  private: boolean;
  /** A test network's name, or null on the real one. */
  network: string | null;
}

export const ZEC: Currency = { symbol: "ZEC", decimals: 8, unit: "zatoshi", private: true, network: null };

export const SOL: Currency = { symbol: "SOL", decimals: 9, unit: "lamports", private: false, network: null };

// What the site says until the server has answered.
let current: Currency = SOL;

/** Set once, when the app learns what the server pays in. */
export function setCurrency(currency: Currency): void {
  current = currency;
}

export function currency(): Currency {
  return current;
}

/** "ZEC" or "SOL". */
export function sym(): string {
  return current.symbol;
}

/** How rewards are paid, for a sentence: "shielded Zcash" or "SOL on Solana". */
export function paidIn(): string {
  return current.private ? "shielded Zcash" : `${current.symbol} on Solana`;
}

/** What kind of address a payment goes to: "shielded Zcash" or "Solana". */
export function addressKind(): string {
  return current.private ? "shielded Zcash" : "Solana";
}

/** Smallest units as a whole amount with no trailing zeros: 250000000 -> "2.5" (in ZEC). */
export function formatZec(units: number): string {
  const sign = units < 0 ? "-" : "";
  // BigInt keeps every digit: a large amount of lamports is past what a float holds exactly.
  const magnitude = BigInt(Math.trunc(Math.abs(units)));
  const per = 10n ** BigInt(current.decimals);
  const whole = magnitude / per;
  const fraction = String(magnitude % per)
    .padStart(current.decimals, "0")
    .replace(/0+$/, "");
  return fraction ? `${sign}${whole}.${fraction}` : `${sign}${whole}`;
}

/** An amount typed by a person, in smallest units. Throws if it is not exact and positive. */
export function parseZec(text: string): number {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!match) throw new Error(`Enter the reward as a number of ${current.symbol}, such as 0.5.`);
  const [, whole = "0", fraction = ""] = match;
  if (fraction.length > current.decimals) {
    throw new Error(`A ${current.symbol} amount has at most ${current.decimals} decimal places.`);
  }
  // Digits are joined as text and read as one integer: no fractional arithmetic.
  const units = Number(whole + fraction.padEnd(current.decimals, "0"));
  if (!Number.isSafeInteger(units)) throw new Error("That amount is too large.");
  if (units <= 0) throw new Error("The reward must be more than zero.");
  return units;
}
