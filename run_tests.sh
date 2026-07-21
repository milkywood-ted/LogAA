#!/bin/sh
# LogAA 테스트 러너 — backend 와 AnalyzingAssistant_v2 는 각자 다른 디렉토리에서
# 실행되는 독립 앱이라(최상위 모듈명 충돌) 서브시스템별로 분리해 돌린다.
# 실제 loganalyzer.db·chroma_db·네트워크는 건드리지 않는다(임시 DB + 스텁).
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"

echo "═══ backend/tests ═══"
( cd "$ROOT/backend" && "$PY" -m pytest tests "$@" )

echo ""
echo "═══ AnalyzingAssistant_v2/tests ═══"
( cd "$ROOT/AnalyzingAssistant_v2" && "$PY" -m pytest tests "$@" )

echo ""
echo "✅ 전체 테스트 통과"
