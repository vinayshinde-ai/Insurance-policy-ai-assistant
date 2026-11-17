#!/usr/bin/env bash
# End-to-end deployment of the Insurance Policy AI Assistant to us-east-1.
# Author: Vinay Shinde, Generative AI Engineer (vinayshinde.tech@outlook.com)
#
# Usage: scripts/deploy.sh [-y]
# Env overrides: ENVIRONMENT (default prod), ALARM_EMAIL, MODEL_ID, AOSS_STANDBY (ENABLED|DISABLED),
#                MIN_INSTANCES, MAX_INSTANCES, INSTANCE_TYPE
set -euo pipefail

REGION="us-east-1"
ENVIRONMENT="${ENVIRONMENT:-prod}"
PLATFORM_STACK="insurance-assistant-${ENVIRONMENT}-platform"
APP_STACK="insurance-assistant-${ENVIRONMENT}-app"
ALARM_EMAIL="${ALARM_EMAIL:-vinayshinde.tech@outlook.com}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

log() { printf '\n==> %s\n' "$*"; }
out() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }
stack_exists() { aws cloudformation describe-stacks --stack-name "$1" >/dev/null 2>&1; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
CALLER="$(aws sts get-caller-identity --query Arn --output text)"
log "Account ${ACCOUNT} | ${CALLER} | region ${REGION} | environment ${ENVIRONMENT}"
if [[ "${1:-}" != "-y" ]]; then
  read -r -p "Deploy to this account? [y/N] " ok
  [[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

log "Checking Bedrock model access"
aws bedrock get-foundation-model --model-identifier amazon.titan-embed-text-v2:0 >/dev/null \
  || { echo "Enable Titan Text Embeddings V2 in the Bedrock console (Model access)."; exit 1; }

# ---------------------------------------------------------------- platform
log "Deploying ${PLATFORM_STACK} (first run takes ~10 minutes)"
platform_params=(Environment="$ENVIRONMENT")
[[ -n "${AOSS_STANDBY:-}" ]] && platform_params+=(AossStandbyReplicas="$AOSS_STANDBY")
aws cloudformation deploy \
  --stack-name "$PLATFORM_STACK" \
  --template-file "$ROOT/cloudformation/01-platform.yaml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "${platform_params[@]}" \
  --tags Project=insurance-policy-ai-assistant Owner="Vinay Shinde" Environment="$ENVIRONMENT" \
  --no-fail-on-empty-changeset
aws cloudformation update-termination-protection --enable-termination-protection --stack-name "$PLATFORM_STACK" >/dev/null

DOCS_BUCKET="$(out "$PLATFORM_STACK" KnowledgeBaseDocsBucketName)"
POLICY_BUCKET="$(out "$PLATFORM_STACK" CustomerPolicyBucketName)"
ARTIFACTS_BUCKET="$(out "$PLATFORM_STACK" ArtifactsBucketName)"
KB_ID="$(out "$PLATFORM_STACK" KnowledgeBaseId)"
DS_ID="$(out "$PLATFORM_STACK" DataSourceId)"

# ---------------------------------------------------------------- data
log "Uploading general policy documents"
aws s3 sync "$ROOT/policy_docs" "s3://${DOCS_BUCKET}/" --delete --exclude ".*"

log "Uploading customer policies (existing files are never deleted)"
aws s3 sync "$ROOT/customer_policy" "s3://${POLICY_BUCKET}/" --exclude "*" --include "*.txt"

log "Syncing the Knowledge Base"
JOB_ID="$(aws bedrock-agent start-ingestion-job --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
  --query ingestionJob.ingestionJobId --output text)"
while true; do
  STATUS="$(aws bedrock-agent get-ingestion-job --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
    --ingestion-job-id "$JOB_ID" --query ingestionJob.status --output text)"
  echo "  ingestion ${JOB_ID}: ${STATUS}"
  case "$STATUS" in
    COMPLETE) break ;;
    FAILED|STOPPED)
      aws bedrock-agent get-ingestion-job --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
        --ingestion-job-id "$JOB_ID" --query ingestionJob.failureReasons
      exit 1 ;;
  esac
  sleep 15
done

# ---------------------------------------------------------------- app artifact
log "Packaging the Streamlit app"
APP_HASH="$(cat "$ROOT"/app/*.py "$ROOT/app/requirements.txt" | shasum -a 256 | cut -c1-12)"
APP_KEY="app/insurance-assistant-${APP_HASH}.zip"
TMP_ZIP="$(mktemp -d)/app.zip"
(cd "$ROOT/app" && zip -q -X "$TMP_ZIP" ./*.py requirements.txt)
aws s3 cp "$TMP_ZIP" "s3://${ARTIFACTS_BUCKET}/${APP_KEY}" --only-show-errors
echo "  uploaded s3://${ARTIFACTS_BUCKET}/${APP_KEY}"

# ---------------------------------------------------------------- app stack
app_params=(Environment="$ENVIRONMENT" PlatformStackName="$PLATFORM_STACK" AppArtifactKey="$APP_KEY" AlarmEmail="$ALARM_EMAIL")
[[ -n "${MODEL_ID:-}" ]] && app_params+=(ModelId="$MODEL_ID")
[[ -n "${MIN_INSTANCES:-}" ]] && app_params+=(MinInstances="$MIN_INSTANCES")
[[ -n "${MAX_INSTANCES:-}" ]] && app_params+=(MaxInstances="$MAX_INSTANCES")
[[ -n "${INSTANCE_TYPE:-}" ]] && app_params+=(InstanceType="$INSTANCE_TYPE")
# The origin secret is generated once. On updates, CloudFormation keeps the previous value.
if ! stack_exists "$APP_STACK"; then
  app_params+=(OriginVerifySecret="$(openssl rand -hex 32)")
fi

log "Deploying ${APP_STACK} (first run takes ~15-20 minutes, mostly CloudFront)"
aws cloudformation deploy \
  --stack-name "$APP_STACK" \
  --template-file "$ROOT/cloudformation/02-app.yaml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "${app_params[@]}" \
  --tags Project=insurance-policy-ai-assistant Owner="Vinay Shinde" Environment="$ENVIRONMENT" \
  --no-fail-on-empty-changeset
aws cloudformation update-termination-protection --enable-termination-protection --stack-name "$APP_STACK" >/dev/null

log "Done"
echo "App URL:        $(out "$APP_STACK" AppUrl)"
echo "Dashboard:      $(out "$APP_STACK" DashboardUrl)"
echo "User pool ID:   $(out "$PLATFORM_STACK" UserPoolId)"
echo
echo "Next: confirm the SNS subscription email sent to ${ALARM_EMAIL}, then create users with scripts/create-users.sh"
