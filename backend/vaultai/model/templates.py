"""Prompt templates, one per document family.

A template tells the model what the document is, what each field means (and
what it is easily confused with), and, for documents worth analyzing, what to
look for. The rules that apply to every task (null for unknown values, exact
quotes, JSON only) live in vaultai.model.prompts so they cannot drift apart.

Field notes are written for the field, never for a particular sample: do not
add a sample's wording here, or the evaluation stops measuring the model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from vaultai.domain import DocType

TEMPLATE_VERSION = "2026.10.3"


@dataclass(frozen=True)
class Template:
    key: str
    noun: str
    description: str
    notes: Mapping[str, str] = field(default_factory=dict)
    analysis: str | None = None
    legal: bool = False

    @property
    def version(self) -> str:
        return f"{self.key}@{TEMPLATE_VERSION}"


_CURRENCY = "ISO currency code of the amounts (USD, EUR, ...), only when a symbol or code is printed."

LEGAL_ANALYSIS = """This is issue spotting for a human reviewer. Report what the text says; do not give legal advice.
- Never say a clause is invalid, illegal, unenforceable, unfair, or risky, and never recommend an action. VaultAI's rules decide which clauses to flag; their flags are listed under facts.
- Look for payment terms, deliverables, notice and non-renewal terms, termination terms, duties that survive the agreement, and signature or acceptance requirements.
- Claims are about the parties, term, fees, renewal, termination, and signatures."""

TEMPLATES: dict[str, Template] = {t.key: t for t in (
    Template(
        "check", "check", "a bank check (cheque) written by a payer to a payee",
        {
            "payer": "the account holder who wrote the check, usually printed at the top left. Not the payee.",
            "bank_name": "the bank the check is drawn on.",
            "payee": "the person or business the check is made out to. Not the payer and not the bank.",
            "amount": "the numeric amount in the amount box.",
            "amount_written": "the amount written out in words, exactly as printed.",
            "currency": _CURRENCY,
            "date": "the date written on the check.",
            "check_number": "the check's own number, usually top right and repeated at the end of the MICR line.",
            "routing_number": "the nine-digit bank routing number, usually first on the MICR line.",
            "account_number": "the payer's account number on the MICR line.",
            "memo": "the memo or \"for\" line.",
        },
    ),
    Template(
        "receipt", "receipt", "a point-of-sale receipt for a completed purchase",
        {
            "merchant": "the store or business that issued the receipt, usually the first line. Not the card network.",
            "merchant_address": "the merchant's street address as printed.",
            "receipt_number": "the receipt or transaction number.",
            "date": "the date of the purchase.",
            "subtotal": "the amount before tax.",
            "tax": "the tax amount charged.",
            "total": "the final amount charged to the customer, including tax. Not the subtotal, tax, change, or tendered amount.",
            "currency": _CURRENCY,
            "payment_method": "how the purchase was paid, such as a card type with its masked number, or cash.",
        },
    ),
    Template(
        "invoice", "invoice", "a bill a vendor sends a customer for goods or services",
        {
            "vendor": "the business that issued the invoice and is owed the money, usually at the top. Not the billed customer.",
            "vendor_address": "the vendor's address as printed.",
            "invoice_number": "the invoice's own number. Not a purchase order, account, or customer number.",
            "invoice_date": "the date the invoice was issued. Not the due date.",
            "due_date": "the date payment is due.",
            "bill_to": "the customer being billed.",
            "po_number": "the customer's purchase order number referenced on the invoice.",
            "payment_terms": "the payment terms as printed, such as \"Net 30\".",
            "subtotal": "the sum of the line items before tax and discounts.",
            "discount": "the discount amount.",
            "tax": "the tax amount.",
            "total": "the final amount the customer must pay after tax and discounts. Not the subtotal or a single line item.",
            "currency": _CURRENCY,
        },
    ),
    Template(
        "purchase_order", "purchase order", "an order a buyer issues to a vendor",
        {
            "po_number": "the purchase order's own number.",
            "buyer": "the organization placing the order and issuing the purchase order, usually at the top.",
            "vendor": "the supplier the order is placed with. Not the buyer and not the ship-to location.",
            "order_date": "the date the purchase order was issued. Not the delivery date.",
            "delivery_date": "the requested delivery date.",
            "ship_to": "the delivery address or location.",
            "payment_terms": "the payment terms as printed.",
            "subtotal": "the sum of the line items before tax.",
            "tax": "the tax amount.",
            "total": "the total value of the order including tax.",
            "currency": _CURRENCY,
        },
    ),
    Template(
        "proposal", "quote or proposal", "a vendor's price quote or proposal to a prospective client",
        {
            "quote_number": "the quote's or proposal's own number.",
            "vendor": "the business offering the quote, usually at the top. Not the client.",
            "client": "who the quote is prepared for.",
            "date": "the date the quote was issued. Not the expiry date.",
            "valid_until": "the date the offer expires.",
            "subtotal": "the amount before tax.",
            "tax": "the tax amount.",
            "total": "the total quoted price.",
            "currency": _CURRENCY,
        },
    ),
    Template(
        "contract", "contract", "an agreement between two or more parties",
        {
            "title": "the agreement's title as printed.",
            "contract_type": "the kind of agreement, as the document names it.",
            "parties": "the legal names of the contracting parties as printed, separated by \"; \". Every name must be in the quoted line.",
            "effective_date": "the date the agreement takes effect.",
            "expiration_date": "the date the initial term ends.",
            "renewal_terms": "the renewal wording.",
            "notice_period": "the notice period for non-renewal, as printed.",
            "renewal_notice_deadline": "a printed calendar date by which non-renewal notice is due.",
            "termination": "the termination wording.",
            "payment_terms": "the payment wording.",
            "contract_value": "the fee or contract value as printed.",
            "currency": _CURRENCY,
            "governing_law": "the state or country whose law governs, as printed.",
            "dispute_resolution": "how disputes are resolved, as printed.",
            "signatories": "the names of the people who sign, separated by \"; \".",
        },
        LEGAL_ANALYSIS, legal=True,
    ),
    Template(
        "agreement_letter", "agreement letter", "a letter that sets out or confirms an agreement, such as an engagement or offer letter",
        {
            "sender": "the person who wrote and signs the letter, usually the name under the closing. Not the recipient.",
            "sender_organization": "the sender's organization.",
            "recipient": "the person the letter is addressed to.",
            "recipient_organization": "the recipient's organization.",
            "letter_date": "the date of the letter, usually above the recipient's address. Not a deadline in the body.",
            "subject": "the subject or \"Re:\" line.",
            "agreement_statement": "the sentence that states what is agreed.",
            "effective_date": "the date the agreement takes effect.",
            "compensation": "the fee or compensation amount.",
            "currency": _CURRENCY,
            "payment_terms": "the payment wording.",
            "acceptance_deadline": "the printed date by which the recipient must accept.",
            "termination": "the termination wording.",
        },
        LEGAL_ANALYSIS, legal=True,
    ),
    Template(
        "statement_of_work", "statement of work", "a statement of work that defines services, deliverables, and fees under an agreement",
        {
            "title": "the statement of work's title as printed.",
            "client": "the customer receiving the work. Not the provider.",
            "provider": "the party performing the work. Not the client.",
            "effective_date": "the date the statement of work starts.",
            "end_date": "the date the work ends.",
            "total": "the total fees.",
            "currency": _CURRENCY,
            "payment_terms": "the payment wording.",
        },
        LEGAL_ANALYSIS, legal=True,
    ),
    Template(
        "policy", "business policy", "an internal business policy that sets rules for staff",
        {
            "title": "the policy's own title. Not the company name or the manual it belongs to.",
            "policy_number": "the policy's number or code.",
            "version": "the policy's version as printed.",
            "owner": "the department or role that owns the policy.",
            "effective_date": "the date the policy takes effect.",
            "review_date": "the next scheduled review date.",
        },
        """This is issue spotting for a human reviewer. Report what the text says; do not give legal or HR advice.
