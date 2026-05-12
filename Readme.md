# LogAA 개발 환경 설치 가이드

## 전제 조건

- Python 3.11 이상
- Node.js 18 이상 / npm 9 이상
- 사내 PyPI 저장소 및 npm 저장소 접근 가능

---

## 디렉토리 구조

```
LogAA/
├── backend/
│   ├── main.py
│   ├── puller_client.py
│   ├── requirements.txt
│   ├── config.yaml
│   └── workspace/          ← 가져온 문제 데이터 저장 (자동 생성)
└── frontend/
    ├── src/
    │   ├── App.jsx
    │   ├── App.css
    │   ├── api.js
    │   └── components/
    ├── index.html
    └── package.json
```

---

## 1. Backend 설치 및 실행

```bash
cd LogAA/backend

# 패키지 설치
pip3 install -r requirements.txt

# 실행 (외부 접근 허용)
uvicorn main:app --host 0.0.0.0 --port 8000
```

### config.yaml 설정

`LogAA/backend/config.yaml`에 실제 Puller 정보를 입력합니다.

```yaml
pullers:
  - name: "사이트명"       # UI에 표시될 이름
    url: "http://<Puller IP>:<Port>"
    site_name: "실제 사이트명"
```

---

## 2. Frontend 설치 및 실행

```bash
cd LogAA/frontend

# 패키지 설치
npm install

# 실행
npm run dev -- --host 0.0.0.0
```

### api.js BASE_URL 설정

`LogAA/frontend/src/api.js` 첫 줄의 `BASE_URL`을 백엔드 서버 주소로 변경합니다.

```js
const BASE_URL = "http://<백엔드 IP>:8000"
```

---

## 3. 접속

| 서비스 | 주소 |
|--------|------|
| Frontend | `http://<서버 IP>:5173` |
| Backend API | `http://<서버 IP>:8000` |
| API 문서 | `http://<서버 IP>:8000/docs` |

---

## 4. 동작 확인

1. 브라우저에서 `http://<서버 IP>:5173` 접속
2. 좌측 Sidebar의 드롭다운에서 Puller 선택
3. Defect ID 입력 후 **가져오기** 클릭
4. 문제 정보(제목, 설명, 첨부파일)가 표시되면 정상

---

## 참고

- 가져온 문제 데이터는 `backend/workspace/<defect_id>/` 에 저장됩니다.
- 최근 10개 케이스는 새로고침 후에도 유지됩니다.
- Backend 재시작 시 기존 데이터는 유지됩니다# LogAA 개발 환경 설치 가이드

## 전제 조건

- Python 3.11 이상
- Node.js 18 이상 / npm 9 이상
- 사내 PyPI 저장소 및 npm 저장소 접근 가능

---

## 디렉토리 구조

```
LogAA/
├── backend/
│   ├── main.py
│   ├── puller_client.py
│   ├── requirements.txt
│   ├── config.yaml
│   └── workspace/          ← 가져온 문제 데이터 저장 (자동 생성)
└── frontend/
    ├── src/
    │   ├── App.jsx
    │   ├── App.css
    │   ├── api.js
    │   └── components/
    ├── index.html
    └── package.json
```

---

## 1. Backend 설치 및 실행

```bash
cd LogAA/backend

# 패키지 설치
pip3 install -r requirements.txt

# 실행 (외부 접근 허용)
uvicorn main:app --host 0.0.0.0 --port 8000
```

### config.yaml 설정

`LogAA/backend/config.yaml`에 실제 Puller 정보를 입력합니다.

```yaml
pullers:
  - name: "사이트명"       # UI에 표시될 이름
    url: "http://<Puller IP>:<Port>"
    site_name: "실제 사이트명"
```

---

## 2. Frontend 설치 및 실행

```bash
cd LogAA/frontend

# 패키지 설치
npm install

# 실행
npm run dev -- --host 0.0.0.0
```

### api.js BASE_URL 설정

`LogAA/frontend/src/api.js` 첫 줄의 `BASE_URL`을 백엔드 서버 주소로 변경합니다.

```js
const BASE_URL = "http://<백엔드 IP>:8000"
```

---

## 3. 접속

| 서비스 | 주소 |
|--------|------|
| Frontend | `http://<서버 IP>:5173` |
| Backend API | `http://<서버 IP>:8000` |
| API 문서 | `http://<서버 IP>:8000/docs` |

---

## 4. 동작 확인

1. 브라우저에서 `http://<서버 IP>:5173` 접속
2. 좌측 Sidebar의 드롭다운에서 Puller 선택
3. Defect ID 입력 후 **가져오기** 클릭
4. 문제 정보(제목, 설명, 첨부파일)가 표시되면 정상

---

## 참고

- 가져온 문제 데이터는 `backend/workspace/<defect_id>/` 에 저장됩니다.
- 최근 10개 케이스는 새로고침 후에도 유지됩니다.
- Backend 재시작 시 기존 데이터는 유지됩니다.# LogAA 개발 환경 설치 가이드

## 전제 조건

- Python 3.11 이상
- Node.js 18 이상 / npm 9 이상
- 사내 PyPI 저장소 및 npm 저장소 접근 가능

---

## 디렉토리 구조

```
LogAA/
├── backend/
│   ├── main.py
│   ├── puller_client.py
│   ├── requirements.txt
│   ├── config.yaml
│   └── workspace/          ← 가져온 문제 데이터 저장 (자동 생성)
└── frontend/
    ├── src/
    │   ├── App.jsx
    │   ├── App.css
    │   ├── api.js
    │   └── components/
    ├── index.html
    └── package.json
```

---

## 1. Backend 설치 및 실행

```bash
cd LogAA/backend

# 패키지 설치
pip3 install -r requirements.txt

# 실행 (외부 접근 허용)
uvicorn main:app --host 0.0.0.0 --port 8000
```

### config.yaml 설정

`LogAA/backend/config.yaml`에 실제 Puller 정보를 입력합니다.

```yaml
pullers:
  - name: "사이트명"       # UI에 표시될 이름
    url: "http://<Puller IP>:<Port>"
    site_name: "실제 사이트명"
```

---

## 2. Frontend 설치 및 실행

```bash
cd LogAA/frontend

# 패키지 설치
npm install

# 실행
npm run dev -- --host 0.0.0.0
```

### api.js BASE_URL 설정

`LogAA/frontend/src/api.js` 첫 줄의 `BASE_URL`을 백엔드 서버 주소로 변경합니다.

```js
const BASE_URL = "http://<백엔드 IP>:8000"
```

---

## 3. 접속

| 서비스 | 주소 |
|--------|------|
| Frontend | `http://<서버 IP>:5173` |
| Backend API | `http://<서버 IP>:8000` |
| API 문서 | `http://<서버 IP>:8000/docs` |

---

## 4. 동작 확인

1. 브라우저에서 `http://<서버 IP>:5173` 접속
2. 좌측 Sidebar의 드롭다운에서 Puller 선택
3. Defect ID 입력 후 **가져오기** 클릭
4. 문제 정보(제목, 설명, 첨부파일)가 표시되면 정상

---

## 참고

- 가져온 문제 데이터는 `backend/workspace/<defect_id>/` 에 저장됩니다.
- 최근 10개 케이스는 새로고침 후에도 유지됩니다.
- Backend 재시작 시 기존 데이터는 유지됩니다..