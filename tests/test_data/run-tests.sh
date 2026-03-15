#!/usr/bin/env bash
# CC-002 — GitOpsAPI happy-path test sequence
# Run from gitopsapi/tests/test_data/
#
# Prerequisites:
#   - Port forward active: freyr:8081 → openclaw Gateway 192.168.4.179:80
#   - Pod running with GITOPSGUI_DEV_ROLE set (or use real auth headers)
#   - GITOPS_SKIP_GITHUB=1 on pod (for local PR store) OR real GitHub token

set -euo pipefail

BASE="http://freyr:8081/api/v1"
H_HOST="Host: gitopsgui.podzone.cloud"
H_JSON="Content-Type: application/json"
H_USER="X-Forwarded-User: martin"
H_GROUPS_CO="X-Auth-Request-Groups: cluster-operators"
H_GROUPS_BM="X-Auth-Request-Groups: build-managers"

echo "=== 1. Liveness check ==="
curl -sf -H "$H_HOST" http://freyr:8081/health && echo " OK"

echo ""
echo "=== 2. Readiness check ==="
curl -sf -H "$H_HOST" http://freyr:8081/ready && echo " OK"

echo ""
echo "=== 3. List clusters (GET /api/v1/clusters) ==="
curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_CO" "$BASE/clusters" | python3 -m json.tool || true

echo ""
echo "=== 4. Create gitopsdev cluster ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_CO" \
  -d @clusters/gitopsdev-create.json "$BASE/clusters" | python3 -m json.tool || true

echo ""
echo "=== 5. Create gitopsete cluster ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_CO" \
  -d @clusters/gitopsete-create.json "$BASE/clusters" | python3 -m json.tool || true

echo ""
echo "=== 6. Create gitopsprod cluster ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_CO" \
  -d @clusters/gitopsprod-create.json "$BASE/clusters" | python3 -m json.tool || true

echo ""
echo "=== 7. Get gitopsdev cluster (GET /api/v1/clusters/gitopsdev) ==="
curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_CO" \
  "$BASE/clusters/gitopsdev" | python3 -m json.tool || true

echo ""
echo "=== 8. List applications (GET /api/v1/applications) ==="
curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_BM" "$BASE/applications" | python3 -m json.tool || true

echo ""
echo "=== 9. Create gitopsapi application ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_BM" \
  -d @applications/gitopsapi-create.json "$BASE/applications" | python3 -m json.tool || true

echo ""
echo "=== 10. Get gitopsapi application ==="
curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_BM" \
  "$BASE/applications/gitopsapi" | python3 -m json.tool || true

echo ""
echo "=== 11. Disable gitopsapi application ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_CO" \
  -d @applications/gitopsapi-disable.json "$BASE/applications/gitopsapi/disable" | python3 -m json.tool || true

echo ""
echo "=== 12. Enable gitopsapi application ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_CO" \
  -d @applications/gitopsapi-enable.json "$BASE/applications/gitopsapi/enable" | python3 -m json.tool || true

echo ""
echo "=== 13. List pipelines (GET /api/v1/pipelines) ==="
curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_BM" "$BASE/pipelines" | python3 -m json.tool || true

echo ""
echo "=== 14. Create pipeline gitopsapi-v0.1.0 ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_BM" \
  -d @pipelines/gitopsapi-v0.1.0-create.json "$BASE/pipelines" | python3 -m json.tool || true

echo ""
echo "=== 15. Get pipeline gitopsapi-v0.1.0 ==="
curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_BM" \
  "$BASE/pipelines/gitopsapi-v0.1.0" | python3 -m json.tool || true

echo ""
echo "=== 16. Record change CHG0000001 ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_BM" \
  -d @pipelines/gitopsapi-v0.1.0-change.json "$BASE/pipelines/gitopsapi-v0.1.0/changes" | python3 -m json.tool || true

echo ""
echo "=== 17. Promote to ETE ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_BM" \
  -d @pipelines/gitopsapi-v0.1.0-promote-ete.json "$BASE/pipelines/gitopsapi-v0.1.0/promote" | python3 -m json.tool || true

echo ""
echo "=== 18. List open PRs ==="
PR_LIST=$(curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_BM" "$BASE/prs?state=open")
echo "$PR_LIST" | python3 -m json.tool || true

# Extract first PR number for subsequent tests
PR_NUMBER=$(echo "$PR_LIST" | python3 -c "import sys,json; prs=json.load(sys.stdin); print(prs[0]['pr_number'] if prs else '')" 2>/dev/null || echo "")

if [ -n "$PR_NUMBER" ]; then
  echo ""
  echo "=== 19. Get PR $PR_NUMBER ==="
  curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_BM" \
    "$BASE/prs/$PR_NUMBER" | python3 -m json.tool || true

  echo ""
  echo "=== 20. Approve PR $PR_NUMBER ==="
  curl -sf -X POST -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_BM" \
    "$BASE/prs/$PR_NUMBER/approve" && echo " OK (204)" || true

  echo ""
  echo "=== 21. Merge PR $PR_NUMBER ==="
  curl -sf -X POST -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_CO" \
    "$BASE/prs/$PR_NUMBER/merge" && echo " OK (204)" || true
else
  echo "(skipping steps 19-21 — no open PRs found)"
fi

echo ""
echo "=== 22. Pipeline history ==="
curl -sf -H "$H_HOST" -H "$H_USER" -H "$H_GROUPS_BM" \
  "$BASE/pipelines/gitopsapi-v0.1.0/history" | python3 -m json.tool || true

echo ""
echo "=== 23. Promote to production ==="
curl -sf -X POST -H "$H_HOST" -H "$H_JSON" -H "$H_USER" -H "$H_GROUPS_BM" \
  -d @pipelines/gitopsapi-v0.1.0-promote-prod.json "$BASE/pipelines/gitopsapi-v0.1.0/promote" | python3 -m json.tool || true

echo ""
echo "=== Done ==="
