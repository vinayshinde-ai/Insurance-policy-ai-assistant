---
inclusion: always
---

# Project structure and workflow

## Layout
```
.
├── app/
│   ├── app.py              # Streamlit UI
│   ├── assistant.py        # Pure logic: validation, prompt building, stream parsing
│   └── requirements.txt    # Pinned runtime deps
├── cloudformation/
│   ├── 01-platform.yaml    # S3, DynamoDB, Cognito, Bedrock KB + AOSS, Guardrail
│   └── 02-app.yaml         # VPC, ALB, ASG, IAM, WAF, CloudFront, alarms, dashboard
├── scripts/
│   ├── deploy.sh           # End-to-end deploy (us-east-1)
│   ├── create-users.sh     # Create Cognito users matching customer_policy/*.txt
│   └── destroy.sh
├── customer_policy/        # <username>.txt sample data
├── policy_docs/            # General policy PDFs for the KB
├── tests/                  # pytest for app logic
└── legacy-cdk/             # Original CDK code, kept for reference
```

## Deployment
- Account: Vinay's AWS account. Region: `us-east-1`. Confirm `aws sts get-caller-identity` before deploying and ask before any create/update/delete stack command.
- Prerequisite: Bedrock model access for the Claude model and Titan Embed Text V2.
- Order: platform -> docs upload + KB ingestion -> app. `scripts/deploy.sh` does all of it.
- Cognito username must equal the file name stem in `customer_policy/`.

## Validation before declaring done
1. `.venv/bin/cfn-lint -r us-east-1 -t cloudformation/*.yaml` passes.
2. `.venv/bin/pytest -q tests` passes.
3. After deploy: CloudFront URL loads, a test user signs in, a question returns a cited answer, a prompt-injection attempt is blocked, the ALB target is healthy.

## Rules
- Never commit credentials or `.env` files. Do not delete the sample data or PDFs.
- Keep `README.md` in sync with the deploy steps.
