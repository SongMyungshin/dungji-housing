from __future__ import annotations

from typing import Any

from .bootstrap import ensure_workspace_path

ensure_workspace_path()

from src.llm import GeminiClient  # type: ignore


class ReviewLLMService:
    def __init__(self) -> None:
        self.client = GeminiClient()

    @property
    def enabled(self) -> bool:
        return bool(self.client.enabled)

    def summarize_search_intent(self, query: str, conditions: list[tuple[str, str]]) -> tuple[str, bool]:
        fallback = self._fallback_intent_summary(query)
        if not self.client.enabled:
            return fallback, False

        prompt = f"""
당신은 공공기관의 청년주택 후보지 예비검토를 지원하는 AI입니다.
사용자 질문과 해석된 조건을 참고해, 담당자가 한눈에 이해할 수 있는 검토 목적 한 줄을 작성하세요.

사용자 질문:
{query}

해석된 조건:
{conditions}

작성 원칙:
- 28자 이상 44자 이하
- "추천"보다 "예비 검토", "우선 검토", "정책 검토" 표현 우선
- 단정 표현보다 검토 지원 표현 사용
- 한 줄 문장만 출력
""".strip()

        try:
            text = self.client.generate_text(prompt, temperature=0.2)
            cleaned = str(text).strip().splitlines()[0].strip()
            return cleaned or fallback, True
        except Exception:
            return fallback, False

    def build_review(
        self,
        candidate: dict[str, Any],
        query: str,
        interpreted_conditions: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        fallback = self._fallback_review(candidate)
        if not self.client.enabled:
            return fallback

        prompt = f"""
당신은 공공기관 담당자의 청년주택 후보지 검토를 돕는 AI입니다.
아래 후보에 대해 공공기관 검토 문체로 짧은 검토 의견을 JSON으로 작성하세요.

사용자 질문:
{query}

해석된 조건:
{interpreted_conditions or []}

후보 정보:
{candidate}

반드시 아래 JSON만 출력하세요.
{{
  "policyFit": "정책 적합성 1~2문장",
  "feasibility": "개발 가능성 1~2문장",
  "risks": ["추가 확인 필요사항 1", "추가 확인 필요사항 2"],
  "overall": "종합 의견 2문장"
}}

표현 원칙:
- "개발 확정", "법적으로 가능", "위험" 같은 단정 표현 금지
- "예비 검토 가능", "추가 확인 필요", "도시계획상 추가 검토사항" 같은 표현 권장
""".strip()
        try:
            data = self.client.generate_json(prompt, temperature=0.2)
            if isinstance(data, dict):
                return {
                    "policyFit": str(data.get("policyFit") or fallback["policyFit"]),
                    "feasibility": str(data.get("feasibility") or fallback["feasibility"]),
                    "risks": data.get("risks") or fallback["risks"],
                    "overall": str(data.get("overall") or fallback["overall"]),
                    "usedGemini": True,
                }
        except Exception:
            pass
        return fallback

    def _fallback_intent_summary(self, query: str) -> str:
        text = (query or "").strip()
        if any(token in text for token in ["청년 수요", "청년가구", "1인가구"]):
            return "청년 수요가 높은 생활권을 우선 검토합니다."
        if any(token in text for token in ["리모델링", "건물"]):
            return "기존 건축물 활용 가능 후보를 우선 검토합니다."
        if any(token in text for token in ["역세권", "지하철", "역 가까운"]):
            return "역세권 접근성이 양호한 후보를 우선 검토합니다."
        if any(token in text for token in ["직장", "직주근접", "업무지구"]):
            return "직장 인근 주거 수요가 높은 생활권을 우선 검토합니다."
        return "질문 의도에 맞는 후보를 예비 검토합니다."

    def _fallback_review(self, candidate: dict[str, Any]) -> dict[str, Any]:
        zone = str(candidate.get("zone") or "-")
        policy_need = str(candidate.get("policyNeed") or "청년수요 확인 필요")
        worker_need = str(candidate.get("workerNeed") or "직장 인근 주거 수요 확인 필요")
        station = str(candidate.get("station") or "-")
        return {
            "policyFit": f"{station} 기준 접근성을 확인했고, {policy_need} 생활권 여부를 함께 참고할 수 있습니다.",
            "feasibility": f"{zone} 기준으로 용도지역 특성과 개발 규모를 함께 검토할 필요가 있습니다.",
            "risks": [
                "도시계획상 추가 검토사항과 특별지구 포함 여부를 별도로 확인할 필요가 있습니다.",
                f"{worker_need} 지표는 보조 자료로 함께 검토할 수 있습니다.",
            ],
            "overall": "해당 후보지는 입지와 정책 수요 지표를 함께 검토할 수 있는 예비 검토 대상입니다. 다만 최종 사업화 전에는 도시계획과 세부 제약 조건을 추가로 확인해야 합니다.",
            "usedGemini": False,
        }
