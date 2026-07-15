#!/bin/bash
# chaos_test.sh — delete the search-api pod and measure recovery time
# Simulates a pod crash. Records exact timestamps for the postmortem.

NAMESPACE="search-sre"
LABEL="app=search-api"
SERVICE_URL="http://localhost:8080"

echo "=============================================="
echo " search-api Chaos Test"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# ── Step 1: Verify service is healthy before chaos ─────────────────────────
echo ""
echo "[1] Pre-chaos health check..."
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "$SERVICE_URL/healthz")
if [ "$RESPONSE" != "200" ]; then
    echo "ERROR: Service not healthy before chaos (HTTP $RESPONSE). Aborting."
    exit 1
fi
echo "    /healthz → $RESPONSE ✅"

# ── Step 2: Record the pod name ────────────────────────────────────────────
POD_NAME=$(kubectl get pods -n $NAMESPACE -l $LABEL -o jsonpath='{.items[0].metadata.name}')
echo ""
echo "[2] Target pod: $POD_NAME"

# ── Step 3: Delete the pod and record the time ────────────────────────────
echo ""
echo "[3] Deleting pod..."
DELETE_TIME=$(date '+%H:%M:%S')
kubectl delete pod -n $NAMESPACE $POD_NAME
echo "    Pod deleted at: $DELETE_TIME"

# ── Step 4: Wait for new pod to appear ────────────────────────────────────
echo ""
echo "[4] Waiting for replacement pod..."
sleep 3
NEW_POD=$(kubectl get pods -n $NAMESPACE -l $LABEL -o jsonpath='{.items[0].metadata.name}')
NEW_POD_TIME=$(date '+%H:%M:%S')
echo "    New pod: $NEW_POD at $NEW_POD_TIME"

# ── Step 5: Poll until Ready ───────────────────────────────────────────────
echo ""
echo "[5] Polling until pod is Ready (1/1)..."
READY_TIME=""
for i in $(seq 1 60); do
    READY=$(kubectl get pod -n $NAMESPACE $NEW_POD -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null)
    if [ "$READY" = "true" ]; then
        READY_TIME=$(date '+%H:%M:%S')
        echo "    Pod Ready at: $READY_TIME (attempt $i)"
        break
    fi
    echo "    Attempt $i — not ready yet..."
    sleep 3
done

if [ -z "$READY_TIME" ]; then
    echo "ERROR: Pod did not become ready within 3 minutes."
    exit 1
fi

# ── Step 6: Verify service is healthy after recovery ──────────────────────
echo ""
echo "[6] Post-chaos health check..."
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "$SERVICE_URL/healthz")
echo "    /healthz → $RESPONSE"

SEARCH_RESPONSE=$(curl -s "$SERVICE_URL/search?q=circuit+breaker" -o /tmp/chaos_search.json)
echo "    /search → $(cat /tmp/chaos_search.json | grep -o '"term":"[^"]*"' | head -1)"

# ── Step 7: Summary ───────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo " RESULTS"
echo "=============================================="
echo " Pod deleted:      $DELETE_TIME"
echo " New pod appeared: $NEW_POD_TIME"
echo " Pod ready (1/1):  $READY_TIME"
echo ""
echo " Copy these timestamps into docs/POSTMORTEM.md"
echo "=============================================="
