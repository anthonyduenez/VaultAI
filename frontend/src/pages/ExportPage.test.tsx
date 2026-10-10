import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { ExportJob, ExportPreview } from "../api/types";
import { ToastProvider } from "../components/Toast";
import { mockFetch } from "../test/fetch";
import { ExportPage } from "./ExportPage";

const preview: ExportPreview = {
  record_count: 3,
  needs_review_count: 1,
  by_type: [{ doc_type: "invoice", count: 3 }],
  sheets: ["Export Info", "Approved Records"],
  rows: [
    {
      doc_id: "doc-1",
      filename: "invoice-0042.pdf",
      doc_type: "invoice",
      status: "approved",
      counterparty: "Acme Supplies",
      doc_date: "2026-09-28",
      primary_id: "INV-0042",
      total_amount: "5328.00",
      currency: "USD",
      approval_kind: "policy",
      approved_by: null,
      human_edits: 0,
    },
  ],
  warnings: [],
};

const job: ExportJob = {
  id: "exp-1",
  created_at: "2026-10-10T10:00:00Z",
  actor: "local-user",
  format: "csv",
  filters: {},
  record_count: 3,
  filename: "vaultai-export.csv",
  sha256: "cd".repeat(32),
  duration_ms: 120,
};

describe("ExportPage", () => {
  it("keeps 'Approved records only' locked and posts exactly the chosen filters", async () => {
    const calls = mockFetch(({ method, url }) => {
      if (url === "/api/exports/preview") return { body: preview };
      if (url === "/api/exports" && method === "GET") return { body: [] };
      if (url === "/api/exports" && method === "POST") return { body: job };
      return undefined;
    });
    render(
      <MemoryRouter>
        <ToastProvider>
          <ExportPage />
        </ToastProvider>
      </MemoryRouter>,
    );
    const user = userEvent.setup();

    const locked = screen.getByRole("checkbox", { name: /Approved records only/ });
    expect(locked).toBeChecked();
    expect(locked).toBeDisabled();
    expect(await screen.findByText("Auto-approval policy")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Invoice" }));
    await user.click(screen.getByRole("checkbox", { name: "Receipt" }));
    fireEvent.change(screen.getByLabelText("Document date from"), { target: { value: "2026-01-01" } });
    await user.click(screen.getByRole("checkbox", { name: /Include a separate .Needs Review. sheet/ }));
    await user.click(screen.getByRole("checkbox", { name: /Include source evidence sheet/ }));
    await user.click(screen.getByRole("radio", { name: /CSV/ }));
    await user.click(screen.getByRole("button", { name: "Create export" }));

    expect(await screen.findByRole("link", { name: "Download export" })).toHaveAttribute("href", "/api/exports/exp-1/download");
    const created = calls.filter((c) => c.method === "POST" && c.url === "/api/exports");
    expect(created).toEqual([
      {
        method: "POST",
        url: "/api/exports",
        csrfHeader: "1",
        body: {
          doc_types: ["invoice", "receipt"],
          date_from: "2026-01-01",
          date_to: null,
          include_needs_review: true,
          include_evidence: false,
          format: "csv",
        },
      },
    ]);
  });
});
