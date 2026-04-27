#!/usr/bin/env bash
# Mint an HS256 JWT for trino-basic.
#
# Usage:
#   ./scripts/generate-jwt.sh                       # subject=fyzanshaik, ttl=1h
#   ./scripts/generate-jwt.sh alice                 # subject=alice
#   ./scripts/generate-jwt.sh alice 86400           # subject=alice, ttl=24h
#
# Trino verifies the token against trino/basic-auth/jwt-key (HMAC).
# The `sub` claim is mapped to the Trino user.
#
# Test with:
#   TOKEN=$(./scripts/generate-jwt.sh)
#   curl -H "Authorization: Bearer $TOKEN" https://trino.fyzanshaik.in/v1/info

set -euo pipefail

SUBJECT="${1:-fyzanshaik}"
TTL_SECONDS="${2:-3600}"

KEY_FILE="$(dirname "$0")/../trino/basic-auth/jwt-key"
if [[ ! -s "$KEY_FILE" ]]; then
  echo "error: $KEY_FILE missing or empty. Run setup.sh first." >&2
  exit 1
fi

NOW=$(date +%s)
EXP=$((NOW + TTL_SECONDS))

b64url() { openssl base64 -e -A | tr '+/' '-_' | tr -d '='; }

# Trino expects the key file to be a base64-encoded HMAC key; it decodes
# before signing. Use the decoded bytes here too.
HEX_KEY=$(base64 -d "$KEY_FILE" | od -A n -v -t x1 | tr -d ' \n')

HEADER=$(printf '{"alg":"HS256","typ":"JWT"}' | b64url)
PAYLOAD=$(printf '{"sub":"%s","iat":%d,"exp":%d}' "$SUBJECT" "$NOW" "$EXP" | b64url)
SIG=$(printf '%s.%s' "$HEADER" "$PAYLOAD" \
  | openssl dgst -binary -sha256 -mac HMAC -macopt hexkey:"$HEX_KEY" \
  | b64url)

printf '%s.%s.%s\n' "$HEADER" "$PAYLOAD" "$SIG"
