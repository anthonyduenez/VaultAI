import { vi } from "vitest";

export interface RecordedCall {
  method: string;
  url: string;
  csrfHeader: string | null;
  body: unknown;
}

export interface Reply {
  status?: number;
  body?: unknown;
}

type Handler = (call: RecordedCall) => Reply | undefined;

/** Replaces global fetch; records every request and answers from `handler` (404 when it returns undefined). */
export function mockFetch(handler: Handler): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const raw = init.body;
      const call: RecordedCall = {
        method: init.method ?? "GET",
        url: String(input),
        csrfHeader: new Headers(init.headers).get("X-VaultAI"),
        body: typeof raw === "string" ? JSON.parse(raw) : (raw ?? null),
      };
      calls.push(call);
      const reply = handler(call) ?? { status: 404, body: { code: "not_found", message: "Not found" } };
      const status = reply.status ?? 200;
      return new Response(status === 204 ? null : JSON.stringify(reply.body ?? null), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}
