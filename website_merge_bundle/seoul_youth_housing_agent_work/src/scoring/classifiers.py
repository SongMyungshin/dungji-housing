import pandas as pd

from config.settings import EXCLUDED_LAND_CATEGORIES


def normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def normalize_station_status(text):
    value = normalize_text(text).lower()
    if not value:
        return ""

    include_tokens = [
        "verified_in",
        "포함확인",
        "역세권 포함",
        "역세권 해당",
    ]
    exclude_tokens = [
        "verified_out",
        "제외확인",
        "역세권 제외",
        "역세권 비해당",
    ]

    if any(token in value for token in include_tokens):
        return "포함확인"
    if any(token in value for token in exclude_tokens):
        return "제외확인"
    return ""


def classify_asset_project(asset_type):
    text = normalize_text(asset_type)
    if "건물" in text:
        return "건물 리모델링형", 28
    if "토지" in text:
        return "토지 신축형", 25
    return "기타 후보지", 12


def classify_land_category(value):
    text = normalize_text(value)
    if not text:
        return "미확인", 5
    if text in {"대", "잡종지"}:
        return "우선검토", 30
    if text in {"전", "답"}:
        return "추가검토", 18
    if text in EXCLUDED_LAND_CATEGORIES:
        return "제외", 0
    return "기타검토", 10


def classify_use_region(main_text, sub_text, special_text):
    combined = " | ".join(
        [
            text
            for text in [
                normalize_text(main_text),
                normalize_text(sub_text),
                normalize_text(special_text),
            ]
            if text
        ]
    )
    if not combined:
        return "미확인", 5, False

    has_special = bool(normalize_text(special_text)) or contains_any(
        combined, ["특별지구", "보호지구", "미관지구"]
    )

    # 청년주거 관점에서는 고밀·혼합 개발이 가능한 용도지역을 우선한다.
    if "준주거지역" in combined:
        return "최우선 검토", 35, has_special
    if contains_any(combined, ["근린상업지역", "일반상업지역", "중심상업지역"]):
        return "상업혼합 검토", 32, has_special
    if "제3종일반주거지역" in combined:
        return "고밀 주거형 검토", 28, has_special
    if "제2종일반주거지역" in combined:
        return "중밀 주거형 검토", 24, has_special
    if contains_any(combined, ["제1종일반주거지역", "제1종전용주거지역"]):
        return "저밀 주거형 검토", 16, True if "제1종전용주거지역" in combined else has_special
    if "준공업지역" in combined:
        return "준공업 혼합 검토", 22, has_special
    if "일반주거지역" in combined:
        return "주거지역 검토", 20, has_special
    if contains_any(combined, ["자연녹지지역", "보전녹지지역", "생산녹지지역", "자연환경보전지역"]):
        return "녹지지역 추가검토", 10, True
    if contains_any(combined, ["특별지구", "보호지구"]):
        return "보호지구 추가검토", 8, True
    return "기타검토", 10, has_special


def classify_area(value):
    if pd.isna(value):
        return "미확인", 0
    area = float(value)
    if area < 60:
        return "제외", 0
    if area < 150:
        return "추가검토", 12
    if area < 200:
        return "소규모 개발 검토 가능", 20
    if area < 330:
        return "본격 검토", 30
    return "우선검토 규모", 40


def classify_station_access(
    asset_type,
    explicit_status,
    explicit_basis,
    nearest_station,
    nearest_station_distance,
    image_available,
):
    asset = normalize_text(asset_type)
    status = normalize_station_status(explicit_status)
    basis = normalize_text(explicit_basis)
    nearest = normalize_text(nearest_station) or "미확인"
    image_flag = normalize_text(image_available).upper() in {"Y", "YES", "TRUE", "1"}

    distance = None
    try:
        if not pd.isna(nearest_station_distance):
            distance = float(nearest_station_distance)
    except Exception:
        distance = None

    # 이미지/승강장 경계 원본이 없을 때는 점거리 기반 예비판정을 사용한다.
    if status == "포함확인":
        band = "확인완료"
        candidate_type = "역세권 확인 후보"
        score = 25
        confidence = "높음"
        needs_recheck = False
        if not basis:
            basis = "이미지 또는 공간자료 기준으로 역세권 포함이 확인됨"
    elif status == "제외확인":
        band = "확인완료"
        candidate_type = "비역세권 확인"
        score = 0
        confidence = "높음"
        needs_recheck = False
        if not basis:
            basis = "이미지 또는 공간자료 기준으로 역세권 제외가 확인됨"
    elif distance is None:
        status = "정보부족"
        band = "거리정보없음"
        candidate_type = "판정유보"
        score = 0
        confidence = "낮음"
        needs_recheck = True
        if not basis:
            basis = "역세권 예비판정에 필요한 거리정보가 없어 추가 자료가 필요함"
    elif distance <= 250:
        status = "예비판정"
        band = "250m 이내"
        candidate_type = "역세권 유력 후보"
        score = 22
        confidence = "중간"
        needs_recheck = True
        if not basis:
            basis = (
                f"{nearest} 기준 점거리 {distance:.1f}m로 250m 이내 예비구간에 해당하며, "
                "승강장 경계 기준 재판정이 필요함"
            )
    elif distance <= 350:
        status = "예비판정"
        band = "250~350m"
        candidate_type = "조건부 역세권 후보"
        score = 12
        confidence = "중간"
        needs_recheck = True
        if not basis:
            basis = (
                f"{nearest} 기준 점거리 {distance:.1f}m로 250~350m 예비구간에 해당하며, "
                "승강장 경계 기준 재판정이 필요함"
            )
    else:
        status = "예비판정"
        band = "350m 초과"
        candidate_type = "비역세권 후보"
        score = 0
        confidence = "중간" if image_flag else "낮음"
        needs_recheck = False
        if not basis:
            basis = f"{nearest} 기준 점거리 {distance:.1f}m로 350m를 초과하는 예비구간임"

    image_review = "권장" if image_flag else "자료없음"
    if image_flag and band in {"250m 이내", "250~350m"}:
        image_review = "상위후보 우선검토"

    # 건물은 리모델링형으로 분류하지만, 역 접근성 자체는 여전히 설명 변수로 유지한다.
    if asset == "건물" and candidate_type == "비역세권 후보":
        basis = basis or "건물형 후보이며 역 접근성은 낮은 편으로 검토됨"

    return (
        status,
        band,
        candidate_type,
        score,
        confidence,
        basis,
        nearest,
        image_review,
        needs_recheck,
    )


def classify_policy_type(asset_type, station_candidate_type, station_band):
    asset = normalize_text(asset_type)
    candidate = normalize_text(station_candidate_type)
    band = normalize_text(station_band)

    if asset == "건물":
        return "리모델링형 청년주거", 12
    if asset != "토지":
        return "기타 후보지", 0

    if candidate in {"역세권 확인 후보", "역세권 유력 후보"}:
        return "청년안심주택형 유력", 15
    if candidate == "조건부 역세권 후보":
        return "조건부 역세권 개발형", 8
    if candidate == "비역세권 후보":
        return "기타 후보지", 0
    if band == "거리정보없음":
        return "역세권 정보보강 필요", 0
    return "기타 후보지", 0
