"""Document-specific extraction schemas.

One registry entry per DocType. The rules extractor, the model prompts, the
validators, the export, and samples/expected.json all key off these field keys.
"""

from __future__ import annotations

from dataclasses import dataclass

from vaultai.domain import DocType, ValueType

T, LT, M, D, ID, B, E = (
    ValueType.TEXT,
    ValueType.LONG_TEXT,
    ValueType.MONEY,
    ValueType.DATE,
    ValueType.IDENTIFIER,
    ValueType.BOOL,
    ValueType.ENUM,
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    value_type: ValueType
    required: bool = False
    # Printed labels that introduce this value ("Invoice No.", "Inv #"). Lowercase, matched case-insensitively.
    aliases: tuple[str, ...] = ()
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocSchema:
    doc_type: DocType
    fields: tuple[FieldSpec, ...]
    # Field keys projected onto the document row for filtering, sorting, and export.
    counterparty: str | None = None
    doc_date: str | None = None
    total: str | None = None
    primary_id: str | None = None
    title: str | None = None
    has_line_items: bool = False

    def field(self, key: str) -> FieldSpec:
        return next(f for f in self.fields if f.key == key)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields)


SIGNATURE_STATUS = ("signed", "partially_signed", "unsigned", "unknown")

_CURRENCY = FieldSpec("currency", "Currency", E, aliases=("currency",), options=("USD", "EUR", "GBP", "CAD", "AUD", "JPY", "MXN"))

