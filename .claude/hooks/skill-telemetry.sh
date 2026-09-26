#!/bin/bash
# aictrl skill usage telemetry
# Fires after PostToolUse in Claude Code (Skill or Read tool)

set -euo pipefail

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)

case "$TOOL_NAME" in
  Skill)
    SKILL_NAME=$(echo "$INPUT" | jq -r '.tool_input.skill // empty' 2>/dev/null)
    SKILL_NAME="${SKILL_NAME##*:}"
    if [ -z "$SKILL_NAME" ]; then
      exit 0
    fi
    ;;
  Read)
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
    if [ -z "$FILE_PATH" ]; then
      exit 0
    fi
    case "$FILE_PATH" in
      */.claude/skills/*.md|*/.claude/commands/*.md|*/.claude/plugins/cache/*/skills/*.md|*/.claude/plugins/cache/*/commands/*.md) ;;
      *) exit 0 ;;
    esac
    dir="$(dirname "$FILE_PATH")"
    SKILL_NAME="$(basename "$FILE_PATH" .md)"
    while [ "$(basename "$dir")" != "skills" ] && [ "$(basename "$dir")" != "commands" ] && [ "$dir" != "/" ]; do
      SKILL_NAME="$(basename "$dir")"
      dir="$(dirname "$dir")"
    done
    ;;
  *)
    exit 0
    ;;
esac

# Normalize: lowercase, replace non-alphanumeric with hyphens
SKILL_NAME=$(echo "$SKILL_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')

# Validate skill name format (must match telemetry API requirements)
if ! echo "$SKILL_NAME" | grep -qE '^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$'; then
  exit 0
fi

DURATION=$(echo "$INPUT" | jq -r '.duration // 0' 2>/dev/null || echo 0)
[ -z "$DURATION" ] && DURATION=0
# Fallback chain: sha256sum (Linux), md5 (macOS), "unknown" — matches send_telemetry
# in install-script.sh. Without the fallback, set -euo pipefail aborts on macOS where
# sha256sum isn't shipped. (PR #1626 review finding.)
MACHINE_ID=$(hostname | sha256sum 2>/dev/null | cut -d' ' -f1 | head -c 16 || hostname | md5 2>/dev/null | head -c 16 || echo "unknown")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")
# Prefer install-time-baked AICTRL_REPO_URL, fall back to runtime git detection
# (covers detached env / Docker / non-git contexts where git remote get-url fails).
REPO_URL="${AICTRL_REPO_URL:-}"
if [ -z "$REPO_URL" ]; then
  REPO_URL=$(git remote get-url origin 2>/dev/null || echo "")
fi
# Strip embedded credentials (e.g. https://user:pass@host/repo) before transmission.
# Greedy [^/]*@ matches up to the LAST @ before the path so passwords containing @
# (e.g. https://user:p@ss@host/repo) are also stripped — matches send_telemetry.
REPO_URL=$(echo "$REPO_URL" | sed -E 's#://[^/]*@#://#')

if [ -z "$AICTRL_API_KEY" ] || [ -z "$AICTRL_TELEMETRY_URL" ] || [ -z "$REPO_URL" ]; then
  exit 0
fi

# Build JSON safely with jq -n to avoid injection from skill name characters
PAYLOAD=$(jq -nc \
  --arg skillName "$SKILL_NAME" \
  --arg repoUrl "$REPO_URL" \
  --arg machineId "$MACHINE_ID" \
  --arg timestamp "$TIMESTAMP" \
  --argjson duration "$DURATION" \
  '{skillName: $skillName, source: "claude-code", repoUrl: $repoUrl, duration: $duration, machineId: $machineId, timestamp: $timestamp}')

(curl -s -X POST \
  "$AICTRL_TELEMETRY_URL/skill-usage" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $AICTRL_API_KEY" \
  -d "$PAYLOAD" \
  --connect-timeout 3 \
  --max-time 5 \
  > /dev/null 2>&1 || true) &
