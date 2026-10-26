#!/usr/bin/env bash
# Build dashboard/dist/cloudops_guardian_dashboard.zip (pure Python + one HTML file; boto3 comes from the Lambda runtime).
set -euo pipefail
cd "$(dirname "$0")/../dashboard"
mkdir -p dist && rm -f dist/cloudops_guardian_dashboard.zip
zip -j -q dist/cloudops_guardian_dashboard.zip lambda_function.py index.html
unzip -l dist/cloudops_guardian_dashboard.zip