SCHEMAS: dict[DocType, DocSchema] = {
    DocType.CHECK: DocSchema(
        DocType.CHECK,
        (
            FieldSpec("payer", "Payer", T),
            FieldSpec("bank_name", "Bank", T),
            FieldSpec("payee", "Payee", T, True, ("pay to the order of", "pay to")),
            FieldSpec("amount", "Amount", M, True, ("amount",)),
            FieldSpec("amount_written", "Written amount", T),
            _CURRENCY,
            FieldSpec("date", "Check date", D, True, ("date",)),
            FieldSpec("check_number", "Check number", ID, True, ("check no", "check number", "no.")),
            FieldSpec("routing_number", "Routing number", ID),
            FieldSpec("account_number", "Account number", ID),
            FieldSpec("memo", "Memo", T, aliases=("memo", "for")),
            FieldSpec("signature_present", "Signature present", B),
        ),
        counterparty="payee", doc_date="date", total="amount", primary_id="check_number",
    ),
    DocType.RECEIPT: DocSchema(
        DocType.RECEIPT,
        (
            FieldSpec("merchant", "Merchant", T, True),
            FieldSpec("merchant_address", "Merchant address", T),
            FieldSpec("receipt_number", "Receipt number", ID, aliases=("receipt #", "receipt no", "receipt number", "transaction")),
            FieldSpec("date", "Date", D, True, ("date",)),
            FieldSpec("subtotal", "Subtotal", M, aliases=("subtotal", "sub-total")),
            FieldSpec("tax", "Tax", M, aliases=("tax", "sales tax")),
            FieldSpec("total", "Total", M, True, ("total", "amount due", "total due")),
            _CURRENCY,
            FieldSpec("payment_method", "Payment method", T, aliases=("payment", "paid by", "card")),
        ),
        counterparty="merchant", doc_date="date", total="total", primary_id="receipt_number", has_line_items=True,
    ),
    DocType.INVOICE: DocSchema(
        DocType.INVOICE,
        (
            FieldSpec("vendor", "Vendor", T, True),
            FieldSpec("vendor_address", "Vendor address", T),
            FieldSpec("invoice_number", "Invoice number", ID, True, ("invoice number", "invoice no", "invoice #", "inv #")),
            FieldSpec("invoice_date", "Invoice date", D, True, ("invoice date", "date of issue", "date")),
            FieldSpec("due_date", "Due date", D, aliases=("due date", "payment due")),
            FieldSpec("bill_to", "Bill to", T, aliases=("bill to", "billed to", "customer")),
            FieldSpec("po_number", "PO number", ID, aliases=("po number", "purchase order", "po #", "p.o.")),
            FieldSpec("payment_terms", "Payment terms", T, aliases=("terms", "payment terms")),
            FieldSpec("subtotal", "Subtotal", M, aliases=("subtotal", "sub-total")),
            FieldSpec("discount", "Discount", M, aliases=("discount",)),
            FieldSpec("tax", "Tax", M, aliases=("tax", "sales tax", "vat")),
            FieldSpec("total", "Total", M, True, ("total due", "amount due", "total", "balance due")),
            _CURRENCY,
        ),
        counterparty="vendor", doc_date="invoice_date", total="total", primary_id="invoice_number", has_line_items=True,
    ),
    DocType.PURCHASE_ORDER: DocSchema(
        DocType.PURCHASE_ORDER,
        (
            FieldSpec("po_number", "PO number", ID, True, ("po number", "purchase order no", "po #")),
            FieldSpec("buyer", "Buyer", T, True),
            FieldSpec("vendor", "Vendor", T, True, ("vendor", "supplier")),
            FieldSpec("order_date", "Order date", D, True, ("order date", "date")),
            FieldSpec("delivery_date", "Delivery date", D, aliases=("delivery date", "deliver by", "required by")),
            FieldSpec("ship_to", "Ship to", T, aliases=("ship to",)),
            FieldSpec("payment_terms", "Payment terms", T, aliases=("terms", "payment terms")),
            FieldSpec("subtotal", "Subtotal", M, aliases=("subtotal",)),
            FieldSpec("tax", "Tax", M, aliases=("tax",)),
            FieldSpec("total", "Total", M, True, ("total",)),
            _CURRENCY,
        ),
        counterparty="vendor", doc_date="order_date", total="total", primary_id="po_number", has_line_items=True,
    ),
    DocType.PROPOSAL: DocSchema(
        DocType.PROPOSAL,
        (
            FieldSpec("quote_number", "Quote number", ID, aliases=("quote number", "quote no", "quote #", "proposal no")),
            FieldSpec("vendor", "Vendor", T, True),
            FieldSpec("client", "Client", T, aliases=("prepared for", "customer", "client")),
            FieldSpec("date", "Date", D, True, ("date",)),
            FieldSpec("valid_until", "Valid until", D, aliases=("valid until", "expires", "valid through")),
            FieldSpec("subtotal", "Subtotal", M, aliases=("subtotal",)),
            FieldSpec("tax", "Tax", M, aliases=("tax",)),
            FieldSpec("total", "Total", M, True, ("total", "quote total")),
            _CURRENCY,
        ),
        counterparty="vendor", doc_date="date", total="total", primary_id="quote_number", has_line_items=True,
    ),
    DocType.CONTRACT: DocSchema(
        DocType.CONTRACT,
        (
            FieldSpec("title", "Contract title", T, True),
            FieldSpec("contract_type", "Contract type", T),
            FieldSpec("parties", "Parties", T, True),
            FieldSpec("effective_date", "Effective date", D, True, ("effective date",)),
            FieldSpec("expiration_date", "Expiration date", D),
            FieldSpec("auto_renewal", "Auto-renewal", B),
            FieldSpec("renewal_terms", "Renewal terms", LT),
            FieldSpec("notice_period", "Non-renewal notice period", T),
            FieldSpec("renewal_notice_deadline", "Non-renewal notice deadline", D),
            FieldSpec("termination", "Termination conditions", LT),
            FieldSpec("payment_terms", "Payment terms", LT),
            FieldSpec("contract_value", "Recurring fee / value", M),
            _CURRENCY,
            FieldSpec("governing_law", "Governing law", T, aliases=("governed by the laws of",)),
            FieldSpec("dispute_resolution", "Dispute resolution", T),
            FieldSpec("signatories", "Signatories", T),
            FieldSpec("signature_status", "Signature status", E, True, options=SIGNATURE_STATUS),
        ),
        counterparty="parties", doc_date="effective_date", total="contract_value", title="title",
    ),
    DocType.STATEMENT_OF_WORK: DocSchema(
        DocType.STATEMENT_OF_WORK,
        (
            FieldSpec("title", "Title", T, True),
            FieldSpec("client", "Client", T, True, ("client",)),
            FieldSpec("provider", "Provider", T, True, ("provider", "contractor", "consultant")),
            FieldSpec("effective_date", "Effective date", D, True, ("effective date", "start date")),
            FieldSpec("end_date", "End date", D, aliases=("end date", "completion date")),
            FieldSpec("total", "Total fees", M, aliases=("total fees", "fixed fee", "total")),
            _CURRENCY,
            FieldSpec("payment_terms", "Payment terms", LT),
            FieldSpec("signature_status", "Signature status", E, options=SIGNATURE_STATUS),
        ),
        counterparty="provider", doc_date="effective_date", total="total", title="title",
    ),
    DocType.AGREEMENT_LETTER: DocSchema(
        DocType.AGREEMENT_LETTER,
        (
            FieldSpec("sender", "Sender", T, True),
            FieldSpec("sender_organization", "Sender organization", T),
            FieldSpec("recipient", "Recipient", T, True),
            FieldSpec("recipient_organization", "Recipient organization", T),
            FieldSpec("letter_date", "Letter date", D, True),
            FieldSpec("subject", "Subject", T, aliases=("re", "subject")),
            FieldSpec("agreement_statement", "Agreement statement", LT),
            FieldSpec("effective_date", "Effective date", D, aliases=("effective date",)),
            FieldSpec("compensation", "Compensation", M),
            _CURRENCY,
            FieldSpec("payment_terms", "Payment terms", LT),
            FieldSpec("acceptance_deadline", "Acceptance deadline", D),
            FieldSpec("termination", "Termination language", LT),
            FieldSpec("signature_status", "Acceptance status", E, True, options=SIGNATURE_STATUS),
        ),
        counterparty="sender_organization", doc_date="letter_date", total="compensation", title="subject",
    ),
    DocType.POLICY: DocSchema(
        DocType.POLICY,
        (
            FieldSpec("title", "Policy title", T, True),
            FieldSpec("policy_number", "Policy number", ID, aliases=("policy no", "policy number", "policy #")),
            FieldSpec("version", "Version", T, aliases=("version",)),
            FieldSpec("owner", "Owner", T, aliases=("owner", "policy owner", "department")),
            FieldSpec("effective_date", "Effective date", D, True, ("effective date",)),
            FieldSpec("review_date", "Next review", D, aliases=("next review", "review date")),
        ),
        counterparty="owner", doc_date="effective_date", primary_id="policy_number", title="title",
    ),
    DocType.ADMIN_FORM: DocSchema(
        DocType.ADMIN_FORM,
        (
            FieldSpec("form_title", "Form title", T, True),
            FieldSpec("form_number", "Form number", ID, aliases=("form", "form no", "form number")),
            FieldSpec("submitted_by", "Submitted by", T, aliases=("legal business name", "name", "submitted by")),
            FieldSpec("date", "Date", D, aliases=("date",)),
            FieldSpec("signature_present", "Signature present", B),
        ),
        counterparty="submitted_by", doc_date="date", primary_id="form_number", title="form_title",
    ),
    DocType.CORRESPONDENCE: DocSchema(
        DocType.CORRESPONDENCE,
        (
            FieldSpec("sender", "From", T, True, ("from",)),
            FieldSpec("recipient", "To", T, aliases=("to",)),
            FieldSpec("date", "Date", D, True, ("date",)),
            FieldSpec("subject", "Subject", T, aliases=("subject", "re")),
        ),
        counterparty="sender", doc_date="date", title="subject",
    ),
    DocType.OTHER: DocSchema(
        DocType.OTHER,
        (
            FieldSpec("title", "Title", T),
            FieldSpec("date", "Date", D, aliases=("date",)),
            FieldSpec("organization", "Organization", T),
            FieldSpec("amount", "Amount", M, aliases=("amount", "total")),
            _CURRENCY,
            FieldSpec("reference", "Reference", ID, aliases=("reference", "ref", "id")),
        ),
        counterparty="organization", doc_date="date", total="amount", primary_id="reference", title="title",
    ),
}

# Generic key-value pairs found on any document are stored as extra fields with this key prefix.
KV_PREFIX = "kv:"

def _enum_options() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for schema in SCHEMAS.values():
        for spec in schema.fields:
            if spec.options:
                assert out.setdefault(spec.key, spec.options) == spec.options, f"{spec.key} options differ by type"
    return out


# Enum fields share their options across document types (currency, signature status).
ENUM_OPTIONS: dict[str, tuple[str, ...]] = _enum_options()


def match_option(key: str, text: str) -> str | None:
    """The canonical option for `key` that `text` names, ignoring case, or None."""
    wanted = text.strip().casefold()
    return next((o for o in ENUM_OPTIONS.get(key, ()) if o.casefold() == wanted), None)
