from __future__ import annotations

import json
from typing import Any


POLICY_TYPE_LABELS = {
    "청년안심주택형 유력": "청년안심주택형 유리",
    "조건부 역세권 개발형": "조건부 개발 가능",
    "조건부 개발 가능": "조건부 개발 가능",
    "리모델링형 청년주거": "리모델링 가능",
    "리모델링 가능": "리모델링 가능",
    "건물 리모델링형": "리모델링 가능",
    "필지결합형": "필지결합형",
    "기타 후보지": "기타 후보지",
    "추가 검토 가능 후보": "기타 후보지",
}

POLICY_TYPE_COLORS = {
    "청년안심주택형 유리": "#5B6CFF",
    "조건부 개발 가능": "#2A9D8F",
    "리모델링 가능": "#F4A261",
    "필지결합형": "#E76FAD",
    "기타 후보지": "#8A94A6",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _number(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def compact_zone_text(zone_main: str = "", zone_sub: str = "") -> str:
    tokens: list[str] = []
    for raw in [zone_main, zone_sub]:
        normalized = _text(raw).replace("/", ",")
        for token in normalized.split(","):
            cleaned = _text(token)
            if cleaned and cleaned not in tokens:
                tokens.append(cleaned)

    general_residential = {"제1종일반주거지역", "제2종일반주거지역", "제3종일반주거지역"}
    if general_residential.issubset(set(tokens)):
        tokens = [token for token in tokens if token not in general_residential]
        tokens.insert(0, "제1/2/3종 일반주거지역")

    if len(tokens) > 3:
        return " · ".join(tokens[:3]) + f" 외 {len(tokens) - 3}"
    return " · ".join(tokens)


def special_zone_label(item: dict[str, Any]) -> str:
    special = (
        _text(item.get("special_zone"))
        or _text(item.get("special_zone_raw"))
        or _text(item.get("special_zone_summary"))
    )
    if not special:
        return "해당사항 없음"
    if "일반미관지구" in special:
        return "미관지구 포함"
    if "공항" in special or "중요시설물보호지구" in special:
        return "공항 보호구역 검토 필요"
    if "UQ" in special:
        return "도시계획 관련 관리구역 확인 필요"
    return "도시계획상 추가 검토사항 존재"


def candidate_policy_type(item: dict[str, Any]) -> str:
    raw = (
        _text(item.get("정책유형분류"))
        or _text(item.get("정책유형"))
        or _text(item.get("policy_group"))
        or _text(item.get("refilter_policy_type_stage1"))
        or "기타 후보지"
    )
    return POLICY_TYPE_LABELS.get(raw, raw)


def station_label(item: dict[str, Any]) -> str:
    station = (
        _text(item.get("nearest_station_point"))
        or _text(item.get("가장가까운역"))
        or _text(item.get("근접역명"))
    )
    fit = _text(item.get("입지판정")) or _text(item.get("역세권예비구간"))
    distance = item.get("nearest_station_point_distance_m") or item.get("station_distance")

    if "250m" in fit or "250m 이내" in fit:
        return f"{station} 도보권(250m)" if station else "도보권(250m)"
    if "350m" in fit:
        return f"{station} 확장 검토권(350m)" if station else "확장 검토권(350m)"

    try:
        return f"{station} {int(float(distance))}m"
    except Exception:
        return station or fit or "-"


def location_tag(item: dict[str, Any]) -> str:
    fit = _text(item.get("입지판정")) or _text(item.get("역세권예비구간"))
    if "250m" in fit:
        return "역세권 예비판정"
    if "350m" in fit:
        return "확장 검토권"
    return "생활권 기반 검토"


def policy_need_label(item: dict[str, Any]) -> str:
    return _text(item.get("청년주거수요") or item.get("youth_supply_label")) or "청년수요 확인 필요"


def worker_need_label(item: dict[str, Any]) -> str:
    return _text(item.get("직주근접등급") or item.get("worker_access_tier")) or "직장 인근 주거 수요 확인 필요"


def compact_risk_label(item: dict[str, Any]) -> str:
    note = _text(item.get("review_note") or item.get("추가검토메모") or item.get("judgment_note"))
    special = special_zone_label(item)
    if special != "해당사항 없음":
        return "특별지구 검토"
    if any(token in note for token in ["추가 검토", "확인 필요", "소규모", "필지결합"]):
        return "추가 확인 필요"
    return "일반 검토"


def candidate_tags(item: dict[str, Any]) -> list[str]:
    tags = [location_tag(item), policy_need_label(item)]
    special = special_zone_label(item)
    if special != "해당사항 없음":
        tags.append("특별지구 검토")
    return tags[:3]


def summary_text(item: dict[str, Any]) -> str:
    policy = candidate_policy_type(item)
    if policy == "청년안심주택형 유리":
        return "청년 수요와 역세권 접근성이 양호한 예비 검토 후보입니다."
    if policy == "조건부 개발 가능":
        return "기본 입지 조건은 갖추고 있으나 도시계획상 추가 검토가 필요한 후보입니다."
    if policy == "리모델링 가능":
        return "기존 건축물 활용 관점에서 예비 검토가 가능한 후보입니다."
    if policy == "필지결합형":
        return "인접 필지와의 결합 가능성을 함께 검토할 수 있는 후보입니다."
    return "정책 목적과 입지 조건을 함께 보며 추가 검토가 필요한 후보입니다."


def _build_policy_fit(item: dict[str, Any]) -> str:
    station = station_label(item)
    policy_need = policy_need_label(item)
    worker_need = worker_need_label(item)
    parts = []
    if station and station != "-":
        parts.append(f"{station} 기준 접근성을 확인했습니다.")
    if policy_need:
        parts.append(f"{policy_need} 생활권으로 정책 검토 필요성이 있습니다.")
    if worker_need and worker_need != "직장 인근 주거 수요 확인 필요":
        parts.append(f"{worker_need} 생활권으로 직장 인근 주거 수요 관점도 참고할 수 있습니다.")
    return " ".join(parts) or "정책 수요와 입지 조건을 함께 검토할 필요가 있습니다."


def _build_feasibility(item: dict[str, Any]) -> str:
    zone_interpret = _text(item.get("용도지역해석") or item.get("zone_interpret"))
    area_review = _text(item.get("면적검토등급") or item.get("area_review"))
    parts = []
    if zone_interpret:
        parts.append(zone_interpret)
    if area_review:
        parts.append(f"면적 기준은 {area_review} 수준입니다.")
    return " ".join(parts) or "용도지역과 면적 기준을 함께 검토할 필요가 있습니다."


def _build_overall(item: dict[str, Any]) -> str:
    ai_reason = _text(item.get("AI추천사유") or item.get("ai_reason") or item.get("추가검토메모"))
    if ai_reason:
        return ai_reason
    return summary_text(item)


def format_candidate_card(item: dict[str, Any], rank: int) -> dict[str, Any]:
    policy_type = candidate_policy_type(item)
    zone = compact_zone_text(
        item.get("zone_main") or item.get("용도지역") or item.get("youth_zone"),
        item.get("zone_sub") or item.get("용도지역세부") or item.get("extra_zone"),
    )
    return {
        "id": _text(item.get("app_row_id") or item.get("_app_row_id")),
        "rank": rank,
        "managementId": _text(item.get("candidate_no") or item.get("candidate_id") or item.get("후보지번호")),
        "address": _text(item.get("address") or item.get("주소")),
        "station": station_label(item),
        "policyType": policy_type,
        "projectType": _text(item.get("사업유형") or item.get("project_type") or item.get("refilter_project_type")),
        "area": f"{_number(item.get('area_sqm') or item.get('면적㎡') or item.get('면적'))}㎡",
        "zone": zone or "-",
        "policyNeed": policy_need_label(item),
        "workerNeed": worker_need_label(item),
        "risk": compact_risk_label(item),
        "summary": summary_text(item),
        "tags": candidate_tags(item),
        "color": POLICY_TYPE_COLORS.get(policy_type, POLICY_TYPE_COLORS["기타 후보지"]),
    }


def format_candidate_detail(item: dict[str, Any], llm_review: dict[str, Any] | None = None) -> dict[str, Any]:
    policy_type = candidate_policy_type(item)
    zone = compact_zone_text(
        item.get("zone_main") or item.get("용도지역") or item.get("youth_zone"),
        item.get("zone_sub") or item.get("용도지역세부") or item.get("extra_zone"),
    )
    special_review = special_zone_label(item)
    review_note = (
        _text(item.get("추가검토메모") or item.get("review_note") or item.get("judgment_note"))
        or "현재 단계에서는 추가 확인이 필요합니다."
    )
    risks = llm_review.get("risks") if llm_review else [review_note]
    return {
        "id": _text(item.get("app_row_id") or item.get("_app_row_id")),
        "managementId": _text(item.get("candidate_no") or item.get("candidate_id") or item.get("후보지번호")),
        "address": _text(item.get("address") or item.get("주소")),
        "policyType": policy_type,
        "station": station_label(item),
        "policyNeed": policy_need_label(item),
        "workerNeed": worker_need_label(item),
        "zone": zone or "-",
        "policyFit": llm_review.get("policyFit") if llm_review else _build_policy_fit(item),
        "feasibility": llm_review.get("feasibility") if llm_review else _build_feasibility(item),
        "specialZoneReview": special_review if special_review != "해당사항 없음" else "현재 단계에서는 특별지구 추가사항이 확인되지 않았습니다.",
        "riskItems": risks,
        "overall": llm_review.get("overall") if llm_review else _build_overall(item),
    }


def build_map_feature(item: dict[str, Any], rank: int, *, prefer_polygon: bool = True) -> dict[str, Any]:
    policy_type = candidate_policy_type(item)
    lat = item.get("lat") or item.get("위도")
    lon = item.get("lon") or item.get("경도")
    properties = {
        "id": _text(item.get("app_row_id") or item.get("_app_row_id")),
        "rank": rank,
        "candidateNo": _text(item.get("candidate_no") or item.get("candidate_id") or item.get("후보지번호")),
        "policyType": policy_type,
        "address": _text(item.get("address") or item.get("주소")),
        "station": station_label(item),
        "area": f"{_number(item.get('area_sqm') or item.get('면적㎡') or item.get('면적'))}㎡",
        "zone": compact_zone_text(
            item.get("zone_main") or item.get("용도지역") or item.get("youth_zone"),
            item.get("zone_sub") or item.get("용도지역세부") or item.get("extra_zone"),
        )
        or "-",
        "policyNeed": policy_need_label(item),
        "workerNeed": worker_need_label(item),
        "risk": compact_risk_label(item),
        "summary": summary_text(item),
        "color": POLICY_TYPE_COLORS.get(policy_type, POLICY_TYPE_COLORS["기타 후보지"]),
        "lat": _float_or_none(lat),
        "lon": _float_or_none(lon),
    }

    polygon = item.get("parcel_polygon_geojson")
    if prefer_polygon and polygon:
        try:
            geometry = json.loads(polygon)
            return {"type": "polygon", "geometry": geometry, "properties": properties}
        except Exception:
            pass

    try:
        return {
            "type": "marker",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": properties,
        }
    except Exception:
        return {
            "type": "marker",
            "geometry": {"type": "Point", "coordinates": [126.9780, 37.5665]},
            "properties": properties,
        }
