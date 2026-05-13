from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .bootstrap import ensure_workspace_path

ensure_workspace_path()

from src.chat import RecommendationChatAgent  # type: ignore
from src.loaders import ColumnMapper, DataLoader  # type: ignore
from src.policy import enrich_with_household_policy, enrich_with_worker_market  # type: ignore

from .formatter import build_map_feature, format_candidate_card, format_candidate_detail
from .llm_service import ReviewLLMService


ALL_DISTRICTS = [
    "강남구",
    "강동구",
    "강북구",
    "강서구",
    "관악구",
    "광진구",
    "구로구",
    "금천구",
    "노원구",
    "도봉구",
    "동대문구",
    "동작구",
    "마포구",
    "서대문구",
    "서초구",
    "성동구",
    "성북구",
    "송파구",
    "양천구",
    "영등포구",
    "용산구",
    "은평구",
    "종로구",
    "중구",
    "중랑구",
]

WHOLE_CITY_TOKENS = [
    "서울 전체",
    "서울시 전체",
    "서울 전역",
    "서울 전 구",
    "서울 전체에서",
    "전체에서",
    "전체 후보",
    "전 지역",
]

DEFAULT_KAKAO_MAP_JS_KEY = "89d5a8b6ef1bc8512e595bc9ffa22608"


@dataclass
class SearchFilters:
    districts: list[str]
    candidate_scope: str = "both"
    station_scope: str = "include_conditional"
    min_area_sqm: int | None = None
    merge_preference: str = "include"
    policy_need_filter: str = "keep"
    worker_market_filter: str = "keep"


