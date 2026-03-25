# FormFill MCP — Smithery Marketplace Listing

---

## Short description

Fill any interactive PDF form from your AI agent — tax forms, HR paperwork, legal documents — in a single tool call.

---

## What it does

FormFill MCP connects your AI agent to PDF forms. Pass a file path and a dictionary of field values; get back a completed PDF. No screenshots, no browser automation, no copy-paste.

Three tools are available:

- **`list_form_fields`** — discover every fillable field in a PDF before you fill it
- **`fill_form`** — fill a PDF form and write the result to disk
- **`fill_form_multipage`** — same as above, optimised for forms that span multiple pages

FormFill works with any standard interactive PDF (AcroForm). It does not currently support scanned/flat PDFs.

---

## Example use cases

### Tax preparation agent
An agent that collects a user's tax data through a conversation, then automatically populates IRS Form W-9, 1099-MISC, or state-specific forms — ready to sign and send.

### HR onboarding agent
An HR bot that walks a new employee through onboarding, then fills out I-9 employment eligibility forms, direct-deposit authorisation forms, and benefits enrolment paperwork — all without the employee ever touching a PDF editor.

### Legal document agent
A legal-tech agent that drafts and populates contract templates, court filing forms, and NDA agreements from structured intake data — cutting hours of paralegal work to seconds.

---

## Pricing

| Tier | Price | Monthly fills |
|---|---|---|
| **Free** | $0 | 50 fills / month |
| **Pro** | $9.99 / month | Unlimited |

No credit card required for the free tier.

---

## How to get an API key

Visit **[formfill.plenitudo.ai](https://formfill.plenitudo.ai)** to generate a free API key instantly. Upgrade to Pro from the same dashboard.

---

## Example tool call

```json
{
  "tool": "fill_form",
  "parameters": {
    "pdf_path": "/Users/alice/forms/w9_blank.pdf",
    "field_values": {
      "topmostSubform[0].Page1[0].f1_01[0]": "Alice Johnson",
      "topmostSubform[0].Page1[0].f1_02[0]": "Alice's Consulting LLC",
      "topmostSubform[0].Page1[0].f1_03[0]": "LLC",
      "topmostSubform[0].Page1[0].f1_05[0]": "742 Evergreen Terrace, Springfield IL 62701",
      "topmostSubform[0].Page1[0].f1_07[0]": "12-3456789"
    },
    "output_path": "/Users/alice/forms/w9_filled.pdf",
    "api_key": "ff_free_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  }
}
```

**Response:**
```json
{
  "ok": true,
  "output_path": "/Users/alice/forms/w9_filled.pdf",
  "fields_filled": 5,
  "pages": 1,
  "message": "Filled PDF saved to /Users/alice/forms/w9_filled.pdf"
}
```

---

## Setup (self-hosted)

1. Clone the repo and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Add to your Claude Desktop config:
   ```json
   {
     "mcpServers": {
       "formfill": {
         "command": "python",
         "args": ["/path/to/formfill-mcp/server.py"]
       }
     }
   }
   ```

3. Pass your API key in each tool call — that's it.

---

## Links

- Website & API keys: [formfill.plenitudo.ai](https://formfill.plenitudo.ai)
- Built by [Plenitudo.ai](https://plenitudo.ai)
