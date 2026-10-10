import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { ActionCenter } from "../api/types";
import { mockFetch } from "../test/fetch";
import { ActionsPage } from "./ActionsPage";

const center: ActionCenter = {
  items: [
    {
      id: "obligation:3",
      category: "renewal",
      severity: "warning",
      title: "Notice due in 22 days",
      detail: "Send the non-renewal notice.",
      doc_id: "doc-1",
      doc_filename: "lease.pdf",
      doc_type: "contract",
      due_date: "2026-11-01",
      obligation_id: 3,
      created_at: "2026-10-01T12:00:00Z",
    },
    {
      id: "needs_review:doc-2",
      category: "needs_review",
      severity: "warning",
      title: "Needs review: invoice.pdf",
      detail: null,
      doc_id: "doc-2",
      doc_filename: "invoice.pdf",
      doc_type: "invoice",
      due_date: null,
      obligation_id: null,
      created_at: "2026-10-01T12:00:00Z",
    },
  ],
  counts: [
    { category: "renewal", count: 1 },
    { category: "needs_review", count: 1 },
  ],
};

describe("ActionsPage", () => {
  it("lists categories in the server's order and states each due date once", async () => {
    mockFetch(({ url }) => (url === "/api/actions" ? { body: center } : undefined));
    render(
      <MemoryRouter>
        <ActionsPage />
      </MemoryRouter>,
    );

    const filters = await screen.findByRole("group", { name: "Filter by category" });
    expect(within(filters).getAllByRole("button").map((b) => b.textContent?.trim())).toEqual([
      "All 2",
      "Renewals & expirations 1",
      "Needs review 1",
    ]);
    expect(screen.getByText("Notice due in 22 days").closest("li")?.textContent).toBe(
      "WarningNotice due in 22 daysSend the non-renewal notice.Contract lease.pdfNov 1, 2026 Done Dismiss",
    );
  });
});