class CandidateService:
    def __init__(self) -> None:
        self.workspace_root = ensure_workspace_path()
        self.backend_root = Path(__file__).resolve().parents[1]
        self.kakao_map_js_key = os.getenv("KAKAO_MAP_JS_KEY") or DEFAULT_KAKAO_MAP_JS_KEY
        self.data_path = self._resolve_data_path()
        self.result_df, self.column_map = self._load_data()
        self.agent = RecommendationChatAgent(self.result_df, self.column_map)
        self.review_llm = ReviewLLMService()
        self.available_districts = list(ALL_DISTRICTS)
        self.overview_payload = self._build_overview_payload()

    def _resolve_data_path(self) -> Path:
        candidates = [
            self.backend_root / "data" / "seoul_youth_housing_candidates_with_lx_polygons.csv",
            self.backend_root / "data" / "seoul_youth_housing_candidates.csv",
            self.workspace_root / "data" / "output" / "seoul_youth_housing_candidates_with_lx_polygons.csv",
            self.workspace_root / "data" / "output" / "seoul_youth_housing_candidates.csv",
        ]
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def _load_data(self) -> tuple[pd.DataFrame, dict[str, str]]:
        df = DataLoader(self.data_path).load()
        df = enrich_with_worker_market(df)
        df = enrich_with_household_policy(df)
        df = self._attach_app_row_ids(df)
        mapper = ColumnMapper(df)
        return df, mapper.map_rule_based()

    def get_options(self) -> dict[str, Any]:
        return {
            "districts": list(ALL_DISTRICTS),
            "mapConfig": {
                "provider": "kakao" if self.kakao_map_js_key else "leaflet",
                "kakaoAppKey": self.kakao_map_js_key or None,
            },
        }

    def get_overview_map(self) -> dict[str, Any]:
        return self.overview_payload

    @staticmethod
    def _attach_app_row_ids(df: pd.DataFrame) -> pd.DataFrame:
        work_df = df.copy()
        raw_ids = work_df.get("_app_row_id")
        if raw_ids is None:
            work_df["_app_row_id"] = [f"base-{idx}" for idx in work_df.index]
            return work_df

        normalized_ids: list[str] = []
        for idx, value in zip(work_df.index, raw_ids.tolist()):
            text = CandidateService._normalize_text(value)
            normalized_ids.append(text or f"base-{idx}")
        work_df["_app_row_id"] = normalized_ids
        return work_df

    def _build_overview_payload(self) -> dict[str, Any]:
        overview_features: list[dict[str, Any]] = []
        detail_by_id: dict[str, Any] = {}

        for rank, (_, row) in enumerate(self.result_df.iterrows(), start=1):
            item = row.to_dict()
            feature = build_map_feature(item, rank, prefer_polygon=False)
            overview_features.append(feature)

            detail = format_candidate_detail(item)
            detail_by_id[detail["id"]] = detail

        return {
            "candidateCount": int(len(self.result_df)),
            "mapFeatures": overview_features,
            "detailById": detail_by_id,
        }

    @staticmethod
    def _normalize_text(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return "" if text.lower() == "nan" else text

    @staticmethod
    def _clean_list(values: Any) -> list[str]:
        if isinstance(values, str):
            values = [values]
        cleaned: list[str] = []
        for value in values or []:
            text = CandidateService._normalize_text(value)
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    def _base_state_from_filters(self, filters: SearchFilters) -> dict[str, Any]:
        return {
            "candidate_scope": filters.candidate_scope or "both",
            "districts": filters.districts or [],
            "station_scope": filters.station_scope or "include_conditional",
            "min_area_sqm": filters.min_area_sqm,
            "merge_preference": filters.merge_preference or "include",
            "merge_only": (filters.merge_preference or "include") == "merge_only",
            "policy_need_filter": filters.policy_need_filter or "keep",
            "worker_market_filter": filters.worker_market_filter or "keep",
            "special_zone_filter": "keep",
            "policy_groups": [],
            "need_reasons": True,
            "sort_priority": "total_score",
        }

    def _finalize_effective_state(self, state: dict[str, Any]) -> dict[str, Any]:
        effective_state = dict(state)
        effective_state["districts"] = self._clean_list(effective_state.get("districts") or [])
        merge_preference = self._normalize_text(effective_state.get("merge_preference")) or "include"
        effective_state["merge_preference"] = merge_preference
        effective_state["merge_only"] = merge_preference == "merge_only"
        effective_state["special_zone_filter"] = (
            self._normalize_text(effective_state.get("special_zone_filter")) or "keep"
        )
        return effective_state

    def _serialize_effective_filters(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "districts": self._clean_list(state.get("districts") or []),
            "candidate_scope": self._normalize_text(state.get("candidate_scope")) or "both",
            "station_scope": self._normalize_text(state.get("station_scope")) or "include_conditional",
            "min_area_sqm": state.get("min_area_sqm"),
            "merge_preference": self._normalize_text(state.get("merge_preference")) or "include",
            "merge_only": bool(state.get("merge_only")),
            "policy_need_filter": self._normalize_text(state.get("policy_need_filter")) or "keep",
            "worker_market_filter": self._normalize_text(state.get("worker_market_filter")) or "keep",
            "special_zone_filter": self._normalize_text(state.get("special_zone_filter")) or "keep",
            "sort_priority": self._normalize_text(state.get("sort_priority")) or "total_score",
        }

    def _detect_districts(self, query: str) -> list[str]:
        return [district for district in ALL_DISTRICTS if district in query]

    @staticmethod
    def _contains_any(text: str, tokens: list[str]) -> bool:
        return any(token in text for token in tokens)

    def _mentions_whole_city_scope(self, query: str) -> bool:
        return self._contains_any(query, WHOLE_CITY_TOKENS)

    @staticmethod
    def _extract_top_k(query: str, default: int) -> int:
        patterns = [
            r"상위\s*(\d+)\s*(개|곳)?",
            r"top\s*(\d+)",
            r"(\d+)\s*(개|곳)",
        ]
        for pattern in patterns:
            match = re.search(pattern, query, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                return max(1, min(10, int(match.group(1))))
            except Exception:
                continue
        return default

    def _rule_parse_query(
        self,
        query: str,
        base_state: dict[str, Any],
        top_k: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        text = self._normalize_text(query)
        lowered = text.lower()
        state = dict(base_state)

        detected_districts = self._detect_districts(text)
        if detected_districts:
            state["districts"] = detected_districts
        elif self._mentions_whole_city_scope(text):
            state["districts"] = []

        if self._contains_any(text, ["리모델링", "리모델링 가능", "리모델링 가능한", "건물", "기존 건축물", "기존 건물"]):
            state["candidate_scope"] = "building"
        elif self._contains_any(text, ["토지", "토지형", "신규 개발", "신규 개발형", "신축형", "부지"]):
            state["candidate_scope"] = "land"

        if self._contains_any(text, ["역세권 상관없이", "생활권 기반", "생활권 전체", "역세권 제외"]):
            state["station_scope"] = "all"
        elif self._contains_any(text, ["250m", "도보권", "핵심 역세권", "역세권 250m"]):
            state["station_scope"] = "core_only"
        elif self._contains_any(text, ["역세권", "역 가까운", "지하철", "350m", "조건부 역세권"]):
            state["station_scope"] = "include_conditional"

        area_match = re.search(r"(\d+)\s*(㎡|m2|m²)", text)
        if area_match:
            state["min_area_sqm"] = int(area_match.group(1))

        if self._contains_any(text, ["필지결합만", "필지 결합만", "결합 후보만"]):
            state["merge_preference"] = "merge_only"
        elif self._contains_any(text, ["필지결합 제외", "필지 결합 제외", "소규모 후보 제외"]):
            state["merge_preference"] = "exclude"
        elif "필지결합" in text or "필지 결합" in text:
            state["merge_preference"] = "include"

        state["special_zone_filter"] = "keep"
        if self._contains_any(
            text,
            [
                "특별지구 제외",
                "특별지구 빼고",
                "특별지구 없는",
                "특별검토 없는",
                "특별검토 필요 없는",
                "리스크 적은",
                "리스크가 적은",
            ],
        ):
            state["special_zone_filter"] = "empty_only"

        sort_priority = "total_score"
        intent_summary = "질문 의도에 맞는 예비 검토 후보를 탐색합니다."
        reasoning_focus: list[str] = ["역세권", "용도지역", "면적"]

        if self._contains_any(text, ["청년 수요", "청년", "1인가구", "청년가구", "청년 가구"]):
            state["policy_need_filter"] = "high"
            sort_priority = "policy_need"
            intent_summary = "청년 수요가 높은 생활권을 우선 검토합니다."
            reasoning_focus = ["청년가구", "1인가구", "정책 필요도"]

        if self._contains_any(text, ["직장 인근", "직장인", "직주근접", "업무지구", "출근", "업무 밀집"]):
            state["worker_market_filter"] = "high"
            sort_priority = "worker_market"
            intent_summary = "직장 인근 주거 수요가 높은 생활권을 우선 검토합니다."
            reasoning_focus = ["직장인구", "생활권", "역세권"]

        if self._contains_any(text, ["면적 큰", "면적이 큰", "면적 큰 순", "면적 순", "규모 큰", "대규모", "넓은", "넓은 순"]):
            sort_priority = "area"
            intent_summary = "개발 규모 확보가 용이한 후보를 우선 검토합니다."
            reasoning_focus = ["면적", "사업 규모"]

        if self._contains_any(text, ["역세권 가까운 순", "역 가까운 순", "가까운 순으로", "역세권 가까운", "역 가까운"]) or "distance" in lowered:
            sort_priority = "station_distance"
            intent_summary = "역세권 접근성이 좋은 후보를 가까운 순으로 검토합니다."
            reasoning_focus = ["역세권", "거리", "접근성"]

        if self._contains_any(text, ["사업성 높은", "사업성", "활용성 높은"]):
            sort_priority = "total_score"
            intent_summary = "사업 가능성과 정책 검토 여건을 함께 고려합니다."
            reasoning_focus = ["종합 점수", "용도지역", "역세권"]

        if self._contains_any(text, ["리모델링", "건물"]):
            intent_summary = "기존 건축물 활용 가능 후보를 우선 검토합니다."
            if sort_priority == "total_score":
                reasoning_focus = ["건물 활용", "정책 필요도", "역세권"]

        if state["special_zone_filter"] == "empty_only":
            intent_summary = "특별지구 제약이 적은 후보를 우선 검토합니다."
            reasoning_focus = list(dict.fromkeys([*reasoning_focus, "특별지구 제외"]))

        requested_top_k = self._extract_top_k(text, top_k if top_k else 5)

        state["sort_priority"] = sort_priority
        interpretation = {
            "intent_summary": intent_summary,
            "districts": state.get("districts", []),
            "candidate_scope": state.get("candidate_scope", "both"),
            "station_scope": state.get("station_scope", "include_conditional"),
            "min_area_sqm": state.get("min_area_sqm"),
            "merge_preference": state.get("merge_preference", "include"),
            "policy_need_filter": state.get("policy_need_filter", "keep"),
            "worker_market_filter": state.get("worker_market_filter", "keep"),
            "special_zone_filter": state.get("special_zone_filter", "keep"),
            "sort_priority": sort_priority,
            "reasoning_focus": reasoning_focus,
            "requested_top_k": requested_top_k,
            "used_gemini": False,
        }
        return state, interpretation

    def _apply_special_zone_filter(self, df: pd.DataFrame, state: dict[str, Any]) -> pd.DataFrame:
        if df.empty or state.get("special_zone_filter") != "empty_only":
            return df
        if "special_zone" not in df.columns:
            return df
        mask = df["special_zone"].fillna("").astype(str).str.strip().eq("")
        return df[mask].copy()

    def _build_interpreted_conditions(
        self,
        state: dict[str, Any],
        interpretation: dict[str, Any],
        used_gemini: bool,
    ) -> list[dict[str, str]]:
        scope_map = {
            "both": "전체 후보",
            "land": "신규 개발형",
            "building": "기존 건축물 활용형",
        }
        station_map = {
            "core_only": "역세권 250m 우선 검토",
            "include_conditional": "역세권 350m 포함",
            "all": "생활권 기반 전체 검토",
        }
        sort_map = {
            "policy_need": "정책 필요도 우선",
            "worker_market": "직장 인근 주거 수요 우선",
            "area": "면적 규모 우선",
            "station_distance": "역세권 가까운 순",
            "total_score": "종합 검토 우선",
        }

        districts = state.get("districts") or []
        search_range = ", ".join(districts) if districts else "서울 전체"

        demand_parts = []
        if state.get("policy_need_filter") == "high":
            demand_parts.append("청년가구 · 1인가구")
        if state.get("worker_market_filter") == "high":
            demand_parts.append("20~34세 직장인구")
        if not demand_parts:
            demand_parts.append("역세권 · 용도지역 · 면적")

        review_mode = "규칙 해석 + 설명 보강" if used_gemini else "규칙 해석"

        return [
            {"label": "정책 목표", "value": interpretation.get("intent_summary") or "질문 의도에 맞는 예비 검토 후보를 탐색합니다."},
            {"label": "탐색 범위", "value": search_range},
            {"label": "후보 유형", "value": scope_map.get(state.get("candidate_scope"), "전체 후보")},
            {"label": "수요 기준", "value": " · ".join(demand_parts)},
            {"label": "정렬 기준", "value": sort_map.get(state.get("sort_priority"), "종합 검토 우선")},
            {"label": "검토 방식", "value": review_mode},
        ]

    def _dedupe_preview_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in items:
            key = (
                self._normalize_text(item.get("주소") or item.get("address")),
                self._normalize_text(item.get("정책유형") or item.get("정책유형분류") or item.get("policy_group")),
                self._normalize_text(item.get("사업유형") or item.get("project_type")),
                self._normalize_text(item.get("후보유형") or item.get("candidate_type")),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def explore(self, query: str, filters: SearchFilters, top_k: int = 5) -> dict[str, Any]:
        base_state = self._base_state_from_filters(filters)
        rule_state, interpretation = self._rule_parse_query(query, base_state, top_k)
        effective_state = self._finalize_effective_state(rule_state)

        intent_summary, used_gemini = self.review_llm.summarize_search_intent(
            query,
            [(key, str(value)) for key, value in interpretation.items() if key in {"intent_summary", "districts", "sort_priority"}],
        )
        interpretation["intent_summary"] = intent_summary
        interpretation["used_gemini"] = used_gemini

        filtered_df = self.agent._apply_filters(effective_state)
        filtered_df = self._apply_special_zone_filter(filtered_df, effective_state)

        effective_top_k = interpretation.get("requested_top_k") or top_k
        preview_items = self.agent._build_candidate_preview(filtered_df, top_k=max(effective_top_k * 2, effective_top_k))
        preview_items = self._dedupe_preview_items(preview_items)[:effective_top_k]

        cards = [format_candidate_card(item, idx + 1) for idx, item in enumerate(preview_items)]
        detail_by_id: dict[str, Any] = {}
        for item in preview_items:
            llm_review = self.review_llm.build_review(
                format_candidate_detail(item),
                query,
                [(entry["label"], entry["value"]) for entry in self._build_interpreted_conditions(effective_state, interpretation, used_gemini)],
            )
            detail = format_candidate_detail(item, llm_review)
            detail_by_id[detail["id"]] = detail

        map_features = [build_map_feature(item, idx + 1, prefer_polygon=True) for idx, item in enumerate(preview_items)]

        return {
            "query": query,
            "matchedCount": int(len(filtered_df)),
            "topK": effective_top_k,
            "usedGemini": used_gemini,
            "intentSummary": interpretation.get("intent_summary") or "",
            "interpretedConditions": self._build_interpreted_conditions(effective_state, interpretation, used_gemini),
            "effectiveFilters": self._serialize_effective_filters(effective_state),
            "candidates": cards,
            "detailById": detail_by_id,
            "mapFeatures": map_features,
            "searchResultIds": [card["id"] for card in cards],
        }
