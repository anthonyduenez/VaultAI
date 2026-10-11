import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { makeIntake } from "../test/fixtures";
import { mockFetch } from "../test/fetch";
import { IntakeQueue } from "./IntakeQueue";

const items = [
  makeIntake({ id: 7, filename: "payroll.exe", mime: null, preflight: "rejected", error: "File type is not supported", document_id: null, doc_status: null }),
  makeIntake({ id: 8, filename: "receipt-0311.png", mime: "image/png" }),
  makeIntake({ id: 9, filename: "invoice-copy.pdf", preflight: "duplicate", duplicate_of: "doc-orig", document_id: null, doc_status: null }),
  makeIntake({ id: 10, filename: "contract.pdf", doc_status: "processing", stage: "OCR page 2/3" }),
];

function renderQueue(onChanged = vi.fn()) {
  render(
    <MemoryRouter>
      <IntakeQueue items={items} onChanged={onChanged} />
    </MemoryRouter>,
  );
  return onChanged;
}

describe("IntakeQueue", () => {
  it("shows rejected files with their reason and states they were never stored", () => {
    renderQueue();
    const row = screen.getByRole("row", { name: /payroll\.exe/ });
    expect(within(row).getByText("Rejected · not stored")).toBeInTheDocument();
    expect(within(row).getByText("File type is not supported")).toBeInTheDocument();
    expect(screen.getByText("Rejected and duplicate files are never stored")).toBeInTheDocument();
  });

  it("links duplicates to the original and shows pipeline progress", () => {
    renderQueue();
    expect(within(screen.getByRole("row", { name: /invoice-copy\.pdf/ })).getByRole("link", { name: "Open the original" })).toHaveAttribute(
      "href",
      "/documents/doc-orig",
    );
    expect(within(screen.getByRole("row", { name: /contract\.pdf/ })).getByText("OCR page 2/3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Process 1 queued document" })).toBeEnabled();
  });

  it("removes a row with a DELETE that carries the CSRF header", async () => {
    const calls = mockFetch(() => ({ status: 204 }));
    const onChanged = renderQueue();
    await userEvent.setup().click(screen.getByRole("button", { name: "Remove payroll.exe from the queue" }));
    expect(calls).toEqual([{ method: "DELETE", url: "/api/intake/7", csrfHeader: "1", body: null }]);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("processes only the selected queued documents", async () => {
    const calls = mockFetch(() => ({ body: { queued: ["doc-8"] } }));
    renderQueue();
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Select receipt-0311.png" }));
    await user.click(screen.getByRole("button", { name: "Process 1 selected" }));
    expect(calls).toEqual([{ method: "POST", url: "/api/intake/process", csrfHeader: "1", body: { intake_ids: [8] } }]);
  });
});
