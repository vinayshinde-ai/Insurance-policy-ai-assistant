#!/usr/bin/env bash
# Delete the Insurance Policy AI Assistant stacks.
# Customer policies, chat history, the Cognito user pool and access logs are RETAINED on purpose.
# Author: Vinay Shinde, Generative AI Engineer (vinayshinde.tech@outlook.com)
set -euo pipefail

REGION="us-east-1"
ENVIRONMENT="${ENVIRONMENT:-prod}"
PLATFORM_STACK="insurance-assistant-${ENVIRONMENT}-platform"
APP_STACK="insurance-assistant-${ENVIRONMENT}-app"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

out() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null || true; }

empty_bucket() {
  local b="$1"
  [[ -z "$b" || "$b" == "None" ]] && return 0
  echo "Emptying s3://${b} (all versions)"
  local listing payload
  listing="$(mktemp)"; payload="$(mktemp)"
  while true; do
    aws s3api list-object-versions --bucket "$b" --max-keys 1000 --output json > "$listing"
    python3 - "$listing" "$payload" <<'PY' || break
import json, sys
d = json.load(open(sys.argv[1])) if open(sys.argv[1]).read().strip() else {}
objs = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in (d.get("Versions") or []) + (d.get("DeleteMarkers") or [])][:1000]
if not objs:
    sys.exit(1)
json.dump({"Objects": objs, "Quiet": True}, open(sys.argv[2], "w"))
PY
    aws s3api delete-objects --bucket "$b" --delete "file://${payload}" >/dev/null
  done
  rm -f "$listing" "$payload"
}

echo "Account $(aws sts get-caller-identity --query Account --output text), region ${REGION}"
read -r -p "Type the environment name (${ENVIRONMENT}) to delete its stacks: " confirm
[[ "$confirm" == "$ENVIRONMENT" ]] || { echo "Aborted."; exit 1; }

if aws cloudformation describe-stacks --stack-name "$APP_STACK" >/dev/null 2>&1; then
  aws cloudformation update-termination-protection --no-enable-termination-protection --stack-name "$APP_STACK" >/dev/null
  LB_ARN="$(out "$APP_STACK" LoadBalancerArn)"
  [[ -n "$LB_ARN" && "$LB_ARN" != "None" ]] && aws elbv2 modify-load-balancer-attributes --load-balancer-arn "$LB_ARN" \
    --attributes Key=deletion_protection.enabled,Value=false >/dev/null
  echo "Deleting ${APP_STACK} (CloudFront removal takes ~10 minutes)"
  aws cloudformation delete-stack --stack-name "$APP_STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$APP_STACK"
fi

if aws cloudformation describe-stacks --stack-name "$PLATFORM_STACK" >/dev/null 2>&1; then
  aws cloudformation update-termination-protection --no-enable-termination-protection --stack-name "$PLATFORM_STACK" >/dev/null
  empty_bucket "$(out "$PLATFORM_STACK" KnowledgeBaseDocsBucketName)"
  empty_bucket "$(out "$PLATFORM_STACK" ArtifactsBucketName)"
  echo "Retained (delete manually if no longer needed):"
  echo "  customer policy bucket: $(out "$PLATFORM_STACK" CustomerPolicyBucketName)"
  echo "  access logs bucket:     $(out "$PLATFORM_STACK" AccessLogsBucketName)"
  echo "  chat history table:     $(out "$PLATFORM_STACK" ChatHistoryTableName) (deletion protection on)"
  echo "  user pool:              $(out "$PLATFORM_STACK" UserPoolId) (deletion protection on)"
  echo "Deleting ${PLATFORM_STACK}"
  aws cloudformation delete-stack --stack-name "$PLATFORM_STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$PLATFORM_STACK"
fi
echo "Done."
