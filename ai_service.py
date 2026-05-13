import json
import json
import os
import re
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
DOTENV_FILE = BASE_DIR / ".env"

REQUEST_TIMEOUT = 15
LLM_EXPLANATION_LIMIT = 5
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


def load_dotenv() -> None:
    if not DOTENV_FILE.exists():
        return
    for raw_line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", GEMINI_MODEL)


HOUSE_TYPE_MAP = {
    "오피스텔": "officetel",
    "원룸": "studio",
    "투룸": "two_room",
    "빌라": "villa",
    "다세대": "villa",
    "아파트": "apartment",
}

LIVING_PREFERENCE_DEFAULTS = {
    "cafe": {"selected": False, "near_m": 500, "acceptable_m": 800, "max_walk_minutes": None},
    "hospital": {"selected": False, "near_m": 1000, "acceptable_m": 1500, "max_walk_minutes": None},
    "laundry": {"selected": False, "near_m": 500, "acceptable_m": 800, "max_walk_minutes": None},
    "gym": {"selected": False, "near_m": 800, "acceptable_m": 1200, "max_walk_minutes": None},
    "large_store": {"selected": False, "near_m": 1500, "acceptable_m": 2500, "max_walk_minutes": None},
    "convenience_store": {"selected": False, "near_m": 300, "acceptable_m": 500, "max_walk_minutes": None},
    "light_food_snack": {"selected": False, "near_m": 500, "acceptable_m": 800, "max_walk_minutes": None},
}

LIVING_PREFERENCE_KEYWORDS = {
    "cafe": ["카페", "커피", "휴게음식점", "디저트"],
    "hospital": ["병원", "의원", "응급", "약국"],
    "laundry": ["세탁소", "빨래방", "코인빨래방"],
    "gym": ["헬스장", "체력단련장", "피트니스", "운동"],
    "large_store": ["대형마트", "대형상가", "대규모점포", "마트"],
    "convenience_store": ["편의점", "생활편의", "생활 편의", "생활편의시설"],
    "light_food_snack": ["음식점", "식당", "간식", "휴게음식", "먹거리"],
}

UNSUPPORTED_PREFERENCE_LABELS = [
    ("조용", "조용한 동네"),
    ("한적", "한적한 동네"),
    ("번잡", "번잡한 상권"),
    ("복잡한 상권", "복잡한 상권"),
    ("시끄럽", "시끄러운 곳"),
    ("소음", "소음 수준"),
    ("안전", "안전한 동네"),
    ("치안", "치안이 좋은 곳"),
    ("범죄", "범죄율"),
    ("밤길", "밤길이 안전한 곳"),
    ("유동인구", "유동인구"),
    ("인구밀도", "인구밀도"),
]


def _clean_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _normalize_query_text(text: str) -> str:
    normalized = _clean_text(text)
    replacements = [
        ("싫어여", "싫어요"),
        ("좋겠어욤", "좋겠어요"),
        ("좋아욤", "좋아요"),
        ("괜춘", "괜찮"),
        ("ㄱㅊ", "괜찮"),
        ("회사근처", "회사 근처"),
        ("직장근처", "직장 근처"),
        ("학교근처", "학교 근처"),
        ("집근처", "집 근처"),
        ("성동구는싫", "성동구는 싫"),
        ("성동구싫", "성동구 싫"),
        ("강남구는좋", "강남구는 좋"),
        ("강남구좋", "강남구 좋"),
        ("월세는높아도", "월세는 높아도"),
        ("통근이편했으면", "통근이 편했으면"),
        ("역까지조금걸어도", "역까지 조금 걸어도"),
    ]
    for raw, replacement in replacements:
        normalized = normalized.replace(raw, replacement)
    return normalized


def _text_variants(text: str) -> tuple[str, str]:
    normalized = _normalize_query_text(text)
    compact = re.sub(r"\s+", "", normalized)
    return normalized, compact


def _contains_any(text: str, keywords: list[str]) -> bool:
    normalized, compact = _text_variants(text)
    for keyword in keywords:
        compact_keyword = keyword.replace(" ", "")
        if keyword in normalized or keyword in compact or compact_keyword in normalized or compact_keyword in compact:
            return True
    return False


def _safe_int(value):
    try:
        return int(float(value))
    except Exception:
        return None


def _extract_minutes(text: str):
    match = re.search(r"(\d{1,3})\s*분", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d{1,2})\s*시간", text)
    if match:
        return int(match.group(1)) * 60
    return None


def _is_living_access_request(text: str) -> bool:
    facility_keywords = list(LIVING_PREFERENCE_KEYWORDS.keys()) + [
        "카페", "병원", "세탁소", "헬스장", "대형마트", "편의점", "생활편의", "주변 편의시설",
    ]
    walking_context = ["걸어서", "도보", "도보로", "도보가", "도보권", "걷", "근처", "가까운", "가까운 곳", "거리", "이내", "안"]
    normalized, compact = _text_variants(text)
    if not _contains_any(text, facility_keywords):
        return False
    if not _contains_any(text, walking_context):
        return False
    return bool(re.search(r"\d{1,3}\s*분", normalized) or re.search(r"\d{1,3}분", compact))


def _extract_commute_minutes(text: str) -> int | None:
    if _is_living_access_request(text):
        return None
    return _extract_minutes(text)


