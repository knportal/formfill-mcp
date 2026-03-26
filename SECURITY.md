# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅ Yes    |
| < 1.0   | ❌ No     |

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not** open a public GitHub issue.

Instead, email us at **security@plenitudo.ai** with:

- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested remediation (optional)

We will acknowledge your report within **72 hours** and aim to release a fix within 14 days for critical issues.

## API Key Security

- **Never commit API keys** to version control. Add `.env` to your `.gitignore`.
- If a key is exposed, **rotate it immediately** via the FormFill dashboard at [formfill.plenitudo.ai](https://formfill.plenitudo.ai).
- Keys prefixed `ff_free_` are free-tier; `ff_pro_` are paid. Both should be treated as secrets.
- Keys are stored hashed in the database — we cannot retrieve your plaintext key after creation.

## Responsible Disclosure

We follow responsible disclosure principles. Researchers who report valid vulnerabilities in good faith will be credited in the release notes (unless they prefer to remain anonymous).