- Each rule people must follow is an obligation. party: who must follow it as printed ("Employees"). A rule that only describes how something works, or what is not reimbursed, is not an obligation.
- Claims are about scope, ownership, dates, and the main rules.""",
        legal=True,
    ),
    Template(
        "correspondence", "memo or letter", "business correspondence such as a memo, letter, or notice",
        {
            "sender": "who the message is from.",
            "recipient": "who the message is addressed to.",
            "date": "the date the message was written or sent. Not a deadline in the body.",
            "subject": "the subject line.",
        },
        """- Each request or requirement the message places on its readers is an obligation. Background and scheduling information is not.
- Claims are about who wrote to whom, about what, and what is asked.""",
    ),
    Template(
        "general", "business document", "a business document",
        {
            "form_title": "the form's title as printed.",
            "form_number": "the form's number or code.",
            "submitted_by": "the person or business completing the form.",
            "date": "the main date on the document, such as the date it was completed or issued.",
            "title": "the document's title as printed.",
            "organization": "the organization that issued the document.",
            "amount": "the main amount on the document.",
            "currency": _CURRENCY,
            "reference": "the document's reference or identifier.",
        },
        """- Each duty or deadline the document states is an obligation.
- Claims describe the document.""",
    ),
)}

BY_TYPE: dict[DocType, Template] = {
    DocType.CHECK: TEMPLATES["check"],
    DocType.RECEIPT: TEMPLATES["receipt"],
    DocType.INVOICE: TEMPLATES["invoice"],
    DocType.PURCHASE_ORDER: TEMPLATES["purchase_order"],
    DocType.PROPOSAL: TEMPLATES["proposal"],
    DocType.CONTRACT: TEMPLATES["contract"],
    DocType.AGREEMENT_LETTER: TEMPLATES["agreement_letter"],
    DocType.STATEMENT_OF_WORK: TEMPLATES["statement_of_work"],
    DocType.POLICY: TEMPLATES["policy"],
    DocType.CORRESPONDENCE: TEMPLATES["correspondence"],
    DocType.ADMIN_FORM: TEMPLATES["general"],
    DocType.OTHER: TEMPLATES["general"],
}

GENERAL_NOUNS = {DocType.ADMIN_FORM: "administrative form", DocType.OTHER: "business document"}

# Documents whose summary and obligations need reading. The rest get a template summary of their verified fields.
ANALYZED_TYPES = frozenset(t for t, template in BY_TYPE.items() if template.analysis) - {DocType.ADMIN_FORM}


def template_for(doc_type: DocType) -> Template:
    return BY_TYPE[doc_type]


def noun(doc_type: DocType) -> str:
    return GENERAL_NOUNS.get(doc_type, BY_TYPE[doc_type].noun)
