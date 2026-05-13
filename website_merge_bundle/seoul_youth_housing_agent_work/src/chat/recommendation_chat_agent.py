import json
import re
from pathlib import Path

import pandas as pd

from src.scoring import create_merged_candidates


NO_MORE_PATTERNS = [
    "없어",
    "없음",
    "없습니다",
    "그건 없어",
    "그건 없습니다",
    "무관",
    "상관없어",
    "상관없음",
    "상관없습니다",
    "그대로",
]


class RecommendationChatAgent:
    def __init__(self, result_df, column_map, gemini=None):
        self.column_map = column_map or {}
        self.gemini = gemini
        self.base_result_df = self._prepare_result_df(result_df.copy(), row_prefix="base")
        self.merged_result_df = self._prepare_result_df(
            create_merged_candidates(self.base_result_df, self.column_map),
            row_prefix="merge",
        )

        district_col = self.column_map.get("district", "district")
        district_source = self.base_result_df
        if not self.merged_result_df.empty and district_col in self.merged_result_df.columns:
            district_source = pd.concat(
                [self.base_result_df[[district_col]], self.merged_result_df[[district_col]]],
                ignore_index=True,
            )
        self.available_districts = sorted(
            {
                str(value).strip()
                for value in district_source[district_col].dropna().tolist()
                if str(value).strip()
            }
        )

    @staticmethod
    def _normalize_text(value):
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        return str(value).strip()

    @staticmethod
    def _normalize_lower(value):
        return RecommendationChatAgent._normalize_text(value).lower()

    @staticmethod
    def _clean_list(values):
        if isinstance(values, str):
            values = [values]
        cleaned = []
        for value in values or []:
            text = RecommendationChatAgent._normalize_text(value)
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @staticmethod
    def _label_for_sort_priority(sort_priority):
        mapping = {
            "policy_need": "정책 필요도 우선",
            "worker_market": "직장 인근 주거 수요 우선",
            "area": "개발 규모 우선",
            "station_distance": "역세권 가까운 순",
            "total_score": "종합 검토 우선",
        }
        return mapping.get(sort_priority or "", "종합 검토 우선")

    @staticmethod
    def _normalize_policy_groups(values):
        if isinstance(values, str):
            values = [values]
        normalized = []
        for value in values or []:
            text = RecommendationChatAgent._normalize_text(value)
            if not text:
                continue
            mapped = text
            if "청년안심주택" in text:
                mapped = "청년안심주택형 유리"
            elif "조건부" in text:
                mapped = "조건부 개발 가능"
            elif "리모델링" in text or "건물" in text:
                mapped = "리모델링 가능"
            elif "필지결합" in text:
                mapped = "필지결합형"
            elif "기타" in text:
                mapped = "기타 후보지"
            if mapped not in normalized:
                normalized.append(mapped)
        return normalized

    @staticmethod
    def _to_bool(value):
        normalized = RecommendationChatAgent._normalize_lower(value)
        return normalized in {"y", "yes", "true", "1", "예", "포함", "가능"}

    @staticmethod
    def save_state_file(path: Path, state: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _derive_candidate_type(self, row):
        composition = self._normalize_text(row.get("구성필지"))
        if composition:
            return "필지결합형"
        if self._normalize_text(row.get("merge_review")) == "결합검토 가능":
            return "필지결합형"
        return "개별필지형"

    def _derive_policy_group(self, row):
        return (
            self._normalize_text(row.get("정책유형분류"))
            or self._normalize_text(row.get("정책유형"))
            or self._normalize_text(row.get("refilter_policy_type_stage1"))
            or "기타 후보지"
        )

    def _derive_location_fit(self, row):
        fit = self._normalize_text(row.get("입지판정")) or self._normalize_text(row.get("역세권예비구간"))
        if fit:
            return fit
        distance = row.get("nearest_station_point_distance_m")
        try:
            distance = float(distance)
        except Exception:
            return "입지 기준 추가 검토"
        if distance <= 250:
            return "250m 충족 (우수)"
        if distance <= 350:
            return "350m 충족 (가능)"
        return "미충족 (추가검토)"

    def _derive_station_possibility(self, row):
        text = self._normalize_text(row.get("역세권가능성")) or self._normalize_text(row.get("역세권후보분류"))
        if text:
            return text
        fit = self._derive_location_fit(row)
        if "250m" in fit:
            return "역세권 가능성 높음"
        if "350m" in fit:
            return "역세권 가능성 있음"
        return "역세권 가능성 낮음"

    def _derive_policy_judgment(self, row):
        return (
            self._normalize_text(row.get("정책적합판단"))
            or self._normalize_text(row.get("final_judgment"))
            or self._normalize_text(row.get("최종검토등급"))
            or "추가 검토 필요"
        )

    def _build_review_note(self, row):
        return (
            self._normalize_text(row.get("추가검토메모"))
            or self._normalize_text(row.get("AI추천사유"))
            or self._normalize_text(row.get("ai_reason"))
            or self._normalize_text(row.get("merge_note"))
            or "추가 검토 필요"
        )

    def _policy_display_order(self, row):
        rowd = row if isinstance(row, dict) else row.to_dict()
        candidate_type = self._normalize_text(rowd.get("후보유형")) or self._derive_candidate_type(rowd)
        project_type = self._normalize_text(rowd.get("사업유형")) or self._normalize_text(rowd.get("refilter_project_type"))
        policy_group = self._normalize_text(rowd.get("정책유형분류")) or self._normalize_text(rowd.get("정책유형")) or self._derive_policy_group(rowd)

        if "청년안심주택" in policy_group:
            return 0
        if "조건부" in policy_group:
            return 1
        if "리모델링" in policy_group or "건물" in project_type:
            return 2
        if "필지결합" in candidate_type:
            return 3
        return 4

    def _prepare_result_df(self, df, row_prefix="row"):
        if df is None or df.empty:
            return pd.DataFrame()
        work_df = df.copy()
        if "_app_row_id" not in work_df.columns:
            work_df["_app_row_id"] = [f"{row_prefix}-{idx}" for idx in range(len(work_df))]

        if "후보유형" not in work_df.columns:
            work_df["후보유형"] = work_df.apply(self._derive_candidate_type, axis=1)
        if "정책유형분류" not in work_df.columns:
            work_df["정책유형분류"] = work_df.apply(self._derive_policy_group, axis=1)
        if "입지판정" not in work_df.columns:
            work_df["입지판정"] = work_df.apply(self._derive_location_fit, axis=1)
        if "역세권가능성" not in work_df.columns:
            work_df["역세권가능성"] = work_df.apply(self._derive_station_possibility, axis=1)
        if "정책적합판단" not in work_df.columns:
            work_df["정책적합판단"] = work_df.apply(self._derive_policy_judgment, axis=1)
        if "추가검토메모" not in work_df.columns:
            work_df["추가검토메모"] = work_df.apply(self._build_review_note, axis=1)
        if "사업유형" not in work_df.columns and "refilter_project_type" in work_df.columns:
            work_df["사업유형"] = work_df["refilter_project_type"]
        if "정책유형" not in work_df.columns and "refilter_policy_type_stage1" in work_df.columns:
            work_df["정책유형"] = work_df["refilter_policy_type_stage1"]
        return work_df

    def _get_working_dataframe(self, state):
        work_df = self.base_result_df.copy()
        if state.get("merge_preference") == "include" and not self.merged_result_df.empty:
            work_df = pd.concat([work_df, self.merged_result_df], ignore_index=True, sort=False)
        return work_df

    def _call_gemini_interpreter(self, current_state, user_message):
        if not self.gemini or not getattr(self.gemini, "enabled", False):
            return None

        state_snapshot = {
            "candidate_scope": current_state.get("candidate_scope", "both"),
            "station_scope": current_state.get("station_scope", "include_conditional"),
            "districts": self._clean_list(current_state.get("districts") or []),
            "min_area_sqm": current_state.get("min_area_sqm"),
            "merge_preference": current_state.get("merge_preference", "include"),
        }
        prompt = f"""
사용자 질문을 서울시 청년주거 후보지 검토조건으로 해석하세요.

현재 기본조건:
{json.dumps(state_snapshot, ensure_ascii=False)}

사용자 질문:
{user_message}

반드시 아래 JSON 객체만 반환하세요.
{{
  "intent_summary": "AI가 이해한 검토 목적 한 줄 요약",
  "candidate_scope": "keep|both|land|building",
  "districts": ["자치구명"],
  "station_scope": "keep|core_only|include_conditional|all",
  "min_area_sqm": null,
  "merge_preference": "keep|include|exclude|merge_only",
  "policy_groups": ["청년안심주택형 유리","조건부 개발 가능","리모델링 가능","필지결합형","기타 후보지"],
  "sort_priority": "policy_need|worker_market|area|total_score",
  "policy_need_filter": "keep|high|high_or_medium",
  "worker_market_filter": "keep|high|high_or_medium",
  "requested_top_k": null,
  "reasoning_focus": ["청년가구","1인가구","직장인구","역세권","리모델링","필지결합"],
  "notes": "조건 해석 시 주의할 점을 짧게"
}}

해석 원칙:
- "청년 수요 높은"은 policy_need 우선으로 해석
- "청년 수요 높은"은 policy_need_filter=high 도 함께 설정
- "역세권 높은", "역세권 좋은"은 station_scope=core_only, sort_priority=total_score로 해석
- "직주근접", "업무지구", "직장인"은 worker_market 우선으로 해석
- "직장 인근 주거 수요 높은"은 worker_market_filter=high 도 함께 설정
- "괜찮은 입지", "쓸만한 곳", "사용할 만한 곳", "볼 만한 곳", "있을까" 같은 표현은 과도하게 좁히지 말고 우선 검토 가능한 후보를 넓게 탐색하는 질문으로 해석
- 여러 자치구가 함께 나오면 districts 에 모두 넣고, 특별한 지시가 없으면 전체 후보 범위에서 우선 검토 가능한 후보를 탐색
- "리모델링 가능한"은 candidate_scope=building
- "역세권 250m"는 station_scope=core_only
- "조건부 역세권 포함"은 station_scope=include_conditional
- "역세권 상관없이"는 station_scope=all
- 자치구명은 질문에 있을 때만 districts에 넣고, 없으면 빈 배열
- "3개 보여줘" 같은 표현은 requested_top_k에 숫자를 넣으세요.
- "몇 개 보여줘", "어디 있어", "있을까"처럼 개수를 뭉뚱그려 말하면 requested_top_k는 5로 해석 가능
- 불확실한 항목은 keep 또는 null로 두세요.
- intent_summary 는 반드시 한국어 한 문장으로 작성하세요.
- 사용자가 직접 말한 명시 조건을 임의로 완화하지 마세요.

예시:
- "역세권 높은 후보 3개 보여줘" -> station_scope=core_only, requested_top_k=3, sort_priority=total_score
- "청년 수요 높은 입지 보여줘" -> sort_priority=policy_need, policy_need_filter=high
- "직장 인근 주거 수요 높은 지역 보여줘" -> sort_priority=worker_market, worker_market_filter=high
- "동대문구, 관악구에 괜찮은 입지가 있을까?" -> districts=["동대문구","관악구"], sort_priority=total_score, candidate_scope=both, station_scope=include_conditional, requested_top_k=5
""".strip()

        try:
            return self.gemini.generate_json(prompt, temperature=0.1)
        except Exception:
            return None

    @staticmethod
    def _is_default_interpretation_value(key, value):
        if key == "candidate_scope":
            return value in {"", "keep", "both"}
        if key == "station_scope":
            return value in {"", "keep", "include_conditional"}
        if key == "merge_preference":
            return value in {"", "keep", "include"}
        if key == "sort_priority":
            return value in {"", "total_score"}
        if key in {"policy_need_filter", "worker_market_filter"}:
            return value in {"", "keep"}
        if key in {"districts", "policy_groups", "reasoning_focus"}:
            return not value
        if key in {"min_area_sqm", "requested_top_k"}:
            return value in {None, "", 0}
        return not value

    @staticmethod
    def _looks_mostly_ascii(text):
        sample = RecommendationChatAgent._normalize_text(text)
        if not sample:
            return True
        meaningful = [ch for ch in sample if ch.isalnum()]
        if not meaningful:
            return True
        ascii_count = sum(1 for ch in meaningful if ord(ch) < 128)
        return ascii_count / max(len(meaningful), 1) >= 0.7

    def _merge_gemini_with_keyword_fallback(self, gemini_interpretation, fallback_interpretation):
        merged = dict(gemini_interpretation or {})
        adjusted = False

        for key in [
            "candidate_scope",
            "districts",
            "station_scope",
            "min_area_sqm",
            "merge_preference",
            "policy_groups",
            "sort_priority",
            "policy_need_filter",
            "worker_market_filter",
            "requested_top_k",
            "reasoning_focus",
        ]:
            fallback_value = fallback_interpretation.get(key)
            if self._is_default_interpretation_value(key, fallback_value):
                continue
            if merged.get(key) != fallback_value:
                merged[key] = fallback_value
                adjusted = True

        fallback_summary = fallback_interpretation.get("intent_summary")
        if self._looks_mostly_ascii(merged.get("intent_summary")) and fallback_summary:
            merged["intent_summary"] = fallback_summary
            adjusted = True

        gemini_focus = self._clean_list(merged.get("reasoning_focus") or [])
        fallback_focus = self._clean_list(fallback_interpretation.get("reasoning_focus") or [])
        if fallback_focus and set(fallback_focus) - set(gemini_focus):
            merged["reasoning_focus"] = list(dict.fromkeys(gemini_focus + fallback_focus))
            adjusted = True

        note_parts = []
        existing_note = self._normalize_text(merged.get("notes"))
        if existing_note:
            note_parts.append(existing_note)
        if adjusted:
            note_parts.append("Gemini 해석 결과에 핵심 키워드 보정을 적용했습니다.")
        merged["notes"] = " ".join(dict.fromkeys(note_parts)).strip()
        merged["used_gemini"] = True
        return merged

    def _fallback_interpretation(self, current_state, user_message):
        text = self._normalize_text(user_message)
        lowered = self._normalize_lower(text)

        sort_priority = "total_score"
        policy_need_filter = "keep"
        worker_market_filter = "keep"
        reasoning_focus = []
        intent_parts = []
        broad_search_requested = any(
            token in lowered
            for token in [
                "괜찮",
                "쓸만",
                "사용할만",
                "사용할 만",
                "볼 만",
                "볼만",
                "있을까",
                "어디 있",
                "찾아줘",
                "추천해",
            ]
        )

        if any(token in lowered for token in ["청년 수요", "청년가구", "1인가구", "정책 필요도"]):
            sort_priority = "policy_need"
            reasoning_focus.extend(["청년가구", "1인가구"])
            intent_parts.append("청년 주거 수요가 높은 생활권 우선")
            if "높" in lowered or "우선" in lowered:
                policy_need_filter = "high"
            else:
                policy_need_filter = "high_or_medium"
        if any(token in lowered for token in ["역세권 높은", "역세권 좋은", "역세권 우선", "역세권 중심"]):
            sort_priority = "total_score"
            reasoning_focus.append("역세권")
            intent_parts.append("역세권 우수 후보 우선")
        if any(token in lowered for token in ["직주근접", "직장", "업무지구", "출근"]):
            sort_priority = "worker_market"
            reasoning_focus.append("직장인구")
            intent_parts.append("직장 인근 주거 수요가 높은 생활권 우선")
            if "높" in lowered or "우수" in lowered:
                worker_market_filter = "high"
            else:
                worker_market_filter = "high_or_medium"
        if any(token in lowered for token in ["규모", "큰", "넓", "면적"]):
            if sort_priority == "total_score":
                sort_priority = "area"
            reasoning_focus.append("면적")
            intent_parts.append("개발 규모 우선")
        if any(token in lowered for token in ["리모델링", "기존 건축물", "건물 활용"]):
            intent_parts.append("기존 건축물 활용형 우선")
        if any(token in lowered for token in ["역세권", "250m", "350m"]):
            reasoning_focus.append("역세권")

        if broad_search_requested:
            if not intent_parts:
                intent_parts.append("우선 검토 가능한 후보 탐색")
            reasoning_focus.extend(["역세권", "면적", "용도지역"])

        districts = [d for d in self.available_districts if d in text]

        candidate_scope = "keep"
        if "건물" in text and "토지" not in text:
            candidate_scope = "building"
        elif "토지" in text and "건물" not in text:
            candidate_scope = "land"
        elif "전체" in text or "모든" in text or "둘 다" in text:
            candidate_scope = "both"

        station_scope = "keep"
        if any(token in lowered for token in ["역세권 높은", "역세권 좋은", "역세권 우선", "역세권 중심"]):
            station_scope = "core_only"
        elif "250m" in lowered and "350" not in lowered:
            station_scope = "core_only"
        elif "350m" in lowered or "조건부" in lowered:
            station_scope = "include_conditional"
        elif "역세권 상관없이" in lowered or ("상관없이" in lowered and "역세권" in lowered):
            station_scope = "all"
        elif broad_search_requested:
            station_scope = "include_conditional"

        merge_preference = "keep"
        if "필지결합" in lowered and ("만" in lowered or "만 보여" in lowered):
            merge_preference = "merge_only"
        elif "필지결합" in lowered and ("제외" in lowered or "빼" in lowered):
            merge_preference = "exclude"
        elif "필지결합" in lowered:
            merge_preference = "include"

        min_area_sqm = None
        area_match = re.search(r'(\d+)\s*㎡', text)
        if area_match:
            min_area_sqm = int(area_match.group(1))
        elif "330" in lowered:
            min_area_sqm = 330
        elif "300" in lowered:
            min_area_sqm = 300
        elif "200" in lowered:
            min_area_sqm = 200

        policy_groups = []
        if "청년안심주택" in lowered:
            policy_groups = ["청년안심주택형 유리"]
        elif "리모델링" in lowered:
            policy_groups = ["리모델링 가능"]
        elif "필지결합" in lowered and merge_preference == "merge_only":
            policy_groups = ["필지결합형"]
        elif "조건부" in lowered:
            policy_groups = ["조건부 개발 가능"]

        requested_top_k = None
        topk_match = re.search(r'(\d+)\s*개', text)
        if topk_match:
            requested_top_k = int(topk_match.group(1))
        elif broad_search_requested and ("몇 개" in lowered or "있을까" in lowered or "어디" in lowered):
            requested_top_k = 5

        intent_summary = " / ".join(intent_parts) if intent_parts else "현재 조건을 바탕으로 적합한 후보를 재정렬"
        return {
            "intent_summary": intent_summary,
            "candidate_scope": candidate_scope,
            "districts": districts,
            "station_scope": station_scope,
            "min_area_sqm": min_area_sqm,
            "merge_preference": merge_preference,
            "policy_groups": policy_groups,
            "sort_priority": sort_priority,
            "policy_need_filter": policy_need_filter,
            "worker_market_filter": worker_market_filter,
            "requested_top_k": requested_top_k,
            "reasoning_focus": self._clean_list(reasoning_focus),
            "notes": "질문에서 확인된 정책 키워드를 기준으로 검토조건을 자동 해석했습니다.",
        }

    def _interpret_user_message(self, current_state, user_message):
        fallback = self._fallback_interpretation(current_state, user_message)
        parsed = self._call_gemini_interpreter(current_state, user_message)
        used_gemini = isinstance(parsed, dict)
        if used_gemini:
            parsed = self._merge_gemini_with_keyword_fallback(parsed, fallback)
        else:
            parsed = fallback

        normalized = {
            "intent_summary": self._normalize_text(parsed.get("intent_summary")) or "현재 조건을 바탕으로 적합한 후보를 재정렬",
            "candidate_scope": self._normalize_text(parsed.get("candidate_scope")) or "keep",
            "districts": self._clean_list(parsed.get("districts") or []),
            "station_scope": self._normalize_text(parsed.get("station_scope")) or "keep",
            "min_area_sqm": parsed.get("min_area_sqm"),
            "merge_preference": self._normalize_text(parsed.get("merge_preference")) or "keep",
            "policy_groups": self._normalize_policy_groups(parsed.get("policy_groups") or []),
            "sort_priority": self._normalize_text(parsed.get("sort_priority")) or "total_score",
            "policy_need_filter": self._normalize_text(parsed.get("policy_need_filter")) or "keep",
            "worker_market_filter": self._normalize_text(parsed.get("worker_market_filter")) or "keep",
            "requested_top_k": parsed.get("requested_top_k"),
            "reasoning_focus": self._clean_list(parsed.get("reasoning_focus") or []),
            "notes": self._normalize_text(parsed.get("notes")),
            "used_gemini": used_gemini,
        }

        if normalized["candidate_scope"] not in {"keep", "both", "land", "building"}:
            normalized["candidate_scope"] = "keep"
        if normalized["station_scope"] not in {"keep", "core_only", "include_conditional", "all"}:
            normalized["station_scope"] = "keep"
        if normalized["merge_preference"] not in {"keep", "include", "exclude", "merge_only"}:
            normalized["merge_preference"] = "keep"
        if normalized["sort_priority"] not in {"policy_need", "worker_market", "area", "total_score"}:
            normalized["sort_priority"] = "total_score"
        if normalized["policy_need_filter"] not in {"keep", "high", "high_or_medium"}:
            normalized["policy_need_filter"] = "keep"
        if normalized["worker_market_filter"] not in {"keep", "high", "high_or_medium"}:
            normalized["worker_market_filter"] = "keep"
        try:
            if normalized["min_area_sqm"] not in (None, ""):
                normalized["min_area_sqm"] = int(float(normalized["min_area_sqm"]))
            else:
                normalized["min_area_sqm"] = None
        except Exception:
            normalized["min_area_sqm"] = None
        try:
            if normalized["requested_top_k"] not in (None, ""):
                normalized["requested_top_k"] = max(1, min(10, int(float(normalized["requested_top_k"]))))
            else:
                normalized["requested_top_k"] = None
        except Exception:
            normalized["requested_top_k"] = None
        return normalized

    def _merge_state_from_interpretation(self, current_state, interpretation):
        merged = dict(current_state or {})
        if interpretation.get("candidate_scope") and interpretation["candidate_scope"] != "keep":
            merged["candidate_scope"] = interpretation["candidate_scope"]
        if interpretation.get("districts"):
            merged["districts"] = interpretation["districts"]
        if interpretation.get("station_scope") and interpretation["station_scope"] != "keep":
            merged["station_scope"] = interpretation["station_scope"]
        if interpretation.get("min_area_sqm") is not None:
            merged["min_area_sqm"] = interpretation["min_area_sqm"]
        if interpretation.get("merge_preference") == "merge_only":
            merged["merge_only"] = True
            merged["merge_preference"] = "include"
        elif interpretation.get("merge_preference") and interpretation["merge_preference"] != "keep":
            merged["merge_preference"] = interpretation["merge_preference"]
            merged["merge_only"] = False
        if interpretation.get("policy_groups"):
            merged["policy_groups"] = interpretation["policy_groups"]
        merged["sort_priority"] = interpretation.get("sort_priority") or merged.get("sort_priority") or "total_score"
        if interpretation.get("policy_need_filter") and interpretation["policy_need_filter"] != "keep":
            merged["policy_need_filter"] = interpretation["policy_need_filter"]
        if interpretation.get("worker_market_filter") and interpretation["worker_market_filter"] != "keep":
            merged["worker_market_filter"] = interpretation["worker_market_filter"]
        if interpretation.get("requested_top_k") is not None:
            merged["requested_top_k"] = interpretation["requested_top_k"]
        return merged

    def _sort_filtered_df(self, df, sort_priority):
        if df is None or df.empty:
            return df
        work_df = df.copy()
        work_df["__policy_order"] = work_df.apply(self._policy_display_order, axis=1)

        priority_columns = []
        sort_ascending = False
        prioritize_numeric_first = False
        if sort_priority == "policy_need":
            priority_columns = ["정책필요도점수", "policy_need_score", "최종점수", "total_score"]
        elif sort_priority == "worker_market":
            priority_columns = ["직장상권점수", "worker_market_score", "최종점수", "total_score"]
        elif sort_priority == "area":
            priority_columns = ["area_sqm", "면적", "최종점수", "total_score"]
            prioritize_numeric_first = True
        elif sort_priority == "station_distance":
            priority_columns = ["nearest_station_point_distance_m", "exit_distance_m", "최종점수", "total_score"]
            sort_ascending = True
            prioritize_numeric_first = True
        else:
            priority_columns = ["최종점수", "total_score", "정책필요도점수", "직장상권점수"]

        numeric_sort = []
        for column in priority_columns:
            if column in work_df.columns:
                temp_col = f"__sort_{column}"
                work_df[temp_col] = pd.to_numeric(work_df[column], errors="coerce")
                numeric_sort.append(temp_col)

        if numeric_sort:
            if prioritize_numeric_first:
                sort_columns = [*numeric_sort, "__policy_order"]
                sort_flags = [*([sort_ascending] * len(numeric_sort)), True]
            else:
                sort_columns = ["__policy_order", *numeric_sort]
                sort_flags = [True, *([sort_ascending] * len(numeric_sort))]
            work_df = work_df.sort_values(by=sort_columns, ascending=sort_flags, na_position="last")
            work_df = work_df.drop(columns=numeric_sort, errors="ignore")
        else:
            work_df = work_df.sort_values(by="__policy_order", ascending=True, na_position="last")

        work_df = work_df.drop(columns=["__policy_order"], errors="ignore")
        return work_df.reset_index(drop=True)

    def _merge_candidate_mask(self, df, candidate_type_col=None):
        mask = pd.Series(False, index=df.index)

        if candidate_type_col and candidate_type_col in df.columns:
            mask = mask | df[candidate_type_col].astype(str).str.contains("필지결합", na=False)

        if "merge_review" in df.columns:
            merge_review = df["merge_review"].astype(str)
            mask = mask | merge_review.str.contains("필지결합|결합", na=False)

        if "small_lot_for_merge" in df.columns:
            mask = mask | df["small_lot_for_merge"].apply(self._to_bool)

        if "구성필지" in df.columns:
            mask = mask | df["구성필지"].fillna("").astype(str).str.strip().ne("")

        return mask

    def _apply_filters(self, state):
        df = self._get_working_dataframe(state)
        if df.empty:
            return df

        policy_groups = state.get("policy_groups") or []
        candidate_scope = state.get("candidate_scope")
        station_scope = state.get("station_scope")
        districts = state.get("districts") or []
        min_area = state.get("min_area_sqm")
        merge_preference = state.get("merge_preference")
        merge_only = state.get("merge_only")
        policy_need_filter = state.get("policy_need_filter", "keep")
        worker_market_filter = state.get("worker_market_filter", "keep")

        candidate_type_col = "후보유형" if "후보유형" in df.columns else None
        project_type_col = "사업유형" if "사업유형" in df.columns else "refilter_project_type" if "refilter_project_type" in df.columns else None
        policy_group_col = "정책유형분류" if "정책유형분류" in df.columns else "정책유형" if "정책유형" in df.columns else "refilter_policy_type_stage1" if "refilter_policy_type_stage1" in df.columns else None
        location_fit_col = "입지판정" if "입지판정" in df.columns else "역세권예비구간" if "역세권예비구간" in df.columns else None
        merge_candidate_mask = self._merge_candidate_mask(df, candidate_type_col)

        if merge_only:
            df = df[merge_candidate_mask].copy()
            merge_candidate_mask = self._merge_candidate_mask(df, candidate_type_col)

        if merge_preference == "exclude":
            df = df[~merge_candidate_mask].copy()
            merge_candidate_mask = self._merge_candidate_mask(df, candidate_type_col)

        if policy_groups and policy_group_col:
            df = df[df[policy_group_col].astype(str).isin(policy_groups)]
        else:
            if candidate_scope == "land" and project_type_col:
                df = df[df[project_type_col].astype(str).eq("토지 신축형")]
            elif candidate_scope == "building" and project_type_col:
                df = df[df[project_type_col].astype(str).eq("건물 리모델링형")]

            if station_scope in {"core_only", "include_conditional"} and project_type_col and location_fit_col:
                land_mask = df[project_type_col].astype(str).eq("토지 신축형")
                if station_scope == "core_only":
                    allowed = {"250m 충족 (우수)", "250m 이내"}
                else:
                    allowed = {"250m 충족 (우수)", "250m 이내", "350m 충족 (가능)", "250~350m"}
                df = pd.concat([
                    df[~land_mask],
                    df[land_mask & df[location_fit_col].astype(str).isin(allowed)],
                ], ignore_index=True)

        district_col = self.column_map.get("district")
        if districts and district_col and district_col in df.columns:
            df = df[df[district_col].astype(str).isin(districts)]

        area_col = self.column_map.get("area")
        if min_area and area_col and area_col in df.columns:
            numeric_area = pd.to_numeric(df[area_col], errors="coerce")
            df = df[numeric_area >= float(min_area)]

        if policy_need_filter != "keep":
            need_col = "정책필요도등급" if "정책필요도등급" in df.columns else "policy_need_tier" if "policy_need_tier" in df.columns else None
            if need_col:
                if policy_need_filter == "high":
                    allowed = {"높음"}
                else:
                    allowed = {"높음", "보통"}
                df = df[df[need_col].astype(str).isin(allowed)].copy()

        if worker_market_filter != "keep":
            worker_col = "직주근접등급" if "직주근접등급" in df.columns else "worker_access_tier" if "worker_access_tier" in df.columns else None
            if worker_col:
                if worker_market_filter == "high":
                    allowed = {"매우 높음", "높음"}
                else:
                    allowed = {"매우 높음", "높음", "보통"}
                df = df[df[worker_col].astype(str).isin(allowed)].copy()

        dedupe_subset = [
            col for col in [self.column_map.get("address"), project_type_col, policy_group_col] if col and col in df.columns
        ]
        if dedupe_subset:
            df = df.drop_duplicates(subset=dedupe_subset, keep="first")
        return self._sort_filtered_df(df, state.get("sort_priority") or "total_score")

    def _summarize_state(self, state):
        parts = []
        candidate_scope = state.get("candidate_scope")
        station_scope = state.get("station_scope")
        districts = state.get("districts") or []
        min_area = state.get("min_area_sqm")
        policy_groups = state.get("policy_groups") or []
        merge_preference = state.get("merge_preference")
        sort_priority = state.get("sort_priority")
        policy_need_filter = state.get("policy_need_filter", "keep")
        worker_market_filter = state.get("worker_market_filter", "keep")

        if policy_groups:
            parts.append("정책유형: " + ", ".join(policy_groups))
        elif candidate_scope == "land":
            parts.append("자산유형: 토지 신축형")
        elif candidate_scope == "building":
            parts.append("자산유형: 건물 리모델링형")
        else:
            parts.append("자산유형: 전체")

        if station_scope == "core_only":
            parts.append("역세권: 250m 충족 후보만")
        elif station_scope == "include_conditional":
            parts.append("역세권: 350m 조건부 포함")
        elif station_scope == "all":
            parts.append("역세권: 전체")

        if districts:
            parts.append("자치구: " + ", ".join(districts))
        if min_area:
            parts.append(f"최소 면적: {int(min_area)}㎡")
        if merge_preference == "include":
            parts.append("필지결합 포함")
        elif merge_preference == "exclude":
            parts.append("필지결합 제외")
        if state.get("merge_only"):
            parts.append("필지결합형만")
        if policy_need_filter == "high":
            parts.append("청년 주거 수요 높음만")
        elif policy_need_filter == "high_or_medium":
            parts.append("청년 주거 수요 높음/보통")
        if worker_market_filter == "high":
            parts.append("직장 인근 주거 수요 높음만")
        elif worker_market_filter == "high_or_medium":
            parts.append("직장 인근 주거 수요 높음/보통")
        if sort_priority:
            parts.append("정렬 기준: " + self._label_for_sort_priority(sort_priority))
        return " · ".join(parts) if parts else "조건 없음"

    def _build_candidate_preview(self, df, top_k=5):
        if df is None or df.empty:
            return []
        preview = []
        for _, row in df.head(top_k).iterrows():
            rowd = row.to_dict()
            preview.append({
                "app_row_id": self._normalize_text(rowd.get("_app_row_id")),
                "후보지번호": self._normalize_text(rowd.get("candidate_id")) or self._normalize_text(rowd.get("후보지번호")) or "-",
                "자치구": self._normalize_text(rowd.get("district")) or self._normalize_text(rowd.get("자치구")),
                "주소": self._normalize_text(rowd.get("address")) or self._normalize_text(rowd.get("주소")),
                "후보유형": self._normalize_text(rowd.get("후보유형")) or self._derive_candidate_type(rowd),
                "정책유형": self._normalize_text(rowd.get("정책유형")) or self._normalize_text(rowd.get("정책유형분류")) or self._normalize_text(rowd.get("refilter_policy_type_stage1")) or "기타 후보지",
                "정책유형분류": self._normalize_text(rowd.get("정책유형분류")) or self._normalize_text(rowd.get("정책유형")) or self._normalize_text(rowd.get("refilter_policy_type_stage1")) or "기타 후보지",
                "사업유형": self._normalize_text(rowd.get("사업유형")) or self._normalize_text(rowd.get("refilter_project_type")),
                "입지판정": self._normalize_text(rowd.get("입지판정")) or self._derive_location_fit(rowd),
                "역세권가능성": self._normalize_text(rowd.get("역세권가능성")) or self._derive_station_possibility(rowd),
                "정책적합판단": self._normalize_text(rowd.get("정책적합판단")) or self._derive_policy_judgment(rowd),
                "근접역명": self._normalize_text(rowd.get("nearest_station_point")) or self._normalize_text(rowd.get("가장가까운역")),
                "면적㎡": rowd.get("area_sqm") or rowd.get("면적"),
                "용도지역": self._normalize_text(rowd.get("zone_main")) or self._normalize_text(rowd.get("용도지역")) or self._normalize_text(rowd.get("youth_zone")),
                "용도지역세부": self._normalize_text(rowd.get("zone_sub")) or self._normalize_text(rowd.get("용도지역세부")) or self._normalize_text(rowd.get("extra_zone")),
                "용도지역검토등급": self._normalize_text(rowd.get("용도지역검토등급")),
                "용도지역해석": self._normalize_text(rowd.get("zone_interpret")) or self._normalize_text(rowd.get("용도지역해석")),
                "최종검토등급": self._normalize_text(rowd.get("최종검토등급")),
                "최종점수": rowd.get("최종점수") or rowd.get("total_score"),
                "추가검토메모": self._build_review_note(rowd),
                "AI추천사유": self._normalize_text(rowd.get("AI추천사유")) or self._normalize_text(rowd.get("ai_reason")),
                "직장상권명": self._normalize_text(rowd.get("직장상권명") or rowd.get("worker_market_name")),
                "청년직장인구": rowd.get("청년직장인구") or rowd.get("young_worker_count"),
                "직주근접등급": self._normalize_text(rowd.get("직주근접등급") or rowd.get("worker_access_tier")),
                "직장상권점수": rowd.get("직장상권점수") or rowd.get("worker_market_score"),
                "인프라해석": self._normalize_text(rowd.get("인프라해석") or rowd.get("infrastructure_comment")),
                "정책필요도점수": rowd.get("정책필요도점수") or rowd.get("policy_need_score"),
                "정책필요도등급": self._normalize_text(rowd.get("정책필요도등급") or rowd.get("policy_need_tier")),
                "청년주거수요": self._normalize_text(rowd.get("청년주거수요") or rowd.get("youth_supply_label")),
                "정책수요해석": self._normalize_text(rowd.get("정책수요해석") or rowd.get("policy_need_comment")),
                "청년가구수": rowd.get("청년가구수") or rowd.get("youth_households"),
                "청년가구비율": rowd.get("청년가구비율") or rowd.get("youth_household_ratio"),
                "청년1인가구수": rowd.get("청년1인가구수") or rowd.get("youth_single_households"),
                "청년1인가구비율": rowd.get("청년1인가구비율") or rowd.get("youth_single_ratio"),
                "청년1인가구20_34": rowd.get("청년1인가구20_34") or rowd.get("young_one_person_20_34"),
                "policy_need_score": rowd.get("policy_need_score") or rowd.get("정책필요도점수"),
                "policy_need_tier": self._normalize_text(rowd.get("policy_need_tier") or rowd.get("정책필요도등급")),
                "youth_supply_label": self._normalize_text(rowd.get("youth_supply_label") or rowd.get("청년주거수요")),
                "policy_need_comment": self._normalize_text(rowd.get("policy_need_comment") or rowd.get("정책수요해석")),
                "PNU": self._normalize_text(rowd.get("PNU")),
                "parcel_polygon_geojson": rowd.get("parcel_polygon_geojson") or "",
                "필지매칭성공": rowd.get("필지매칭성공") or "",
                "위도": rowd.get("lat") or rowd.get("위도"),
                "경도": rowd.get("lon") or rowd.get("경도"),
                "면적검토등급": self._normalize_text(rowd.get("면적검토등급")) or self._normalize_text(rowd.get("area_review")),
            })
        return preview

    def _build_local_recommendation(self, state, filtered_df, top_k=5):
        if filtered_df is None or filtered_df.empty:
            return "현재 조건에 맞는 후보가 없습니다. 자치구, 역세권 범위, 면적 조건을 조금 넓혀 다시 검토해 보세요."
        summary = self._summarize_state(state)
        top = filtered_df.head(top_k)
        districts = ", ".join(sorted(top[self.column_map.get("district", "district")].astype(str).unique())[:3])
        worker_note = ""
        if "직주근접등급" in top.columns:
            strong = top[top["직주근접등급"].astype(str).isin(["매우 높음", "높음", "보통"])]
            if not strong.empty:
                worker_note = " 상위 후보에는 청년 직장인 수요가 높은 생활권이 포함되어 있어 직장 인근 주거 수요 관점의 정책 검토도 가능합니다."
        policy_note = ""
        if "정책필요도등급" in top.columns:
            demand_candidates = top[top["정책필요도등급"].astype(str).isin(["높음", "보통"])]
            if not demand_candidates.empty:
                top_districts = ", ".join(
                    sorted(demand_candidates[self.column_map.get("district", "district")].astype(str).unique())[:3]
                )
                policy_note = f" 청년 가구·1인가구 수요가 확인된 {top_districts} 생활권이 포함되어 정책 필요도 관점의 우선 검토가 가능합니다."
        return f"{summary} 기준으로 총 {len(filtered_df)}건이 검토되었고, 상위 후보는 {districts}에 분포합니다.{policy_note}{worker_note} 오른쪽 카드에서 후보별 기본 정보를 확인하고 필요하면 추가 조건으로 다시 정리해 보세요."

    def _build_interpreted_conditions(self, state, interpretation):
        conditions = []
        intent_summary = interpretation.get("intent_summary") or "현재 조건을 바탕으로 적합한 후보를 재정렬"
        conditions.append(("검토 목적", intent_summary))

        scope_map = {
            "land": "토지 신축형",
            "building": "기존 건축물 활용형",
            "both": "전체 후보",
        }
        candidate_scope = state.get("candidate_scope", "both")
        conditions.append(("후보 유형", scope_map.get(candidate_scope, "전체 후보")))

        districts = self._clean_list(state.get("districts") or [])
        conditions.append(("자치구", ", ".join(districts) if districts else "전체"))

        station_scope = state.get("station_scope", "include_conditional")
        station_map = {
            "core_only": "역세권 250m 중심",
            "include_conditional": "역세권 350m 조건부 포함",
            "all": "역세권 범위 전체 검토",
        }
        conditions.append(("역세권 기준", station_map.get(station_scope, "역세권 350m 조건부 포함")))

        min_area = state.get("min_area_sqm")
        conditions.append(("최소 면적", f"{int(min_area)}㎡ 이상" if min_area else "제한 없음"))

        merge_label = "포함"
        if state.get("merge_only"):
            merge_label = "필지결합형만"
        elif state.get("merge_preference") == "exclude":
            merge_label = "제외"
        conditions.append(("필지결합 검토", merge_label))

        focus_items = self._clean_list(interpretation.get("reasoning_focus") or [])
        if focus_items:
            conditions.append(("수요 기준", ", ".join(focus_items)))

        policy_need_filter = state.get("policy_need_filter", "keep")
        if policy_need_filter == "high":
            conditions.append(("청년·1인가구 정책 수요", "높음 후보만"))
        elif policy_need_filter == "high_or_medium":
            conditions.append(("청년·1인가구 정책 수요", "높음/보통 후보 포함"))

        worker_market_filter = state.get("worker_market_filter", "keep")
        if worker_market_filter == "high":
            conditions.append(("직장 인근 주거 수요", "높음 후보만"))
        elif worker_market_filter == "high_or_medium":
            conditions.append(("직장 인근 주거 수요", "높음/보통 후보 포함"))

        conditions.append(("정렬 기준", self._label_for_sort_priority(state.get("sort_priority") or "total_score")))
        return conditions

    def _merge_state_from_text(self, current_state, user_message):
        text = self._normalize_text(user_message)
        merged = dict(current_state or {})

        found_districts = [d for d in self.available_districts if d in text]
        if found_districts:
            merged["districts"] = found_districts

        if "필지결합" in text and ("만" in text or "만 보여" in text):
            merged["merge_only"] = True
            merged["merge_preference"] = "include"
        elif "필지결합" in text and ("제외" in text or "빼" in text):
            merged["merge_preference"] = "exclude"
            merged["merge_only"] = False
        elif "필지결합" in text:
            merged["merge_preference"] = "include"

        if "건물" in text and "토지" not in text:
            merged["candidate_scope"] = "building"
        elif "토지" in text and "건물" not in text:
            merged["candidate_scope"] = "land"
        elif "둘 다" in text or "전체" in text:
            merged["candidate_scope"] = "both"

        if "250m" in text and "350" not in text:
            merged["station_scope"] = "core_only"
        elif "350m" in text or "조건부" in text:
            merged["station_scope"] = "include_conditional"
        elif "역세권 상관없이" in text or "상관없이" in text:
            merged["station_scope"] = "all"

        m = re.search(r'(\d+)\s*㎡', text)
        if m:
            merged["min_area_sqm"] = int(m.group(1))

        if "보고서" in text:
            merged["output_mode"] = "report"
        elif "지도" in text:
            merged["output_mode"] = "map_data"
        elif "순위" in text:
            merged["output_mode"] = "ranking"
        else:
            merged.setdefault("output_mode", "detailed")

        merged.setdefault("need_reasons", True)
        merged.setdefault("policy_groups", [])
        merged.setdefault("merge_only", False)
        merged.setdefault("merge_preference", "include")
        merged.setdefault("candidate_scope", "both")
        merged.setdefault("station_scope", "include_conditional")
        merged.setdefault("sort_priority", "total_score")
        return merged

    def chat_turn(self, current_state, user_message, top_k=5):
        base_state = self._merge_state_from_text(current_state or {}, user_message)
        interpretation = self._interpret_user_message(base_state, user_message)
        merged_state = self._merge_state_from_interpretation(base_state, interpretation)
        filtered_df = self._apply_filters(merged_state)
        effective_top_k = interpretation.get("requested_top_k") or top_k
        candidate_preview = self._build_candidate_preview(filtered_df, top_k=effective_top_k)
        assistant_message = self._build_local_recommendation(merged_state, filtered_df, top_k=effective_top_k)
        interpreted_conditions = self._build_interpreted_conditions(merged_state, interpretation)
        return {
            **merged_state,
            "assistant_message": assistant_message,
            "ready_to_search": True,
            "missing_fields": [],
            "matched_candidate_count": int(len(filtered_df)),
            "condition_summary": self._summarize_state(merged_state),
            "candidate_preview": candidate_preview,
            "merged_candidate_count": int(len(self.merged_result_df)),
            "ai_intent_summary": interpretation.get("intent_summary") or "현재 조건을 바탕으로 적합한 후보를 재정렬",
            "ai_interpreted_conditions": interpreted_conditions,
            "ai_sort_priority_label": self._label_for_sort_priority(merged_state.get("sort_priority") or "total_score"),
            "ai_interpretation_note": interpretation.get("notes") or "",
            "ai_used_gemini": bool(interpretation.get("used_gemini")),
            "ai_requested_top_k": effective_top_k,
        }
