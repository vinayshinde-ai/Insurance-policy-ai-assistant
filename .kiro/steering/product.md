---
inclusion: always
---

# Product: Insurance Policy AI Assistant

Owner and author: **Vinay Shinde, Generative AI Engineer** (`vinayshinde.tech@outlook.com`). This is Vinay's project. Use his name and email in the README, LICENSE, template `Metadata`, the `Owner` tag, and as the default alarm notification email.

## Use case
A customer-facing chat assistant ("Felix") for a motor insurance company ("AnyCompany"). A signed-in policyholder asks questions about their own policy and gets answers that:
- combine the customer's private policy (`customer_policy/<username>.txt`) with general policy documents (`policy_docs/*.pdf`) retrieved from a Bedrock Knowledge Base,
- cite the source documents (file name, page or section only when present in the retrieved context),
- are screened by Amazon Bedrock Guardrails (harmful content, prompt attack, grounding, relevance),
- use only those documents, and point the user to `insurance-queries@anycompany.com` when they need more help.

## Request flow
CloudFront (+ WAF) -> ALB -> EC2 running Streamlit -> Cognito login -> S3 (customer policy) + Bedrock KB Retrieve (OpenSearch Serverless vectors, Titan Embed Text V2) -> Claude Haiku via Bedrock with Guardrail -> response streamed to the UI. Chat turns are stored per session in DynamoDB.

## Decisions made by the owner
- Region: **us-east-1**.
- Deployment: **AWS CloudFormation** into Vinay's own AWS account. End state is plain templates under `cloudformation/`, deployed with `aws cloudformation deploy`, with no CDK CLI dependency.
- **No encryption work.** Do not add KMS keys, customer-managed keys, SSE-KMS, or other encryption settings. Leave AWS service defaults as they are. Keep HTTPS between the viewer and CloudFront.
- Goal: production-grade reliability, security hygiene (apart from the encryption exclusion), observability and operability.
