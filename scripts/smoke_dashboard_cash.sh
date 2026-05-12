#!/usr/bin/env zsh
set -euo pipefail

cd "/Users/mac/Desktop/projet floussy"
source ".venv/bin/activate"

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://floussy:floussy@127.0.0.1:5432/floussy_test}"
BASE="http://127.0.0.1:8000"

if [[ "$DATABASE_URL" != *"/floussy_test"* && "$DATABASE_URL" != *"/test"* ]]; then
  echo "ERROR: Refusing to run smoke script on non-test database: $DATABASE_URL"
  echo "Use a dedicated test DB (example: .../floussy_test)."
  exit 1
fi

docker compose up -d
alembic upgrade head

# Start API in background
python -m uvicorn app.main:app --port 8000 > /tmp/floussy-uvicorn.log 2>&1 &
UVICORN_PID=$!
trap 'kill $UVICORN_PID >/dev/null 2>&1 || true' EXIT

# Wait for health
for _i in {1..50}; do
  if curl -fsS "$BASE/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if ! curl -fsS "$BASE/health" >/dev/null 2>&1; then
  echo "ERROR: /health not reachable"
  tail -n 200 /tmp/floussy-uvicorn.log || true
  exit 1
fi

echo "1) Create user..."
EMAIL="dashboard-smoke-$(date +%s)@example.com"
USER_JSON=$(curl -sS -w "\n%{http_code}" -X POST "$BASE/users" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"currency\":\"MAD\",\"sweep_interval_days\":7}")

USER_STATUS=$(echo "$USER_JSON" | tail -n 1)
USER_BODY=$(echo "$USER_JSON" | sed '$d')

if [[ "$USER_STATUS" -lt 200 || "$USER_STATUS" -ge 300 ]]; then
  echo "ERROR: HTTP $USER_STATUS creating user"
  echo "$USER_BODY" | python -m json.tool || echo "$USER_BODY"
  exit 1
fi

echo "$USER_BODY" | python -m json.tool >/dev/null
USER_ID=$(python -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$USER_BODY")
echo "USER_ID=$USER_ID"

echo "2) Create category..."
CATEGORY_JSON=$(curl -sS -w "\n%{http_code}" -X POST "$BASE/categories" \
  -H "Content-Type: application/json" \
  -H "X-User-Id: $USER_ID" \
  -d '{"name":"Salary"}')

CAT_STATUS=$(echo "$CATEGORY_JSON" | tail -n 1)
CAT_BODY=$(echo "$CATEGORY_JSON" | sed '$d')

if [[ "$CAT_STATUS" -lt 200 || "$CAT_STATUS" -ge 300 ]]; then
  echo "ERROR: HTTP $CAT_STATUS creating category"
  echo "$CAT_BODY" | python -m json.tool || echo "$CAT_BODY"
  exit 1
fi

echo "$CAT_BODY" | python -m json.tool >/dev/null
CATEGORY_ID=$(python -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$CAT_BODY")
echo "CATEGORY_ID=$CATEGORY_ID"

echo "3) Create INCOME transaction (+500, Cash movement)..."
TX_JSON=$(curl -sS -w "\n%{http_code}" -X POST "$BASE/transactions" \
  -H "Content-Type: application/json" \
  -H "X-User-Id: $USER_ID" \
  -d "{\"type\":\"income\",\"category_id\":\"$CATEGORY_ID\",\"amount\":\"500.00\",\"occurred_on\":\"$(date +%F)\",\"description\":\"Pay\"}")

TX_STATUS=$(echo "$TX_JSON" | tail -n 1)
TX_BODY=$(echo "$TX_JSON" | sed '$d')

if [[ "$TX_STATUS" -lt 200 || "$TX_STATUS" -ge 300 ]]; then
  echo "ERROR: HTTP $TX_STATUS creating transaction"
  echo "$TX_BODY" | python -m json.tool || echo "$TX_BODY"
  exit 1
fi

echo "$TX_BODY" | python -m json.tool >/dev/null
echo "Transaction OK"

echo "4) Dashboard..."
DASH_JSON=$(curl -sS -w "\n%{http_code}" "$BASE/dashboard" -H "X-User-Id: $USER_ID")
DASH_STATUS=$(echo "$DASH_JSON" | tail -n 1)
DASH_BODY=$(echo "$DASH_JSON" | sed '$d')

if [[ "$DASH_STATUS" -lt 200 || "$DASH_STATUS" -ge 300 ]]; then
  echo "ERROR: HTTP $DASH_STATUS fetching dashboard"
  echo "$DASH_BODY" | python -m json.tool || echo "$DASH_BODY"
  tail -n 200 /tmp/floussy-uvicorn.log || true
  exit 1
fi

if [[ -z "${DASH_BODY}" ]]; then
  echo "ERROR: empty /dashboard response"
  tail -n 200 /tmp/floussy-uvicorn.log || true
  exit 1
fi

echo "$DASH_BODY" | python -m json.tool >/dev/null

echo "5) Assertions..."
python -c 'import json,sys
d = json.load(sys.stdin)
cash = d.get("cash_balance")
if cash not in ("500", "500.0", "500.00"):
    raise SystemExit(f"ERROR: expected cash_balance ~500, got {cash}")
envs = d.get("envelopes", [])
cash_items = [e for e in envs if e["envelope"].get("is_cash")]
if len(cash_items) != 1:
    raise SystemExit(f"ERROR: expected 1 cash envelope in dashboard.envelopes, got {len(cash_items)}")
closing = cash_items[0]["balance"]["closing_balance"]
if closing not in ("500", "500.0", "500.00"):
    raise SystemExit(f"ERROR: expected cash envelope closing_balance ~500, got {closing}")
print("OK ✅ cash_balance + cash envelope included")
' <<<"$DASH_BODY"

echo "SMOKE TEST OK ✅"
