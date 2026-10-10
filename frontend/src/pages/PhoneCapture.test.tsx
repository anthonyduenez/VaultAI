import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CaptureSession, PhonePairing } from "../api/types";
import { mockFetch } from "../test/fetch";
import { PhoneCapture } from "./PhoneCapture";

const session: CaptureSession = {
  id: "s1",
  kind: "phone",
  status: "active",
  device_id: null,
  pages: [],
  created_at: "2026-10-10T10:00:00Z",
  expires_at: null,
  intake_id: null,
  document_id: null,
};

const pairing: PhonePairing = { session, url: "http://192.168.1.20:8766/c/abc", code: "4821", qr_svg: "<svg/>" };

afterEach(() => {
  vi.useRealTimers();
});

describe("PhoneCapture", () => {
  it("polls the session until the phone upload completes, then stops", async () => {
    vi.useFakeTimers();
    const statuses: CaptureSession[] = [session, { ...session, status: "completed", document_id: "doc-9" }];
    const calls = mockFetch(({ method, url }) => {
      if (method === "POST" && url === "/api/phone/sessions") return { body: pairing };
      if (method === "GET" && url === "/api/phone/sessions/s1") return { body: statuses.shift() };
      return undefined;
    });
    const onImported = vi.fn();
    render(
      <MemoryRouter>
        <PhoneCapture onImported={onImported} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Start pairing" }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("4821")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(10_000));

    expect(screen.getByText("Received — document imported")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open document" })).toHaveAttribute("href", "/documents/doc-9");
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "POST /api/phone/sessions",
      "GET /api/phone/sessions/s1",
      "GET /api/phone/sessions/s1",
    ]);
  });
});
