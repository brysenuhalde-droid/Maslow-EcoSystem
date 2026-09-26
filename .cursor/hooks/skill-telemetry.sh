#!/bin/bash
# aictrl skill usage telemetry
# Fires on beforeReadFile in Cursor IDE
# Filters for skill files (.cursor/rules/*.mdc), extracts slug, POSTs telemetry
# MUST output {"permission":"allow"} to stdout — never block file reads

set -euo pipefail

INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | jq -r '.file_path // empty' 2>/dev/null)

# Only track .cursor/rules/*.mdc files
case "$FILE_PATH" in
  */.cursor/rules/*.mdc) ;;
  *) echo '{"permission":"allow"}'; exit 0 ;;
esac

# Extract skill slug from filename: /path/.cursor/rules/code-review.mdc -> code-review
FILENAME=$(basename "$FILE_PATH" .mdc)
SKILL_NAME=$(echo "$FILENAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')

# Validate skill name matches telemetry API pattern (min 2 chars, a-z0-9-)
if ! echo "$SKILL_NAME" | grep -qE '^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$'; then
  echo '{"permission":"allow"}'
  exit 0
fi

MACHINE_ID=$(hostname | sha256sum | cut -d' ' -f1 | head -c 16)
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")
REPO_URL=$(git remote get-url origin 2>/dev/null || echo "")

# POST telemetry in background — never delay the file read
if [ -n "$AICTRL_API_KEY" ] && [ -n "$AICTRL_TELEMETRY_URL" ] && [ -n "$REPO_URL" ]; then
  curl -s -X POST \
    "$AICTRL_TELEMETRY_URL/skill-usage" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $AICTRL_API_KEY" \
    -d "$(jq -n \
      --arg sn "$SKILL_NAME" \
      --arg src "cursor" \
      --arg repo "$REPO_URL" \
      --arg mid "$MACHINE_ID" \
      --arg ts "$TIMESTAMP" \
      '{skillName: $sn, source: $src, repoUrl: $repo, machineId: $mid, timestamp: $ts}')" \
    --connect-timeout 3 \
    --max-time 5 \
    > /dev/null 2>&1 &
fi

# CRITICAL: always allow the read
echo '{"permission":"allow"}'
