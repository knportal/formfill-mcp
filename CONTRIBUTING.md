# Contributing to FormFill MCP

Thanks for your interest in contributing! This project is maintained by [Plenitudo AI](https://plenitudo.ai).

## Dev Environment Setup

### Prerequisites

- Python 3.10+
- `pip` and `venv`
- [wrangler](https://developers.cloudflare.com/workers/wrangler/) (for Cloudflare Worker changes)
- [Stripe CLI](https://stripe.com/docs/stripe-cli) (for webhook testing)

### Install

```bash
git clone https://github.com/knportal/formfill-mcp.git
cd formfill-mcp

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Fill in .env values for local dev
```

### Run the server locally

```bash
source venv/bin/activate
python server.py
# MCP server starts on http://localhost:8000
```

### Create a test API key

```bash
python manage_keys.py create --tier free
# Outputs: ff_free_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## Making Changes

### Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/) for Python code
- Use descriptive variable names; avoid single-letter names outside loops
- All tool functions must return JSON strings (`json.dumps({...})`)
- Log meaningful events at appropriate levels (INFO for normal ops, ERROR for failures)

### Testing

Before submitting a PR, manually test that:
1. `list_form_fields` returns correct field names for a sample PDF
2. `fill_form` produces a valid filled PDF
3. Invalid API keys return `{"error": "...", "ok": false}`
4. Usage limits are enforced for free-tier keys

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add support for checkbox fields
fix: handle PDFs with no form fields gracefully
docs: update README with new Claude Desktop config format
chore: bump pypdf to 4.x
```

## Pull Request Process

1. Fork the repo and create a branch: `git checkout -b feat/your-feature`
2. Make your changes with focused commits
3. Push to your fork and open a PR against `main`
4. Describe what you changed and why in the PR description
5. One of the maintainers will review within a few days

## Questions?

Open an issue or email [hello@plenitudo.ai](mailto:hello@plenitudo.ai).
