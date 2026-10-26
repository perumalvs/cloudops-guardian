#!/usr/bin/env bash
# Read-only: dump a role's trust policy, attached managed policies and inline policies to baseline/iam/<role>/
# Usage: tools/capture_role.sh <role-name>
set -euo pipefail
R="$1"; D="baseline/iam/$R"; mkdir -p "$D"
aws iam get-role --role-name "$R" --query Role.AssumeRolePolicyDocument > "$D/trust.json"
for ARN in $(aws iam list-attached-role-policies --role-name "$R" --query 'AttachedPolicies[].PolicyArn' --output text); do
  N=${ARN##*/}; V=$(aws iam get-policy --policy-arn "$ARN" --query Policy.DefaultVersionId --output text)
  aws iam get-policy-version --policy-arn "$ARN" --version-id "$V" --query PolicyVersion.Document > "$D/managed_$N.json"
done
for N in $(aws iam list-role-policies --role-name "$R" --query PolicyNames --output text); do
  aws iam get-role-policy --role-name "$R" --policy-name "$N" --query PolicyDocument > "$D/inline_$N.json"
done
ls "$D"
