import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeDetail, makeField } from "../test/fixtures";
import { mockFetch } from "../test/fetch";
import { FieldTable } from "./FieldTable";

const fields = [
  makeField({
    key: "total_amount",
    label: "Total amount",
    value_type: "money",
    required: true,
    currency: "USD",
    value: "5400.00",
    ai_value: "5328.00",
    origin: "human",
    ai_origin: "model",
    edited_by: "dana",
    edited_at: "2026-10-02T09:00:00Z",
  }),
  makeField({ key: "invoice_number", label: "Invoice number", value_type: "identifier", value: "INV-0042", ai_value: "INV-0042" }),
];

function renderTable(onSaved = vi.fn()) {
  render(<FieldTable docId="doc-1" fields={fields} editable selectedId={null} onSelect={vi.fn()} onSaved={onSaved} />);
  return onSaved;
}

describe("FieldTable", () => {
  it("marks a corrected field as human-edited and keeps the AI value visible", () => {
    renderTable();
    const row = screen.getByRole("row", { name: /Total amount/ });
    expect(within(row).getByText("Human-edited")).toBeInTheDocument();
    expect(within(row).getByText("$5,400.00")).toBeInTheDocument();
    expect(within(row).getByText("$5,328.00").tagName).toBe("S");
    expect(within(row).getByText("by dana")).toBeInTheDocument();

    const untouched = screen.getByRole("row", { name: /Invoice number/ });
    expect(within(untouched).getByText("AI · rules")).toBeInTheDocument();
    expect(within(untouched).queryByText(/AI read/)).toBeNull();
  });

  it("sends the value as typed, trimmed, with the CSRF header when an edit is saved", async () => {
    const detail = makeDetail();
    const calls = mockFetch(() => ({ body: detail }));
    const onSaved = renderTable();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Edit Total amount" }));
    const input = screen.getByLabelText("New value for Total amount");
    await user.clear(input);
    await user.type(input, " ¥6,000.50 ");
    await user.type(screen.getByLabelText("Reason for the correction"), "Matches PO 4471");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(calls).toEqual([
      {
        method: "PATCH",
        url: "/api/documents/doc-1/fields/total_amount",
        csrfHeader: "1",
        body: { value: "¥6,000.50", note: "Matches PO 4471" },
      },
    ]);
    expect(onSaved).toHaveBeenCalledWith(detail);
  });

  it("shows the server's message when it rejects the value, and sends null for a cleared field", async () => {
    const calls = mockFetch(() => ({ status: 422, body: { code: "invalid_value", message: "Enter an amount such as 1234.56." } }));
    const onSaved = renderTable();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Edit Total amount" }));
    const input = screen.getByLabelText("New value for Total amount");
    await user.clear(input);
    await user.type(input, "five thousand");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter an amount such as 1234.56.");

    await user.clear(input);
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(calls.map((c) => c.body)).toEqual([
      { value: "five thousand", note: null },
      { value: null, note: null },
    ]);
    expect(onSaved).not.toHaveBeenCalled();
  });
});
