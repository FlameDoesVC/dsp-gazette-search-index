#!/usr/bin/env sh
# Regenerate src/lib/types.ts from the running API.
#
# Run after any change to api/schemas.py. A rename there should be a compile
# error here, which is the entire point of generating rather than hand-writing.
set -eu
URL="${1:-http://localhost:8000/api/v1/openapi.json}"
npx openapi-typescript "$URL" -o src/lib/types.ts
echo "regenerated src/lib/types.ts from $URL"
