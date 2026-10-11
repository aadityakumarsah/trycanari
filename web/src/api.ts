// The only way the app reads or changes anything: the JSON API, on this origin.
//
// A sponsor's requests are authenticated by an HttpOnly session cookie the
// browser sends by itself, so no API key is ever held where script can read it.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ValidationItem {
  loc?: unknown[];
  msg?: string;
}

/** A message a person can act on, from whatever shape the API's error body has. */
export function errorDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    // Request validation errors: one item per field.
    return (detail as ValidationItem[])
      .map((item) => {
        const field = (item.loc ?? []).slice(1).join(".");
        return field ? `${field}: ${item.msg ?? "invalid"}` : (item.msg ?? "invalid");
      })
      .join("; ");
  }
  return fallback;
}

export interface CallOptions {
  body?: unknown;
  /** A hunter's status token, sent as a bearer token. */
  token?: string;
  signal?: AbortSignal;
}

export async function api<T>(method: string, path: string, options: CallOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  const init: RequestInit = { method, headers, credentials: "same-origin" };
  if (options.body !== undefined) init.body = JSON.stringify(options.body);
  if (options.signal) init.signal = options.signal;

  const response = await fetch(path, init);
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const fallback =
      response.status === 429 ? "Too many requests. Wait a minute and try again." : `Request failed (${response.status}).`;
    throw new ApiError(response.status, errorDetail(body, fallback));
  }
  return body as T;
}

/** What to show for anything thrown by a call. */
export function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.name !== "AbortError") {
    return "The server could not be reached. Check your connection and try again.";
  }
  return "";
}
