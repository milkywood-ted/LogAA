"""AA V2 테스트 공용 설정.

api/router/cases.py 는 import 시점에 `_kb = KBSearch()` 로 ChromaDB 클라이언트를
생성한다. 테스트가 **실제 chroma_db 디렉토리를 건드리지 않도록**, 테스트 모듈이
import 되기 전에(=이 conftest 가 먼저 로드될 때) ChromaDB 클라이언트를 페이크로
교체한다. 네트워크·임베딩 호출은 각 테스트에서 `_kb` 를 스텁으로 덮어 차단한다.
"""

import chromadb


class _FakeCollection:
    def count(self):
        return 0

    def upsert(self, **kwargs):
        pass

    def delete(self, **kwargs):
        pass

    def query(self, **kwargs):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_or_create_collection(self, *args, **kwargs):
        return _FakeCollection()


# 모듈 로드 시점(테스트 파일 import 이전)에 패치 — 실제 PersistentClient 미사용
chromadb.PersistentClient = _FakeClient
