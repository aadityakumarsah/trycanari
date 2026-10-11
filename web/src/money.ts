// Money as text. Amounts are integer zatoshi; nothing here does float arithmetic on them.

const ZATOSHI_PER_ZEC = 100_000_000;
const DECIMALS = 8;

/** Zatoshi as a ZEC amount with no trailing zeros: 250000000 -> "2.5". */
export function formatZec(zatoshi: number): string {
  const sign = zatoshi < 0 ? "-" : "";
  const magnitude = Math.abs(zatoshi);
  const whole = Math.floor(magnitude / ZATOSHI_PER_ZEC);
  const fraction = String(magnitude % ZATOSHI_PER_ZEC)
    .padStart(DECIMALS, "0")
    .replace(/0+$/, "");
  return fraction ? `${sign}${whole}.${fraction}` : `${sign}${whole}`;
}

/** A ZEC amount typed by a person, as zatoshi. Throws if it is not an exact, positive amount. */
export function parseZec(text: string): number {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!match) throw new Error("Enter the reward as a number of ZEC, such as 0.5.");
  const [, whole = "0", fraction = ""] = match;
  if (fraction.length > DECIMALS) throw new Error("A ZEC amount has at most 8 decimal places.");
  // Digits are joined as text and read as one integer: no fractional arithmetic.
  const zatoshi = Number(whole + fraction.padEnd(DECIMALS, "0"));
  if (!Number.isSafeInteger(zatoshi)) throw new Error("That amount is too large.");
  if (zatoshi <= 0) throw new Error("The reward must be more than zero.");
  return zatoshi;
}
