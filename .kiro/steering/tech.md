---
inclusion: always
---

# Tech stack and conventions

## Stack
- Python 3, Streamlit, `streamlit-cognito-auth`, boto3.
- Bedrock: `bedrock-runtime` (`invoke_model_with_response_stream`), `bedrock-agent-runtime` (`retrieve`), Guardrails, Knowledge Base on OpenSearch Serverless, Titan Text Embeddings V2 (1024 dims).
- Model ID is a parameter. Default: `global.anthropic.claude-haiku-4-5-20251001-v1:0`.
- AWS: S3 (KB docs, customer policies, access logs, app artifacts), DynamoDB (on demand), Cognito, VPC with 2 AZs and NAT, EC2 Auto Scaling, ALB, CloudFront, WAFv2 (scope CLOUDFRONT).
- Local tooling: AWS CLI v2, Node 20, Python 3.9 (system), Docker. Install `cfn-lint` in a virtualenv to validate templates.

## Known issues in the original sample (fix during migration)
Application (`user_data_script.sh`):
- Unquoted heredoc `EOF` lets bash expand `$` and backticks inside the Python. Ship the app as a file from S3 instead.
- Unused imports (`yaml`, `random`, `time`, `base64`, `pandas`, `numpy`); unpinned packages.
- `authenticator.login()` called twice.
- `GUARDRAIL_VERSION = "DRAFT"`; use a published `AWS::Bedrock::GuardrailVersion`.
- `max_tokens: 10000`; use about 1024, configurable.
- Bedrock client `max_attempts: 0`; enable standard/adaptive retries.
- `user_policy()` swallows all exceptions and builds the S3 key from the raw username. Validate against `^[A-Za-z0-9_.-]{1,64}$`, handle `NoSuchKey` separately, log other errors.
- Chat history is a comma-joined string. Store one item per turn (`session_id` + `ts`) with `username`, TTL and guardrail outcome. Never log policy text.
- Handle guardrail interventions and stream errors without crashing the UI.
- Run Streamlit under `systemd`, not `nohup`.

Infrastructure:
- Single `t2.micro`, no recovery. Use Launch Template + ASG (min 1, max 2) with ELB health checks; `t3.small` default.
- `AmazonBedrockFullAccess` is too broad. Scope to the model/inference profile, guardrail and KB.
- Instance role can read the access-log bucket. Remove.
- Self sign-up is open (username + email, verified by code). The pre sign-up Lambda reserves usernames that have a policy file; never remove that check, because policies are matched by username.
- The guardrail grounds every streamed chunk, so any fixed text the model may repeat (such as the support email) must be in the grounding source.
- Timestamped resource names break updates. Use stack-name-based names.
- Production data: `DeletionPolicy: Retain` on the customer policy bucket and DynamoDB table, PITR on DynamoDB, versioning on the customer policy bucket.
- Add log groups, alarms (ALB 5xx, unhealthy hosts, ASG), SNS topic to `vinayshinde.tech@outlook.com`, VPC flow logs, dashboard.
- WAF rule name says 300 but limit is 100. Parameterize and name it correctly.
- Add a CloudFront-to-ALB secret header checked by the ALB listener, because the CloudFront prefix list admits any distribution.

## Conventions
- Parameters over hard-coded values: environment name, model ID, instance type, prefix list, alarm email, rate limit.
- Tags on every resource: `Project=insurance-policy-ai-assistant`, `Owner=Vinay Shinde`, `Environment=<env>`.
- Least-privilege IAM. Comment any wildcard resource with the reason.
- Python: type hints, `logging`, no secrets in code, small testable functions.
