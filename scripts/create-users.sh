#!/usr/bin/env bash
# Create a Cognito user for each customer_policy/<username>.txt file (or for the usernames given).
# Users get a temporary password and must set a new one at first sign-in.
# Author: Vinay Shinde, Generative AI Engineer (vinayshinde.tech@outlook.com)
#
# Usage: scripts/create-users.sh [username ...]
set -euo pipefail

REGION="us-east-1"
ENVIRONMENT="${ENVIRONMENT:-prod}"
PLATFORM_STACK="insurance-assistant-${ENVIRONMENT}-platform"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

POOL_ID="$(aws cloudformation describe-stacks --stack-name "$PLATFORM_STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)"

if [[ $# -gt 0 ]]; then
  users=("$@")
else
  users=()
  for f in "$ROOT"/customer_policy/*.txt; do users+=("$(basename "$f" .txt)"); done
fi

read -r -s -p "Temporary password for new users (12+ chars, upper, lower, digit, symbol): " TEMP_PW; echo

for u in "${users[@]}"; do
  if [[ ! "$u" =~ ^[A-Za-z0-9_.-]{1,64}$ ]]; then
    echo "skip ${u}: username must match ^[A-Za-z0-9_.-]{1,64}$"; continue
  fi
  if aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" --username "$u" >/dev/null 2>&1; then
    echo "exists ${u}"; continue
  fi
  aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$u" \
    --temporary-password "$TEMP_PW" --message-action SUPPRESS >/dev/null
  echo "created ${u}"
done
echo "Share the temporary password securely. Each user sets a new password at first sign-in."
