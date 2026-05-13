# 청년주택 후보지 예비검토 웹사이트 전달본

이 폴더는 친구 웹사이트와 기능까지 합칠 수 있도록 정리한 전달용 패키지입니다.

포함 범위:
- `frontend/`: 화면 HTML, CSS, JS, 로고 이미지
- `backend/`: FastAPI 서버와 검색 API
- `seoul_youth_housing_agent_work/`: 후보 선별, 필터링, 점수화, 수요 보강, LLM 보조 모듈
- `requirements.txt`: 이 전달본 실행용 통합 패키지 목록
- `.env.example`: 카카오/Gemini 키 예시

## 실행 방법

프로젝트 루트에서 아래 순서로 실행합니다.

```powershell
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

브라우저 접속:

- 메인 화면: `http://127.0.0.1:8000/`
- 부지 예비검토 화면: `http://127.0.0.1:8000/site-review.html`

## 환경 변수

`.env.example`를 참고해서 `.env`를 만들 수 있습니다.

주요 항목:
- `KAKAO_MAP_JS_KEY`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`

주의:
- 카카오맵은 친구 쪽 도메인/로컬 주소를 카카오 개발자 콘솔에 등록해야 합니다.
- Gemini 키가 없어도 서비스는 동작하지만, 설명 보강은 규칙 기반 fallback으로 동작합니다.

## 친구에게 같이 알려주면 좋은 것

- 이 패키지는 `청년주택 후보지 예비검토` 기능만 묶어둔 전달본입니다.
- 자연어 질의 + 검토 조건 필터 + 지도 시각화 + 후보 상세 검토가 포함되어 있습니다.
- 만약 친구 사이트에 경로만 붙일 거라면 `frontend/`와 `backend/`를 기준으로 통합하면 됩니다.
- 만약 API 라우트만 붙일 거라면 `backend/`와 `seoul_youth_housing_agent_work/`를 함께 옮겨야 합니다.

## 전달 전 체크

- 필요 없으면 `.env`는 넣지 말고 `.env.example`만 보내기
- 데이터 파일 포함 여부 확인
  - 현재 전달본에는 실행에 필요한 CSV가 포함되어 있습니다
- 친구가 바로 실행할 수 있게 이 폴더 자체를 zip으로 보내면 편합니다
