# Contributing
Maintainer: Vinay Shinde, Generative AI Engineer (vinayshinde.tech@outlook.com)

1. Open an issue describing the bug or change before starting large work.
2. Keep pull requests focused. Include tests for logic in `app/assistant.py`.
3. Before submitting, run:
   ```bash
   .venv/bin/pytest -q tests
   .venv/bin/cfn-lint -r us-east-1 -t cloudformation/*.yaml
   ```
4. Never commit credentials, `.env` files or real customer data.

## Reporting security issues
Do not open a public issue. Email vinayshinde.tech@outlook.com with details and steps to reproduce.