def _extract_access_minutes(text: str, category_keywords: list[str]) -> int | None:
    normalized, compact = _text_variants(text)
    if not _contains_any(text, category_keywords):
        return None

    patterns = [
        r"(?:걸어서|도보로|도보|걷(?:어서|는)?|근처|가까운 곳|가까운|거리|이내|안)[^0-9]{0,10}(\d{1,3})\s*분",
        r"(\d{1,3})\s*분[^0-9]{0,10}(?:거리|이내|안|거리에|안에|쯤|정도)",
    ]
    compact_patterns = [
        r"(?:걸어서|도보로|도보|걷(?:어서|는)?|근처|가까운곳|가까운|거리|이내|안)[^0-9]{0,10}(\d{1,3})분",
        r"(\d{1,3})분[^0-9]{0,10}(?:거리|이내|안|거리에|안에|쯤|정도)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    for pattern in compact_patterns:
        match = re.search(pattern, compact)
        if match:
            return int(match.group(1))
    return None


def _extract_budget(text: str) -> tuple[int | None, int | None]:
    deposit = None
    rent = None

    deposit_patterns = [
        r"보증금(?:은|이|을|를)?\s*(\d{2,5})\s*만\s*원?",
        r"보증금\s*(\d{2,5})\s*만",
        r"보증금\s*(\d{2,5})",
        r"(\d{2,5})\s*만원?\s*이하.*보증금",
        r"전세\s*(\d{2,5})\s*만",
        r"전세\s*(\d{2,5})",
        r"보증금\s*천만",
        r"천만\s*안\s*넘",
    ]
    for pattern in deposit_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        if "천만" in pattern:
            deposit = 1000
        else:
            deposit = int(match.group(1))
        break

    rent_patterns = [
        r"월세(?:는|가|을|를)?\s*(\d{1,4})\s*만\s*원?",
        r"월세\s*(\d{1,4})\s*만",
        r"월세\s*(\d{1,4})",
        r"(\d{1,4})\s*만원?\s*이하.*월세",
    ]
    for pattern in rent_patterns:
        match = re.search(pattern, text)
        if match:
            rent = int(match.group(1))
            break

    return deposit, rent


def _extract_house_types(text: str) -> list[str]:
    found = []
    for label, key in HOUSE_TYPE_MAP.items():
        if label in text and key not in found:
            found.append(key)
    return found


def _infer_transport(text: str, current_state: dict | None = None) -> dict:
    current_transport = (current_state or {}).get("transport", {}) or {}
    current_mode = (current_state or {}).get("transport_mode")
    normalized, compact = _text_variants(text)

    has_car = _contains_any(text, ["자동차", "자차", "차로", "운전"])
    has_transit = _contains_any(text, ["대중교통", "버스", "지하철", "전철"])

    primary_mode = current_transport.get("primary_mode") or current_mode or "unknown"
    secondary_modes = list(current_transport.get("secondary_modes", []))
    transit_detail = list(current_transport.get("transit_detail", []))

    if _contains_any(text, ["대중교통 우선", "대중교통이 더", "대중교통을 주로"]):
        primary_mode = "transit"
        if has_car and "car" not in secondary_modes:
            secondary_modes.append("car")
    elif _contains_any(text, ["자동차 우선", "차가 더", "자동차를 주로"]):
        primary_mode = "car"
        if has_transit and "transit" not in secondary_modes:
            secondary_modes.append("transit")
    elif has_car and has_transit:
        if primary_mode not in {"car", "transit"}:
            primary_mode = "transit"
        if primary_mode == "transit" and "car" not in secondary_modes:
            secondary_modes.append("car")
        if primary_mode == "car" and "transit" not in secondary_modes:
            secondary_modes.append("transit")
    elif has_transit:
        primary_mode = "transit"
    elif has_car:
        primary_mode = "car"

    if ("지하철" in normalized or "지하철" in compact) and "subway" not in transit_detail:
        transit_detail.append("subway")
    if ("버스" in normalized or "버스" in compact) and "bus" not in transit_detail:
        transit_detail.append("bus")

    return {
        "primary_mode": primary_mode,
        "secondary_modes": secondary_modes,
        "transit_detail": transit_detail,
        "needs_clarification": primary_mode == "unknown",
    }


def _priority_from_text(text: str, keywords: list[str]) -> str:
    if not _contains_any(text, keywords):
        return "none"
    if _contains_any(text, ["제일 중요", "가장 중요", "최대한", "무조건", "꼭", "싫", "피하고"]):
        return "high"
    if _contains_any(text, ["중요", "선호", "좋", "원", "필요"]):
        return "medium"
    return "low"


def _extract_soft_preferences(text: str, current_state: dict | None = None) -> dict:
    current = ((current_state or {}).get("soft_preferences") or {}).copy()
    current.setdefault("commute_time_priority", "none")
    current.setdefault("walking_distance_priority", "none")
    current.setdefault("transfer_count_priority", "none")
    current.setdefault("budget_priority", "none")
    current.setdefault("transit_access_priority", "none")
    long_walk_ok = _contains_any(text, ["조금 걸어도 괜찮", "조금 걸어도 좋아", "역까지 조금 걸어도", "도보 조금 더", "걷는 건 괜찮", "멀어도 괜찮"])

    if _contains_any(text, ["통근", "출근", "출퇴근", "통학", "직장", "회사", "학교", "빨리"]):
        current["commute_time_priority"] = "high" if _contains_any(text, ["편", "가까", "좋", "원", "필요", "싫", "피하고", "최대한", "무조건"]) else "medium"
    walking_keywords = ["도보", "걷", "걸어", "근처", "가까운", "역까지", "정류장까지"]
    if _contains_any(text, walking_keywords):
        if _contains_any(text, ["괜찮", "상관없", "길어도", "멀어도", "조금 걸어도"]):
            pass
        else:
            current["walking_distance_priority"] = "high" if _contains_any(text, ["가까", "짧", "짧을", "도보", "근처", "좋", "싫", "피하고"]) else "medium"
    transfer_minimize_phrases = [
        "환승은 최대한 적게",
        "환승 적게",
        "환승 많이 하는 건 싫",
        "환승 없는 게 좋아",
        "한 번에 갈 수 있",
        "갈아타는 거 싫",
        "갈아타기 싫",
        "환승 최소",
        "환승 줄",
        "갈아타지",
    ]
    if _contains_any(text, ["환승"]) or _contains_any(text, ["갈아타", "한 번에 갈 수 있"]):
        current["transfer_count_priority"] = "high" if _contains_any(text, transfer_minimize_phrases + ["적게", "적", "줄", "최소", "별로", "싫", "피하고", "좋"]) else "medium"
    if _contains_any(text, ["보증금", "월세", "예산", "전세"]):
        current["budget_priority"] = "high"
    transit_keywords = ["역세권", "지하철역", "버스정류장", "대중교통", "지하철", "역", "정류장"]
    transit_force = _contains_any(text, ["피하", "별로", "없", "최대한", "싫", "줄", "최소"])
    if _contains_any(text, transit_keywords) and (transit_force or not long_walk_ok):
        current["transit_access_priority"] = "high" if _contains_any(text, ["피하", "별로", "없", "적", "짧", "가까", "중요", "좋"]) else "medium"
    return current


def _extract_route_preferences(text: str, current_state: dict | None = None) -> dict:
    current = ((current_state or {}).get("route_preferences") or {}).copy()
    if _contains_any(text, ["지하철은 최대한 피", "지하철 피", "지하철 싫", "지하철은 별로", "지하철이 별로", "지하철이 없", "지하철 적", "지하철 없는"]):
        current["avoid_subway"] = True
    elif _contains_any(text, ["지하철 괜찮"]):
        current["avoid_subway"] = False
    return current


def _extract_geo_constraints(text: str, current_state: dict | None = None) -> dict:
    current = ((current_state or {}).get("geo_constraints") or {}).copy()
    excluded = list(dict.fromkeys(current.get("excluded_districts", []) or []))
    preferred = list(dict.fromkeys(current.get("preferred_districts", []) or []))
    normalized, compact = _text_variants(text)

    district_hits = re.findall(r"([가-힣]{2,10}구)", normalized)
    for district in district_hits:
        district = district.strip()
        if not district:
            continue
        window_patterns = [f"{district}는 싫", f"{district} 싫", f"{district} 피", f"{district} 제외", f"{district}은 싫", f"{district}은 피"]
        if _contains_any(text, window_patterns):
            if district not in excluded:
                excluded.append(district)
        elif _contains_any(text, [f"{district} 좋아", f"{district} 선호", f"{district} 근처", f"{district} 쪽", f"{district}였으면"]):
            if district not in preferred:
                preferred.append(district)

    avoid_remote_area = current.get("avoid_remote_area")
    if _contains_any(text, ["너무 외진", "외진 곳", "한적한데 너무 멀", "너무 멀리"]):
        avoid_remote_area = True
    elif _contains_any(text, ["외진 건 괜찮", "멀어도 괜찮", "조용한 외곽"]):
        avoid_remote_area = False

    current["excluded_districts"] = excluded
    current["preferred_districts"] = preferred
    current["avoid_remote_area"] = avoid_remote_area
    return current


def _extract_tradeoff_policy(text: str, current_state: dict | None = None) -> dict:
    current = ((current_state or {}).get("tradeoff_policy") or {}).copy()
    if _contains_any(text, ["월세가 조금 높아도", "월세는 조금 높아도", "조금 더 내도", "통근이 편했으면", "통근이 더 중요", "회사 가까운 곳"]):
        current["pay_more_for_commute"] = True
    elif _contains_any(text, ["월세는 낮을수록", "예산이 더 중요", "싼 곳", "비용이 중요"]):
        current["pay_more_for_commute"] = False

    if _contains_any(text, ["도보는 좀 길어도", "걷는 건 괜찮", "역까지 조금 걸어도", "장점이 더 중요"]):
        current["accept_longer_walk_for_lower_rent"] = True
    elif _contains_any(text, ["도보는 짧아야", "걷는 건 싫", "역이 가까운 게", "도보가 중요"]):
        current["accept_longer_walk_for_lower_rent"] = False

    return current


def _extract_living_preferences(text: str, current_state: dict | None = None) -> dict:
    current = json.loads(json.dumps((current_state or {}).get("living_preferences") or {}))
    normalized = {}
    for category, defaults in LIVING_PREFERENCE_DEFAULTS.items():
        normalized[category] = json.loads(json.dumps(current.get(category) or defaults))
        normalized[category].setdefault("selected", False)
        normalized[category].setdefault("near_m", defaults["near_m"])
        normalized[category].setdefault("acceptable_m", defaults["acceptable_m"])
        normalized[category].setdefault("max_walk_minutes", defaults.get("max_walk_minutes"))

    for category, keywords in LIVING_PREFERENCE_KEYWORDS.items():
        if _contains_any(text, keywords):
            normalized[category]["selected"] = True
            access_minutes = _extract_access_minutes(text, keywords)
            if access_minutes is not None:
                normalized[category]["max_walk_minutes"] = access_minutes
                normalized[category]["near_m"] = int(access_minutes * 70)
                normalized[category]["acceptable_m"] = int(access_minutes * 95)

    broad_facility_phrases = [
        "편의시설이 많",
        "생활 편의가 좋",
        "생활편의",
        "생활 편의",
        "생활편의도",
        "주변에 뭐가 많",
        "번화가처럼 편의시설",
        "생활권이 편했",
        "편의시설 많은 곳",
        "생활 인프라 좋은 곳",
    ]
    if _contains_any(text, broad_facility_phrases):
        for category in ["cafe", "convenience_store", "large_store", "light_food_snack"]:
            normalized[category]["selected"] = True

    return normalized


def _extract_unsupported_preferences(text: str, current_state: dict | None = None) -> list[str]:
    current = list((current_state or {}).get("unsupported_preferences") or [])
    labels = []
    for keyword, label in UNSUPPORTED_PREFERENCE_LABELS:
        if _contains_any(text, [keyword]) and label not in labels:
            labels.append(label)
    if _contains_any(text, ["사람이 어느 정도", "적당히 사람", "사람이 적당히", "사람도 적당히", "사람도 어느 정도", "적당한 유동인구"]):
        labels.append("적당한 유동인구")
    if _contains_any(text, ["너무 조용", "적당히 붐비", "생활권"]):
        labels.append("생활 분위기")
    for label in current:
        if label not in labels:
            labels.append(label)
    return list(dict.fromkeys(labels))


def _extract_user_intent_notes(text: str, geo_constraints: dict | None = None) -> dict:
    geo_constraints = geo_constraints or {}
    excluded = geo_constraints.get("excluded_districts") or []
    preferred = geo_constraints.get("preferred_districts") or []
    must_have = []
    nice_to_have = []

    if _contains_any(text, ["직장 근처", "회사 근처", "학교 근처", "직장과 가까", "회사와 가까", "학교와 가까"]):
        must_have.append("직장/학교 근처")
    if _contains_any(text, ["너무 외진 곳은 싫", "외진 곳은 싫", "외곽은 싫"]):
        must_have.append("너무 외진 곳 제외")

    return {
        "must_have": list(dict.fromkeys(must_have)),
        "nice_to_have": list(dict.fromkeys(nice_to_have)),
    }


def _extract_needs_clarification(text: str, current_state: dict | None = None) -> list[str]:
    current_state = current_state or {}
    normalized, compact = _text_variants(text)
    notes = []

    has_living_focused = _contains_any(text, list(LIVING_PREFERENCE_KEYWORDS.keys()) + [
        "카페", "병원", "세탁소", "헬스장", "대형마트", "편의점", "생활편의", "주변 편의시설"
    ])
    has_unsupported_density = _contains_any(text, ["조용", "한적", "번잡", "붐비", "사람", "유동인구", "생활권"])
    has_generic_proximity = _contains_any(text, ["근처", "가까운", "주변", "옆", "근방"])
    has_specific_geo = bool((current_state.get("geo_constraints") or {}).get("excluded_districts")) or bool((current_state.get("geo_constraints") or {}).get("preferred_districts"))
    has_budget = any((current_state.get("hard_constraints") or {}).get(key) is not None for key in ["deposit_max", "rent_max", "max_commute_minutes"])
    has_tradeoff = bool((current_state.get("tradeoff_policy") or {}).get("pay_more_for_commute") is not None or (current_state.get("tradeoff_policy") or {}).get("accept_longer_walk_for_lower_rent") is not None)
    has_mode = (current_state.get("transport", {}) or {}).get("primary_mode") not in {None, "unknown"}

    if len(compact) <= 7 or (len(compact) <= 12 and has_generic_proximity and not any([has_specific_geo, has_living_focused, has_tradeoff])):
        notes.append("세부 조건 보완")

    if has_unsupported_density and has_living_focused:
        notes.append("조용함과 생활편의 우선순위")
    elif has_generic_proximity and not any([has_specific_geo, has_living_focused, has_tradeoff]):
        notes.append("직주근접 세부 기준")

    if _contains_any(text, ["적당히", "어느 정도", "애매", "무난"]) and not notes:
        notes.append("우선순위 구체화")

    return list(dict.fromkeys(notes))


def _summary_entry(
    label: str,
    *,
    condition_type: str,
    source: str,
    display_group: str,
    display_in_ai_summary: bool,
) -> dict:
    return {
        "label": label,
        "type": condition_type,
        "source": source,
        "display_group": display_group,
        "display_in_ai_summary": display_in_ai_summary,
    }


def _selected_living_labels(living: dict) -> list[str]:
    living_labels = []
    living_map = {
        "cafe": "카페",
        "hospital": "병원",
        "laundry": "세탁소",
        "gym": "헬스장",
        "large_store": "대형마트",
        "convenience_store": "편의점",
        "light_food_snack": "간식/식당",
    }
    for category, label in living_map.items():
        config = living.get(category) or {}
        if isinstance(config, dict) and config.get("selected"):
            living_labels.append(label)
    return living_labels


def _build_summary_conditions(
    text: str,
    parsed: dict,
    *,
    base_state: dict | None = None,
    include_base_filter: bool = False,
) -> list[dict]:
    conditions: list[dict] = []
    normalized, _compact = _text_variants(text)

    empty_state: dict = {}
    geo = _extract_geo_constraints(text, empty_state)
    route = _extract_route_preferences(text, empty_state)
    tradeoff = _extract_tradeoff_policy(text, empty_state)
    soft = _extract_soft_preferences(text, empty_state)
    living = _extract_living_preferences(text, empty_state)
    unsupported = _extract_unsupported_preferences(text, empty_state)
    intent_notes = _extract_user_intent_notes(text, geo)
    hard = {
        "max_commute_minutes": _extract_commute_minutes(normalized),
        "deposit_max": None,
        "rent_max": None,
    }
    deposit_explicit, rent_explicit = _extract_budget(normalized)
    hard["deposit_max"] = deposit_explicit
    hard["rent_max"] = rent_explicit
    clarify_state = {
        "geo_constraints": geo,
        "hard_constraints": hard,
        "tradeoff_policy": tradeoff,
        "living_preferences": living,
        "route_preferences": route,
    }
    needs_clarification = _extract_needs_clarification(text, clarify_state)

    if geo.get("excluded_districts"):
        conditions.append(_summary_entry(
            f"제외 지역: {', '.join(geo['excluded_districts'])}",
            condition_type="geo_exclusion",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))
    if geo.get("preferred_districts"):
        conditions.append(_summary_entry(
            f"선호 지역: {', '.join(geo['preferred_districts'])}",
            condition_type="geo_preference",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))
    if geo.get("avoid_remote_area") is True:
        conditions.append(_summary_entry(
            "외진 곳 회피",
            condition_type="geo_remote_avoidance",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    if hard["max_commute_minutes"] is not None:
        conditions.append(_summary_entry(
            f"통근시간 {int(hard['max_commute_minutes'])}분 이내",
            condition_type="commute_limit",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    if deposit_explicit is not None or rent_explicit is not None:
        budget_bits = []
        if deposit_explicit is not None:
            budget_bits.append(f"보증금 {int(deposit_explicit)}만원 이하")
        if rent_explicit is not None:
            budget_bits.append(f"월세 {int(rent_explicit)}만원 이하")
        conditions.append(_summary_entry(
            f"예산 조건: {', '.join(budget_bits)}",
            condition_type="budget_limit",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    if tradeoff.get("pay_more_for_commute") is True:
        conditions.append(_summary_entry(
            "통근 우선 / 비용 일부 양보",
            condition_type="tradeoff_commute_cost",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))
    if tradeoff.get("pay_more_for_commute") is False:
        conditions.append(_summary_entry(
            "비용 우선",
            condition_type="tradeoff_cost_priority",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))
    if tradeoff.get("accept_longer_walk_for_lower_rent") is True:
        conditions.append(_summary_entry(
            "도보 길어도 비용 절감 허용",
            condition_type="tradeoff_walk_rent",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    if soft.get("transfer_count_priority") == "high":
        conditions.append(_summary_entry(
            "환승은 적을수록 좋음",
            condition_type="transfer_preference",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    broad_facility_phrases = [
        "편의시설이 많",
        "생활 편의가 좋",
        "생활편의",
        "생활 편의",
        "생활편의도",
        "주변에 뭐가 많",
        "번화가처럼 편의시설",
        "생활권이 편했",
        "편의시설 많은 곳",
        "생활 인프라 좋은 곳",
    ]
    if _contains_any(normalized, broad_facility_phrases):
        if not any(config.get("selected") for config in living.values() if isinstance(config, dict)):
            living["cafe"] = {"selected": True, "near_m": 500, "acceptable_m": 800}
            living["convenience_store"] = {"selected": True, "near_m": 300, "acceptable_m": 500}
            living["large_store"] = {"selected": True, "near_m": 1500, "acceptable_m": 2500}
            living["light_food_snack"] = {"selected": True, "near_m": 500, "acceptable_m": 800}

    if _contains_any(normalized, [
        "대중교통으로 다니기 편",
        "대중교통으로 다니기 좋",
        "대중교통이 편",
        "대중교통 접근성",
        "대중교통으로다닐만",
        "버스나 지하철",
        "버스가 편",
        "지하철역이 가까",
        "역세권",
        "정류장이 가까",
    ]):
        conditions.append(_summary_entry(
            "대중교통 접근성 중요",
            condition_type="transit_access",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    if route.get("avoid_subway") is True:
        conditions.append(_summary_entry(
            "지하철은 최소화",
            condition_type="route_avoid_subway",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    living_labels = _selected_living_labels(living)
    if living_labels:
        living_bits = []
        for category, config in living.items():
            if not isinstance(config, dict) or not config.get("selected"):
                continue
            label = _selected_living_labels({category: config})
            if not label:
                continue
            label_text = label[0]
            max_walk_minutes = config.get("max_walk_minutes")
            if max_walk_minutes is not None:
                label_text = f"{label_text} 도보 약 {int(max_walk_minutes)}분 이내"
            living_bits.append(label_text)
        label = "생활 편의시설이 많은 곳" if _contains_any(normalized, broad_facility_phrases) and len(living_bits) >= 3 else f"생활 편의: {', '.join(living_bits or living_labels)}"
        conditions.append(_summary_entry(
            label,
            condition_type="living_preference",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    if unsupported:
        conditions.append(_summary_entry(
            f"현재 데이터로 직접 판단하기 어려운 조건: {', '.join(unsupported)}",
            condition_type="unsupported_preference",
            source="user_natural_language",
            display_group="unsupported",
            display_in_ai_summary=True,
        ))

    if needs_clarification:
        conditions.append(_summary_entry(
            f"추가 질문 필요: {', '.join(needs_clarification)}",
            condition_type="needs_clarification",
            source="internal_inference",
            display_group="clarification",
            display_in_ai_summary=False,
        ))

    if intent_notes.get("must_have"):
        conditions.append(_summary_entry(
            f"꼭 필요한 조건: {', '.join(intent_notes['must_have'])}",
            condition_type="must_have",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))
    if intent_notes.get("nice_to_have"):
        conditions.append(_summary_entry(
            f"참고 조건: {', '.join(intent_notes['nice_to_have'])}",
            condition_type="nice_to_have",
            source="user_natural_language",
            display_group="applied",
            display_in_ai_summary=True,
        ))

    if include_base_filter and base_state:
        base_hard = (base_state.get("hard_constraints") or {}) if isinstance(base_state, dict) else {}
        if base_hard.get("max_commute_minutes") is not None:
            conditions.append(_summary_entry(
                f"통근시간 {int(base_hard['max_commute_minutes'])}분 이내",
                condition_type="commute_limit",
                source="base_filter",
                display_group="base_filter",
                display_in_ai_summary=False,
            ))
        if base_hard.get("deposit_max") is not None:
            conditions.append(_summary_entry(
                f"보증금 {int(base_hard['deposit_max'])}만원 이하",
                condition_type="budget_limit",
                source="base_filter",
                display_group="base_filter",
                display_in_ai_summary=False,
            ))
        if base_hard.get("rent_max") is not None:
            conditions.append(_summary_entry(
                f"월세 {int(base_hard['rent_max'])}만원 이하",
                condition_type="budget_limit",
                source="base_filter",
                display_group="base_filter",
                display_in_ai_summary=False,
            ))
        if base_state.get("workplace"):
            conditions.append(_summary_entry(
                "직장/학교 위치 설정",
                condition_type="workplace_location",
                source="base_filter",
                display_group="base_filter",
                display_in_ai_summary=False,
            ))

        final_hard = (parsed.get("hard_constraints") or {}) if isinstance(parsed, dict) else {}
        if final_hard.get("max_commute_minutes") is not None:
            conditions.append(_summary_entry(
                f"최종 통근시간 {int(final_hard['max_commute_minutes'])}분 이내",
                condition_type="commute_limit",
                source="final_applied_condition",
                display_group="final_applied_condition",
                display_in_ai_summary=False,
            ))
        if final_hard.get("deposit_max") is not None:
            conditions.append(_summary_entry(
                f"최종 보증금 {int(final_hard['deposit_max'])}만원 이하",
                condition_type="budget_limit",
                source="final_applied_condition",
                display_group="final_applied_condition",
                display_in_ai_summary=False,
            ))
        if final_hard.get("rent_max") is not None:
            conditions.append(_summary_entry(
                f"최종 월세 {int(final_hard['rent_max'])}만원 이하",
                condition_type="budget_limit",
                source="final_applied_condition",
                display_group="final_applied_condition",
                display_in_ai_summary=False,
            ))

    deduped = []
    seen = set()
    for item in conditions:
        key = (
            item.get("label"),
            item.get("type"),
            item.get("source"),
            item.get("display_group"),
            bool(item.get("display_in_ai_summary")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_summary_conditions(
    text: str,
    parsed: dict,
    *,
    base_state: dict | None = None,
    include_base_filter: bool = False,
) -> list[dict]:
    return _build_summary_conditions(
        text,
        parsed,
        base_state=base_state,
        include_base_filter=include_base_filter,
    )


def _merge_request_state(base: dict | None, update: dict) -> dict:
    state = json.loads(json.dumps(base or {}))
    state.setdefault("transport", {})
    state.setdefault("hard_constraints", {})
    state.setdefault("soft_preferences", {})
    state.setdefault("route_preferences", {})
    state.setdefault("geo_constraints", {})
    state.setdefault("tradeoff_policy", {})
    state.setdefault("living_preferences", {})
    state.setdefault("unsupported_preferences", [])
    state.setdefault("conversation_flags", {})

    for section in ["transport", "hard_constraints", "soft_preferences", "route_preferences", "geo_constraints", "tradeoff_policy", "conversation_flags"]:
        state[section].update(update.get(section, {}) or {})
    if isinstance(update.get("living_preferences"), dict):
        state["living_preferences"].update(update.get("living_preferences") or {})
    if update.get("unsupported_preferences") is not None:
        state["unsupported_preferences"] = list(update.get("unsupported_preferences") or [])

    for key in ["transport_mode", "deposit_max", "rent_max", "max_commute_minutes"]:
        if update.get(key) is not None:
            state[key] = update[key]
    for key in ["confidence", "needs_clarification", "must_have", "nice_to_have"]:
        if update.get(key) is not None:
            state[key] = update[key]
    return state


def _flatten_request_state(state: dict) -> dict:
    transport = state.get("transport", {}) or {}
    hard = state.get("hard_constraints", {}) or {}
    geo = state.get("geo_constraints", {}) or {}
    tradeoff = state.get("tradeoff_policy", {}) or {}
    user_notes = state.get("user_intent_notes", {}) or {}
    flattened = dict(state)
    flattened["transport_mode"] = transport.get("primary_mode") if transport.get("primary_mode") != "unknown" else None
    flattened["deposit_max"] = hard.get("deposit_max")
    flattened["rent_max"] = hard.get("rent_max")
    flattened["max_commute_minutes"] = hard.get("max_commute_minutes")
    flattened["geo_constraints"] = geo
    flattened["tradeoff_policy"] = tradeoff
    flattened["living_preferences"] = state.get("living_preferences", {}) or {}
    flattened["unsupported_preferences"] = state.get("unsupported_preferences", []) or []
    flattened["must_have"] = user_notes.get("must_have", [])
    flattened["nice_to_have"] = user_notes.get("nice_to_have", [])
    flattened["confidence"] = state.get("confidence")
    flattened["needs_clarification"] = state.get("needs_clarification", [])
    return flattened


def parse_query_text(query_text: str, current_state: dict | None = None) -> dict:
    text = _normalize_query_text(query_text)
    merged = _merge_request_state(current_state, {})
    hard = (merged.get("hard_constraints") or {}).copy()

    minutes = _extract_commute_minutes(text)
    deposit, rent = _extract_budget(text)
    if minutes is not None:
        hard["max_commute_minutes"] = minutes
    if deposit is not None:
        hard["deposit_max"] = deposit
        merged.setdefault("conversation_flags", {})["deposit_decided"] = True
        merged["deposit_decided"] = True
    if rent is not None:
        hard["rent_max"] = rent
        merged.setdefault("conversation_flags", {})["rent_decided"] = True
        merged["rent_decided"] = True
    merged["transport"] = _infer_transport(text, merged)
    merged["soft_preferences"] = _extract_soft_preferences(text, merged)
    merged["route_preferences"] = _extract_route_preferences(text, merged)
    merged["geo_constraints"] = _extract_geo_constraints(text, merged)
    merged["tradeoff_policy"] = _extract_tradeoff_policy(text, merged)
    merged["living_preferences"] = _extract_living_preferences(text, merged)
    merged["unsupported_preferences"] = _extract_unsupported_preferences(text, merged)
    merged["user_intent_notes"] = _extract_user_intent_notes(text, merged.get("geo_constraints"))
    merged["confidence"] = None
    merged["needs_clarification"] = _extract_needs_clarification(text, merged)
    merged["hard_constraints"] = hard

    flattened = _flatten_request_state(merged)
    flattened["summary_conditions"] = _build_summary_conditions(text, flattened)
    return flattened


def _call_gemini_parser(query_text: str, current_state: dict | None = None) -> dict | None:
    if not GEMINI_API_KEY:
        return None
    prompt = (
        "사용자 주거 추천 조건을 JSON으로 구조화하세요. "
        "응답은 JSON만 반환하세요.\n"
        "스키마:\n"
        "{"
        '"transport":{"primary_mode":"car|transit|unknown","secondary_modes":[],"transit_detail":[]},'
        '"hard_constraints":{"max_commute_minutes":null,"deposit_max":null,"rent_max":null},'
        '"soft_preferences":{"commute_time_priority":"none","walking_distance_priority":"none","transfer_count_priority":"none","budget_priority":"none","transit_access_priority":"none"},'
        '"route_preferences":{"avoid_subway":false},'
        '"geo_constraints":{"excluded_districts":[],"preferred_districts":[],"avoid_remote_area":null},'
        '"living_preferences":{"cafe":{"selected":false,"near_m":500,"acceptable_m":800,"max_walk_minutes":null},"hospital":{"selected":false,"near_m":1000,"acceptable_m":1500,"max_walk_minutes":null},"laundry":{"selected":false,"near_m":500,"acceptable_m":800,"max_walk_minutes":null},"gym":{"selected":false,"near_m":800,"acceptable_m":1200,"max_walk_minutes":null},"large_store":{"selected":false,"near_m":1500,"acceptable_m":2500,"max_walk_minutes":null},"convenience_store":{"selected":false,"near_m":300,"acceptable_m":500,"max_walk_minutes":null},"light_food_snack":{"selected":false,"near_m":500,"acceptable_m":800,"max_walk_minutes":null}},'
        '"tradeoff_policy":{"pay_more_for_commute":null,"accept_longer_walk_for_lower_rent":null},'
        '"unsupported_preferences":[],' 
        '"user_intent_notes":{"must_have":[],"nice_to_have":[]},'
        '"confidence":0.0,"needs_clarification":[]'
        "}\n"
        "Rules:\n"
        "- Extract explicit district mentions into geo_constraints.\n"
        "- Interpret phrases like '카페가 가까웠으면' as living_preferences.cafe.selected=true and '병원, 세탁소, 헬스장, 대형마트' as the matching living_preferences categories.\n"
        "- When a facility is mentioned together with a walking radius such as '걸어서 10분 거리에 헬스장', store it as living_preferences.<category>.max_walk_minutes and do not treat it as commute time.\n"
        "- Keep only currently measurable facility categories in living_preferences; do not put quietness, safety, noise, or crowding there.\n"
        "- Interpret phrases like '조용한 동네', '번잡한 상권', '안전한 동네', '밤길이 안전한 곳', '소음 수준', '유동인구' as unsupported_preferences.\n"
        "- Interpret phrases like '성동구는 싫어요' as excluded_districts.\n"
        "- Interpret phrases like '지하철이 별로 없었으면 좋겠어요' as avoid_subway=true and transit_access_priority=high.\n"
        "- Interpret phrases like '근처', '가까운 곳', '도보' as walking_distance_priority or commute_time_priority.\n"
        "- Interpret trade-off statements like '월세가 조금 높아도 통근이 편했으면' as pay_more_for_commute=true.\n"
        "- When the request is vague, short, or mixes conflicting preferences, fill needs_clarification with short topic labels such as '직주근접 세부 기준' or '조용함과 생활편의 우선순위'.\n"
        "- Do not invent values that the user did not express.\n"
        "Examples:\n"
        '- "성동구는 싫어요" -> geo_constraints.excluded_districts=["성동구"]\n'
        '- "회사 근처가 좋긴 한데 너무 번잡한 곳은 싫어요" -> geo_constraints.preferred_districts=[...], soft_preferences.walking_distance_priority="medium", unsupported_preferences=["번잡한 상권"]\n'
        '- "주변에 카페나 병원이 가까웠으면 좋겠어요" -> living_preferences.cafe.selected=true, living_preferences.hospital.selected=true\n'
        '- "걸어서 10분 거리에 헬스장 있었으면 좋겠어요" -> living_preferences.gym.selected=true, living_preferences.gym.max_walk_minutes=10\n'
        '- "지하철이 별로 없었으면 좋겠어요" -> soft_preferences.transit_access_priority="high", route_preferences.avoid_subway=true\n'
        '- "조용한 동네가 좋고, 밤길이 안전했으면 좋겠어요" -> unsupported_preferences=["조용한 동네","밤길이 안전한 곳"]\n'
        '- "월세가 조금 높아도 통근이 편했으면 좋겠어요" -> tradeoff_policy.pay_more_for_commute=true\n'
        f"현재 상태: {json.dumps(current_state or {}, ensure_ascii=False)}\n"
        f"사용자 입력: {query_text}"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    response = requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    text = payload["candidates"][0]["content"]["parts"][0]["text"]
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def parse_query_with_gemini(query_text: str, current_state: dict | None = None) -> dict:
    base = parse_query_text(query_text, current_state)
    try:
        parsed = _call_gemini_parser(query_text, current_state)
    except Exception:
        parsed = None
    if not parsed:
        return base

    merged = _merge_request_state(current_state, parsed)
    if _is_living_access_request(query_text) and not ((current_state or {}).get("hard_constraints") or {}).get("max_commute_minutes"):
        merged.setdefault("hard_constraints", {})["max_commute_minutes"] = None
    merged["needs_clarification"] = _extract_needs_clarification(query_text, merged)
    flattened = _flatten_request_state(merged)
    flattened["summary_conditions"] = _build_summary_conditions(query_text, flattened)
    return flattened


def find_missing_fields(parsed: dict, *, require_transport_mode: bool = False) -> list[str]:
    missing = []
    if require_transport_mode and not parsed.get("transport_mode"):
        missing.append("transport_mode")
    return missing


def build_follow_up_question(missing_fields: list[str]) -> str:
    return "원하는 조건을 조금만 더 알려주세요."


def _build_stage_question(state: dict) -> str:
    hard = state.get("hard_constraints", {}) or {}
    transport = state.get("transport", {}) or {}
    primary_mode = transport.get("primary_mode")
    preferred_house_types = ["skip"]
    if hard.get("deposit_max") is None and hard.get("rent_max") is None:
        return "예산 조건을 알려주세요. 예: 보증금 1500만원 이하, 월세 60만원 이하"
    if hard.get("max_commute_minutes") is None:
        if primary_mode == "car":
            return "자동차 기준 최대 통근시간을 알려주세요. 예: 40분 이내"
        return "대중교통 기준 최대 통근시간을 알려주세요. 예: 50분 이내"
    if primary_mode == "transit" and state.get("soft_preferences", {}).get("transfer_count_priority") == "none" and not state.get("route_preferences", {}).get("avoid_subway"):
        return "교통에서 중요하게 보는 조건을 알려주세요. 예: 환승 적은 곳, 도보 짧은 곳, 지하철은 최대한 피하고 싶어요"
    if primary_mode == "car" and state.get("soft_preferences", {}).get("transit_access_priority") == "none":
        return "자동차 기준 외에 추가로 볼 조건이 있나요? 예: 가끔 대중교통도 이용해요, 차로 30분 이내면 좋겠어요"
    if not preferred_house_types:
        return "선호하는 주택 유형이 있나요? 예: 오피스텔, 원룸, 빌라, 아파트, 상관없음"
    return "생활환경에서 중요하게 보는 조건이 있으면 알려주세요. 예: 코인빨래방, 공원, 병원, 편의점"


def chat_turn_with_gemini(workplace_name: str, workplace_address: str, current_state: dict, message: str, timeout: int = REQUEST_TIMEOUT) -> dict:
    del workplace_name, workplace_address, timeout
    parsed = parse_query_with_gemini(message, current_state)
    missing_fields = find_missing_fields(parsed, require_transport_mode=not bool((current_state or {}).get("transport_mode")))
    clarification_notes = parsed.get("needs_clarification") or []
    need_more_info = bool(missing_fields or clarification_notes)
    if missing_fields:
        assistant_message = build_follow_up_question(missing_fields)
    elif clarification_notes:
        assistant_message = f"조건을 조금만 더 구체적으로 알려주세요. 특히 {clarification_notes[0]} 중 무엇을 더 우선하는지 알려주시면 좋아요."
    else:
        assistant_message = _build_stage_question(parsed)

    hard = parsed.get("hard_constraints", {}) or {}
    parsed["conversation_flags"] = parsed.get("conversation_flags", {}) or {}
    parsed["conversation_flags"]["ready_to_search"] = bool(
        parsed.get("transport_mode")
        and (hard.get("deposit_max") is not None or hard.get("rent_max") is not None)
        and hard.get("max_commute_minutes") is not None
    )

    return {
        "assistant_message": assistant_message,
        "parsed": parsed,
        "missing_fields": missing_fields,
        "need_more_info": need_more_info,
    }


def _recommendation_prompt_payload(recommendations: list[dict]) -> list[dict]:
    items = []
    for item in recommendations:
        context = item.get("explanation_context", {}) or {}
        items.append(
            {
                "rank": item.get("rank"),
                "title": item.get("title"),
                "score": item.get("score"),
                "address": item.get("address"),
                "deal_type": item.get("deal_type"),
                "deposit_manwon": item.get("deposit_manwon"),
                "monthly_rent_manwon": item.get("monthly_rent_manwon"),
                "duration_min": item.get("duration_min"),
                "total_walk_m": item.get("total_walk_m"),
                "first_walk_min": item.get("first_walk_min"),
                "transfer_count": item.get("display_transfer_count") if item.get("display_transfer_count") is not None else item.get("transfer_count"),
                "subway_section_count": item.get("subway_section_count"),
                "secondary_car_duration_min": (item.get("secondary_car") or {}).get("duration_min"),
                "secondary_transit_duration_min": (item.get("secondary_transit") or {}).get("duration_min"),
                "secondary_transit_walk_m": (item.get("secondary_transit") or {}).get("total_walk_m"),
                "secondary_transit_transfer_count": ((item.get("secondary_transit") or {}).get("display_transfer_count") if (item.get("secondary_transit") or {}).get("display_transfer_count") is not None else (item.get("secondary_transit") or {}).get("transfer_count")),
                "route_summary": item.get("route_summary"),
                "car_time_profile": item.get("car_time_profile"),
                "car_route_requested_provider": item.get("car_route_requested_provider"),
                "car_route_provider": item.get("car_route_provider"),
                "car_route_time_basis": item.get("car_route_time_basis"),
                "car_route_direction": item.get("car_route_direction"),
                "car_route_http_status": item.get("car_route_http_status"),
                "car_route_response_received": item.get("car_route_response_received"),
                "car_route_error_code": item.get("car_route_error_code"),
                "car_route_error_message": item.get("car_route_error_message"),
                "car_route_failure_detail": item.get("car_route_failure_detail"),
                "car_route_fallback_used": item.get("car_route_fallback_used"),
                "reason": item.get("reason"),
                "score_breakdown": item.get("score_breakdown"),
                "living_matches": context.get("living_matches") or [],
                "living_details": context.get("living_details") or [],
                "living_reference_tags": context.get("living_reference_tags") or [],
                "selected_living_categories": context.get("selected_living_categories") or [],
                "requested_living_preferences": context.get("requested_living_preferences") or {},
                "geo_constraints": context.get("geo_constraints") or {},
                "tradeoff_policy": context.get("tradeoff_policy") or {},
                "constraints": context.get("constraints") or {},
                "strong_points": context.get("strong_points", []),
                "weak_points": context.get("weak_points", []),
                "vs_prev": context.get("vs_prev"),
                "vs_next": context.get("vs_next"),
            }
        )
    return items


def _pretty_int(value) -> str | None:
    if value is None:
        return None
    try:
        return str(int(round(float(value))))
    except Exception:
        return None


def _contains_any(text: str, patterns: list[str]) -> bool:
    return any(pattern in text for pattern in patterns)


def _selected_living_categories(item: dict) -> list[str]:
    context = item.get("explanation_context", {}) or {}
    categories = context.get("selected_living_categories") or []
    return [str(category) for category in categories if category]


def _has_selected_living_preferences(item: dict) -> bool:
    return bool(_selected_living_categories(item))


def _car_time_band_text(time_band: str) -> str:
    mapping = {
        "very_relaxed": "설정한 최대 통근시간보다 훨씬 여유 있는 구간",
        "relaxed": "통근 시간에 여유가 있는 구간",
        "comfortable": "통근 시간이 안정적인 구간",
        "within_limit": "최대 통근시간 안에는 들어오는 구간",
        "over_limit": "최대 통근시간을 넘는 구간",
    }
    return mapping.get(time_band, "통근시간 판단 정보가 부족한 구간")


def _car_budget_band_text(budget_band: str) -> str:
    mapping = {
        "very_light": "예산 부담이 매우 낮은 편이에요.",
        "light": "예산 여유가 있는 편이에요.",
        "moderate": "예산 안에서 무난한 편이에요.",
        "tight": "예산 상한에 가까운 편이에요.",
        "over_budget": "예산을 초과할 가능성이 있어요.",
    }
    return mapping.get(budget_band, "예산 조건은 확인이 더 필요해요.")


def _car_distance_band_text(distance_band: str, distance_km) -> str | None:
    if distance_km is None:
        return None
    mapping = {
        "very_near": f"주행거리도 {float(distance_km):.2f}km로 매우 가까운 편이에요.",
        "near": f"주행거리도 {float(distance_km):.2f}km로 가까운 편이에요.",
        "normal": f"주행거리는 {float(distance_km):.2f}km로 무난한 편이에요.",
        "far": f"주행거리가 {float(distance_km):.2f}km라 운전 부담은 조금 생길 수 있어요.",
        "very_far": f"주행거리가 {float(distance_km):.2f}km라 자동차 이동 부담이 커질 수 있어요.",
    }
    return mapping.get(distance_band)


def _car_margin_text(commute_margin_ratio, max_commute) -> str | None:
    if commute_margin_ratio is None or not max_commute:
        return None
    if commute_margin_ratio >= 0.45:
        return f"최대 통근시간 {int(max_commute)}분 대비 여유가 큰 후보예요."
    if commute_margin_ratio >= 0.2:
        return f"최대 통근시간 {int(max_commute)}분 안에서 안정적으로 들어와요."
    if commute_margin_ratio >= 0:
        return f"최대 통근시간 {int(max_commute)}분 안에는 들어오지만 여유는 크지 않아요."
    return f"최대 통근시간 {int(max_commute)}분 기준에서는 여유가 부족해요."


def _car_preferred_secondary_text(item: dict) -> str | None:
    context = item.get("explanation_context", {}) or {}
    secondary = context.get("secondary_metrics", {}) or {}
    if secondary.get("transit_commute_time_min") is not None and "transit" in (context.get("secondary_modes") or []):
        return f"대중교통으로는 약 {int(secondary['transit_commute_time_min'])}분 정도라 다른 이동수단과도 비교해볼 수 있어요."
    return None


def _car_time_profile_sentence(item: dict) -> str | None:
    context = item.get("explanation_context", {}) or {}
    profile = item.get("car_time_profile") or context.get("car_time_profile") or {}
    if not isinstance(profile, dict) or not profile.get("enabled"):
        return None
    route_direction = str(
        item.get("car_route_direction")
        or context.get("car_route_direction")
        or profile.get("route_direction")
        or "to_work"
    ).strip()
    profile_key = str(profile.get("profile_key") or "").strip()
    selected_car_time = str(
        item.get("selected_car_time")
        or context.get("selected_car_time")
        or profile.get("time")
        or profile.get("selected_car_time")
        or ""
    ).strip()
    time_supported = item.get("car_route_time_supported")
    if time_supported is None:
        time_supported = context.get("car_route_time_supported")
    actual_provider = str(item.get("car_route_provider") or context.get("car_route_provider") or "").strip()
    requested_provider = str(item.get("car_route_requested_provider") or context.get("car_route_requested_provider") or "").strip()
    time_bits = selected_car_time.split(":", 1)
    if len(time_bits) == 2 and time_bits[0].isdigit() and time_bits[1].isdigit():
        hour24 = int(time_bits[0])
        minute = int(time_bits[1])
        meridiem = "오후" if hour24 >= 12 else "오전"
        hour12 = hour24 % 12 or 12
        time_phrase = f"{meridiem} {hour12}시" if minute == 0 else f"{meridiem} {hour12}시 {minute}분"
    else:
        time_phrase = selected_car_time
    direction_label = "퇴근" if route_direction in {"from_work", "work_to_home"} else "출근"
    if profile_key == "weekday_evening_6":
        basis = "평일 오후 6시 퇴근 기준"
    elif profile_key == "custom":
        basis = f"사용자가 선택한 {time_phrase} {direction_label} 기준"
    else:
        basis = "평일 오전 8시 출근 기준"
    if time_supported is True or actual_provider == "TMAP_TIME_MACHINE":
        return f"자동차 통근시간은 {basis}으로 계산했어요."
    if requested_provider == "TMAP_TIME_MACHINE":
        return f"선택한 시간 기준은 추천 조건으로 저장했지만, 현재 연결된 자동차 경로 API는 시간대별 예측 소요시간을 직접 반영하지 않아 기본 자동차 경로 기준으로 계산했어요. {basis}은 함께 참고했어요."
    return f"자동차 통근시간은 {basis}으로 계산했어요."


def _car_summary_and_detail(item: dict) -> tuple[str, str]:
    context = item.get("explanation_context", {}) or {}
    metrics = context.get("primary_metrics", {}) or {}
    budget = context.get("budget", {}) or {}
    constraints = context.get("constraints", {}) or {}
    ranking_summary = context.get("ranking_summary", {}) or {}
    vs_prev = context.get("vs_prev") or {}
    rank = int(item.get("rank") or 0)

    distance_km = item.get("distance_km") or item.get("car_distance_km")
    max_commute = constraints.get("max_commute_minutes")
    commute = metrics.get("commute_time_min") or item.get("duration_min")
    time_band = metrics.get("car_time_band") or item.get("car_time_band") or "unknown"
    distance_band = metrics.get("car_distance_band") or item.get("car_distance_band") or "unknown"
    commute_margin_ratio = metrics.get("commute_margin_ratio")
    budget_band = budget.get("budget_band") or item.get("budget_band") or "unknown"
    budget_pressure = budget.get("budget_pressure_score") or item.get("budget_pressure_score")
    rent_margin_ratio = budget.get("rent_margin_ratio") or item.get("rent_margin_ratio")
    same_time_band = bool(vs_prev.get("same_car_time_band"))
    same_distance_band = bool(vs_prev.get("same_distance_band"))
    time_band_changed = bool(vs_prev.get("car_time_band_changed"))
    meaningful_time_diff = bool(vs_prev.get("meaningful_car_time_diff"))
    budget_pressure_gap = vs_prev.get("budget_pressure_gap")
    rent_gap = vs_prev.get("rent_gap_manwon")
    deposit_gap = vs_prev.get("deposit_gap_manwon")
    distance_gap = vs_prev.get("distance_km_gap")
    area_gap = vs_prev.get("area_gap_sqm")
    top_time_band = ranking_summary.get("rank_1_car_time_band")

    budget_text = _car_budget_band_text(budget_band)
    distance_text = _car_distance_band_text(distance_band, distance_km)
    margin_text = _car_margin_text(commute_margin_ratio, max_commute)
    secondary_text = _car_preferred_secondary_text(item)
    time_sentence = _car_time_profile_sentence(item)

    if rank == 1:
        if time_band in {"very_relaxed", "relaxed"} and budget_band in {"very_light", "light"}:
            summary = "자동차 통근 시간이 여유 있고 예산 부담도 낮아 기본 조건 기준으로 가장 균형이 좋은 후보예요."
        elif time_band in {"very_relaxed", "relaxed"}:
            summary = "설정한 최대 통근시간보다 훨씬 여유 있어 자동차 이동 부담이 낮은 후보예요."
        elif budget_band in {"very_light", "light"}:
            summary = "자동차 통근 조건을 충족하면서 예산 여유가 커 가장 먼저 추천할 만한 후보예요."
        else:
            summary = "자동차 통근과 예산 조건이 모두 안정적으로 맞아 가장 먼저 추천된 후보예요."
        detail = margin_text or distance_text or budget_text
        if time_sentence:
            summary = f"{time_sentence} {summary}"
        return summary, detail or ""

    if rank in {2, 3}:
        if same_time_band and not meaningful_time_diff:
            if rent_gap is not None and rent_gap <= -5:
                summary = "상위 후보와 자동차 이동 부담은 비슷하지만, 월세 여유가 있어 함께 볼 만한 후보입니다."
                detail = budget_text
            elif rent_gap is not None and rent_gap >= 5:
                summary = "자동차 이동은 안정권이지만, 월세가 상위 후보보다 높아 예산 여유는 조금 줄어드는 후보입니다."
                detail = margin_text or distance_text
            elif deposit_gap is not None and deposit_gap <= -200:
                summary = "상위 후보와 자동차 통근 시간은 비슷하지만, 보증금 부담이 낮아 초기 비용 쪽 장점이 있는 후보입니다."
                detail = budget_text
            elif deposit_gap is not None and deposit_gap >= 200:
                summary = "자동차 통근 시간은 상위 후보와 비슷하지만, 보증금 부담이 더 커 2순위로 보는 후보입니다."
                detail = budget_text
            elif distance_gap is not None and distance_gap >= 1.0 and not same_distance_band:
                summary = "통근 시간은 비슷하지만, 주행거리가 더 길어 운전 부담은 조금 더 있는 후보입니다."
                detail = distance_text
            elif distance_gap is not None and distance_gap <= -1.0:
                summary = "상위 후보와 자동차 이동 부담은 비슷하고, 주행거리가 더 짧아 함께 볼 만한 후보입니다."
                detail = distance_text
            elif area_gap is not None and area_gap >= 3.0:
                summary = "자동차 시간은 비슷한 구간이고, 면적이 더 넓어 조건을 함께 비교해볼 만한 후보입니다."
                detail = budget_text or distance_text
            else:
                summary = "상위 후보와 자동차 시간은 같은 안정권이라, 예산이나 주행거리 차이를 함께 보는 편이 좋은 후보입니다."
                detail = budget_text or distance_text
            if time_sentence:
                summary = f"{time_sentence} {summary}"
            return summary, detail or ""

        if time_band_changed and top_time_band and time_band != top_time_band:
            if time_band in {"very_relaxed", "relaxed", "comfortable"}:
                summary = "자동차 통근은 안정권에 들어오지만, 상위 후보보다 이동 여유가 한 단계 줄어 후순위로 보는 후보입니다."
            else:
                summary = "기본 조건은 충족하지만, 상위 후보보다 자동차 이동 부담이 커 비교가 필요한 후보입니다."
            detail = margin_text or distance_text or budget_text
            if time_sentence:
                summary = f"{time_sentence} {summary}"
            return summary, detail or ""

        if budget_band in {"very_light", "light"} and (rent_gap is not None and rent_gap <= -5 or deposit_gap is not None and deposit_gap <= -200):
            if rent_gap is not None and rent_gap <= -5:
                summary = "자동차 시간 차이는 크지 않지만, 월세 부담이 더 낮아 함께 검토할 만한 후보입니다."
            else:
                summary = "자동차 시간 차이는 크지 않지만, 보증금 부담이 더 낮아 함께 검토할 만한 후보입니다."
            detail = budget_text
            if time_sentence:
                summary = f"{time_sentence} {summary}"
            return summary, detail or ""

        if rent_gap is not None and rent_gap >= 5:
            summary = "자동차 통근은 안정권이지만, 월세가 상위 후보보다 높아 후순위로 보는 후보입니다."
            detail = budget_text
        elif deposit_gap is not None and deposit_gap >= 200:
            summary = "자동차 통근은 안정권이지만, 보증금 부담이 상위 후보보다 커 후순위로 보는 후보입니다."
            detail = budget_text
        elif distance_gap is not None and distance_gap >= 1.0:
            summary = "통근 시간은 조건 안에 들어오지만, 주행거리가 상위 후보보다 길어 운전 부담이 조금 더 있는 후보입니다."
            detail = distance_text
        else:
            summary = "자동차 통근은 안정권이지만, 예산 여유나 주행거리 조건에서 상위 후보보다 앞서지 못한 후보입니다."
            detail = margin_text or budget_text or distance_text
            if time_sentence:
                summary = f"{time_sentence} {summary}"
            return summary, detail or ""

    if time_band == "within_limit":
        if budget_band in {"very_light", "light"}:
            summary = "최대 통근시간 안에는 들어오고 예산 여유도 있어, 뒤쪽 후보 중에서는 다시 볼 만한 편입니다."
            detail = "자동차 이동 여유는 상위 후보보다 적지만 비용 부담은 덜한 편입니다."
        else:
            summary = "최대 통근시간 안에는 들어오지만, 상위 후보보다 자동차 이동 여유가 적어 후순위로 보는 후보입니다."
            detail = margin_text or budget_text
        return summary, detail or ""

    if time_band_changed and meaningful_time_diff:
        summary = "상위 후보보다 자동차 시간이 체감상 한 구간 길어져 후순위로 보는 후보입니다."
        detail = margin_text or distance_text or budget_text
        return summary, detail or ""

    if same_time_band and not meaningful_time_diff and budget_band in {"very_light", "light"}:
        if rent_gap is not None and rent_gap <= -5:
            summary = "자동차 이동 조건은 상위 후보와 비슷하지만, 월세 여유가 있어 후보군에 남은 집입니다."
        elif deposit_gap is not None and deposit_gap <= -200:
            summary = "자동차 이동 조건은 상위 후보와 비슷하지만, 보증금 여유가 있어 후보군에 남은 집입니다."
        else:
            summary = "자동차 이동 조건은 상위 후보와 비슷하지만, 예산 여유가 있어 후보군에 남은 집입니다."
        detail = budget_text
        return summary, detail or ""

    if rent_gap is not None and rent_gap >= 5:
        summary = "기본 조건은 맞지만, 월세가 상위 후보보다 높아 예산 여유는 더 적은 편입니다."
        detail = budget_text
    elif deposit_gap is not None and deposit_gap >= 200:
        summary = "기본 조건은 맞지만, 보증금 부담이 상위 후보보다 커 뒤쪽에 배치된 후보입니다."
        detail = budget_text
    elif distance_gap is not None and distance_gap >= 1.0:
        summary = "기본 조건은 맞지만, 주행거리가 상위 후보보다 길어 운전 부담은 조금 더 있는 후보입니다."
        detail = distance_text
    else:
        summary = "기본 조건은 충족하지만, 자동차 이동 여유나 예산 조건에서 상위 후보보다 강점이 적은 후보입니다."
        detail = budget_text if budget_band in {"tight", "over_budget"} else (distance_text or margin_text or secondary_text or budget_text)
    return summary, detail or ""


def _car_deterministic_explanation(item: dict) -> tuple[str, str]:
    summary, detail = _car_summary_and_detail(item)
    time_sentence = _car_time_profile_sentence(item)
    if time_sentence:
        summary = f"{time_sentence} {summary}"
        detail = f"{time_sentence} {detail}" if detail else time_sentence
    return summary, detail


def _car_reason_missing_fields(item: dict) -> list[str]:
    context = item.get("explanation_context", {}) or {}
    constraints = context.get("constraints", {}) or {}
    budget = _budget_context(item)
    metrics = (item.get("ranking_trace") or {}).get("metrics") or (context.get("primary_metrics") or {})
    missing = []
    if _safe_float(metrics.get("duration_min") or item.get("duration_min")) is None:
        missing.append("duration_min")
    if _safe_float(constraints.get("max_commute_minutes")) is None:
        missing.append("max_commute_minutes")
    if budget["deposit"] is None and budget["monthly_rent"] is None:
        missing.append("budget")
    if budget["area_pyeong"] is None and budget["area_sqm"] is None:
        missing.append("area")
    return missing


def _normalize_generated_explanation(item: dict, rank_summary: str, llm_reason: str) -> tuple[str, str]:
    context = item.get("explanation_context", {}) or {}
    primary_mode = context.get("primary_mode") or "transit"
    secondary_modes = context.get("secondary_modes", []) or []
    metrics = context.get("primary_metrics", {}) or {}
    first_walk_min = metrics.get("first_walk_min")
    route_status = item.get("route_status") or metrics.get("route_status")

    banned_terms = [
        "바로 위 후보",
        "바로 아래 후보",
        "예산적합도",
        "지하철 회피",
        "도보부담",
        "raw_score",
        "weighted",
        "기여도",
    ]
    transit_negative_patterns = [
        "접근성은 한 번 더",
        "접근성은 직접 확인",
        "접근성 부담",
        "역이나 정류장 접근성은 한 번 더",
        "아주 뛰어난 편은 아니",
    ]
    walkable_banned_patterns = [
        "환승",
        "정류장",
        "지하철",
        "대중교통 접근성",
        "첫 정류장",
        "첫 역",
        "체감 접근성",
        "직접 확인해보는 것이 좋아요",
    ]
    generic_banned_patterns = [
        "보증금은 ",
        "월세는 ",
        "체감 접근성",
        "직접 확인해보는 것이 좋아요",
        "첫 정류장",
        "첫 역",
    ]

    summary = (rank_summary or "").strip()
    reason = (llm_reason or "").strip()

    if primary_mode == "car" and not _has_selected_living_preferences(item):
        return _car_deterministic_explanation(item)

    # Force both modes to appear when both were selected.
    if primary_mode == "transit" and "car" in secondary_modes and "자동차" not in reason:
        secondary_car = (context.get("secondary_metrics") or {}).get("car_commute_time_min")
        if secondary_car is not None:
            reason = f"{reason} 자동차로는 약 {int(secondary_car)}분 정도 걸립니다.".strip()
    if primary_mode == "car" and "transit" in secondary_modes and "대중교통" not in reason:
        secondary_transit = (context.get("secondary_metrics") or {}).get("transit_commute_time_min")
        if secondary_transit is not None:
            reason = f"{reason} 대중교통으로는 약 {int(secondary_transit)}분 정도 걸립니다.".strip()

    invalid = False
    if _contains_any(summary, banned_terms) or _contains_any(reason, banned_terms):
        invalid = True
    if _contains_any(reason, generic_banned_patterns):
        invalid = True
    if first_walk_min is not None and int(first_walk_min) <= 5 and _contains_any(reason, transit_negative_patterns):
        invalid = True
    if route_status in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"} and _contains_any(reason, walkable_banned_patterns):
        invalid = True
    if primary_mode == "transit" and "car" in secondary_modes and "자동차" not in reason:
        invalid = True
    if primary_mode == "car" and "transit" in secondary_modes and "대중교통" not in reason:
        invalid = True

    if invalid:
        return _fallback_rank_explanation(item)
    return summary, reason


def _fallback_rank_explanation(item: dict) -> tuple[str, str]:
    rank = int(item.get("rank") or 0)
    context = item.get("explanation_context", {}) or {}
    route_status = item.get("route_status") or context.get("route_status")
    metrics = context.get("primary_metrics", {}) or {}
    walk_minutes = metrics.get("commute_time_min") or item.get("duration_min")
    walk_distance = metrics.get("walk_m") or item.get("total_walk_m")
    budget = context.get("budget", {}) or {}
    constraints = context.get("constraints", {}) or {}
    budget_score = ((item.get("score_breakdown") or {}).get("factors") or [])

    if route_status in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        summary = "이 집은 직장/학교와 가까워 걸어서 이동하기 좋아요."
        details = []
        if walk_minutes is not None:
            details.append(f"도보 {int(walk_minutes)}분 정도라 출퇴근 부담이 크지 않습니다.")
        budget_feel = None
        deposit = budget.get("deposit_manwon")
        rent = budget.get("monthly_rent_manwon")
        deposit_limit = constraints.get("deposit_max")
        rent_limit = constraints.get("rent_max")
        ratios = []
        if deposit is not None and deposit_limit:
            ratios.append(float(deposit) / float(deposit_limit))
        if rent is not None and rent_limit:
            ratios.append(float(rent) / float(rent_limit))
        if ratios:
            worst = max(ratios)
            if worst <= 0.7:
                budget_feel = "예산 부담도 비교적 낮아 생활비를 안정적으로 가져가기 좋습니다."
        if budget_feel:
            details.append(budget_feel)
        else:
            details.append("가까운 거리 자체가 가장 큰 장점이라 이동 부담을 줄이기 좋습니다.")
        if walk_distance is not None and int(walk_distance) >= 900:
            details.append("다만 매일 걷는 거리는 조금 있는 편이라 날씨 영향은 감안하는 게 좋습니다.")
        else:
            details.append("대중교통을 탈 필요 없이 가까운 거리 자체가 강점인 선택지입니다.")
        return summary, " ".join(details)

    if (context.get("primary_mode") or "transit") == "car" and not _has_selected_living_preferences(item):
        return _car_deterministic_explanation(item)

    strengths = context.get("strong_points", []) or []
    strength_keys = context.get("strong_point_keys", []) or []
    weak_points = context.get("weak_points", []) or []
    weak_point_keys = context.get("weak_point_keys", []) or []
    primary_mode = context.get("primary_mode") or "transit"
    secondary_modes = context.get("secondary_modes", []) or []
    secondary_metrics = context.get("secondary_metrics", {}) or {}
    score_breakdown = item.get("score_breakdown", {}) or {}

    def fit_phrase() -> str:
        if primary_mode == "car":
            if "transit" in secondary_modes:
                return "자동차를 우선으로 보되 대중교통 이동 시간도 함께 확인하고 싶은 사람"
            if "budget" in strength_keys:
                return "자동차 통근을 유지하면서 예산 부담을 줄이고 싶은 사람"
            if "commute" in strength_keys:
                return "자동차로 빠르게 이동하고 싶은 사람"
            return "자동차 통근을 기준으로 집을 찾는 사람"
        if "car" in secondary_modes:
            return "대중교통을 우선으로 보되 자동차 이동 시간도 함께 확인하고 싶은 사람"
        if "budget" in strength_keys:
            if budget.get("deal_type") == "월세":
                return "월세 부담을 낮추고 싶은 사람"
            return "예산 부담을 줄이고 싶은 사람"
        if "commute" in strength_keys:
            return "통근 시간을 안정적으로 관리하고 싶은 사람"
        if "transfer" in strength_keys:
            return "환승이 적은 경로를 선호하는 사람"
        if "walking" in strength_keys:
            return "도보 이동이 짧은 집을 찾는 사람"
        if "transit_access" in strength_keys:
            return "대중교통 접근성이 중요한 사람"
        if "subway" in strength_keys:
            return "지하철 이용 패턴이 중요한 사람"
        return "예산과 통근 조건을 함께 보는 사람"

    def budget_sentence() -> str | None:
        deposit = budget.get("deposit_manwon")
        rent = budget.get("monthly_rent_manwon")
        deposit_limit = constraints.get("deposit_max")
        rent_limit = constraints.get("rent_max")

        def budget_feel() -> str:
            ratios = []
            if deposit is not None and deposit_limit:
                ratios.append(float(deposit) / float(deposit_limit))
            if rent is not None and rent_limit:
                ratios.append(float(rent) / float(rent_limit))
            if not ratios:
                return "예산을 무리하게 끌어올리지 않아도 되는 편이에요."
            worst = max(ratios)
            if worst <= 0.45:
                return "예산 부담이 꽤 낮은 편이에요."
            if worst <= 0.7:
                return "예산 안에서 안정적으로 선택할 수 있는 편이에요."
            return "예산 상한에 가까워 다른 후보보다 여유는 적을 수 있어요."

        if budget.get("deal_type") == "월세" and rent is not None and deposit is not None:
            return f"보증금은 {int(deposit)}만원, 월세는 {int(rent)}만원 수준이고, {budget_feel()}"
        if budget.get("deal_type") == "월세" and rent is not None:
            return f"월세가 {int(rent)}만원 수준이라 {budget_feel()}"
        if deposit is not None:
            return f"보증금이 {int(deposit)}만원 수준이라 {budget_feel()}"
        return None

    def commute_sentence() -> str | None:
        commute = metrics.get("commute_time_min")
        if commute is None:
            return None
        return f"통근 시간은 약 {int(commute)}분 정도라 직장이나 학교까지 이동 시간은 안정적인 편이에요."

    def walk_sentence() -> str | None:
        walk = metrics.get("walk_m")
        if walk is None:
            return None
        if walk <= 400:
            return f"총 도보 이동은 약 {int(walk)}m 정도라 이동 부담이 크지 않은 편이에요."
        return f"총 도보 이동은 약 {int(walk)}m 정도로, 이동 피로도는 조금 더 살펴볼 만해요."

    def transfer_sentence() -> str | None:
        transfers = metrics.get("transfer_count")
        if transfers is None:
            return None
        if transfers <= 0:
            return "환승이 없어 출퇴근 동선이 단순한 편이에요."
        if transfers == 1:
            return "환승은 1회라 출퇴근 동선이 단순한 편이에요."
        return f"환승은 {int(transfers)}회 정도라 이동 경로가 아주 단순한 편은 아니에요."

    def transit_access_sentence() -> str | None:
        access_walk_m = metrics.get("first_walk_m")
        access_walk_min = metrics.get("first_walk_min")
        if access_walk_m is None and access_walk_min is None:
            return "대중교통 접근성도 무난한 편이라 일상적으로 이동하기에는 큰 불편이 적어요."
        if access_walk_min is not None and int(access_walk_min) <= 5:
            return f"집에서 역/정류장까지 도보 {int(access_walk_min)}분 정도라 출발 동선이 짧은 편이에요."
        if access_walk_min is not None and int(access_walk_min) <= 10:
            return f"집에서 역/정류장까지 도보 {int(access_walk_min)}분 정도라 접근성은 무난한 편이에요."
        if access_walk_m is not None:
            return f"집에서 역/정류장까지 도보가 약 {int(access_walk_m)}m라 접근성은 여유 있게 보는 편이 좋아요."
        return "집에서 역/정류장까지 가는 거리는 함께 살펴볼 만해요."

    def subway_sentence() -> str | None:
        subway_count = metrics.get("subway_section_count")
        if subway_count is None:
            return None
        if subway_count <= 1:
            return "지하철 이용 구간이 과하게 많지 않아 이동 패턴이 복잡하지 않은 편이에요."
        return "지하철 이용 구간은 선호와 맞는지 실제 경로를 한 번 보는 것이 좋아요."

    def build_strength_sentences() -> list[str]:
        sentences = []
        ordered_keys = strength_keys or []
        if primary_mode == "car":
            ordered_keys = ["budget", "commute"]
        for key in ordered_keys:
            if key == "budget":
                sentence = budget_sentence()
            elif key == "commute":
                sentence = commute_sentence()
            elif key == "walking":
                sentence = walk_sentence()
            elif key == "transfer":
                sentence = transfer_sentence()
            elif key == "transit_access":
                sentence = transit_access_sentence()
            elif key == "subway":
                sentence = subway_sentence()
            else:
                sentence = None
            if sentence and sentence not in sentences:
                sentences.append(sentence)
            if len(sentences) >= 2:
                break
        if not sentences:
            if primary_mode == "car":
                fallback = budget_sentence() or commute_sentence()
            else:
                fallback = budget_sentence() or commute_sentence() or transfer_sentence() or walk_sentence()
            if fallback:
                sentences.append(fallback)
        if primary_mode == "transit" and "car" in secondary_modes and secondary_metrics.get("car_commute_time_min") is not None:
            sentences.append(f"자동차로는 약 {int(secondary_metrics['car_commute_time_min'])}분 정도라 다른 이동수단과도 비교해보기 쉬운 편이에요.")
        if primary_mode == "car" and "transit" in secondary_modes and secondary_metrics.get("transit_commute_time_min") is not None:
            transit_transfer = secondary_metrics.get("transit_transfer_count")
            transit_walk = secondary_metrics.get("transit_walk_m")
            extra = []
            if transit_transfer is not None:
                extra.append("환승 없음" if int(transit_transfer) <= 0 else f"환승 {int(transit_transfer)}회")
            if transit_walk is not None:
                extra.append(f"총 도보 {int(transit_walk)}m")
            tail = f" ({', '.join(extra)})" if extra else ""
            sentences.append(f"대중교통으로는 약 {int(secondary_metrics['transit_commute_time_min'])}분 정도{tail} 걸려 다른 이동수단과 차이도 확인할 수 있어요.")
        return sentences

    def weakest_available_key() -> str:
        candidates = []
        for key, payload in score_breakdown.items():
            weight = payload.get("weight")
            raw_score = payload.get("raw_score")
            if weight in (None, 0) or raw_score is None:
                continue
            if primary_mode == "car" and key in {"walking", "transfer", "transit_access", "subway"}:
                continue
            candidates.append((float(raw_score), key))
        if not candidates:
            return ""
        candidates.sort(key=lambda entry: entry[0])
        return candidates[0][1]

    def caution_sentence() -> str:
        key = weak_point_keys[0] if weak_point_keys else weakest_available_key()
        deposit = budget.get("deposit_manwon")
        rent = budget.get("monthly_rent_manwon")
        deposit_limit = constraints.get("deposit_max")
        rent_limit = constraints.get("rent_max")
        commute = metrics.get("commute_time_min")
        max_commute = constraints.get("max_commute_minutes")
        walk = metrics.get("walk_m")
        first_walk_m = metrics.get("first_walk_m")
        first_walk_min = metrics.get("first_walk_min")
        transfers = metrics.get("transfer_count")
        subway_count = metrics.get("subway_section_count")
        if primary_mode == "car" and key in {"walking", "transfer", "transit_access", "subway"}:
            key = weakest_available_key()
        if key == "budget":
            if deposit is not None and deposit_limit:
                gap = float(deposit_limit) - float(deposit)
                if gap <= 150:
                    return f"다만 보증금이 예산 상한에 가까워 다른 후보보다 자금 여유는 적을 수 있어요."
            if rent is not None and rent_limit:
                gap = float(rent_limit) - float(rent)
                if gap <= 5:
                    return "다만 월세가 예산 상한에 가까워 매달 느끼는 부담은 한 번 더 따져보는 것이 좋아요."
            if deposit is not None and rent is not None:
                return f"다만 보증금 {int(deposit)}만원, 월세 {int(rent)}만원 조합이 본인 예산에 정말 편한 수준인지는 확인해보는 것이 좋아요."
            return "다만 예산 면에서는 아주 여유로운 후보는 아닐 수 있어요."
        if key == "commute":
            if commute is not None and max_commute:
                margin = float(max_commute) - float(commute)
                if margin <= 5:
                    return f"다만 통근 시간이 기준에 꽤 가깝기 때문에 출퇴근 여유를 넉넉하게 보려면 아쉬울 수 있어요."
            return "다만 통근 시간이 아주 짧은 편은 아니라 더 빠른 이동을 원하면 아쉬울 수 있어요."
        if key == "walking":
            if walk is not None:
                return f"다만 역/정류장까지 걷는 거리가 약 {int(walk)}m라 도보 부담은 조금 더 살펴볼 만해요."
            return "다만 역/정류장까지 걷는 거리는 한 번 더 살펴볼 만해요."
        if key == "transfer":
            if transfers is not None:
                return f"다만 환승이 {int(transfers)}회 정도라 이동 흐름이 아주 단순한 편은 아닐 수 있어요."
            return "다만 환승 흐름은 아주 단순한 편은 아닐 수 있어요."
        if key == "transit_access":
            if first_walk_min is not None or first_walk_m is not None:
                if first_walk_min is not None and int(first_walk_min) <= 5:
                    if transfers is not None and transfers >= 2:
                        return f"다만 환승이 {int(transfers)}회 정도라 이동 흐름은 한 번 더 확인해보는 것이 좋아요."
                    if commute is not None and max_commute and (float(max_commute) - float(commute)) <= 5:
                        return "다만 통근 시간이 기준에 가까운 편이라 출퇴근 여유를 넉넉하게 보려면 한 번 더 따져보는 것이 좋아요."
                    return "다만 예산과 통근 중 무엇을 더 우선하는지에 따라 순서가 달라질 수 있어요."
                if first_walk_min is not None and int(first_walk_min) <= 10:
                    return f"다만 집에서 역/정류장까지 도보 {int(first_walk_min)}분 정도라 접근성은 조금 더 살펴볼 만해요."
                if first_walk_m is not None:
                    return f"다만 집에서 역/정류장까지 도보가 약 {int(first_walk_m)}m라 접근성 부담은 조금 더 살펴볼 만해요."
            return "다만 역/정류장 접근성은 한 번 더 살펴볼 만해요."
        if key == "subway":
            if subway_count is not None and subway_count >= 2:
                return "다만 지하철 이용 구간이 나뉘는 편이라 이동 흐름은 조금 더 살펴볼 만해요."
            return "다만 지하철 이용 흐름은 선호와 맞는지 한 번 살펴볼 만해요."
        if primary_mode == "car":
            return "다만 예산이나 통근 둘 중 하나에서 아주 강하게 앞서는 후보는 아니라 우선순위는 조금 밀릴 수 있어요."
        return "다만 예산이나 이동 편의 중 무엇을 더 우선할지에 따라 순서가 달라질 수 있어요."

    def final_judgment_sentence() -> str:
        key = weak_point_keys[0] if weak_point_keys else weakest_available_key()
        if rank == 1:
            return "전체적으로 예산과 통근 조건의 균형이 좋아 가장 먼저 볼 만해요."
        if rank in (2, 3):
            if key == "budget":
                return "통근이 괜찮다면 예산 여유를 얼마나 중시하느냐에 따라 선택 순서가 달라질 수 있어요."
            if key == "commute":
                return "예산 조건이 마음에 든다면 통근 시간을 어느 정도까지 받아들일지에 따라 판단이 갈려요."
            return "핵심 조건이 잘 맞으면 상위권 대안으로 볼 수 있어요."
        if key == "budget":
            return "초기 자금이나 월세 여유가 더 중요하다면 뒤로 두고 볼 수 있어요."
        if key == "commute":
            return "더 빠른 통근보다 가격이나 다른 조건을 우선한다면 뒤로 두고 볼 수 있어요."
        if key == "walking":
            return "역이나 정류장까지 걷는 거리를 감수할 수 있다면 고려해볼 만해요."
        if key == "transfer":
            return "환승이 조금 늘어나도 괜찮다면 뒤로 두고 비교해볼 수 있어요."
        return "조건을 조금 조정할 수 있다면 뒤로 두고 비교해볼 수 있어요."

    def summary_sentence() -> str:
        weak_key = weak_point_keys[0] if weak_point_keys else weakest_available_key()
        if rank == 1:
            return f"이 집은 {fit}에게 가장 잘 맞아요."
        if rank in (2, 3):
            if weak_key == "budget":
                return f"이 집은 {fit}에게 잘 맞지만, 예산 여유까지 넉넉하게 보려면 1위보다 한 단계 뒤에 두기 좋아요."
            if weak_key == "commute":
                return f"이 집은 {fit}에게 꽤 잘 맞지만, 더 빠른 통근을 우선하면 1위보다 대안에 가까워요."
            if weak_key == "walking":
                return f"이 집은 {fit}에게 괜찮은 선택지지만, 도보 부담까지 줄이려면 1위보다 우선순위는 조금 낮아요."
            if weak_key == "transfer":
                return f"이 집은 {fit}에게 잘 맞는 편이지만, 이동 경로를 더 단순하게 보고 싶다면 상위 대안 정도예요."
            return f"이 집은 {fit}이라면 좋은 대안이지만, 통근시간이나 예산 조건에서 상위 후보보다 앞서는 항목은 적어요."
        if weak_key == "budget":
            return f"이 집은 {fit}에게 맞는 편이지만, 예산 여유까지 넉넉하게 보려면 우선순위는 조금 낮아요."
        if weak_key == "commute":
            return f"이 집은 {fit}에게 맞는 편이지만, 통근 시간을 더 짧게 가져가고 싶다면 뒤로 밀릴 수 있어요."
        if weak_key == "walking":
            return f"이 집은 {fit}에게 맞을 수 있지만, 역이나 정류장까지 걷는 거리까지 따지면 우선순위는 조금 낮아요."
        if weak_key == "transfer":
            return f"이 집은 {fit}에게 맞을 수 있지만, 환승이 적은 경로를 우선하면 우선순위는 조금 낮아요."
        if weak_key == "subway":
            return f"이 집은 {fit}에게 맞을 수 있지만, 지하철 이용 흐름까지 깔끔하게 보려면 우선순위는 조금 낮아요."
        if weak_key == "transit_access":
            return f"이 집은 {fit}에게 맞을 수 있지만, 역이나 정류장 접근성까지 따지면 우선순위는 조금 낮아요."
        return f"이 집은 {fit}이라면 볼 수 있지만, 핵심 조건에서 강하게 앞서지는 않아요."

    fit = fit_phrase()
    summary = summary_sentence()
    final_line = final_judgment_sentence()

    detail_parts = build_strength_sentences()
    detail_parts.append(caution_sentence())
    detail_parts.append(final_line)
    detail = " ".join(detail_parts)
    return summary, detail


def _deterministic_display_reason(item: dict) -> str:
    context = item.get("explanation_context", {}) or {}
    rank = int(item.get("rank") or context.get("rank") or 0)
    primary_mode = context.get("primary_mode") or "transit"
    route_status = item.get("route_status") or context.get("route_status")
    strength_keys = context.get("strong_point_keys", []) or []
    weak_keys = context.get("weak_point_keys", []) or []
    living_selected = _has_selected_living_preferences(item)
    living_matches = item.get("living_matches") or []
    living_references = item.get("living_reference_tags") or []
    has_living_merit = living_selected and bool(living_matches or living_references)

    def weakest_key() -> str:
        return str(weak_keys[0]) if weak_keys else ""

    def top_strength() -> str:
        return str(strength_keys[0]) if strength_keys else ""

    weak_key = weakest_key()
    strong_key = top_strength()

    if route_status in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        if rank <= 1:
            return "직장/학교와 가까운 점이 가장 뚜렷해서 가장 먼저 볼 만해요."
        if rank == 2:
            return "가까운 거리 자체는 분명한 장점이라 상위 후보와 함께 비교해볼 만해요."
        if rank == 3:
            return "이동 부담을 줄이는 장점은 분명하지만 다른 조건까지 함께 보면 한 단계 더 비교해볼 만해요."
        if rank == 4:
            return "직주 거리 메리트는 있지만 다른 조건까지 함께 보면 뒤쪽으로 두고 볼 만해요."
        return "거리 장점은 남아 있지만 뒤쪽 후보로 비교하기 좋아요."

    if rank <= 1:
        if has_living_merit:
            return "통근 조건에 생활 편의까지 함께 보기 좋아 가장 먼저 볼 만해요."
        if strong_key == "budget":
            return "예산과 통근 조건의 균형이 가장 안정적이라 가장 먼저 볼 만해요."
        if strong_key == "commute":
            return "이동 부담을 안정적으로 관리하기 좋아 가장 먼저 볼 만해요."
        return "전체 조건의 균형이 가장 안정적이라 가장 먼저 볼 만해요."

    if rank <= 3:
        if weak_key == "budget":
            return "기본 조건은 잘 맞지만 상위 후보보다 예산 여유는 적은 편이에요." if rank == 2 else "예산 쪽 여유가 상위 후보보다 덜해 한 단계 더 비교해보면 좋아요."
        if weak_key == "commute":
            return "예산이나 다른 조건은 괜찮지만 상위 후보보다 통근 여유는 덜한 편이에요." if rank == 2 else "통근 여유에서는 상위 후보보다 한 단계 아쉬움이 있어 추가 비교해보면 좋아요."
        if weak_key == "walking":
            return "전반적인 조건은 괜찮지만 상위 후보보다 도보 이동 부담은 조금 더 있는 편이에요." if rank == 2 else "기본 조건은 맞지만 도보 이동 부담까지 보면 조금 더 비교해보면 좋아요."
        if weak_key == "transfer":
            return "기본 조건은 맞지만 상위 후보보다 이동 동선이 조금 더 복잡한 편이에요." if rank == 2 else "이동 동선의 단순함에서는 상위 후보보다 한 단계 아쉬움이 있어요."
        if weak_key == "transit_access":
            return "통근 자체는 가능하지만 상위 후보보다 대중교통 접근 체감은 덜 편한 편이에요." if rank == 2 else "대중교통 접근 체감까지 따지면 상위 후보보다 조금 더 비교해보면 좋아요."
        if weak_key == "subway":
            return "기본 조건은 맞지만 이동 방식의 편의성에서는 상위 후보보다 호불호가 있을 수 있어요." if rank == 2 else "이동 방식의 취향까지 고려하면 상위 후보보다 한 단계 뒤에 두기 좋아요."
        if primary_mode == "car":
            return "자동차 이동 기준으로는 볼 만하지만 상위 후보보다 강점이 덜 뚜렷해요." if rank == 2 else "자동차 이동 조건은 맞지만 한 단계 더 비교해보면 좋아요."
        return "기본 조건은 잘 맞지만 상위 후보보다 강점이 덜 뚜렷해요." if rank == 2 else "전체 조건은 무난하지만 상위 후보 다음 순서로 검토해보면 좋아요."

    if weak_key == "budget":
        return "기본 조건은 충족하지만 상위 후보보다 예산 여유가 적어 뒤쪽으로 두고 볼 만해요." if rank == 4 else "예산 여유까지 고려하면 뒤쪽으로 비교해보면 좋아요."
    if weak_key == "commute":
        return "예산이나 다른 조건은 볼 만하지만 통근 여유는 상위 후보보다 약한 편이에요." if rank == 4 else "통근 여유를 더 중시한다면 뒤쪽으로 비교해보면 좋아요."
    if weak_key == "walking":
        return "이동 부담을 더 줄이고 싶다면 상위 후보를 먼저 보는 편이 좋아요." if rank == 4 else "도보 이동 부담까지 따지면 뒤쪽으로 비교해보면 좋아요."
    if weak_key == "transfer":
        return "조건에 따라 볼 수는 있지만 이동 동선의 단순함은 상위 후보보다 약한 편이에요." if rank == 4 else "이동 동선의 단순함을 중시하면 뒤쪽으로 비교해보면 좋아요."
    if weak_key == "transit_access":
        return "기본 조건은 맞지만 접근 동선까지 고려하면 우선순위는 조금 낮아요." if rank == 4 else "접근 동선까지 함께 보면 뒤쪽으로 비교해보면 좋아요."
    if weak_key == "subway":
        return "이동 방식의 취향까지 따지면 상위 후보보다 우선순위가 낮을 수 있어요." if rank == 4 else "이동 방식의 취향까지 고려하면 뒤쪽으로 비교해보면 좋아요."
    if has_living_merit:
        return "생활 편의까지 함께 보면 의미는 있지만 전체 우선순위는 조금 낮아요." if rank == 4 else "생활 편의 장점은 있지만 뒤쪽으로 비교해보면 좋아요."
    if primary_mode == "car":
        return "자동차 이동 기준으로는 검토할 수 있지만 우선순위는 조금 낮아요." if rank == 4 else "자동차 이동 기준으로도 뒤쪽으로 비교해보면 좋아요."
    return "조건에 따라 검토할 수는 있지만 우선순위는 조금 낮아요." if rank == 4 else "조건에 따라 볼 수는 있지만 뒤쪽으로 비교해보면 좋아요."


def _safe_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def has_final_consonant(word: str) -> bool:
    text = str(word or "").strip()
    if not text:
        return False
    ch = text[-1]
    code = ord(ch)
    if code < 0xAC00 or code > 0xD7A3:
        return False
    return (code - 0xAC00) % 28 != 0


def topic_josa(word: str) -> str:
    return "은" if has_final_consonant(word) else "는"


def subject_label(word: str) -> str:
    text = str(word or "").strip()
    if not text:
        return ""
    return f"{text}{topic_josa(text)}"


def _preferred_transfer_count(item: dict, metrics: dict | None = None) -> float | None:
    candidates = [
        item.get("display_transfer_count") if isinstance(item, dict) else None,
        item.get("transfer_count") if isinstance(item, dict) else None,
        (metrics or {}).get("transfer_count") if isinstance(metrics, dict) else None,
    ]
    for value in candidates:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _commute_margin_level(commute_usage_ratio: float | None) -> str:
    if commute_usage_ratio is None:
        return "unknown"
    if commute_usage_ratio <= 0.33:
        return "huge_margin"
    if commute_usage_ratio <= 0.50:
        return "large_margin"
    if commute_usage_ratio <= 0.70:
        return "moderate_margin"
    if commute_usage_ratio <= 1.00:
        return "low_margin"
    return "over_limit"


def _money_margin_level(usage_ratio: float | None) -> str:
    if usage_ratio is None:
        return "unknown"
    if usage_ratio <= 0.45:
        return "very_light"
    if usage_ratio <= 0.65:
        return "light"
    if usage_ratio <= 0.85:
        return "moderate"
    if usage_ratio <= 1.00:
        return "tight"
    return "over_budget"


def _avg_speed_kmh(distance_km, duration_min) -> float | None:
    distance = _safe_float(distance_km)
    minutes = _safe_float(duration_min)
    if distance is None or minutes is None or minutes <= 0:
        return None
    return distance / (minutes / 60.0)


def _car_reason_key_and_text(item: dict) -> tuple[str, str]:
    context = item.get("explanation_context", {}) or {}
    rank = int(item.get("rank") or context.get("rank") or 0)
    metrics = context.get("primary_metrics", {}) or {}
    budget = context.get("budget", {}) or {}
    constraints = context.get("constraints", {}) or {}
    rank1 = context.get("rank1_reference", {}) or {}
    prev = context.get("prev_reference", {}) or {}
    deal_type = str(item.get("deal_type") or budget.get("deal_type") or "")

    car_time_band = str(item.get("car_time_band") or metrics.get("car_time_band") or "unknown")
    car_distance_band = str(item.get("car_distance_band") or metrics.get("car_distance_band") or "unknown")
    budget_band = str(item.get("budget_band") or budget.get("budget_band") or "unknown")
    duration_min = _safe_float(item.get("duration_min"))
    max_commute_minutes = _safe_float(constraints.get("max_commute_minutes"))
    commute_usage_ratio = _safe_float(item.get("commute_usage_ratio"))
    if commute_usage_ratio is None and duration_min is not None and max_commute_minutes:
        commute_usage_ratio = duration_min / max_commute_minutes
    commute_margin_ratio = _safe_float(item.get("commute_margin_ratio") or metrics.get("commute_margin_ratio"))

    rent_usage_ratio = _safe_float(item.get("rent_usage_ratio") or budget.get("rent_usage_ratio"))
    deposit_usage_ratio = _safe_float(item.get("deposit_usage_ratio") or budget.get("deposit_usage_ratio"))
    rent_margin_ratio = _safe_float(item.get("rent_margin_ratio") or budget.get("rent_margin_ratio"))
    deposit_margin_ratio = _safe_float(item.get("deposit_margin_ratio") or budget.get("deposit_margin_ratio"))

    monthly_rent = _safe_float(item.get("monthly_rent_manwon"))
    prev_rent = _safe_float(prev.get("monthly_rent_manwon"))
    prev_duration_min = _safe_float(prev.get("duration_min"))
    prev_rent_usage_ratio = _safe_float(prev.get("rent_usage_ratio"))
    prev_deposit_usage_ratio = _safe_float(prev.get("deposit_usage_ratio"))
    prev_commute_usage_ratio = _safe_float(prev.get("commute_usage_ratio"))
    prev_commute_margin = _safe_float(prev.get("commute_margin_ratio"))
    rank1_duration_min = _safe_float(rank1.get("duration_min"))
    rank1_commute_usage_ratio = _safe_float(rank1.get("commute_usage_ratio"))
    rank1_commute_margin = _safe_float(rank1.get("commute_margin_ratio"))
    rank1_deposit_margin = _safe_float(rank1.get("deposit_margin_ratio"))
    rank1_rent_margin = _safe_float(rank1.get("rent_margin_ratio"))

    area_pyeong = _safe_float(item.get("area_pyeong"))
    prev_area_pyeong = _safe_float(prev.get("area_pyeong"))
    built_year = _safe_float(item.get("built_year"))
    prev_built_year = _safe_float(prev.get("built_year"))
    rank1_built_year = _safe_float(rank1.get("built_year"))
    if area_pyeong is None:
        area_sqm = _safe_float(item.get("area_sqm"))
        area_pyeong = area_sqm / 3.3058 if area_sqm else None
    if prev_area_pyeong is None:
        prev_area_sqm = _safe_float(prev.get("area_sqm"))
        prev_area_pyeong = prev_area_sqm / 3.3058 if prev_area_sqm else None

    distance_km = _safe_float(item.get("distance_km"))
    prev_distance_km = _safe_float(prev.get("distance_km"))
    avg_speed_kmh = _avg_speed_kmh(distance_km, duration_min)
    prev_avg_speed_kmh = _avg_speed_kmh(prev_distance_km, prev_duration_min)

    if prev_commute_usage_ratio is None and prev_duration_min is not None and max_commute_minutes:
        prev_commute_usage_ratio = prev_duration_min / max_commute_minutes
    if rank1_commute_usage_ratio is None and rank1_duration_min is not None and max_commute_minutes:
        rank1_commute_usage_ratio = rank1_duration_min / max_commute_minutes

    commute_margin_level = _commute_margin_level(commute_usage_ratio)
    rent_margin_level = _money_margin_level(rent_usage_ratio)
    deposit_margin_level = _money_margin_level(deposit_usage_ratio)

    item["commute_usage_ratio"] = commute_usage_ratio
    item["rent_usage_ratio"] = rent_usage_ratio
    item["deposit_usage_ratio"] = deposit_usage_ratio
    item["commute_margin_level"] = commute_margin_level
    item["rent_margin_level"] = rent_margin_level
    item["deposit_margin_level"] = deposit_margin_level
    item["avg_speed_kmh"] = avg_speed_kmh

    same_time_band_as_rank1 = car_time_band == str(rank1.get("car_time_band") or "")
    same_time_band_as_prev = car_time_band == str(prev.get("car_time_band") or "")
    same_distance_band_as_rank1 = car_distance_band == str(rank1.get("car_distance_band") or "")
    rent_better_than_prev = monthly_rent is not None and prev_rent is not None and monthly_rent < prev_rent
    rent_worse_than_prev = monthly_rent is not None and prev_rent is not None and monthly_rent > prev_rent
    rent_margin_good = rent_margin_ratio is not None and rent_margin_ratio >= 0.2
    deposit_margin_good = deposit_margin_ratio is not None and deposit_margin_ratio >= 0.2
    budget_light = budget_band in {"very_light", "light"}
    weaker_than_rank1_on_commute = (
        commute_margin_ratio is not None and rank1_commute_margin is not None and commute_margin_ratio + 0.05 < rank1_commute_margin
    )
    weaker_than_rank1_on_budget = (
        ((deposit_margin_ratio is not None and rank1_deposit_margin is not None and deposit_margin_ratio + 0.08 < rank1_deposit_margin))
        or ((rent_margin_ratio is not None and rank1_rent_margin is not None and rent_margin_ratio + 0.08 < rank1_rent_margin))
        or rent_margin_level == "tight"
        or deposit_margin_level == "tight"
    )
    same_commute_margin_level_as_prev = commute_margin_level == _commute_margin_level((1 - prev_commute_margin) if prev_commute_margin is not None else None)
    better_commute_than_prev = (
        commute_usage_ratio is not None and prev_commute_usage_ratio is not None and commute_usage_ratio + 0.03 < prev_commute_usage_ratio
    )
    better_commute_than_rank1 = (
        commute_usage_ratio is not None and rank1_commute_usage_ratio is not None and commute_usage_ratio + 0.03 < rank1_commute_usage_ratio
    )
    commute_clearly_worse_than_prev = (
        commute_usage_ratio is not None and prev_commute_usage_ratio is not None and commute_usage_ratio > prev_commute_usage_ratio + 0.08
    )
    rent_margin_clearly_better = (
        rent_usage_ratio is not None and prev_rent_usage_ratio is not None and rent_usage_ratio + 0.15 < prev_rent_usage_ratio
    )
    rent_margin_clearly_worse = (
        rent_usage_ratio is not None and prev_rent_usage_ratio is not None and rent_usage_ratio > prev_rent_usage_ratio + 0.15
    )
    deposit_margin_clearly_better = (
        deposit_usage_ratio is not None and prev_deposit_usage_ratio is not None and deposit_usage_ratio + 0.08 < prev_deposit_usage_ratio
    )
    deposit_margin_clearly_worse = (
        deposit_usage_ratio is not None and prev_deposit_usage_ratio is not None and deposit_usage_ratio > prev_deposit_usage_ratio + 0.08
    )
    similar_budget_to_prev = (
        rent_usage_ratio is not None and prev_rent_usage_ratio is not None and abs(rent_usage_ratio - prev_rent_usage_ratio) <= 0.15
        and deposit_usage_ratio is not None and prev_deposit_usage_ratio is not None and abs(deposit_usage_ratio - prev_deposit_usage_ratio) <= 0.18
    )
    area_clearly_better = area_pyeong is not None and prev_area_pyeong is not None and area_pyeong >= prev_area_pyeong + 1.0
    longer_distance_but_efficient = (
        distance_km is not None and prev_distance_km is not None and distance_km >= prev_distance_km + 0.3
        and avg_speed_kmh is not None and prev_avg_speed_kmh is not None and avg_speed_kmh >= prev_avg_speed_kmh + 1.5
    )
    budget_clearly_better_than_prev = rent_margin_clearly_better or deposit_margin_clearly_better
    budget_clearly_worse_than_prev = (
        rent_margin_clearly_worse or deposit_margin_clearly_worse or rent_margin_level == "tight" or deposit_margin_level == "tight"
    )
    better_area_but_budget_tight = area_clearly_better and budget_clearly_worse_than_prev
    newer_than_prev = built_year is not None and prev_built_year is not None and built_year >= prev_built_year + 5
    older_than_prev = built_year is not None and prev_built_year is not None and built_year <= prev_built_year - 5
    newer_than_rank1 = built_year is not None and rank1_built_year is not None and built_year >= rank1_built_year + 5
    is_jeonse = deal_type == "전세" or ((item.get("monthly_rent_manwon") or 0) <= 0)

    max_commute_text = f"\ucd5c\ub300 {int(max_commute_minutes)}\ubd84" if max_commute_minutes else "\uc124\uc815\ud55c \ud1b5\uadfc\uc2dc\uac04"

    def _commute_phrase(usage_ratio: float | None) -> str:
        if usage_ratio is None:
            return "\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 \ud5c8\uc6a9 \ubc94\uc704 \uc548\uc5d0 \ub4e4\uc5b4\uc640\uc694."
        if usage_ratio <= 0.33:
            return f"\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 {max_commute_text}\ubcf4\ub2e4 \ucda9\ubd84\ud788 \uc9e7\uc544\uc694."
        if usage_ratio <= 0.5:
            return f"\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 {max_commute_text}\ubcf4\ub2e4 \uc5ec\uc720 \uc788\ub294 \ud3b8\uc774\uc5d0\uc694."
        if usage_ratio <= 0.7:
            return f"\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 {max_commute_text} \uc548\uc5d0\uc11c \ubb34\ub9ac \uc5c6\ub294 \uc218\uc900\uc774\uc5d0\uc694."
        return f"\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 {max_commute_text}\uc5d0 \ube44\uad50\uc801 \uac00\uae4c\uc6b4 \ud3b8\uc774\uc5d0\uc694."

    def _budget_phrase(rent_level: str, deposit_level: str) -> str:
        if rent_level in {"very_light", "light"} and deposit_level in {"very_light", "light"}:
            return "\uc6d4\uc138\uc640 \ubcf4\uc99d\uae08\ub3c4 \ubaa8\ub450 \uc608\uc0b0 \uc0c1\ud55c\ubcf4\ub2e4 \uc5ec\uc720 \uc788\uc5b4\uc694."
        if rent_level == "tight" and deposit_level in {"very_light", "light"}:
            return "\uc6d4\uc138\ub294 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae5d\uc9c0\ub9cc \ubcf4\uc99d\uae08 \ubd80\ub2f4\uc740 \ub0ae\uc740 \ud3b8\uc774\uc5d0\uc694."
        if deposit_level == "tight" and rent_level in {"very_light", "light"}:
            return "\ubcf4\uc99d\uae08\uc740 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae5d\uc9c0\ub9cc \uc6d4\uc138 \ubd80\ub2f4\uc740 \ub0ae\uc740 \ud3b8\uc774\uc5d0\uc694."
        if rent_level == "tight" and deposit_level == "tight":
            return "\uc6d4\uc138\uc640 \ubcf4\uc99d\uae08\uc774 \ubaa8\ub450 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6b4 \ud3b8\uc774\uc5d0\uc694."
        return "\uc6d4\uc138\uc640 \ubcf4\uc99d\uae08\uc740 \ubaa8\ub450 \uac10\ub2f9 \uac00\ub2a5\ud55c \ubc94\uc704\uc608\uc694."

    def _area_phrase() -> str | None:
        if area_pyeong is None:
            return None
        rounded = round(area_pyeong, 1)
        rounded_text = f"{int(rounded)}\ud3c9" if rounded.is_integer() else f"{rounded:.1f}\ud3c9"
        return f"\uba74\uc801\uc740 \uc57d {rounded_text}\uc608\uc694"

    def _build_phrase() -> str | None:
        if built_year is None:
            return None
        return f"\uc900\uacf5\uc5f0\ub3c4\ub294 {int(built_year)}\ub144\uc73c\ub85c \ube44\uad50\uc801 \ucd5c\uadfc \ub9e4\ubb3c\uc5d0 \uac00\uae4c\uc6b4 \ud3b8\uc774\uc5d0\uc694"

    if is_jeonse:
        deposit_head = (
            "\uc804\uc138\uae08\uc774 \uc608\uc0b0 \uc0c1\ud55c\ubcf4\ub2e4 \uc5ec\uc720 \uc788\ub294 \ud3b8\uc774\uace0"
            if deposit_margin_level in {"very_light", "light"}
            else "\uc804\uc138\uae08\uc740 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6b4 \ud3b8\uc774\uace0"
        )
        commute_head = _commute_phrase(commute_usage_ratio).rstrip(".")
        area_head = _area_phrase()
        build_head = _build_phrase()

        if rank == 1:
            first_bits = [f"\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 {max_commute_text}\ubcf4\ub2e4 \ucda9\ubd84\ud788 \uc9e7\uace0", "\uc804\uc138\uae08\ub3c4 \uc608\uc0b0 \uc0c1\ud55c\ubcf4\ub2e4 \uc5ec\uc720 \uc788\uc5b4\uc694" if deposit_margin_level in {"very_light", "light"} else "\uc804\uc138\uae08\uc740 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6b4 \ud3b8\uc774\uc5d0\uc694"]
            if build_head:
                first_bits.append(build_head.rstrip("."))
            first = ", ".join(first_bits[:3])
            second = "\ud1b5\uadfc\uacfc \uc804\uc138\uae08 \ubd80\ub2f4\uc744 \ud568\uaed8 \ubcf4\uba74 \uade0\ud615\uc774 \uac00\uc7a5 \uc88b\uc544 1\uc21c\uc704\ub85c \ucd94\ucc9c\ub410\uc5b4\uc694."
            return "jeonse_top_balanced", f"{first}. {second}"
        if better_commute_than_rank1 and deposit_margin_level not in {"very_light", "light"}:
            first = f"{commute_head}, \uc804\uc138\uae08\uc740 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6cc \ucd08\uae30 \ube44\uc6a9 \ubd80\ub2f4\uc774 \ud070 \ud3b8\uc774\uc5d0\uc694"
            second = f"\ud1b5\uadfc \uc5ec\uc720\ub294 1\uc704\ubcf4\ub2e4\ub3c4 \uc88b\uc9c0\ub9cc \uc804\uc138\uae08 \ubd80\ub2f4 \ub54c\ubb38\uc5d0 {rank}\uc21c\uc704\ub85c \ub0b4\ub824\uac14\uc5b4\uc694."
            return "better_commute_but_deposit_tight", f"{first}. {second}"
        if same_commute_margin_level_as_prev and deposit_margin_clearly_better:
            first_bits = ["\uc804\uc138\uae08\uc740 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ubd80\ub2f4\uc774 \ub0ae\uace0", commute_head.replace("\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 ", "\uc790\ub3d9\ucc28 \ud1b5\uadfc\ub3c4 ")]
            first = ", ".join(bit.rstrip(".") for bit in first_bits)
            if newer_than_prev:
                second = f"\uc804\uc138\uae08\uacfc \uc900\uacf5\uc5f0\ub3c4\ub294 \ub354 \ub0ab\uc9c0\ub9cc \uba74\uc801\uc774\ub098 \ub2e4\ub978 \uae30\ubcf8 \uc870\uac74\uae4c\uc9c0 \ud568\uaed8 \ubcf4\uba74 {rank}\uc21c\uc704\uc5d0\uc11c \ube44\uad50\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
                return "jeonse_better_deposit_newer_build", f"{first}. {second}"
            second = f"\uc804\uc138\uae08 \ubd80\ub2f4\uc740 \ub35c\ud558\uc9c0\ub9cc \uba74\uc801\uc774\ub098 \uc900\uacf5\uc5f0\ub3c4\uae4c\uc9c0 \ud569\uce58\uba74 {rank}\uc21c\uc704\uc5d0\uc11c \uac80\ud1a0\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
            return "jeonse_better_deposit", f"{first}. {second}"
        if same_time_band_as_prev and similar_budget_to_prev and area_clearly_better:
            first = f"{commute_head}, {_area_phrase() or '\uba74\uc801\uc740 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub113\uc740 \ud3b8\uc774\uc5d0\uc694'}"
            if newer_than_prev:
                second = f"\ud3c9\uc218\uc640 \uc900\uacf5\uc5f0\ub3c4\uc5d0\uc11c\ub294 \uc7a5\uc810\uc774 \uc788\uc9c0\ub9cc \uc804\uc138\uae08 \uc218\uc900\uacfc \ud1b5\uadfc \uade0\ud615\uc740 \uc55e\uc120 \ud6c4\ubcf4\uac00 \uc870\uae08 \ub354 \uc88b\uc544 {rank}\uc21c\uc704\uc5d0 \ubc30\uce58\ub410\uc5b4\uc694."
                return "jeonse_larger_area_newer_build", f"{first}. {second}"
            second = f"\ud3c9\uc218\ub294 \ub354 \uc88b\uc9c0\ub9cc \uc804\uc138\uae08\uacfc \ud1b5\uadfc \uade0\ud615\uc740 \uc55e\uc120 \ud6c4\ubcf4\uac00 \uc870\uae08 \ub354 \uc88b\uc544 {rank}\uc21c\uc704\uc5d0 \ubc30\uce58\ub410\uc5b4\uc694."
            return "jeonse_same_budget_larger_area", f"{first}. {second}"
        if newer_than_prev and not deposit_margin_clearly_worse:
            first_bits = [commute_head, build_head.rstrip(".") if build_head else None, deposit_head]
            first = ", ".join(bit for bit in first_bits if bit)[:200]
            second = f"\uc900\uacf5\uc5f0\ub3c4\ub294 \uc7a5\uc810\uc774\uc9c0\ub9cc \uc804\uc138\uae08\uacfc \ud1b5\uadfc \uc870\uac74\uc744 \ud568\uaed8 \ubcf4\uba74 {rank}\uc21c\uc704\uc5d0\uc11c \ube44\uad50\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
            return "jeonse_newer_build", f"{first}. {second}"
        if older_than_prev and deposit_margin_level not in {"very_light", "light"}:
            first = f"{commute_head}, \uc804\uc138\uae08\ub3c4 \uc5ec\uc720\uac00 \ud06c\uc9c0 \uc54a\uace0 \uc900\uacf5\uc5f0\ub3c4\ub3c4 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \uc624\ub798\ub41c \ud3b8\uc774\uc5d0\uc694"
            second = f"\uae30\ubcf8 \uc870\uac74\uc740 \ucda9\uc871\ud558\uc9c0\ub9cc \uc804\uc138\uae08\uacfc \uc5f0\uc2dd\uc5d0\uc11c \uc55e\uc120 \ud6c4\ubcf4\ubcf4\ub2e4 \ubc00\ub824 {rank}\uc21c\uc704\ub85c \ub0b4\ub824\uac14\uc5b4\uc694."
            return "jeonse_older_build_and_deposit_weaker", f"{first}. {second}"
        first_bits = [commute_head, deposit_head]
        if area_head:
            first_bits.append(area_head.rstrip("."))
        elif build_head:
            first_bits.append(build_head.rstrip("."))
        first = ", ".join(first_bits[:3])
        if area_pyeong is not None and area_pyeong < 14:
            second = f"\ud1b5\uadfc\uc740 \ubb34\ub09c\ud558\uc9c0\ub9cc \uc804\uc138\uae08 \uba54\ub9ac\ud2b8\uac00 \ud06c\uc9c0 \uc54a\uace0 \ud3c9\uc218\ub3c4 \uc791\uc740 \ud3b8\uc774\ub77c {rank}\uc21c\uc704\ub85c \ubc00\ub9b0 \ud6c4\ubcf4\uc608\uc694."
        elif deposit_margin_level not in {"very_light", "light"}:
            second = f"\ud3c9\uc218\ub294 \uad1c\ucc2e\uc9c0\ub9cc \uc804\uc138\uae08\uc774 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ube60\ub4ef\ud55c \ud3b8\uc774\ub77c {rank}\uc21c\uc704\uc5d0\uc11c \uac80\ud1a0\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
        else:
            second = f"\uc804\uc138\uae08, \ud1b5\uadfc, \ud3c9\uc218 \uc870\uac74\uc740 \ubb34\ub09c\ud558\uc9c0\ub9cc \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub6f0\ub294 \uc6b0\uc704\uac00 \uc791\uc544 {rank}\uc21c\uc704\uc5d0\uc11c \uac80\ud1a0\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
        return "jeonse_balanced", f"{first}. {second}"

    if rank == 1 and commute_margin_level == "huge_margin" and rent_margin_level in {"very_light", "light"} and deposit_margin_level in {"very_light", "light"}:
        first = f"\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 {max_commute_text}\ubcf4\ub2e4 \ucda9\ubd84\ud788 \uc9e7\uace0, \uc6d4\uc138\uc640 \ubcf4\uc99d\uae08\ub3c4 \ubaa8\ub450 \uc608\uc0b0 \uc0c1\ud55c\ubcf4\ub2e4 \uc5ec\uc720 \uc788\uc5b4\uc694"
        second = "\ud1b5\uadfc\uacfc \uc608\uc0b0\uc744 \ub3d9\uc2dc\uc5d0 \ub9cc\uc871\ud558\ub294 \uade0\ud615\uc774 \uac00\uc7a5 \uc88b\uc544 1\uc21c\uc704\ub85c \ucd94\ucc9c\ub410\uc5b4\uc694."
        return "huge_commute_margin_budget_light", f"{first}. {second}"
    if better_commute_than_rank1 and rent_margin_level == "tight":
        first = f"\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 {max_commute_text}\ubcf4\ub2e4 \ucda9\ubd84\ud788 \uc9e7\uace0, \uc6d4\uc138\ub294 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6cc \ub9e4\ub2ec \ubd80\ub2f4\uc774 \ud070 \ud3b8\uc774\uc5d0\uc694"
        second = f"\ud1b5\uadfc \uc5ec\uc720\ub294 1\uc704\ubcf4\ub2e4\ub3c4 \uc88b\uc9c0\ub9cc \ube44\uc6a9 \ubd80\ub2f4\uc774 \ub354 \ucee4\uc11c {rank}\uc21c\uc704\ub85c \ub0b4\ub824\uac14\uc5b4\uc694."
        return "better_commute_but_rent_tight", f"{first}. {second}"
    if better_commute_than_rank1 and deposit_margin_level == "tight":
        first = f"{_commute_phrase(commute_usage_ratio)} \ubcf4\uc99d\uae08\uc740 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6cc \ucd08\uae30 \ube44\uc6a9 \ubd80\ub2f4\uc774 \ud070 \ud3b8\uc774\uc5d0\uc694."
        second = f"\ud1b5\uadfc \uc5ec\uc720\ub294 1\uc704\ubcf4\ub2e4\ub3c4 \uc88b\uc9c0\ub9cc \ubcf4\uc99d\uae08 \ubd80\ub2f4 \ub54c\ubb38\uc5d0 {rank}\uc21c\uc704\ub85c \ubc00\ub9b0 \ud6c4\ubcf4\uc608\uc694."
        return "better_commute_but_deposit_tight", f"{first} {second}"
    if better_commute_than_prev and rent_margin_level == "tight":
        first = f"{_commute_phrase(commute_usage_ratio)} \uc6d4\uc138\ub294 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6cc \ub9e4\ub2ec \ubd80\ub2f4\uc774 \ub354 \ucee4\uc9c8 \uc218 \uc788\uc5b4\uc694."
        second = f"\ud1b5\uadfc\uc740 \ubc14\ub85c \uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub0ab\uc9c0\ub9cc \uc6d4\uc138 \ubd80\ub2f4 \ub54c\ubb38\uc5d0 {rank}\uc21c\uc704\uc5d0 \uba38\ubb3c \ud6c4\ubcf4\uc608\uc694."
        return "better_commute_but_rent_tight", f"{first}. {second}"
    if better_commute_than_prev and deposit_margin_level == "tight":
        first = f"{_commute_phrase(commute_usage_ratio)} \ubcf4\uc99d\uae08\uc740 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6cc \ucd08\uae30 \ube44\uc6a9 \ubd80\ub2f4\uc774 \ub354 \ucee4\uc9c8 \uc218 \uc788\uc5b4\uc694."
        second = f"\ud1b5\uadfc\uc740 \ubc14\ub85c \uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub0ab\uc9c0\ub9cc \ubcf4\uc99d\uae08 \ubd80\ub2f4 \ub54c\ubb38\uc5d0 {rank}\uc21c\uc704\uc5d0 \uba38\ubb3c \ud6c4\ubcf4\uc608\uc694."
        return "better_commute_but_deposit_tight", f"{first} {second}"
    if better_area_but_budget_tight:
        first = f"{_area_phrase() or '\uba74\uc801\uc740 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub113\uc740 \ud3b8\uc774\uc5d0\uc694.'} \uc6d4\uc138\ub098 \ubcf4\uc99d\uae08\uc740 \ub354 \ub192\uc740 \ud3b8\uc774\uc5d0\uc694."
        second = f"\uacf5\uac04\uc740 \uac15\uc810\uc774\uc9c0\ub9cc \uc608\uc0b0 \ubd80\ub2f4\uc774 \ub354 \ucee4\uc11c {rank}\uc21c\uc704\ub85c \ubc00\ub9b0 \ud6c4\ubcf4\uc608\uc694."
        return "better_area_but_budget_tight", f"{first} {second}"
    if budget_clearly_better_than_prev and commute_clearly_worse_than_prev:
        first = f"{_budget_phrase(rent_margin_level, deposit_margin_level)} \ub2e4\ub9cc \uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \uc5ec\uc720\uac00 \uc801\uc5b4\uc694."
        second = f"\uc608\uc0b0 \uad6c\uc870\ub294 \ub354 \ub0ab\uc9c0\ub9cc \ud1b5\uadfc \uc870\uac74\uc5d0\uc11c \ubc00\ub824 {rank}\uc21c\uc704\uc5d0 \ubc30\uce58\ub41c \ud6c4\ubcf4\uc608\uc694."
        return "better_budget_but_commute_weaker", f"{first} {second}"
    if same_commute_margin_level_as_prev and rent_margin_clearly_better:
        first = f"\uc6d4\uc138\ub294 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \uc5ec\uc720 \uc788\uace0, {_commute_phrase(commute_usage_ratio).replace('\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 ', '\uc790\ub3d9\ucc28 \ud1b5\uadfc\ub3c4 ').rstrip('.')}"
        if deposit_margin_clearly_worse:
            second = "\ub2e4\ub9cc \ubcf4\uc99d\uae08\uc774 \ub354 \ub192\uc544 \ucd08\uae30 \ube44\uc6a9\uc5d0\uc11c \ucc28\uc774\uac00 \ub098\uc11c, \uc608\uc0b0 \uad6c\uc870\ub97c \uc5b4\ub5bb\uac8c \ubcf4\ub290\ub0d0\uc5d0 \ub530\ub77c \ube44\uad50\ud560 \ub9cc\ud55c \ud6c4\ubcf4\uc608\uc694."
        else:
            second = "\ud1b5\uadfc \uc870\uac74\ub3c4 \uc548\uc815\uc801\uc774\ub77c \uc6d4\uc138 \ubd80\ub2f4\uc744 \ub354 \uc904\uc774\uace0 \uc2f6\uc744 \ub54c \ube44\uad50\ud574\ubcfc \ub9cc\ud55c \ud6c4\ubcf4\uc608\uc694."
        return "same_commute_margin_better_rent", f"{first}. {second}"
    if same_commute_margin_level_as_prev and deposit_margin_clearly_better:
        first = f"\ubcf4\uc99d\uae08\uc740 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ubd80\ub2f4\uc774 \ub0ae\uace0, {_commute_phrase(commute_usage_ratio).replace('\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 ', '\uc790\ub3d9\ucc28 \ud1b5\uadfc\ub3c4 ').rstrip('.')}"
        second = f"\ucd08\uae30 \ube44\uc6a9\uc740 \ub35c \ub4e4\uc9c0\ub9cc \uc6d4\uc138\ub098 \uacf5\uac04 \uc870\uac74\uae4c\uc9c0 \ubcf4\uba74 {rank}\uc21c\uc704\uc5d0\uc11c \uac80\ud1a0\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
        return "same_commute_margin_better_deposit", f"{first}. {second}"
    if same_commute_margin_level_as_prev and deposit_margin_clearly_worse:
        first = f"{_commute_phrase(commute_usage_ratio)} \ubcf4\uc99d\uae08\uc740 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub354 \ub192\uc544 \ucd08\uae30 \ube44\uc6a9 \ubd80\ub2f4\uc774 \ucee4\uc694."
        second = f"\ud1b5\uadfc \uc870\uac74\uc740 \ube44\uc2b7\ud558\uc9c0\ub9cc \ubcf4\uc99d\uae08 \ucc28\uc774 \ub54c\ubb38\uc5d0 {rank}\uc21c\uc704\ub85c \ubc00\ub9b0 \ud6c4\ubcf4\uc608\uc694."
        return "same_commute_margin_worse_deposit", f"{first} {second}"
    if rent_margin_level in {"very_light", "light"} and deposit_margin_level == "tight":
        first = f"\uc6d4\uc138\ub294 \uc5ec\uc720 \uc788\uc9c0\ub9cc \ubcf4\uc99d\uae08\uc740 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6cc \ucd08\uae30 \ube44\uc6a9 \ubd80\ub2f4\uc774 \ucee4\uc694. {_commute_phrase(commute_usage_ratio)}"
        second = f"\uc6d4\uc138 \uc870\uac74\uc740 \uad1c\ucc2e\uc9c0\ub9cc \ubcf4\uc99d\uae08 \uc81c\uc57d \ub54c\ubb38\uc5d0 {rank}\uc21c\uc704\ub85c \ub0b4\ub824\uc628 \ud6c4\ubcf4\uc608\uc694."
        return "rent_light_deposit_tight", f"{first} {second}"
    if rent_margin_level == "tight" and deposit_margin_level in {"very_light", "light"}:
        first = f"\ubcf4\uc99d\uae08 \ubd80\ub2f4\uc740 \ub0ae\uc9c0\ub9cc \uc6d4\uc138\ub294 \uc608\uc0b0 \uc0c1\ud55c\uc5d0 \uac00\uae4c\uc6cc \ub9e4\ub2ec \uc9c0\ucd9c\uc740 \ud070 \ud3b8\uc774\uc5d0\uc694. {_commute_phrase(commute_usage_ratio)}"
        second = f"\ucd08\uae30 \ube44\uc6a9\uc740 \ub35c \ub4e4\uc9c0\ub9cc \uc6d4\uc138 \ubd80\ub2f4 \ub54c\ubb38\uc5d0 {rank}\uc21c\uc704\uc5d0\uc11c \ube44\uad50\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
        return "rent_tight_deposit_light", f"{first} {second}"
    if commute_margin_level == "moderate_margin" and budget_light:
        first = f"{_commute_phrase(commute_usage_ratio)} {_budget_phrase(rent_margin_level, deposit_margin_level)}"
        second = f"\uc608\uc0b0 \uc870\uac74\uc740 \uc88b\uc9c0\ub9cc \ud1b5\uadfc \uc5ec\uc720\uac00 \uc0c1\uc704 \ud6c4\ubcf4\ub9cc\ud07c \ud06c\uc9c0 \uc54a\uc544 {rank}\uc21c\uc704\uc5d0 \ub193\uc778 \ud6c4\ubcf4\uc608\uc694."
        return "moderate_commute_margin_budget_light", f"{first} {second}"
    if commute_margin_level == "low_margin" and budget_light:
        first = f"\uc6d4\uc138\uc640 \ubcf4\uc99d\uae08 \ubd80\ub2f4\uc740 \ub0ae\uc9c0\ub9cc \uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 {max_commute_text}\uc5d0 \ub354 \uac00\uae4c\uc6b4 \ud3b8\uc774\uc5d0\uc694."
        second = f"\uc608\uc0b0 \uba54\ub9ac\ud2b8\ub294 \ubd84\uba85\ud558\uc9c0\ub9cc \ud1b5\uadfc \uc5ec\uc720\uac00 \uc791\uc544 {rank}\uc21c\uc704\uc5d0\uc11c \uac80\ud1a0\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
        return "low_commute_margin_budget_light", f"{first} {second}"
    if same_time_band_as_prev and similar_budget_to_prev and area_clearly_better:
        first = f"\ud1b5\uadfc\uacfc \uc608\uc0b0 \uc870\uac74\uc740 \uc0c1\uc704 \ud6c4\ubcf4\uc640 \ud070 \ucc28\uc774\uac00 \uc5c6\uc9c0\ub9cc, {_area_phrase() or '\uba74\uc801\uc774 \ub354 \ub113\uc5b4'}"
        second = f"\uacf5\uac04 \uba74\uc5d0\uc11c\ub294 \uc7a5\uc810\uc774 \uc788\uc9c0\ub9cc \uae30\ubcf8 \uc870\uac74\uc758 \uade0\ud615\uc740 \uc55e\uc120 \ud6c4\ubcf4\uac00 \uc870\uae08 \ub354 \uc88b\uc544 {rank}\uc21c\uc704\uc5d0 \ubc30\uce58\ub410\uc5b4\uc694."
        return "same_budget_larger_area", f"{first}. {second}"
    if commute_margin_level == "huge_margin" and rent_margin_level in {"very_light", "light"} and deposit_margin_level in {"very_light", "light"}:
        first = f"{_commute_phrase(commute_usage_ratio)} {_budget_phrase(rent_margin_level, deposit_margin_level)}"
        second = f"\ud1b5\uadfc\uacfc \uc608\uc0b0 \uc870\uac74\uc740 \uc88b\uc9c0\ub9cc \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub208\uc5d0 \ub744\ub294 \uc6b0\uc704\uac00 \uc801\uc5b4 {rank}\uc21c\uc704\uc5d0 \uba38\ubb3c \ud6c4\ubcf4\uc608\uc694."
        return "huge_commute_margin_budget_light", f"{first}. {second}"
    if longer_distance_but_efficient:
        first = f"\uc8fc\ud589\uac70\ub9ac\ub294 \uc870\uae08 \ub354 \uae38\uc9c0\ub9cc \ud3c9\uade0 \uc774\ub3d9 \ud6a8\uc728\uc740 \uad1c\ucc2e\uace0, {_commute_phrase(commute_usage_ratio).replace('\uc790\ub3d9\ucc28 \ud1b5\uadfc\uc740 ', '\uc790\ub3d9\ucc28 \ud1b5\uadfc\ub3c4 ')}"
        second = f"\uc774\ub3d9 \ud6a8\uc728\uc740 \ub098\uc058\uc9c0 \uc54a\uc9c0\ub9cc \uc608\uc0b0\uc774\ub098 \uacf5\uac04 \uc870\uac74\uae4c\uc9c0 \ud569\uce58\uba74 {rank}\uc21c\uc704\uc5d0\uc11c \ube44\uad50\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
        return "longer_distance_but_efficient_route", f"{first} {second}"
    if same_time_band_as_rank1 and same_distance_band_as_rank1 and not weaker_than_rank1_on_budget and rent_margin_good and deposit_margin_good:
        first = f"{_commute_phrase(commute_usage_ratio)} {_budget_phrase(rent_margin_level, deposit_margin_level)}"
        second = f"\uae30\ubcf8 \uc870\uac74\uc740 \uc804\ubc18\uc801\uc73c\ub85c \uc548\uc815\uc801\uc774\uc9c0\ub9cc \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub3cb\ubcf4\uc774\ub294 \ucc28\uc774\uac00 \uc791\uc544 {rank}\uc21c\uc704\uc5d0 \ub193\uc600\uc5b4\uc694."
        return "balanced_basic", f"{first} {second}"
    if weaker_than_rank1_on_commute and weaker_than_rank1_on_budget:
        first = f"\uc790\ub3d9\ucc28 \ud1b5\uadfc \uc5ec\uc720\uc640 \uc608\uc0b0 \uc5ec\uc720\uac00 \ubaa8\ub450 1\uc704\ubcf4\ub2e4 \uc801\uace0, {_area_phrase() or '\uba74\uc801\uc73c\ub85c \ub9cc\ud68c\ud558\uae30\ub3c4 \uc5b4\ub824\uc6b4 \ud3b8\uc774\uc5d0\uc694.'}"
        second = f"\uae30\ubcf8 \uc870\uac74\uc740 \ucda9\uc871\ud558\uc9c0\ub9cc \ud1b5\uadfc\uacfc \ube44\uc6a9\uc744 \ud568\uaed8 \ubcf4\uba74 {rank}\uc21c\uc704\ub85c \ubc00\ub9b4 \uc218\ubc16\uc5d0 \uc5c6\ub294 \ud6c4\ubcf4\uc608\uc694."
        return "commute_and_budget_weaker", f"{first} {second}"
    if car_distance_band in {"far", "very_far"} and weaker_than_rank1_on_budget:
        first = f"\uc8fc\ud589\uac70\ub9ac\ub3c4 \ub354 \uae38\uace0 \uc6d4\uc138\ub098 \ubcf4\uc99d\uae08 \uc5ec\uc720\ub3c4 \uc881\uc740 \ud3b8\uc774\uc5d0\uc694. {_commute_phrase(commute_usage_ratio)}"
        second = f"\ud1b5\uadfc \uac70\ub9ac\uc640 \uc608\uc0b0 \ubaa8\ub450\uc5d0\uc11c \uc55e\uc120 \ud6c4\ubcf4\ubcf4\ub2e4 \uc57d\ud574 {rank}\uc21c\uc704\ub85c \ub0b4\ub824\uac04 \ud6c4\ubcf4\uc608\uc694."
        return "farther_and_budget_tight", f"{first} {second}"
    if budget_light and rent_better_than_prev and not rent_worse_than_prev:
        first = f"\uc6d4\uc138 \ubd80\ub2f4\uc740 \uc0c1\uc704 \ud6c4\ubcf4\ubcf4\ub2e4 \ub0ae\uace0 {_budget_phrase(rent_margin_level, deposit_margin_level)}"
        second = f"\uc608\uc0b0 \uba54\ub9ac\ud2b8\ub294 \uc788\uc9c0\ub9cc \ud1b5\uadfc \uc5ec\uc720\uac00 \uc870\uae08 \ub35c\ud574 {rank}\uc21c\uc704\uc5d0 \uba38\ubb3c \ud6c4\ubcf4\uc608\uc694."
        return "slower_but_cheaper", f"{first} {second}"
    first = f"{_commute_phrase(commute_usage_ratio)} {_budget_phrase(rent_margin_level, deposit_margin_level)}"
    second = f"\uba74\uc801\uc774\ub098 \uc774\ub3d9 \ud6a8\uc728\uae4c\uc9c0 \ud568\uaed8 \ubcf4\uba74 \uc0c1\uc704 \ud6c4\ubcf4\uc640 \ud070 \ucc28\uc774\ub294 \uc544\ub2c8\uc5b4\uc11c {rank}\uc21c\uc704\uc5d0\uc11c \ube44\uad50\ub418\ub294 \ud6c4\ubcf4\uc608\uc694."
    return "similar_to_upper_option", f"{first} {second}"


def _budget_context(item: dict) -> dict:
    context = item.get("explanation_context", {}) or {}
    trace_budget = ((item.get("ranking_trace") or {}).get("metrics") or {})
    budget = context.get("budget", {}) or {}
    constraints = context.get("constraints", {}) or {}
    trade_type = str(budget.get("deal_type") or item.get("deal_type") or "")
    is_jeonse = trade_type == "전세"
    return {
        "trade_type": trade_type,
        "is_jeonse": is_jeonse,
        "deposit": _safe_float(trace_budget.get("deposit_manwon") or budget.get("deposit_manwon") or item.get("deposit_manwon")),
        "monthly_rent": None if is_jeonse else _safe_float(trace_budget.get("monthly_rent_manwon") or budget.get("monthly_rent_manwon") or item.get("monthly_rent_manwon")),
        "deposit_limit": _safe_float(constraints.get("deposit_max")),
        "rent_limit": None if is_jeonse else _safe_float(constraints.get("rent_max")),
        "deposit_usage_ratio": _safe_float(budget.get("deposit_usage_ratio") or item.get("deposit_usage_ratio")),
        "rent_usage_ratio": None if is_jeonse else _safe_float(budget.get("rent_usage_ratio") or item.get("rent_usage_ratio")),
        "building_age": _safe_float(budget.get("building_age")),
        "built_year": _safe_float(budget.get("built_year") or item.get("built_year")),
        "area_pyeong": _safe_float(budget.get("area_pyeong") or item.get("area_pyeong")),
        "area_sqm": _safe_float(budget.get("area_sqm") or item.get("area_sqm")),
    }


def _reason_mode(item: dict) -> str:
    trace = item.get("ranking_trace") or {}
    if trace.get("route_status") in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        return "walkable"
    if trace.get("route_status") == "CAR":
        return "car"
    if trace.get("route_status") == "TRANSIT":
        return "transit"
    context = item.get("explanation_context", {}) or {}
    route_status = item.get("route_status") or ((context.get("primary_metrics") or {}).get("route_status"))
    if route_status in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        return "walkable"
    if (context.get("primary_mode") or "transit") == "car":
        return "car"
    return "transit"


def _distance_m_for_item(item: dict, metrics: dict | None = None) -> float | None:
    metrics = metrics or {}
    distance_m = _safe_float(metrics.get("distance_m") or item.get("direct_distance_m"))
    if distance_m is not None:
        return distance_m
    direct_distance_km = _safe_float(item.get("direct_distance_km"))
    if direct_distance_km is not None:
        return direct_distance_km * 1000.0
    distance_km = _safe_float(metrics.get("distance_km") or item.get("distance_km"))
    if distance_km is not None:
        return distance_km * 1000.0
    return None


def _commute_band_text(commute_ratio: float | None) -> str:
    if commute_ratio is None:
        return "통근 기준 안에 들어오는 편이에요."
    if commute_ratio <= 0.60:
        return "설정한 통근 한도보다 여유가 있는 편이에요."
    if commute_ratio <= 0.80:
        return "설정한 통근 한도 안에서 안정적으로 들어와요."
    if commute_ratio <= 1.00:
        return "통근 한도 안에는 들어오지만 여유가 아주 크지는 않아요."
    return "통근 한도에 다소 가까워 우선순위는 조금 낮아질 수 있어요."


def _walk_to_station_band_text(minutes: float | None) -> str:
    if minutes is None:
        return ""
    if minutes <= 5:
        return "집에서 역/정류장까지 가는 시작 동선이 짧아요."
    if minutes <= 10:
        return "집에서 역/정류장까지 접근은 무난한 편이에요."
    return "집에서 역/정류장까지는 조금 넉넉하게 보는 편이 좋아요."


def _total_walk_band_text(minutes: float | None) -> str:
    if minutes is None:
        return ""
    if minutes <= 10:
        return "총 도보가 짧아 이동 부담이 낮아요."
    if minutes <= 15:
        return "총 도보는 무난한 편이에요."
    if minutes <= 20:
        return "총 도보가 조금 있어 매일 이동 피로는 감안하는 편이 좋아요."
    return "총 도보가 긴 편이라 매일 이동 피로를 고려하는 것이 좋아요."


def _transfer_band_text(count: float | None) -> str:
    if count is None:
        return ""
    count = int(count)
    if count == 0:
        return "환승이 없어 이동 흐름이 단순해요."
    if count == 1:
        return "환승 1회로 이동 흐름이 크게 복잡하지는 않아요."
    if count == 2:
        return "환승이 2회라 경로가 조금 복잡한 편이에요."
    return f"환승이 {count}회라 매일 이용하면 다소 번거로울 수 있어요."


def _budget_sentence_for_reason(item: dict) -> str:
    budget = _budget_context(item)
    deposit = budget["deposit"]
    rent = budget["monthly_rent"]
    deposit_limit = budget["deposit_limit"]
    rent_limit = budget["rent_limit"]
    deposit_ratio = budget["deposit_usage_ratio"]
    rent_ratio = budget["rent_usage_ratio"]

    if budget["is_jeonse"]:
        if deposit is None:
            return ""
        if deposit_ratio is not None and deposit_ratio <= 0.7:
            feel = "초기 자금 부담이 비교적 낮아요."
        elif deposit_ratio is not None and deposit_ratio <= 0.9:
            feel = "입력한 예산 기준 안에서 무리 없는 편이에요."
        else:
            feel = "보증금 상한에 가까워 초기 자금 여유는 크지 않아요."
        if deposit_limit:
            return f"전세보증금은 {int(deposit)}만원으로 입력한 보증금 상한 안에 들어오고, {feel}"
        return f"전세보증금은 {int(deposit)}만원 수준이고, {feel}"

    if deposit is None and rent is None:
        return ""
    if rent_ratio is not None and rent_ratio <= 0.7:
        feel = "매달 부담이 비교적 낮아요."
    elif rent_ratio is not None and rent_ratio <= 0.9:
        feel = "입력한 예산 기준 안에서 무리 없는 편이에요."
    else:
        feel = "월세가 상한에 가까워 매달 부담은 한 번 더 따져보는 것이 좋아요."
    if deposit is not None and rent is not None:
        return f"보증금은 {int(deposit)}만원, 월세는 {int(rent)}만원으로 {feel}"
    if rent is not None:
        return f"월세는 {int(rent)}만원으로 {feel}"
    return f"보증금은 {int(deposit)}만원으로 예산 범위 안에 들어오는 편이에요."


def _property_sentence_for_reason(item: dict) -> str:
    budget = _budget_context(item)
    area_pyeong = budget["area_pyeong"]
    built_year = budget["built_year"]
    building_age = budget["building_age"]
    parts = []
    if area_pyeong is not None:
        if area_pyeong <= 6:
            parts.append("다소 아담한 편이라 공간 활용을 고려해야 해요.")
        elif area_pyeong <= 9:
            parts.append("1인 거주 기준으로는 무난한 공간감이에요.")
        elif area_pyeong <= 14:
            parts.append("1인 거주 기준으로 실사용 공간에 여유가 있는 편이에요.")
        else:
            parts.append("공간 여유가 큰 편이라 면적을 중시한다면 장점이 분명해요.")
    if built_year is not None and building_age is not None:
        if building_age <= 5:
            parts.append("비교적 최근 건물이라 연식 측면에서는 장점이 있어요.")
        elif building_age <= 10:
            parts.append("연식이 아주 오래되지 않아 무난한 선택지로 볼 수 있어요.")
        elif building_age >= 21:
            parts.append("연식이 있는 편이라 실제 관리 상태는 방문 시 확인해보는 것이 좋아요.")
    return " ".join(parts[:2])


def _geo_sentence_for_reason(item: dict) -> str:
    context = item.get("explanation_context", {}) or {}
    geo = context.get("geo_constraints", {}) or {}
    tradeoff = context.get("tradeoff_policy", {}) or {}
    unsupported = context.get("unsupported_preferences", []) or []
    parts = []

    preferred_districts = geo.get("preferred_districts") or []
    if preferred_districts:
        parts.append(f"사용자가 선호한 {preferred_districts[0]} 쪽 기준과도 잘 맞아요.")
    if geo.get("avoid_remote_area"):
        parts.append("너무 외진 곳은 피하고 싶은 조건에도 맞는 편이에요.")
    if tradeoff.get("pay_more_for_commute") is True:
        parts.append("월세가 조금 높아도 통근이 편한 쪽을 우선하는 기준을 반영했어요.")
    elif tradeoff.get("pay_more_for_commute") is False:
        parts.append("비용 부담을 더 줄이려는 기준도 함께 반영했어요.")
    if any("번잡" in str(item) for item in unsupported):
        parts.append("번잡한 상권은 현재 데이터로 직접 판단하기 어려워 참고 조건으로만 봤어요.")
    if any("조용" in str(item) for item in unsupported):
        parts.append("조용한 분위기는 현재 데이터로 직접 판단하기 어려워 참고 조건으로만 봤어요.")
    return " ".join(parts)


def _living_sentence_for_reason(item: dict) -> str:
    context = item.get("explanation_context", {}) or {}
    living_matches = context.get("living_matches") or []
    living_references = context.get("living_reference_tags") or []
    requested_living = context.get("requested_living_preferences") or {}
    selected_categories = context.get("selected_living_categories") or []

    label_map = {
        "cafe": "카페",
        "hospital": "병원",
        "laundry": "세탁소",
        "gym": "헬스장",
        "large_store": "대형마트",
        "convenience_store": "편의점",
        "light_food_snack": "간단음식/간식",
    }

    parts = []
    for match in living_matches[:2]:
        label = str(match.get("label") or label_map.get(match.get("category"), "")).strip()
        distance_m = match.get("distance_m")
        if not label:
            continue
        label_text = subject_label(label)
        if distance_m is not None:
            walk_minutes = max(1, int(round(float(distance_m) / 70.0)))
            cfg = requested_living.get(match.get("category") or "") or {}
            max_walk_minutes = _safe_float(cfg.get("max_walk_minutes"))
            if max_walk_minutes is not None:
                parts.append(f"{label_text} 도보 약 {walk_minutes}분({int(distance_m)}m) 거리라 기준 {int(max_walk_minutes)}분 이내에 들어와요.")
            else:
                parts.append(f"{label_text} 도보 약 {walk_minutes}분({int(distance_m)}m) 거리에 있어요.")
        else:
            cfg = requested_living.get(match.get("category") or "") or {}
            max_walk_minutes = _safe_float(cfg.get("max_walk_minutes"))
            if max_walk_minutes is not None:
                parts.append(f"{label_text} 설정한 {int(max_walk_minutes)}분 기준 안에서 확인돼요.")
            else:
                parts.append(f"{label_text} 조건도 함께 확인돼요.")

    if not parts and selected_categories:
        selected_labels = []
        for category in selected_categories[:3]:
            cfg = requested_living.get(category) or {}
            label = label_map.get(category, category)
            max_walk_minutes = cfg.get("max_walk_minutes")
            if max_walk_minutes is not None:
                selected_labels.append(f"{subject_label(label)} 도보 {int(max_walk_minutes)}분 이내")
            else:
                selected_labels.append(subject_label(label))
        if selected_labels:
            parts.append(f"생활 편의 조건으로 {', '.join(selected_labels)}를 확인했어요.")

    if not parts and living_references:
        ref_labels = [str(ref.get("label") or "").strip() for ref in living_references[:2] if str(ref.get("label") or "").strip()]
        if ref_labels:
            ref_text = ", ".join(subject_label(label) for label in ref_labels)
            parts.append(f"주변에 {ref_text} 같은 시설도 함께 확인돼요.")

    return " ".join(parts)


def _comparison_sentence_for_transit(item: dict) -> str:
    trace = item.get("ranking_trace") or {}
    rank = int(item.get("rank") or 0)
    if rank <= 1:
        return "통근과 비용 조건의 균형이 가장 좋아 1순위로 추천할 수 있어요."
    diffs = trace.get("vs_rank1", {}) or {}
    pieces = []
    duration_diff = _safe_float(diffs.get("duration_diff_min"))
    walk_to_station_diff = _safe_float(diffs.get("walk_to_station_diff_min"))
    total_walk_diff = _safe_float(diffs.get("total_walk_diff_min"))
    transfer_diff = _safe_float(diffs.get("transfer_diff"))
    rent_diff = _safe_float(diffs.get("rent_diff_manwon"))
    deposit_diff = _safe_float(diffs.get("deposit_diff_manwon"))
    area_diff = _safe_float(diffs.get("area_diff_pyeong"))
    if duration_diff is not None and abs(duration_diff) >= 3:
        pieces.append(f"1위보다 통근시간이 {abs(int(duration_diff))}분 {'길어' if duration_diff > 0 else '짧아'}")
    if transfer_diff is not None and transfer_diff >= 1:
        pieces.append(f"환승이 {int(abs(transfer_diff))}회 더 있어")
    if walk_to_station_diff is not None and walk_to_station_diff >= 3:
        pieces.append(f"역/정류장까지 걷는 시간이 {int(abs(walk_to_station_diff))}분 더 길어")
    if total_walk_diff is not None and total_walk_diff >= 3:
        pieces.append(f"총 도보가 {int(abs(total_walk_diff))}분 더 있어")
    if not pieces:
        if rent_diff is not None and abs(rent_diff) >= 5:
            pieces.append(f"월세가 1위보다 {int(abs(rent_diff))}만원 {'높아' if rent_diff > 0 else '낮아'}")
        elif deposit_diff is not None and abs(deposit_diff) >= 200:
            pieces.append(f"보증금이 1위보다 {int(abs(deposit_diff))}만원 {'높아' if deposit_diff > 0 else '낮아'}")
        elif area_diff is not None and abs(area_diff) >= 1.5:
            pieces.append(f"면적이 1위보다 {abs(area_diff):.1f}평 {'넓어' if area_diff > 0 else '좁아'}")
    if not pieces:
        if rank == 2:
            return "1위와 조건 차이는 크지 않지만, 통근과 예산 균형을 함께 보면 다음 순서로 보기 좋아요."
        if rank == 3:
            return "통근 조건은 안정적이지만, 비용과 이동 동선까지 함께 보면 세 번째로 비교해볼 만해요."
        if rank == 4:
            return "통근은 충분히 가능하지만, 상위 후보보다 비용과 이동 동선의 여유가 덜해 네 번째로 보는 편이 좋아요."
        return "통근은 가능한 편이지만, 비용과 이동 부담을 함께 보면 뒤쪽 후보로 비교하기 좋아요."
    if rank == 2:
        return f"{' '.join(pieces)}. 그래서 통근과 비용 균형을 함께 보면 다음 순서로 보기 좋아요."
    if rank == 3:
        return f"{' '.join(pieces)}. 그래서 통근은 좋지만 다른 조건까지 함께 보면 세 번째로 비교해볼 만해요."
    if rank == 4:
        return f"{' '.join(pieces)}. 그래서 상위 후보보다 우선순위가 낮아 네 번째로 보는 편이 좋아요."
    return f"{' '.join(pieces)}. 그래서 뒤쪽 후보로 비교하기 좋아요."


def _comparison_sentence_for_walkable(item: dict) -> str:
    trace = item.get("ranking_trace") or {}
    rank = int(item.get("rank") or 0)
    if rank <= 1:
        return ""
    diffs = trace.get("vs_rank1", {}) or {}
    rent_diff = _safe_float(diffs.get("rent_diff_manwon"))
    deposit_diff = _safe_float(diffs.get("deposit_diff_manwon"))
    area_diff = _safe_float(diffs.get("area_diff_pyeong"))
    age_diff = _safe_float(diffs.get("building_age_diff"))
    reasons = []
    if rent_diff is not None and abs(rent_diff) >= 5:
        reasons.append(f"월세가 1위보다 {abs(int(rent_diff))}만원 {'높아' if rent_diff > 0 else '낮아'}")
    if deposit_diff is not None and abs(deposit_diff) >= 200:
        reasons.append(f"보증금이 1위보다 {abs(int(deposit_diff))}만원 {'높아' if deposit_diff > 0 else '낮아'}")
    if area_diff is not None and abs(area_diff) >= 1.5:
        reasons.append(f"면적이 1위보다 {abs(area_diff):.1f}평 {'넓어' if area_diff > 0 else '좁아'}")
    if age_diff is not None and abs(age_diff) >= 5:
        reasons.append("건물 연식이 1위보다 더 최근이라" if age_diff < 0 else "건물 연식이 1위보다 오래돼")
    if not reasons:
        if rank == 2:
            return "1위와 통근 체감 차이는 거의 없지만, 예산과 공간 조건을 함께 보면 다음 순서로 보기 좋아요."
        if rank == 3:
            return "위치만 보면 더 가깝지만, 예산과 공간 조건을 함께 보면 세 번째로 비교해볼 만해요."
        if rank == 4:
            return "같은 도보권 안에서는 통근보다 비용과 공간 차이가 더 크게 작용해 네 번째로 보는 편이 좋아요."
        return "가까운 거리 장점이 있더라도 비용과 공간 조건을 함께 보면 뒤쪽 후보로 비교하기 좋아요."
    if rank == 2:
        return f"1위와 통근 체감 차이는 거의 없지만, {' '.join(reasons)}. 그래서 비용·공간 균형을 함께 보면 다음 순서로 보기 좋아요."
    if rank == 3:
        return f"위치만 보면 더 가깝지만, {' '.join(reasons)}. 그래서 세 번째로 비교해볼 만해요."
    if rank == 4:
        return f"같은 도보권 안에서는 통근보다 비용과 공간 차이가 더 크게 작용합니다. {' '.join(reasons)}. 그래서 네 번째로 보는 편이 좋아요."
    return f"가까운 거리 장점이 있지만 {' '.join(reasons)}. 그래서 뒤쪽 후보로 비교하기 좋아요."


def build_walkable_final_reason(item: dict) -> str:
    context = item.get("explanation_context", {}) or {}
    trace = item.get("ranking_trace") or {}
    metrics = (trace.get("metrics") or {}) if trace else (context.get("primary_metrics", {}) or {})
    constraints = context.get("constraints", {}) or {}
    rank = int(item.get("rank") or 0)
    duration_min = _safe_float(metrics.get("duration_min") or context.get("primary_metrics", {}).get("commute_time_min") or item.get("duration_min"))
    distance_m = _distance_m_for_item(item, metrics)
    max_commute_minutes = _safe_float(constraints.get("max_commute_minutes"))
    commute_ratio = None if duration_min is None or not max_commute_minutes else duration_min / max_commute_minutes
    budget_sentence = _budget_sentence_for_reason(item)
    property_sentence = _property_sentence_for_reason(item)
    geo_sentence = _geo_sentence_for_reason(item)

    if distance_m is not None and distance_m <= 500:
        distance_label = "직주근접성이 매우 좋아요"
    elif distance_m is not None and distance_m <= 700:
        distance_label = "도보 통근이 충분히 가능한 편이에요"
    elif distance_m is not None and distance_m <= 1000:
        distance_label = "도보 통근은 가능하지만 체감은 있는 편이에요"
    else:
        distance_label = "도보 통근을 우선 검토할 수 있어요"

    if duration_min is not None and distance_m is not None:
        first = (
            f"직장/학교까지 걸어서 {int(duration_min)}분권이고, 약 {int(distance_m)}m 거리라 {distance_label}."
            if duration_min <= 5
            else f"직장/학교까지 걸어서 {int(duration_min)}분이고, 약 {int(distance_m)}m 거리라 {distance_label}."
        )
    else:
        first = "직장/학교까지 걸어서 통근 가능한 편이에요."
    second = _commute_band_text(commute_ratio)
    if duration_min is not None:
        if duration_min <= 8:
            second = "매일 이동 부담도 꽤 낮아요."
        elif duration_min <= 15:
            second = "일상 통근용으로는 충분히 무난해요."
        elif duration_min <= 20:
            second = "다만 매일 걸으면 피로도는 조금 느낄 수 있어요."
    if rank <= 1:
        metric_focus = "같은 도보권 후보 중에서는 비용 부담이 낮고 공간도 무난해 균형이 좋아요."
        final = "통근, 비용, 공간 조건을 함께 보면 가장 먼저 볼 만해요."
    else:
        metric_focus = _comparison_sentence_for_walkable(item)
        final = "도보 통근은 강점이 있지만, 비용과 공간까지 함께 보면 우선순위는 조금 내려가요."
    parts = [first, second, budget_sentence, property_sentence, geo_sentence, metric_focus, final]
    return " ".join(part for part in parts if part).strip()


def build_transit_final_reason(item: dict) -> str:
    context = item.get("explanation_context", {}) or {}
    trace = item.get("ranking_trace") or {}
    metrics = (trace.get("metrics") or {}) if trace else (context.get("primary_metrics", {}) or {})
    constraints = context.get("constraints", {}) or {}
    duration_min = _safe_float(metrics.get("duration_min") or context.get("primary_metrics", {}).get("commute_time_min") or item.get("duration_min"))
    max_commute_minutes = _safe_float(constraints.get("max_commute_minutes"))
    walk_to_station_min = _safe_float(metrics.get("walk_to_station_min") or metrics.get("first_walk_min"))
    total_walk_min = _safe_float(metrics.get("total_walk_min") or metrics.get("walk_time_min") or item.get("walk_time_min"))
    transfer_count = _preferred_transfer_count(item, metrics)
    commute_ratio = None if duration_min is None or not max_commute_minutes else duration_min / max_commute_minutes
    rank = int(item.get("rank") or context.get("rank") or 0)

    first = f"대중교통 기준 총 {int(duration_min)}분으로 통근 한도 안에 들어와요." if duration_min is not None and max_commute_minutes else "대중교통 통근 기준을 충족해요."
    second = _commute_band_text(commute_ratio)
    total_walk_text = _total_walk_band_text(total_walk_min)
    third = ""
    if walk_to_station_min is not None and total_walk_min is not None:
        if walk_to_station_min <= 5:
            access_clause = "시작 동선이 짧은 편이고"
        elif walk_to_station_min <= 10:
            access_clause = "시작 동선은 무난한 편이고"
        else:
            access_clause = "시작 동선은 조금 넉넉하게 보는 편이고"
        third = f"집에서 역/정류장까지 도보 {int(walk_to_station_min)}분, 총 도보 {int(total_walk_min)}분이라 {access_clause} 이동 부담도 함께 볼 수 있어요."
    elif walk_to_station_min is not None:
        if walk_to_station_min <= 5:
            third = f"집에서 역/정류장까지 도보 {int(walk_to_station_min)}분이라 시작 동선이 짧아요."
        elif walk_to_station_min <= 10:
            third = f"집에서 역/정류장까지 도보 {int(walk_to_station_min)}분이라 시작 동선은 무난해요."
        else:
            third = f"집에서 역/정류장까지 도보 {int(walk_to_station_min)}분이라 시작 동선은 조금 넉넉하게 보는 게 좋아요."
    elif total_walk_min is not None:
        third = f"총 도보 {int(total_walk_min)}분이라 이동 부담을 함께 볼 수 있어요."
    fourth = _transfer_band_text(transfer_count)
    fifth = _budget_sentence_for_reason(item)
    sixth = _comparison_sentence_for_transit(item)
    property_sentence = _property_sentence_for_reason(item)
    geo_sentence = _geo_sentence_for_reason(item)
    if duration_min is not None and transfer_count is not None and int(transfer_count) <= 0:
        fourth = "환승이 없어 이동 흐름이 단순해요."
    if rank <= 1:
        final = "통근, 비용, 이동 동선을 함께 보면 가장 먼저 볼 만해요."
    elif rank <= 3:
        final = "조건은 무난해서 상위 후보와 함께 비교해볼 만해요."
    elif rank == 4:
        final = "상위 후보보다 여유는 조금 덜하지만, 비교 후보로는 충분해요."
    else:
        final = "뒤쪽 후보로 두고 다른 조건과 함께 비교해보면 좋아요."
    parts = [first, second, third, fourth, fifth, property_sentence, geo_sentence, sixth, final]
    return " ".join(part for part in parts if part).strip()


def _polish_final_reason_text(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    replacements = [
        ("전체 균형은", "전체 균형으로 보면"),
        ("기본 조건은 잘 맞지만", "핵심 조건은 맞지만"),
        ("추천할 만한 후보예요.", "추천할 만해요."),
        ("검토되는 후보예요.", "볼 수 있어요."),
        ("검토할 만한 후보예요.", "볼 만해요."),
        ("비교할 후보예요.", "비교해보면 좋아요."),
        ("후보예요.", "볼 수 있어요."),
        ("후보입니다.", "볼 수 있어요."),
        ("좋은 후보입니다.", "보기 좋아요."),
        ("좋은 후보예요.", "보기 좋아요."),
        ("먼저 보기 좋은 후보예요.", "먼저 보기 좋아요."),
        ("후순위로 보는 후보예요.", "뒤쪽으로 비교해보면 좋아요."),
    ]
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)
    cleaned = cleaned.replace("  ", " ")
    cleaned = cleaned.replace("..", ".")
    return cleaned.strip()


def _build_reason_sections(item: dict) -> list[dict]:
    mode = _reason_mode(item)
    context = item.get("explanation_context", {}) or {}
    metrics = context.get("primary_metrics", {}) or {}
    budget = context.get("budget", {}) or {}
    constraints = context.get("constraints", {}) or {}
    route_status = item.get("route_status") or context.get("route_status")
    rank = int(item.get("rank") or context.get("rank") or 0)

    commute = _safe_float(metrics.get("duration_min") or context.get("primary_metrics", {}).get("commute_time_min") or item.get("duration_min"))
    max_commute = _safe_float(constraints.get("max_commute_minutes"))
    first_walk_min = _safe_float(metrics.get("first_walk_min") or item.get("first_walk_min"))
    total_walk_min = _safe_float(metrics.get("walk_time_min") or item.get("walk_time_min"))
    total_walk_m = _safe_float(metrics.get("walk_m") or metrics.get("total_walk_m") or item.get("total_walk_m") or item.get("walk_distance_m"))
    transfer_count = _preferred_transfer_count(item, metrics)
    area_pyeong = _safe_float(budget.get("area_pyeong") or item.get("area_pyeong"))
    living_matches = item.get("living_matches") or context.get("living_matches") or []
    living_refs = item.get("living_reference_tags") or context.get("living_reference_tags") or []

    sections: list[dict] = []

    def _push(key: str, title: str, icon: str, text: str | None) -> None:
        if text:
            sections.append({
                "key": key,
                "icon": icon,
                "title": title,
                "text": _polish_final_reason_text(str(text).strip()),
            })

    if mode == "car":
        _push("commute", "이동 조건", "⏱", _car_time_profile_sentence(item) or _car_commute_sentence(item))
        _push("budget", "예산", "💰", _car_budget_sentence(item))
        _push("space", "공간", "🏠", _car_property_sentence(item))
        _push("living", "생활 편의", "🏪", _car_living_sentence(item) or _geo_sentence_for_reason(item))
        _push("overall", "종합 판단", "✅", _polish_final_reason_text(str(item.get("llm_reason") or item.get("final_reason") or item.get("recommendation_reason") or item.get("reason") or "").strip()))
        return sections

    if route_status in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        commute_text = "직장/학교와 가까워 걸어서 이동하기 좋아요."
        if commute is not None:
            commute_text = f"직장/학교까지 도보 {int(commute)}분 정도라 이동이 편해요."
        walk_text = "걸어서 이동 가능한 거리예요."
        if total_walk_m is not None:
            walk_bits = [f"총 도보 {int(total_walk_m)}m"]
            if total_walk_min is not None:
                walk_bits.insert(0, f"전체 도보 {int(total_walk_min)}분")
            if first_walk_min is not None:
                walk_bits.insert(0, f"집에서 역/정류장까지 도보 {int(first_walk_min)}분")
            walk_text = f"{' / '.join(walk_bits)}라 도보 부담을 함께 보기 좋아요."
        budget_text = _budget_sentence_for_reason(item)
        if not budget_text and rank <= 1:
            budget_text = "예산 부담이 낮으면 더욱 잘 맞는 타입이에요."
        space_text = ""
        if area_pyeong is not None:
            if area_pyeong < 6:
                space_text = "면적은 작은 편이라 통근을 우선할 때 더 잘 맞아요."
            elif area_pyeong < 8:
                space_text = "기본 생활은 가능한 면적이라 1인 가구 기준으로는 무난해요."
            elif area_pyeong < 12:
                space_text = "1인 거주 기준으로는 무난한 공간감을 기대할 수 있어요."
            elif area_pyeong < 18:
                space_text = "면적이 비교적 넉넉해 실사용 공간도 괜찮아요."
            else:
                space_text = "공간 여유가 큰 편이라 면적을 중시할 때 장점이 있어요."
        living_text = _living_sentence_for_reason(item)
        if not living_text and living_matches:
            living_bits = []
            for match in living_matches[:2]:
                label = str(match.get("label") or "").strip()
                distance_m = _safe_float(match.get("distance_m"))
                if not label:
                    continue
                if distance_m is not None:
                    living_bits.append(f"{subject_label(label)} 도보 약 {max(1, int(round(distance_m / 70.0)))}분({int(distance_m)}m) 거리에 있어요.")
            if living_bits:
                living_text = " ".join(living_bits)
        if not living_text and living_refs:
            living_text = f"주변에 {subject_label(str(living_refs[0]['label']).strip())} 같은 시설도 함께 확인돼요."
        overall_text = _deterministic_display_reason(item)
        _push("commute", "통근 조건", "⏱", commute_text)
        _push("walk", "도보 이동", "🚶", walk_text)
        _push("budget", "예산", "💰", budget_text)
        _push("space", "공간", "🏠", space_text)
        _push("living", "생활 편의", "🏪", living_text)
        _push("overall", "종합 판단", "✅", overall_text)
        return sections

    if commute is not None or max_commute is not None:
        if commute is not None and max_commute is not None:
            commute_text = f"대중교통 기준 총 {int(commute)}분이라 설정한 통근 한도 안에 들어와요."
        elif commute is not None:
            commute_text = f"대중교통으로는 총 {int(commute)}분 정도 걸려요."
        else:
            commute_text = "대중교통 통근 기준을 충족해요."
        _push("commute", "통근 조건", "⏱", commute_text)

    walk_bits = []
    if first_walk_min is not None:
        walk_bits.append(f"집에서 역/정류장까지 도보 {int(first_walk_min)}분")
    if total_walk_min is not None:
        walk_bits.append(f"전체 도보 이동은 {int(total_walk_min)}분")
    if total_walk_m is not None:
        walk_bits.append(f"총 도보 거리는 {int(total_walk_m)}m")
    if walk_bits:
        _push("walk", "도보 이동", "🚶", f"{', '.join(walk_bits)}라 도보 이동 흐름을 함께 확인할 수 있어요.")

    if transfer_count is not None:
        _push(
            "transfer",
            "환승",
            "🔁",
            "환승이 없어 이동 경로가 단순한 편이에요."
            if int(transfer_count) <= 0
            else f"환승은 {int(transfer_count)}회라 이동 경로가 아주 단순하지는 않아요.",
        )

    budget_text = _budget_sentence_for_reason(item)
    _push("budget", "예산", "💰", budget_text)

    if area_pyeong is not None:
        if area_pyeong < 6:
            space_text = "면적은 작은 편이라 공간보다 통근을 우선할 때 더 잘 맞아요."
        elif area_pyeong < 8:
            space_text = "기본 생활은 가능한 면적이라 1인 가구 기준으로는 무난한 편이에요."
        elif area_pyeong < 12:
            space_text = "1인 거주 기준으로는 무난한 공간감을 기대할 수 있어요."
        elif area_pyeong < 18:
            space_text = "면적이 비교적 넉넉해 실사용 공간 면에서도 장점이 있어요."
        else:
            space_text = "공간 여유가 큰 편이라 면적을 중시한다면 장점이 분명해요."
        _push("space", "공간", "🏠", space_text)

    living_text = _living_sentence_for_reason(item)
    if not living_text and living_matches:
        living_bits = []
        for match in living_matches[:2]:
            label = str(match.get("label") or "").strip()
            distance_m = _safe_float(match.get("distance_m"))
            if not label:
                continue
            if distance_m is not None:
                living_bits.append(f"{subject_label(label)} 도보 약 {max(1, int(round(distance_m / 70.0)))}분({int(distance_m)}m) 거리에 있어요.")
        living_text = " ".join(living_bits)
    if living_text or living_refs:
        _push("living", "생활 편의", "🏪", living_text or "생활 편의 기준을 참고했어요.")

    final_text = _polish_final_reason_text(str(item.get("llm_reason") or item.get("final_reason") or item.get("recommendation_reason") or item.get("reason") or "").strip())
    if not final_text:
        _, fallback_detail = _fallback_rank_explanation(item)
        final_text = _polish_final_reason_text(fallback_detail)
    _push("overall", "종합 판단", "✅", final_text)
    return sections


def _car_commute_sentence(item: dict) -> str:
    context = item.get("explanation_context", {}) or {}
    trace = item.get("ranking_trace") or {}
    metrics = (trace.get("metrics") or {}) if trace else (context.get("primary_metrics", {}) or {})
    constraints = context.get("constraints", {}) or {}
    duration_min = _safe_float(metrics.get("duration_min") or item.get("duration_min"))
    max_commute_minutes = _safe_float(constraints.get("max_commute_minutes"))
    if duration_min is None and max_commute_minutes is None:
        return "자동차 통근 기준을 충족해요."
    if duration_min is None:
        return f"자동차 통근시간은 설정한 최대 {int(max_commute_minutes)}분 기준 안에서 검토할 수 있어요."
    if not max_commute_minutes:
        return f"자동차로 약 {int(duration_min)}분 걸려요."

    margin = int(round(max_commute_minutes - duration_min))
    if margin > 0:
        return f"자동차로 약 {int(duration_min)}분 걸려 설정한 최대 {int(max_commute_minutes)}분보다 {margin}분 여유가 있어요."
    if margin == 0:
        return f"자동차로 약 {int(duration_min)}분 걸려 설정한 최대 {int(max_commute_minutes)}분에 딱 맞아요."
    return f"자동차로 약 {int(duration_min)}분 걸려 설정한 최대 {int(max_commute_minutes)}분보다 {abs(margin)}분 초과하는 편이라 다른 조건까지 함께 봐야 해요."


def _car_budget_sentence(item: dict) -> str:
    budget = _budget_context(item)
    deposit = budget["deposit"]
    rent = budget["monthly_rent"]
    deposit_limit = budget["deposit_limit"]
    rent_limit = budget["rent_limit"]
    deposit_ratio = budget["deposit_usage_ratio"]
    rent_ratio = budget["rent_usage_ratio"]

    if budget["is_jeonse"]:
        if deposit is None:
            return ""
        if deposit_limit:
            gap = int(round(deposit_limit - deposit))
            if gap >= 300:
                return f"전세금은 {int(deposit)}만원으로 예산 상한보다 {gap}만원 낮아 초기 자금 부담은 비교적 덜한 편이에요."
            if gap >= 0 and deposit_ratio is not None and deposit_ratio <= 0.9:
                return f"전세금은 {int(deposit)}만원으로 입력한 예산 기준 안에서 비교적 무리 없어요."
            if gap >= 0:
                return f"전세금은 {int(deposit)}만원으로 예산 상한에 가까워 초기 자금 부담은 한 번 더 확인하는 것이 좋아요."
        if deposit_ratio is not None and deposit_ratio >= 0.9:
            return f"전세금은 {int(deposit)}만원으로 보증금 상한에 가까운 편이라 초기 자금 부담은 한 번 더 확인하는 것이 좋아요."
        return f"전세금은 {int(deposit)}만원 수준으로 예산 범위 안에서 맞출 수 있어요."

    if deposit is None and rent is None:
        return ""
    if deposit is not None and rent is not None and deposit_limit and rent_limit:
        deposit_gap = int(round(deposit_limit - deposit))
        rent_gap = int(round(rent_limit - rent))
        if rent_gap >= 10 and deposit_gap >= 200:
            return f"보증금은 {int(deposit)}만원, 월세는 {int(rent)}만원으로 설정한 예산 안에 들어와 비용 조건은 무난한 편이에요."
        if rent_gap >= 0 and deposit_gap >= 200:
            return f"보증금은 {int(deposit)}만원, 월세는 {int(rent)}만원으로 설정한 예산 안에 들어오고, 다만 월세가 상한에 가까운 편이라 매달 부담은 한 번 더 확인하는 것이 좋아요."
        if deposit_gap >= 0 and rent_gap >= 10:
            return f"보증금은 {int(deposit)}만원, 월세는 {int(rent)}만원으로 설정한 예산 안에 들어오고, 다만 보증금이 상한에 가까워 초기 비용은 한 번 더 확인하는 것이 좋아요."
        if rent_gap >= 0 and deposit_gap >= 0:
            return f"보증금은 {int(deposit)}만원, 월세는 {int(rent)}만원으로 설정한 예산 안에 들어오지만 보증금과 월세 모두 상한에 가까운 편이라 비용 부담은 한 번 더 확인하는 것이 좋아요."
    if rent is not None and rent_limit:
        rent_gap = int(round(rent_limit - rent))
        if rent_gap >= 10:
            return f"월세는 {int(rent)}만원으로 예산 상한보다 여유가 있어 매달 부담은 비교적 덜한 편이에요."
        if rent_gap >= 0:
            return f"월세는 {int(rent)}만원으로 상한에 가까워 매달 부담은 한 번 더 따져보는 것이 좋아요."
    if deposit is not None and deposit_limit:
        deposit_gap = int(round(deposit_limit - deposit))
        if deposit_gap >= 200:
            return f"보증금은 {int(deposit)}만원으로 예산 상한보다 여유가 있는 편이에요."
        if deposit_gap >= 0:
            return f"보증금은 {int(deposit)}만원으로 예산 상한에 가까워 초기 비용은 확인해보는 것이 좋아요."
    return _budget_sentence_for_reason(item)


def _car_property_sentence(item: dict) -> str:
    budget = _budget_context(item)
    area_pyeong = budget["area_pyeong"]
    built_year = budget["built_year"]
    building_age = budget["building_age"]

    parts = []
    if area_pyeong is not None:
        rounded = round(area_pyeong, 1)
        area_text = f"{int(rounded)}평" if rounded.is_integer() else f"{rounded:.1f}평"
        if area_pyeong <= 6:
            parts.append(f"면적은 약 {area_text}으로 다소 아담한 편이라 공간 활용을 고려해야 해요.")
        elif area_pyeong <= 9:
            parts.append(f"면적은 약 {area_text}으로 1인 거주 기준 무난한 공간감이에요.")
        elif area_pyeong <= 14:
            parts.append(f"면적은 약 {area_text}으로 1인 거주 기준 실사용 공간에 여유가 있는 편이에요.")
        else:
            parts.append(f"면적은 약 {area_text}으로 공간 여유가 큰 편이라 면적을 중시한다면 장점이 있어요.")

    if built_year is not None and building_age is not None:
        if building_age <= 5:
            parts.append(f"준공연도는 {int(built_year)}년으로 비교적 최근 건물이라 연식 측면에서도 강점이 있어요.")
        elif building_age <= 10:
            parts.append(f"준공연도는 {int(built_year)}년으로 아주 오래되지 않아 무난한 선택지로 볼 수 있어요.")
        elif building_age >= 21:
            parts.append(f"준공연도는 {int(built_year)}년으로 연식이 있는 편이라 실제 관리 상태는 방문 시 확인해보는 것이 좋아요.")
    elif built_year is not None:
        parts.append(f"준공연도는 {int(built_year)}년이에요.")

    return " ".join(parts[:2])


def _car_living_sentence(item: dict) -> str:
    context = item.get("explanation_context", {}) or {}
    living_matches = context.get("living_matches") or []
    requested_living = context.get("requested_living_preferences") or {}
    selected_categories = context.get("selected_living_categories") or []
    label_map = {
        "cafe": "카페",
        "hospital": "병원",
        "laundry": "세탁소",
        "gym": "헬스장",
        "large_store": "대형마트",
        "convenience_store": "편의점",
        "light_food_snack": "간단음식/간식",
    }

    details = []
    for match in living_matches[:2]:
        label = str(match.get("label") or label_map.get(match.get("category"), "")).strip()
        distance_m = match.get("distance_m")
        if not label:
            continue
        label_text = subject_label(label)
        if distance_m is not None:
            walk_minutes = max(1, int(round(float(distance_m) / 70.0)))
            cfg = requested_living.get(match.get("category") or "") or {}
            max_walk_minutes = _safe_float(cfg.get("max_walk_minutes"))
            if max_walk_minutes is not None:
                details.append(f"{label_text} 도보 약 {walk_minutes}분({int(distance_m)}m), 기준 {int(max_walk_minutes)}분 이내")
            else:
                details.append(f"{label_text} 도보 약 {walk_minutes}분({int(distance_m)}m)")
        else:
            details.append(label_text)
    if details:
        return f"생활 편의 조건으로 {', '.join(details)}를 확인했어요."

    if selected_categories:
        selected_labels = []
        for category in selected_categories[:3]:
            cfg = requested_living.get(category) or {}
            label = label_map.get(category, category)
            max_walk_minutes = cfg.get("max_walk_minutes")
            if max_walk_minutes is not None:
                selected_labels.append(f"{subject_label(label)} 도보 약 {int(max_walk_minutes)}분 이내")
            else:
                selected_labels.append(subject_label(label))
        if selected_labels:
            return f"생활 편의 조건으로 {', '.join(selected_labels)}를 확인했어요."
    return ""


def _car_comparison_sentence(item: dict) -> str:
    trace = item.get("ranking_trace") or {}
    context = item.get("explanation_context", {}) or {}
    rank = int(item.get("rank") or context.get("rank") or 0)
    vs_prev = trace.get("vs_prev", {}) or {}
    vs_rank1 = trace.get("vs_rank1", {}) or {}
    next_ref = context.get("vs_next", {}) or {}

    def _budget_diff_text(diff: float | None, label: str, compare_to: str) -> str:
        value = _safe_float(diff)
        if value is None or abs(value) < (200 if label == "보증금" else 5):
            return ""
        direction = "높아" if value > 0 else "낮아"
        if label == "월세":
            return f"월세는 {compare_to}보다 {abs(int(value))}만원 {direction} 비용 부담은 {'더 큰 편이에요' if value > 0 else '덜한 편이에요'}"
        return f"{label}은 {compare_to}보다 {abs(int(value))}만원 {direction} 초기 비용 부담은 {'더 큰 편이에요' if value > 0 else '덜한 편이에요'}"

    def _area_diff_text(diff: float | None, compare_to: str) -> str:
        value = _safe_float(diff)
        if value is None or abs(value) < 1.0:
            return ""
        direction = "더 넓은 편이에요" if value > 0 else "더 좁아요"
        return f"면적은 {compare_to}보다 약 {abs(value):.1f}평 {direction}"

    def _age_diff_text(diff: float | None, compare_to: str) -> str:
        value = _safe_float(diff)
        if value is None or abs(value) < 5:
            return ""
        if value > 0:
            return f"건물 연식은 {compare_to}보다 더 오래된 편이에요"
        return f"건물 연식은 {compare_to}보다 더 최근인 편이에요"

    def _housing_tradeoff_sentence(area_text: str, age_text: str, rank_value: int) -> str:
        if area_text and age_text:
            area_core = area_text.replace("면적은 ", "").replace(" 더 좁아요", " 더 좁고").replace(" 더 넓은 편이에요", " 더 넓은 편이고")
            age_core = age_text.replace("건물 연식은 ", "건물 연식도 ").replace("편이에요", "편이라")
            return f"다만 {area_core} {age_core}, 주거 조건까지 함께 보면 {rank_value}순위로 볼 수 있어요."
        if area_text:
            area_core = area_text.replace("면적은 ", "면적이 ")
            return f"다만 {area_core}, 주거 조건까지 함께 보면 {rank_value}순위로 볼 수 있어요."
        if age_text:
            age_core = age_text.replace("건물 연식은 ", "건물 연식이 ")
            return f"다만 {age_core}, 주거 조건까지 함께 보면 {rank_value}순위로 볼 수 있어요."
        return f"그래서 상위 후보보다 우선순위가 낮아 {rank_value}순위로 볼 수 있어요."

    if rank <= 1:
        next_rank = int(next_ref.get("other_rank") or 2)
        commute_diff = _safe_float(next_ref.get("commute_time_gap_min"))
        deposit_text = _budget_diff_text(next_ref.get("deposit_gap_manwon"), "보증금", f"{next_rank}위 후보")
        rent_text = _budget_diff_text(next_ref.get("rent_gap_manwon"), "월세", f"{next_rank}위 후보")
        area_gap_sqm = _safe_float(next_ref.get("area_gap_sqm"))
        area_gap_pyeong = (area_gap_sqm / 3.3058) if area_gap_sqm is not None else None
        area_text = _area_diff_text(area_gap_pyeong, f"{next_rank}위 후보")
        if commute_diff is not None and abs(commute_diff) >= 2:
            if deposit_text or rent_text or area_text:
                follow = deposit_text or rent_text or area_text
                return f"{next_rank}위 후보보다 통근시간이 {abs(int(commute_diff))}분 {'짧고' if commute_diff < 0 else '길지만'}, {follow}. 그래서 자동차 통근성과 예산의 균형이 가장 좋아 1순위로 추천했어요."
            return f"{next_rank}위 후보보다 통근시간이 {abs(int(commute_diff))}분 {'짧아' if commute_diff < 0 else '길어'} 자동차 통근성과 예산의 균형이 가장 좋아 1순위로 추천했어요."
        return "바로 다음 후보와 비교해도 통근과 예산의 균형이 가장 안정적이라 1순위로 추천했어요."

    compare_to = "1위 후보" if rank == 2 else "바로 위 후보"
    source = vs_rank1 if rank == 2 else vs_prev
    commute_diff = _safe_float(source.get("duration_diff_min"))
    budget_text = _budget_diff_text(source.get("deposit_diff_manwon"), "보증금", compare_to) or _budget_diff_text(source.get("rent_diff_manwon"), "월세", compare_to)
    area_text = _area_diff_text(source.get("area_diff_pyeong"), compare_to)
    age_text = _age_diff_text(source.get("building_age_diff"), compare_to)

    if rank == 2:
        if commute_diff is not None and abs(commute_diff) >= 2:
            if budget_text:
                return f"{budget_text} 다만 통근시간이 {compare_to}보다 {abs(int(commute_diff))}분 {'길어' if commute_diff > 0 else '짧아'} 2순위로 볼 수 있어요."
            return f"통근시간이 {compare_to}보다 {abs(int(commute_diff))}분 {'길어' if commute_diff > 0 else '짧아'} 2순위로 볼 수 있어요."
        if budget_text:
            return f"통근시간 차이는 크지 않지만, {budget_text} 2순위로 볼 수 있어요."
        return "1위와 큰 차이는 없지만 통근·예산·공간을 함께 보면 조금 밀려 2순위로 볼 수 있어요."

    if commute_diff is not None and abs(commute_diff) >= 2:
        first = f"{compare_to}보다 통근시간은 {abs(int(commute_diff))}분 {'더 길지만' if commute_diff > 0 else '더 짧지만'}"
        if budget_text:
            first = f"{first}, {budget_text}"
        elif area_text:
            first = f"{first}, {area_text}"
        elif age_text:
            first = f"{first}, {age_text}"
        if area_text or age_text:
            return f"{first}. {_housing_tradeoff_sentence(area_text if area_text not in first else '', age_text if age_text not in first else '', rank)}"
        return f"{first}. 그래서 상위 후보보다 우선순위가 낮아 {rank}순위로 볼 수 있어요."
    if budget_text or area_text or age_text:
        first = budget_text or area_text or age_text
        if area_text or age_text:
            return f"{first}. {_housing_tradeoff_sentence(area_text if area_text != first else '', age_text if age_text != first else '', rank)}"
        return f"{first}. 그래서 상위 후보보다 우선순위가 낮아 {rank}순위로 볼 수 있어요."
    return f"조건 자체는 맞지만 상위 후보와 비교했을 때 뚜렷한 우위가 크지 않아 {rank}순위로 볼 수 있어요."


def build_car_final_reason(item: dict) -> str:
    _reason_key, _text = _car_reason_key_and_text(item)
    time_sentence = _car_time_profile_sentence(item)
    commute_sentence = _car_commute_sentence(item)
    budget_sentence = _car_budget_sentence(item)
    property_sentence = _car_property_sentence(item)
    living_sentence = _car_living_sentence(item)
    comparison_sentence = _car_comparison_sentence(item)
    fallback_geo_sentence = _geo_sentence_for_reason(item) if not living_sentence else ""
    parts = [time_sentence, commute_sentence, budget_sentence, property_sentence, living_sentence or fallback_geo_sentence, comparison_sentence]
    return " ".join(part for part in parts if part).strip()


def build_mode_final_reason(item: dict) -> tuple[str, str]:
    mode = _reason_mode(item)
    if mode == "walkable":
        return mode, build_walkable_final_reason(item)
    if mode == "car":
        return mode, build_car_final_reason(item)
    return mode, build_transit_final_reason(item)


def _compact_reason_block_list(blocks: list[dict]) -> list[dict]:
    compacted = []
    seen = set()
    for block in blocks:
      if not isinstance(block, dict):
          continue
      icon = str(block.get("icon") or "•").strip() or "•"
      title = str(block.get("title") or block.get("label") or "근거").strip() or "근거"
      text = _polish_final_reason_text(str(block.get("text") or block.get("description") or "").strip())
      if not text:
          continue
      signature = (title, text)
      if signature in seen:
          continue
      seen.add(signature)
      compacted.append({"icon": icon, "title": title, "text": text})
    return compacted


def _build_reason_summary_blocks(item: dict) -> list[dict]:
    mode = _reason_mode(item)
    context = item.get("explanation_context", {}) or {}
    metrics = context.get("primary_metrics", {}) or {}
    budget = context.get("budget", {}) or {}
    constraints = context.get("constraints", {}) or {}
    summary: list[dict] = []

    def _push(icon: str, title: str, text: str | None) -> None:
        if text:
            summary.append({"icon": icon, "title": title, "text": _polish_final_reason_text(str(text).strip())})

    if mode == "car":
        commute = _safe_float(metrics.get("commute_time_min") or item.get("duration_min"))
        distance_km = _safe_float(metrics.get("distance_km") or item.get("distance_km"))
        time_band = str(item.get("car_time_band") or metrics.get("car_time_band") or "").strip()
        budget_band = str(item.get("budget_band") or (item.get("explanation_context", {}) or {}).get("budget", {}).get("budget_band") or "").strip()
        distance_text = None
        if distance_km is not None:
            distance_text = f"{distance_km:.1f}km" if abs(distance_km - int(distance_km)) > 1e-6 else f"{int(distance_km)}km"
        _push("⏱", "이동", f"자동차 {int(commute)}분" if commute is not None else None)
        _push("🚗", "거리", f"주행 {distance_text}" if distance_text else None)
        _push("⌚", "시간대", _car_time_band_text(time_band) if time_band else None)
        _push("₩", "예산", _car_budget_band_text(budget_band) if budget_band else None)
        return _compact_reason_block_list(summary[:4])

    duration_min = _safe_float(metrics.get("commute_time_min") or item.get("duration_min"))
    first_walk_min = _safe_float(metrics.get("first_walk_min") or item.get("first_walk_min"))
    total_walk_min = _safe_float(metrics.get("walk_time_min") or item.get("walk_time_min"))
    total_walk_m = _safe_float(metrics.get("walk_m") or item.get("total_walk_m") or item.get("walk_distance_m"))
    transfer_count = _preferred_transfer_count(item, metrics)
    board_minutes = None
    if duration_min is not None and total_walk_min is not None:
        board_minutes = max(0, int(round(duration_min - total_walk_min)))
    mode_label = "도보" if mode == "walkable" else "대중교통"
    _push("⏱", "통근", f"{mode_label} {int(duration_min)}분" if duration_min is not None else None)
    if mode == "walkable":
        if total_walk_m is not None:
            _push("🚶", "도보", f"도보 {int(total_walk_m)}m")
    else:
        walk_text = []
        if first_walk_min is not None:
            walk_text.append(f"집→역 {int(first_walk_min)}분")
        if total_walk_min is not None:
            walk_text.append(f"전체 도보 {int(total_walk_min)}분")
        if total_walk_m is not None:
            walk_text.append(f"{int(total_walk_m)}m")
        if walk_text:
            _push("🚶", "도보", " / ".join(walk_text))
        if board_minutes is not None and board_minutes > 0:
            _push("🚌", "탑승", f"탑승 {board_minutes}분")
    if transfer_count is not None:
        _push("🔁", "환승", "환승 없음" if int(transfer_count) <= 0 else f"환승 {int(transfer_count)}회")
    budget_text = _budget_sentence_for_reason(item)
    if budget_text:
        _push("💰", "예산", budget_text)
    area_pyeong = _safe_float(budget.get("area_pyeong") or item.get("area_pyeong"))
    if area_pyeong is not None:
        if area_pyeong < 6:
            space_text = "작은 편"
        elif area_pyeong < 8:
            space_text = "무난한 편"
        elif area_pyeong < 12:
            space_text = "1인 거주 무난"
        elif area_pyeong < 18:
            space_text = "공간 여유 있음"
        else:
            space_text = "공간 여유 큼"
        _push("🏠", "공간", space_text)
    living_matches = item.get("living_matches") or context.get("living_matches") or []
    living_refs = item.get("living_reference_tags") or context.get("living_reference_tags") or []
    if living_matches or living_refs:
        living_text = livingSummaryText(item)
        if not living_text and living_refs:
            living_text = f"{living_refs[0]['label']} 참고"
        _push("🏪", "생활", living_text)
    return _compact_reason_block_list(summary[:4])

def _build_reason_detail_blocks(item: dict) -> list[dict]:
    structured = item.get("reason_detail_blocks") or item.get("structured_reason_blocks") or item.get("reason_sections") or []
    if isinstance(structured, list) and structured:
        return _compact_reason_block_list(structured)
    sections = _build_reason_sections(item)
    detail_sections = [section for section in sections if section.get("key") in {"commute", "walk", "transfer", "budget", "space", "living", "overall"}]
    return _compact_reason_block_list(detail_sections)

def _call_gemini_recommendation_explanations(recommendations: list[dict], request_state: dict | None = None) -> dict | None:
    if not GEMINI_API_KEY or not recommendations:
        return None

    prompt = (
        "You explain ranked housing recommendations in Korean.\n"
        "Do not restate raw listing specs like '통근시간 18분입니다. 보증금 100만원입니다.' as standalone explanation.\n"
        "Do not mention internal scores, contribution values, decimals, raw_score, weighted points, or engineering variable names.\n"
        "Write like a housing recommendation service, not like a ranking analysis report.\n"
        "Rules:\n"
        "1. Return Korean only.\n"
        "2. For each item, write rank_summary in one sentence that says who this home fits and, for ranks below 1, why it is not the strongest option.\n"
        "3. Write llm_reason in 3 to 4 short sentences: who it fits, 1~2 strengths, 1 caution, final judgment.\n"
        "4. Focus on user conditions such as budget burden, commute time, walking distance, transfers, transit access, and living convenience.\n"
        "4-1. If the primary mode is car only, do not mention walking to stations, transfers, subway use, or transit access unless a secondary transit preference is explicitly active.\n"
        "4-2. If both transit and car are active, mention the primary mode first but also mention the secondary mode's travel time naturally when it helps the user compare options.\n"
        "5. Do not use phrases like '바로 위 후보보다', '바로 아래 후보보다' unless absolutely necessary. Adjacent-rank comparison should be hidden background, not the main wording.\n"
        "6. Never output awkward internal terms like '예산적합도', '지하철 회피', '도보부담', 'raw_score'. Convert them into natural user-facing Korean such as '예산 조건', '지하철 이용 조건', '도보 이동 부담'.\n"
        "7. Avoid particle errors. Do not mechanically append 은/는 after internal labels.\n"
        "8. Prefer simple rounded expressions like '6만원', '약 1분'. Avoid decimals like '6.0만원' or '436.36점'.\n"
        "9. Rank tone should vary: rank 1 = strongest recommendation, rank 2~3 = good alternative, rank 4~5 = lower-priority but still viable for some users.\n"
        "10. Never mention hidden scoring systems or internal feature names.\n"
        "11. Avoid generic openings like '살펴볼 만한 후보예요' by themselves. The first sentence must feel specific and distinguishable across items.\n"
        "12. Avoid repeating the same ending phrase in every sentence. Use natural Korean such as '먼저 보기 좋아요', '함께 보면 괜찮아요', '한 번 더 비교해보면 좋아요' depending on the rank and condition.\n"
        "Example tone:\n"
        "[1위] 이 집은 보증금 부담이 낮고 통근 시간도 짧아 가장 균형이 좋은 후보예요. 예산을 아끼면서도 이동 부담이 크지 않아 무난하게 살기 좋습니다. 다만 실제 이동 동선은 한 번 확인해보는 것이 좋아요.\n"
        "[2위] 이 집은 1위만큼은 아니지만, 월세 부담과 통근 조건을 함께 보는 사람에게 충분히 좋은 대안이에요. 예산을 조금 더 쓸 수 있다면 선택 순서를 비교해볼 만합니다. 다만 도보 이동 부담은 한 번 확인해보는 것이 좋아요.\n"
        'Output JSON only. Format: {"items":[{"rank":1,"rank_summary":"...","llm_reason":"..."}]}\n'
        f"User context: {json.dumps(request_state or {}, ensure_ascii=False)}\n"
        f"Recommendation data: {json.dumps(_recommendation_prompt_payload(recommendations), ensure_ascii=False)}"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    response = requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def _collect_llm_explanations_in_batches(ranked_items: list[dict], request_state: dict | None = None) -> dict[int, dict]:
    explanation_by_rank: dict[int, dict] = {}
    batch_size = 5

    for start in range(0, len(ranked_items), batch_size):
        batch = ranked_items[start:start + batch_size]
        try:
            llm_items = _call_gemini_recommendation_explanations(batch, request_state)
        except Exception:
            llm_items = None
        if not isinstance(llm_items, dict):
            continue
        for item in llm_items.get("items", []):
            rank = item.get("rank")
            if rank is not None:
                explanation_by_rank[int(rank)] = item
    return explanation_by_rank


def enrich_recommendations_with_llm(payload: dict, request_state: dict | None = None) -> dict:
    recommendations = payload.get("recommendations") or []
    if not recommendations:
        return payload

    result = json.loads(json.dumps(payload))
    ranked_items = result.get("recommendations", [])
    primary_mode = (((request_state or {}).get("transport") or {}).get("primary_mode")) or (request_state or {}).get("transport_mode")
    selected_living = (request_state or {}).get("living_preferences") or {}
    has_selected_living = any(isinstance(value, dict) and value.get("selected") for value in selected_living.values())

    explanation_by_rank = _collect_llm_explanations_in_batches(ranked_items[:LLM_EXPLANATION_LIMIT], request_state)

    for item in ranked_items:
        try:
            explanation = explanation_by_rank.get(int(item.get("rank") or 0), {})
            llm_rank_summary = (explanation.get("rank_summary") or "").strip()
            llm_reason = (explanation.get("llm_reason") or "").strip()
            if not llm_rank_summary or not llm_reason:
                fallback_summary, fallback_detail = _fallback_rank_explanation(item)
                llm_rank_summary = llm_rank_summary or fallback_summary
                llm_reason = llm_reason or fallback_detail
            llm_rank_summary, llm_reason = _normalize_generated_explanation(item, llm_rank_summary, llm_reason)
            item["llm_rank_summary"] = llm_rank_summary
            item["llm_reason"] = llm_reason
            mode, final_reason = build_mode_final_reason(item)
            item["reason_mode"] = mode
            item["reason_key"] = mode
            item["short_reason"] = _deterministic_display_reason(item)
            item["summary_reason"] = item["short_reason"]
            item["final_reason"] = _polish_final_reason_text(final_reason)
            item["reason"] = item["final_reason"]
            item["recommendation_reason"] = item["final_reason"]
            item["explanation"] = item["final_reason"]
            item["reason_source"] = f"{mode}_detailed_reason"
            item["missing_reason_fields"] = _car_reason_missing_fields(item) if mode == "car" else []
            item["car_reason_generation_failed"] = False
            item["reason_sections"] = _build_reason_sections(item)
            item["reason_summary_blocks"] = _build_reason_summary_blocks(item)
            item["reason_detail_blocks"] = _build_reason_detail_blocks(item)
            item["structured_reason_blocks"] = item["reason_detail_blocks"]
            if mode == "car":
                item["car_reason"] = item["final_reason"]
        except Exception as exc:
            fallback_summary, fallback_detail = _fallback_rank_explanation(item)
            fallback_text = _polish_final_reason_text(" ".join(part for part in [fallback_summary, fallback_detail] if part).strip())
            mode = _reason_mode(item)
            item["llm_rank_summary"] = fallback_summary
            item["llm_reason"] = fallback_detail
            item["reason_mode"] = mode
            item["reason_key"] = mode
            item["short_reason"] = _deterministic_display_reason(item)
            item["summary_reason"] = item["short_reason"]
            item["final_reason"] = fallback_text
            item["reason"] = fallback_text
            item["recommendation_reason"] = fallback_text
            item["explanation"] = fallback_text
            item["reason_source"] = "fallback_missing_car_fields" if mode == "car" else f"{mode}_fallback_reason"
            item["missing_reason_fields"] = _car_reason_missing_fields(item) if mode == "car" else []
            item["car_reason_generation_failed"] = True
            item["reason_generation_error"] = exc.__class__.__name__
            item["reason_sections"] = _build_reason_sections(item)
            item["reason_summary_blocks"] = _build_reason_summary_blocks(item)
            item["reason_detail_blocks"] = _build_reason_detail_blocks(item)
            item["structured_reason_blocks"] = item["reason_detail_blocks"]
            if mode == "car":
                item["car_reason"] = fallback_text

    result.setdefault("meta", {})
    result["meta"]["used_llm_for_explanations"] = bool(explanation_by_rank)
    return result








