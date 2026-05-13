import json
import math
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd
import requests
from pyproj import Transformer
from requests import RequestException


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IS_VERCEL_DEPLOYMENT = bool(os.getenv("VERCEL"))
CACHE_DIR = Path(
    os.getenv(
        "DUNGJI_CACHE_DIR",
        str(Path(tempfile.gettempdir()) / "dungji-cache") if os.getenv("VERCEL") else str(BASE_DIR / "cache")
    )
)
DOTENV_FILE = BASE_DIR / ".env"
GEOCODE_CACHE_FILE = CACHE_DIR / "geocode_cache.json"
ROUTE_CACHE_FILE = CACHE_DIR / "route_cache.json"
ROUTE_DISPLAY_VERSION = 13
ROUTE_DISPLAY_MODE = os.getenv("ROUTE_DISPLAY_MODE", "odsay").strip().lower()
if ROUTE_DISPLAY_MODE not in {"odsay", "kakao"}:
    ROUTE_DISPLAY_MODE = "odsay"

DEFAULT_TRANSPORT_MODE = "transit"
TOP_N = 20
CAR_ROUTE_SAMPLE_LIMIT = 12 if IS_VERCEL_DEPLOYMENT else 20
TRANSIT_ROUTE_SAMPLE_LIMIT = 4 if IS_VERCEL_DEPLOYMENT else 8
SECONDARY_TRANSIT_SAMPLE_LIMIT = 0 if IS_VERCEL_DEPLOYMENT else 5
TRANSIT_ROUTE_AXIS_LIMIT = 3 if IS_VERCEL_DEPLOYMENT else 6
CAR_ROUTE_AXIS_LIMIT = 4 if IS_VERCEL_DEPLOYMENT else 8
TRANSIT_ROUTE_POOL_LIMIT = 4 if IS_VERCEL_DEPLOYMENT else 18
CAR_ROUTE_POOL_LIMIT = 4 if IS_VERCEL_DEPLOYMENT else 18
REQUEST_TIMEOUT = 10 if IS_VERCEL_DEPLOYMENT else 15
CAR_REQUEST_TIMEOUT = 10 if IS_VERCEL_DEPLOYMENT else 15
TRANSIT_REQUEST_TIMEOUT = 8 if IS_VERCEL_DEPLOYMENT else 10
WALKING_REQUEST_TIMEOUT = 8
CAR_ROUTE_WORKERS = 1 if IS_VERCEL_DEPLOYMENT else 2
TRANSIT_ROUTE_WORKERS = 1 if (ROUTE_DISPLAY_MODE == "kakao" or IS_VERCEL_DEPLOYMENT) else 4
WALKABLE_AUTO_MAX_M = 700
WALKABLE_OPTIONAL_MAX_M = 1000
NEAR_DESTINATION_MAX_KM = 1.5
MID_DISTANCE_MAX_KM = 3.0
FAR_DISTANCE_THRESHOLD_KM = 5.0

HOUSE_TYPE_LABELS = {
    "officetel": "오피스텔",
    "apartment": "아파트",
    "villa": "빌라",
    "studio": "원룸",
    "two_room": "투룸",
}

PRIORITY_MULTIPLIER = {
    "high": 1.35,
    "medium": 1.0,
    "low": 0.75,
    "none": 0.45,
}

BASE_WEIGHT_MAP = {
    "commute": 45,
    "walking": 15,
    "transfer": 15,
    "budget": 20,
    "transit_access": 5,
    "infra": 12,
    "geo_preference": 0,
    "target_proximity": 25,
    "car_distance": 10,
    "area": 10,
}

FACTOR_META = {
    "commute": {"label": "통근시간", "score_key": "commute_score"},
    "walking": {"label": "도보 이동 부담", "score_key": "walking_score"},
    "transfer": {"label": "환승 부담", "score_key": "transfer_score"},
    "budget": {"label": "예산 조건", "score_key": "budget_score"},
    "transit_access": {"label": "대중교통 접근성", "score_key": "transit_access_score"},
    "infra": {"label": "생활 인프라", "score_key": "infra_score"},
    "geo_preference": {"label": "지역 선호", "score_key": "geo_preference_score"},
    "target_proximity": {"label": "목적지 근접성", "score_key": "target_proximity_score"},
    "car_distance": {"label": "자동차 주행거리", "score_key": "car_distance_score"},
    "area": {"label": "면적 조건", "score_key": "area_score"},
    "subway": {"label": "지하철 이용 조건", "score_key": "subway_score"},
}

CAR_TIME_BAND_SCORE = {
    "very_relaxed": 100.0,
    "relaxed": 90.0,
    "comfortable": 80.0,
    "within_limit": 65.0,
    "over_limit": 0.0,
    "unknown": 0.0,
}

CAR_DISTANCE_BAND_SCORE = {
    "very_near": 100.0,
    "near": 90.0,
    "normal": 75.0,
    "far": 55.0,
    "very_far": 35.0,
    "unknown": 0.0,
}

BUDGET_BAND_SCORE = {
    "very_light": 100.0,
    "light": 90.0,
    "moderate": 78.0,
    "tight": 60.0,
    "over_budget": 0.0,
    "unknown": 60.0,
}

KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_MOBILITY_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"
KAKAO_WALKING_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/affiliate/walking/v1/directions"
KAKAO_PUBTRANS_URL = "https://map.kakao.com/route/pubtrans.json"
ODSAY_SEARCH_URL = "https://api.odsay.com/v1/api/searchPubTransPathT"
ODSAY_LANE_URL = "https://api.odsay.com/api/loadLane"
TMAP_TRANSIT_URL = "https://apis.openapi.sk.com/transit/routes"
TMAP_CAR_ROUTE_URL = "https://apis.openapi.sk.com/tmap/routes"
TMAP_TIME_MACHINE_URL = "https://apis.openapi.sk.com/tmap/routes/prediction"


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
KAKAO_JS_KEY = os.getenv("KAKAO_JS_KEY", "")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
ODSAY_API_KEY = os.getenv("ODSAY_API_KEY", "")
TMAP_TRANSIT_APP_KEY = os.getenv("TMAP_TRANSIT_APP_KEY", "")
TMAP_APP_KEY = os.getenv("TMAP_APP_KEY", "") or TMAP_TRANSIT_APP_KEY


def ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(exist_ok=True)


def load_json_cache(path: Path) -> dict:
    ensure_cache_dir()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json_cache(path: Path, payload: dict) -> None:
    ensure_cache_dir()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


GEOCODE_CACHE = load_json_cache(GEOCODE_CACHE_FILE)
ROUTE_CACHE = load_json_cache(ROUTE_CACHE_FILE)
ROUTE_CACHE_LOCK = Lock()
ROUTE_CACHE_DIRTY = False
KAKAO_TRANSIT_FETCH_LOCK = Lock()
LISTING_FRAME_CACHE: pd.DataFrame | None = None
LISTING_FRAME_CACHE_SIGNATURE: tuple | None = None
DISTRICT_CACHE: list[str] | None = None
DISTRICT_CACHE_SIGNATURE: tuple | None = None
POI_FRAME_CACHE: dict[str, tuple[tuple, pd.DataFrame]] = {}
TM128_TO_WGS84 = Transformer.from_crs("EPSG:2097", "EPSG:4326", always_xy=True)
WGS84_TO_WCONG = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)
WCONG_TO_WGS84 = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)

LIVING_CATEGORY_META = {
    "laundry": {"label": "세탁소", "near_m": 500, "acceptable_m": 800, "file": DATA_DIR / "서울시_영업중_세탁업_주소좌표.csv"},
    "convenience_store": {"label": "편의점", "near_m": 300, "acceptable_m": 500, "file": DATA_DIR / "서울시_영업중_휴게음식점.csv"},
    "cafe": {"label": "카페", "near_m": 500, "acceptable_m": 800, "file": DATA_DIR / "서울시_영업중_휴게음식점.csv"},
    "light_food_snack": {"label": "간단음식/간식", "near_m": 500, "acceptable_m": 800, "file": DATA_DIR / "서울시_영업중_휴게음식점.csv"},
    "gym": {"label": "헬스장", "near_m": 800, "acceptable_m": 1200, "file": DATA_DIR / "서울시_영업중_체력단련장.csv"},
    "hospital": {"label": "병원", "near_m": 1000, "acceptable_m": 1500, "file": DATA_DIR / "서울시_병원_정리.csv"},
    "large_store": {"label": "대형점포", "near_m": 1500, "acceptable_m": 2500, "file": DATA_DIR / "서울시_영업중_대규모점포.csv"},
}

HYGIENE_TYPE_TO_CATEGORY = {
    "편의점": "convenience_store",
    "커피숍": "cafe",
    "다방": "cafe",
    "전통찻집": "cafe",
    "떡카페": "cafe",
    "일반조리판매": "light_food_snack",
    "패스트푸드": "light_food_snack",
    "아이스크림": "light_food_snack",
    "과자점": "light_food_snack",
    "기타 휴게음식점": "light_food_snack",
    "푸드트럭": "light_food_snack",
    "철도역구내": "station_food_reference",
    "극장": "culture_leisure_reference",
    "유원지": "culture_leisure_reference",
    "키즈카페": "culture_leisure_reference",
    "호프/통닭": "nightlife_reference",
    "단란주점": "nightlife_reference",
    "백화점": None,
    "관광호텔": None,
    "공항": None,
    "고속도로": None,
}

INFRA_CATEGORY_SCORING_POLICY = {
    "convenience_store": {"score_enabled": True, "score_weight": 1.0, "display_name": "편의점"},
    "cafe": {"score_enabled": True, "score_weight": 1.0, "display_name": "카페"},
    "light_food_snack": {"score_enabled": True, "score_weight": 0.8, "display_name": "간단음식/간식"},
    "station_food_reference": {"score_enabled": False, "reference_tag_enabled": True, "display_name": "역 주변 편의시설"},
    "culture_leisure_reference": {"score_enabled": False, "reference_tag_enabled": True, "display_name": "문화/여가 시설"},
    "nightlife_reference": {"score_enabled": False, "reference_tag_enabled": True, "display_name": "야간 상권"},
}

REFERENCE_CATEGORY_RADIUS = {
    "station_food_reference": 400,
    "culture_leisure_reference": 500,
    "nightlife_reference": 500,
}


def to_number(value, default=0.0):
    if pd.isna(value):
        return default
    cleaned = str(value).replace(",", "").strip()
    if not cleaned:
        return default
    try:
        return float(cleaned)
    except Exception:
        return default


def _route_cache_get(key: str):
    with ROUTE_CACHE_LOCK:
        return ROUTE_CACHE.get(key)


def _route_cache_set(key: str, value) -> None:
    global ROUTE_CACHE_DIRTY
    with ROUTE_CACHE_LOCK:
        ROUTE_CACHE[key] = value
        ROUTE_CACHE_DIRTY = True


def _route_cache_delete(key: str) -> None:
    global ROUTE_CACHE_DIRTY
    with ROUTE_CACHE_LOCK:
        if key in ROUTE_CACHE:
            ROUTE_CACHE.pop(key, None)
            ROUTE_CACHE_DIRTY = True


def flush_route_cache() -> None:
    global ROUTE_CACHE_DIRTY
    with ROUTE_CACHE_LOCK:
        if not ROUTE_CACHE_DIRTY:
            return
        save_json_cache(ROUTE_CACHE_FILE, ROUTE_CACHE)
        ROUTE_CACHE_DIRTY = False


def _is_invalid_car_cache_entry(value) -> bool:
    if not isinstance(value, dict):
        return True
    duration_min = to_number(value.get("duration_min"), None)
    distance_km = to_number(value.get("distance_km"), None)
    path_segments = value.get("path_segments") or []
    if duration_min is None or distance_km is None:
        return True
    if duration_min <= 1 and distance_km <= 0:
        return True
    if duration_min <= 2 and not path_segments:
        return True
    return False


def _is_low_fidelity_transit_route(value) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("route_type") != "transit":
        return False
    path_segments = value.get("path_segments") or []
    transit_segments = [segment for segment in path_segments if (segment or {}).get("type") in {"bus", "subway"}]
    if not transit_segments:
        return False
    # Older cache entries were built from station anchors only, so each transit segment
    # collapsed to a simple straight line with two points.
    return all(len((segment or {}).get("points") or []) <= 2 for segment in transit_segments)


def _segment_kind(segment: dict | None) -> str:
    raw = str((segment or {}).get("type") or (segment or {}).get("normalized_type") or "").strip().lower()
    if raw in {"walk", "bus", "subway", "car"}:
        return raw
    return raw


def _is_stale_transit_display_cache(value) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("route_type") != "transit":
        return False
    if int(to_number(value.get("route_display_version"), 0) or 0) != ROUTE_DISPLAY_VERSION:
        return True
    if str(value.get("route_display_mode") or "").strip().lower() != ROUTE_DISPLAY_MODE:
        return True
    display_steps = value.get("display_steps") or []
    display_segments = value.get("display_path_segments") or []
    if not display_steps or not display_segments:
        return True
    if len(display_steps) != len(display_segments):
        return True
    for step, segment in zip(display_steps, display_segments):
        step_type = _segment_kind(step)
        segment_type = _segment_kind(segment)
        if step_type != segment_type:
            return True
        if step_type == "subway" and segment_type == "bus":
            return True
    return False


def cleanup_route_cache() -> None:
    global ROUTE_CACHE_DIRTY
    removed = []
    with ROUTE_CACHE_LOCK:
        for key in list(ROUTE_CACHE.keys()):
            value = ROUTE_CACHE.get(key)
            if str(key).startswith("car:") and _is_invalid_car_cache_entry(value):
                removed.append(key)
                ROUTE_CACHE.pop(key, None)
            elif str(key).startswith("transit:") and _is_low_fidelity_transit_route(value):
                removed.append(key)
                ROUTE_CACHE.pop(key, None)
            elif str(key).startswith("transit:") and _is_stale_transit_display_cache(value):
                removed.append(key)
                ROUTE_CACHE.pop(key, None)
            elif str(key).startswith("transit:") and isinstance(value, dict) and value.get("route_type") == "unavailable":
                removed.append(key)
                ROUTE_CACHE.pop(key, None)
        if removed:
            ROUTE_CACHE_DIRTY = True
    if removed:
        flush_route_cache()


cleanup_route_cache()


def sqm_to_display_pyeong(area_sqm) -> int | None:
    numeric = to_number(area_sqm, None)
    if numeric is None:
        return None
    if numeric <= 0:
        return None
    return int(math.floor((numeric / 3.3058) + 0.5))


def _safe_int(value):
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius_km = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return haversine_km(lat1, lng1, lat2, lng2) * 1000.0


def _transform_tm128_to_wgs84(x_value, y_value) -> tuple[float | None, float | None]:
    try:
        x = float(str(x_value).strip())
        y = float(str(y_value).strip())
    except Exception:
        return None, None
    if not x or not y:
        return None, None
    try:
        lng, lat = TM128_TO_WGS84.transform(x, y)
    except Exception:
        return None, None
    return float(lat), float(lng)


def _poi_signature(path: Path) -> tuple:
    exists = path.exists()
    stat = path.stat() if exists else None
    return (str(path), exists, stat.st_mtime_ns if stat else None, stat.st_size if stat else None)


def _classify_snack_csv_row(row: dict) -> str | None:
    business_type = str(row.get("위생업태명") or "").strip()
    name = str(row.get("사업장명") or "").strip()
    lowered_name = name.lower()

    convenience_patterns = ["cu", "gs25", "g25", "세븐일레븐", "이마트24", "emart24", "씨유", "지에스25", "미니스톱"]
    cafe_patterns = ["카페", "커피", "스타벅스", "투썸", "이디야", "메가", "컴포즈", "빽다방", "할리스", "엔젤리너스", "커피빈"]

    if any(pattern in lowered_name or pattern in name for pattern in convenience_patterns):
        return "convenience_store"
    if any(pattern in lowered_name or pattern in name for pattern in cafe_patterns):
        return "cafe"
    return HYGIENE_TYPE_TO_CATEGORY.get(business_type)


def _load_poi_frame(category: str) -> pd.DataFrame:
    meta = LIVING_CATEGORY_META.get(category) or {}
    path = meta.get("file")
    if not path or not Path(path).exists():
        return pd.DataFrame(columns=["name", "lat", "lng"])

    signature = _poi_signature(Path(path))
    cached = POI_FRAME_CACHE.get(category)
    if isinstance(cached, tuple) and cached[0] == signature:
        return cached[1]

    try:
        frame = pd.read_csv(path)
    except Exception:
        normalized = pd.DataFrame(columns=["name", "lat", "lng"])
        POI_FRAME_CACHE[category] = (signature, normalized)
        return normalized

    rows = []
    if category == "hospital":
        for row in frame.to_dict(orient="records"):
            lat = to_number(row.get("병원위도"), None)
            lng = to_number(row.get("병원경도"), None)
            if lat is None or lng is None:
                continue
            rows.append({"name": row.get("기관명") or "병원", "lat": lat, "lng": lng})
    elif path.name.endswith("휴게음식점.csv"):
        for row in frame.to_dict(orient="records"):
            classified = _classify_snack_csv_row(row)
            if classified != category:
                continue
            lat, lng = _transform_tm128_to_wgs84(row.get("좌표정보(X)"), row.get("좌표정보(Y)"))
            if lat is None or lng is None:
                continue
            rows.append({
                "name": row.get("사업장명") or LIVING_CATEGORY_META.get(category, {}).get("label") or category,
                "lat": lat,
                "lng": lng,
                "raw_type": str(row.get("위생업태명") or "").strip(),
            })
    else:
        for row in frame.to_dict(orient="records"):
            lat, lng = _transform_tm128_to_wgs84(row.get("좌표정보(X)"), row.get("좌표정보(Y)"))
            if lat is None or lng is None:
                continue
            rows.append({"name": row.get("사업장명") or meta.get("label") or category, "lat": lat, "lng": lng})

    normalized = pd.DataFrame(rows, columns=["name", "lat", "lng"]).dropna(subset=["lat", "lng"])
    POI_FRAME_CACHE[category] = (signature, normalized)
    return normalized


def _living_distance_score(distance_m: float | None, near_m: float, acceptable_m: float) -> float:
    if distance_m is None:
        return 0.0
    if distance_m <= near_m:
        return 100.0
    if distance_m >= acceptable_m:
        return 0.0
    ratio = (distance_m - near_m) / max(1.0, (acceptable_m - near_m))
    return max(0.0, 100.0 - (ratio * 100.0))


def evaluate_reference_categories(geo: dict) -> list[dict]:
    snack_path = DATA_DIR / "서울시_영업중_휴게음식점.csv"
    signature = _poi_signature(snack_path)
    cache_key = "__reference_snack__"
    cached = POI_FRAME_CACHE.get(cache_key)
    if isinstance(cached, tuple) and cached[0] == signature:
        source_frame = cached[1]
    else:
        try:
            raw = pd.read_csv(snack_path)
        except Exception:
            return []
        rows = []
        for row in raw.to_dict(orient="records"):
            category = _classify_snack_csv_row(row)
            policy = INFRA_CATEGORY_SCORING_POLICY.get(category or "")
            if not policy or not policy.get("reference_tag_enabled"):
                continue
            lat, lng = _transform_tm128_to_wgs84(row.get("좌표정보(X)"), row.get("좌표정보(Y)"))
            if lat is None or lng is None:
                continue
            rows.append({
                "name": row.get("사업장명") or policy.get("display_name") or "참고 시설",
                "lat": lat,
                "lng": lng,
                "category": category,
                "label": policy.get("display_name") or category,
            })
        source_frame = pd.DataFrame(rows)
        POI_FRAME_CACHE[cache_key] = (signature, source_frame)

    references = []
    if source_frame.empty:
        return references
    for category, radius_m in REFERENCE_CATEGORY_RADIUS.items():
        subset = source_frame[source_frame["category"] == category]
        if subset.empty:
            continue
        best_distance = None
        best_label = INFRA_CATEGORY_SCORING_POLICY.get(category, {}).get("display_name") or category
        for poi in subset.itertuples(index=False):
            distance_m = haversine_m(geo["lat"], geo["lng"], float(poi.lat), float(poi.lng))
            if best_distance is None or distance_m < best_distance:
                best_distance = distance_m
        if best_distance is not None and best_distance <= radius_m:
            references.append({
                "category": category,
                "label": best_label,
                "distance_m": int(round(best_distance)),
            })
    return references


def evaluate_living_preferences(geo: dict, living_preferences: dict | None) -> dict:
    preferences = living_preferences or {}
    selected = []
    for category, config in preferences.items():
        if isinstance(config, dict) and config.get("selected"):
            selected.append((category, config))

    if not selected:
        return {"infra_score": 0.0, "matches": [], "details": [], "reference_tags": []}

    details = []
    for category, config in selected:
        meta = LIVING_CATEGORY_META.get(category)
        if not meta:
            continue
        poi_frame = _load_poi_frame(category)
        if poi_frame.empty:
            details.append({
                "category": category,
                "label": meta["label"],
                "distance_m": None,
                "score": 0.0,
                "matched": False,
            })
            continue

        max_walk_minutes = config.get("max_walk_minutes")
        if max_walk_minutes is not None:
            walk_minutes = max(1, int(max_walk_minutes))
            near_m = int(config.get("near_m") or round(walk_minutes * 70))
            acceptable_m = int(config.get("acceptable_m") or max(round(walk_minutes * 90), near_m + 200))
        else:
            near_m = int(config.get("near_m") or meta["near_m"])
            acceptable_m = int(config.get("acceptable_m") or meta["acceptable_m"])
        policy = INFRA_CATEGORY_SCORING_POLICY.get(category, {})
        score_weight = float(policy.get("score_weight", 1.0))
        best_distance = None
        best_name = None
        best_lat = None
        best_lng = None
        for poi in poi_frame.itertuples(index=False):
            distance_m = haversine_m(geo["lat"], geo["lng"], float(poi.lat), float(poi.lng))
            if best_distance is None or distance_m < best_distance:
                best_distance = distance_m
                best_name = str(poi.name)
                best_lat = float(poi.lat)
                best_lng = float(poi.lng)
        score = _living_distance_score(best_distance, near_m, acceptable_m) * score_weight
        details.append({
            "category": category,
            "label": meta["label"],
            "distance_m": int(round(best_distance)) if best_distance is not None else None,
            "score": round(score, 2),
            "matched": bool(best_distance is not None and best_distance <= acceptable_m),
            "place_name": best_name,
            "lat": best_lat,
            "lng": best_lng,
        })

    if not details:
        return {"infra_score": 0.0, "matches": [], "details": [], "reference_tags": []}

    infra_score = sum(item["score"] for item in details) / len(details)
    matches = [item for item in details if item.get("matched")]
    reference_tags = evaluate_reference_categories(geo)
    return {"infra_score": round(infra_score, 2), "matches": matches, "details": details, "reference_tags": reference_tags}


def _csv_paths() -> dict:
    return {
        "apartment": DATA_DIR / "25gu_apt.csv",
        "multi_family": DATA_DIR / "25gu_mhouse_all.csv",
        "officetel": DATA_DIR / "25gu_officetel_all.csv",
    }


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


STANDARD_DATA_COLUMNS = [
    "district",
    "legal_dong",
    "jibun",
    "deal_type",
    "deposit_eok",
    "monthly_rent_manwon",
    "area_sqm",
    "built_year",
    "listing_name",
    "residence_type",
    "house_type",
    "x",
    "y",
    "matched_name",
    "matched_address",
    "coord_method",
]


def _standardize_listing_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    if len(frame.columns) < len(STANDARD_DATA_COLUMNS):
        return pd.DataFrame()
    normalized = frame.iloc[:, : len(STANDARD_DATA_COLUMNS)].copy()
    normalized.columns = STANDARD_DATA_COLUMNS
    return normalized


def _listing_file_signature() -> tuple:
    signature = []
    for path in _csv_paths().values():
        exists = path.exists()
        stat = path.stat() if exists else None
        signature.append((str(path), exists, stat.st_mtime_ns if stat else None, stat.st_size if stat else None))
    return tuple(signature)


def available_districts() -> list[str]:
    global DISTRICT_CACHE, DISTRICT_CACHE_SIGNATURE
    signature = _listing_file_signature()
    if DISTRICT_CACHE is not None and DISTRICT_CACHE_SIGNATURE == signature:
        return DISTRICT_CACHE

    districts = set()
    for path in _csv_paths().values():
        if not path.exists():
            continue
        try:
            frame = _standardize_listing_frame(pd.read_csv(path))
            if frame.empty:
                continue
            districts.update(frame["district"].dropna().astype(str).tolist())
        except Exception:
            continue
    DISTRICT_CACHE = sorted(districts)
    DISTRICT_CACHE_SIGNATURE = signature
    return DISTRICT_CACHE


def _load_apartment() -> pd.DataFrame:
    frame = _standardize_listing_frame(_read_csv(_csv_paths()["apartment"]))
    if frame.empty:
        return frame
    return pd.DataFrame(
        {
            "district": frame["district"].astype(str),
            "legal_dong": frame["legal_dong"].fillna("").astype(str),
            "jibun": frame["jibun"].fillna("").astype(str),
            "address": frame["matched_address"].fillna("").astype(str),
            "listing_name": frame["listing_name"].fillna("").astype(str),
            "house_type": frame["house_type"].fillna("아파트").astype(str),
            "deal_type": frame["deal_type"].fillna("").astype(str),
            "deposit_manwon": frame["deposit_eok"].apply(lambda value: to_number(value, None) * 10000 if pd.notna(value) else None),
            "monthly_rent_manwon": frame["monthly_rent_manwon"].apply(lambda value: to_number(value, None)),
            "area_sqm": frame["area_sqm"].apply(lambda value: to_number(value, 0)),
            "floor": pd.Series([""] * len(frame), index=frame.index),
            "contract_date": pd.Series([""] * len(frame), index=frame.index),
            "lat": frame["y"].apply(lambda value: to_number(value, None)),
            "lng": frame["x"].apply(lambda value: to_number(value, None)),
            "has_price_info": True,
        }
    )


def _load_multi_family() -> pd.DataFrame:
    frame = _standardize_listing_frame(_read_csv(_csv_paths()["multi_family"]))
    if frame.empty:
        return frame
    return pd.DataFrame(
        {
            "district": frame["district"].astype(str),
            "legal_dong": frame["legal_dong"].fillna("").astype(str),
            "jibun": frame["jibun"].fillna("").astype(str),
            "address": frame["matched_address"].fillna("").astype(str),
            "listing_name": frame["listing_name"].fillna("").astype(str),
            "house_type": frame["house_type"].fillna("?ㅼ꽭?").astype(str),
            "deal_type": frame["deal_type"].fillna("").astype(str),
            "deposit_manwon": frame["deposit_eok"].apply(lambda value: to_number(value, None) * 10000 if pd.notna(value) else None),
            "monthly_rent_manwon": frame["monthly_rent_manwon"].apply(lambda value: to_number(value, None)),
            "area_sqm": frame["area_sqm"].apply(lambda value: to_number(value, 0)),
            "floor": pd.Series([""] * len(frame), index=frame.index),
            "contract_date": pd.Series([""] * len(frame), index=frame.index),
            "lat": frame["y"].apply(lambda value: to_number(value, None)),
            "lng": frame["x"].apply(lambda value: to_number(value, None)),
            "has_price_info": True,
        }
    )


def _load_officetel() -> pd.DataFrame:
    frame = _standardize_listing_frame(_read_csv(_csv_paths()["officetel"]))
    if frame.empty:
        return frame
    return pd.DataFrame(
        {
            "district": frame["district"].astype(str),
            "legal_dong": frame["legal_dong"].fillna("").astype(str),
            "jibun": frame["jibun"].fillna("").astype(str),
            "address": frame["matched_address"].fillna("").astype(str),
            "listing_name": frame["listing_name"].fillna("").astype(str),
            "house_type": frame["house_type"].fillna("오피스텔").astype(str),
            "deal_type": frame["deal_type"].fillna("").astype(str),
            "deposit_manwon": frame["deposit_eok"].apply(lambda value: to_number(value, None) * 10000 if pd.notna(value) else None),
            "monthly_rent_manwon": frame["monthly_rent_manwon"].apply(lambda value: to_number(value, None)),
            "area_sqm": frame["area_sqm"].apply(lambda value: to_number(value, 0)),
            "floor": pd.Series([""] * len(frame), index=frame.index),
            "contract_date": pd.Series([""] * len(frame), index=frame.index),
            "lat": frame["y"].apply(lambda value: to_number(value, None)),
            "lng": frame["x"].apply(lambda value: to_number(value, None)),
            "has_price_info": True,
        }
    )


def read_listing_frames() -> pd.DataFrame:
    global LISTING_FRAME_CACHE, LISTING_FRAME_CACHE_SIGNATURE
    signature = _listing_file_signature()
    if LISTING_FRAME_CACHE is not None and LISTING_FRAME_CACHE_SIGNATURE == signature:
        return LISTING_FRAME_CACHE.copy()

    frames = [
        _load_apartment(),
        _load_multi_family(),
        _load_officetel(),
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged["display_name"] = merged["listing_name"].where(
        merged["listing_name"].astype(str).str.strip() != "",
        merged["house_type"],
    )
    merged["area_pyeong"] = merged["area_sqm"].apply(sqm_to_display_pyeong)
    merged = merged.drop_duplicates(
        subset=["district", "address", "deposit_manwon", "monthly_rent_manwon", "house_type"]
    )
    LISTING_FRAME_CACHE = merged.copy()
    LISTING_FRAME_CACHE_SIGNATURE = signature
    return merged


def kakao_headers() -> dict:
    if not KAKAO_REST_API_KEY:
        raise RuntimeError("KAKAO_REST_API_KEY가 설정되지 않았습니다.")
    return {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}


def geocode_address(address: str) -> dict | None:
    if not address:
        return None
    cached = GEOCODE_CACHE.get(address)
    if cached and cached.get("source") not in {"district-fallback", "legacy-fallback"}:
        return cached
    if not KAKAO_REST_API_KEY:
        return None
    try:
        response = requests.get(KAKAO_LOCAL_URL, params={"query": address}, headers=kakao_headers(), timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        documents = response.json().get("documents", [])
    except RequestException:
        return None

    result = None
    if documents:
        first = documents[0]
        result = {"lat": float(first["y"]), "lng": float(first["x"]), "source": "kakao-local"}
    else:
        try:
            keyword_response = requests.get(
                KAKAO_KEYWORD_URL,
                params={"query": address, "size": 1},
                headers=kakao_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            keyword_response.raise_for_status()
            keyword_docs = keyword_response.json().get("documents", [])
        except RequestException:
            return None
        if not keyword_docs:
            return None
        first = keyword_docs[0]
        result = {"lat": float(first["y"]), "lng": float(first["x"]), "source": "kakao-keyword"}

    GEOCODE_CACHE[address] = result
    save_json_cache(GEOCODE_CACHE_FILE, GEOCODE_CACHE)
    return result


def search_workplaces(query: str) -> list[dict]:
    if not KAKAO_REST_API_KEY:
        raise RuntimeError("직장 검색을 위해 KAKAO_REST_API_KEY가 필요합니다.")
    try:
        response = requests.get(
            KAKAO_KEYWORD_URL,
            params={"query": query, "size": 10},
            headers=kakao_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except RequestException as exc:
        raise RuntimeError(f"직장 검색 API 호출에 실패했습니다: {exc}") from exc
    results = []
    for item in response.json().get("documents", []):
        address = item.get("road_address_name") or item.get("address_name") or ""
        if not address:
            continue
        results.append(
            {
                "name": item.get("place_name") or query,
                "address": address,
                "lat": float(item["y"]),
                "lng": float(item["x"]),
            }
        )
    return results


def listing_geo(row: dict) -> dict | None:
    lat = row.get("lat")
    lng = row.get("lng")
    if lat is None or lng is None:
        return None
    return {"lat": float(lat), "lng": float(lng), "source": "listing"}


def _route_cache_key(start: dict, end: dict, mode: str, provider: str | None = None) -> str:
    provider_part = f":{provider}" if provider else ""
    return f"{mode}{provider_part}:{round(start['lat'], 6)},{round(start['lng'], 6)}:{round(end['lat'], 6)},{round(end['lng'], 6)}"


def _default_car_prediction_time(trip_type: str) -> str:
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    hour = 18 if trip_type == "commute_from_work" else 8
    return f"{tomorrow.year:04d}-{tomorrow.month:02d}-{tomorrow.day:02d}T{hour:02d}:00:00+0900"


def _car_selected_time_phrase(selected_car_time: str | None) -> str:
    raw = str(selected_car_time or "").strip()
    parts = raw.split(":", 1)
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return raw
    hour24 = int(parts[0])
    minute = int(parts[1])
    meridiem = "오후" if hour24 >= 12 else "오전"
    hour12 = hour24 % 12 or 12
    if minute == 0:
        return f"{meridiem} {hour12}시"
    return f"{meridiem} {hour12}시 {minute}분"


def _normalize_car_time_profile(profile: dict | None) -> dict:
    raw = profile or {}
    if isinstance(raw, str):
        raw = {"profile_key": raw}

    enabled = bool(raw.get("enabled"))
    profile_key = str(
        raw.get("profile_key")
        or raw.get("car_time_profile")
        or raw.get("profile")
        or ""
    ).strip()
    route_hint = str(
        raw.get("route_direction")
        or raw.get("car_route_direction")
        or raw.get("direction")
        or ""
    ).strip()
    trip_hint = str(raw.get("trip_type") or raw.get("tripType") or "").strip()
    if trip_hint not in {"commute_to_work", "commute_from_work"}:
        if route_hint in {"from_work", "work_to_home"} or profile_key == "weekday_evening_6":
            trip_hint = "commute_from_work"
        else:
            trip_hint = "commute_to_work"

    if route_hint in {"to_work", "home_to_work"}:
        route_direction = "to_work"
    elif route_hint in {"from_work", "work_to_home"}:
        route_direction = "from_work"
    else:
        route_direction = "from_work" if trip_hint == "commute_from_work" else "to_work"
    selected_car_time = str(
        raw.get("time")
        or raw.get("selected_car_time")
        or raw.get("selectedCarTime")
        or ""
    ).strip()
    if not selected_car_time:
        selected_car_time = "18:00" if trip_hint == "commute_from_work" else "08:00"
    time_bits = selected_car_time.split(":", 1)
    if len(time_bits) != 2 or not time_bits[0].isdigit() or not time_bits[1].isdigit():
        selected_car_time = "18:00" if trip_hint == "commute_from_work" else "08:00"

    if profile_key not in {"weekday_morning_8", "weekday_evening_6", "custom"}:
        profile_key = "weekday_evening_6" if trip_hint == "commute_from_work" and selected_car_time == "18:00" else "weekday_morning_8"
        if selected_car_time not in {"08:00", "18:00"}:
            profile_key = "custom"

    prediction_type = str(
        raw.get("prediction_type")
        or raw.get("predictionType")
        or raw.get("time_basis")
        or raw.get("car_route_time_basis")
        or "departure"
    ).strip()
    if prediction_type not in {"departure", "arrival"}:
        prediction_type = "departure"
    prediction_time = str(raw.get("prediction_time") or raw.get("predictionTime") or "").strip()
    if enabled and not prediction_time:
        base = datetime.now() + timedelta(days=1)
        hh, mm = selected_car_time.split(":", 1)
        prediction_time = f"{base:%Y-%m-%d}T{int(hh):02d}:{int(mm):02d}:00+0900"
    return {
        "enabled": enabled,
        "profile_key": profile_key,
        "trip_type": trip_hint,
        "route_direction": route_direction,
        "time_basis": prediction_type,
        "selected_car_time": selected_car_time,
        "time": selected_car_time,
        "prediction_type": prediction_type,
        "prediction_time": prediction_time or None,
    }


def _car_route_requested_provider_label(car_time_profile: dict | None) -> str:
    profile = _normalize_car_time_profile(car_time_profile)
    return "TMAP_TIME_MACHINE" if profile.get("enabled") else "LIVE_KAKAO"


def _car_route_provider_label(car_time_profile: dict | None, actual_provider: str | None = None) -> str:
    provider = str(actual_provider or "").strip()
    if provider:
        return provider
    return _car_route_requested_provider_label(car_time_profile)


def _car_route_time_label(car_time_profile: dict | None) -> str | None:
    profile = _normalize_car_time_profile(car_time_profile)
    if not profile.get("enabled"):
        return None
    selected_car_time = str(profile.get("time") or profile.get("selected_car_time") or "").strip()
    direction_label = "퇴근" if profile.get("trip_type") == "commute_from_work" else "출근"
    if profile.get("profile_key") == "weekday_evening_6":
        return "평일 오후 6시 퇴근"
    if profile.get("profile_key") == "custom":
        return f"사용자가 선택한 {_car_selected_time_phrase(selected_car_time)} {direction_label}"
    return "평일 오전 8시 출근"


def _car_route_time_basis_label(car_time_profile: dict | None) -> str | None:
    profile = _normalize_car_time_profile(car_time_profile)
    if not profile.get("enabled"):
        return None
    return str(profile.get("time_basis") or "departure")


def _car_route_direction_label(car_time_profile: dict | None) -> str:
    profile = _normalize_car_time_profile(car_time_profile)
    return str(profile.get("route_direction") or "to_work")


def _car_route_endpoints(start: dict, end: dict, car_time_profile: dict | None) -> tuple[dict, dict]:
    profile = _normalize_car_time_profile(car_time_profile)
    if profile.get("enabled") and profile.get("trip_type") == "commute_from_work":
        return end, start
    return start, end


def _car_time_basis_sentence(car_time_profile: dict | None) -> str | None:
    label = _car_route_time_label(car_time_profile)
    if not label:
        return None
    return f"자동차 통근시간은 {label} 기준으로 계산했어요."


def _tmap_prediction_type_and_time(car_time_profile: dict | None) -> tuple[str, str]:
    profile = _normalize_car_time_profile(car_time_profile)
    prediction_time = str(profile.get("prediction_time") or "").strip() or _default_car_prediction_time(profile.get("trip_type"))
    prediction_type = "departure"
    return prediction_type, prediction_time


def _tmap_car_route_request_config(start: dict, end: dict, car_time_profile: dict | None) -> dict:
    profile = _normalize_car_time_profile(car_time_profile)
    request_start, request_end = _car_route_endpoints(start, end, profile)
    prediction_type, prediction_time = _tmap_prediction_type_and_time(profile)
    return {
        "url": TMAP_TIME_MACHINE_URL,
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "appKey": TMAP_APP_KEY,
        },
        "json": {
            "routesInfo": {
                "departure": {
                    "name": "출발지",
                    "lon": str(request_start["lng"]),
                    "lat": str(request_start["lat"]),
                    "depSearchFlag": "03",
                },
                "destination": {
                    "name": "도착지",
                    "lon": str(request_end["lng"]),
                    "lat": str(request_end["lat"]),
                    "destSearchFlag": "03",
                },
                "predictionType": prediction_type,
                "predictionTime": prediction_time,
                "searchOption": "00",
                "tollgateCarType": "car",
                "trafficInfo": "N",
            },
        },
        "timeout": CAR_REQUEST_TIMEOUT,
    }


def _tmap_route_error_detail(status_code: int | None, payload) -> str:
    if status_code:
        return f"HTTP {status_code}"
    if isinstance(payload, dict):
        error = payload.get("error") or payload.get("status") or payload.get("message")
        if isinstance(error, dict):
            parts = []
            for key in ("code", "message", "msg", "detail"):
                value = error.get(key)
                if value:
                    parts.append(str(value).strip())
            if parts:
                return " / ".join(parts)
        if isinstance(error, list):
            parts = []
            for item in error:
                if isinstance(item, dict):
                    for key in ("code", "message", "msg", "detail"):
                        value = item.get(key)
                        if value:
                            parts.append(str(value).strip())
                            break
            if parts:
                return " / ".join(parts)
        if error:
            return str(error).strip()
    return "알 수 없는 오류"


def _tmap_route_error_info(status_code: int | None, payload) -> tuple[str | None, str | None]:
    if status_code is not None:
        error_code = f"HTTP_{int(status_code)}"
    else:
        error_code = None
    error_message = None
    if isinstance(payload, dict):
        error = payload.get("error") or payload.get("status") or payload.get("message")
        if isinstance(error, dict):
            error_code = str(error.get("code") or error_code or "").strip() or error_code
            for key in ("message", "msg", "detail"):
                value = error.get(key)
                if value:
                    error_message = str(value).strip()
                    break
        elif isinstance(error, list) and error:
            first = error[0] or {}
            if isinstance(first, dict):
                error_code = str(first.get("code") or error_code or "").strip() or error_code
                for key in ("message", "msg", "detail"):
                    value = first.get(key)
                    if value:
                        error_message = str(value).strip()
                        break
        elif error:
            error_message = str(error).strip()
    return error_code, error_message


def _parse_tmap_car_route_payload(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    features = payload.get("features") or []
    if not isinstance(features, list) or not features:
        return None

    summary_props = None
    path = []
    path_segments = []
    steps = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        if summary_props is None and any(key in properties for key in ("totalDistance", "totalTime", "taxiFare", "departureTime", "arrivalTime")):
            summary_props = properties

        geom_type = str(geometry.get("type") or "").strip()
        if geom_type == "LineString":
            points = []
            for coordinate in geometry.get("coordinates") or []:
                if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                    continue
                lng = to_number(coordinate[0], None)
                lat = to_number(coordinate[1], None)
                if lng is None or lat is None:
                    continue
                points.append({"lat": float(lat), "lng": float(lng)})
            if len(points) >= 2:
                path_segments.append({"type": "car", "style": "solid", "points": points})
                _append_points(path, points)
        elif geom_type == "Point":
            description = str(properties.get("description") or properties.get("name") or "").strip()
            point_type = str(properties.get("pointType") or "").strip()
            if description or point_type:
                steps.append({
                    "type": "car",
                    "name": str(properties.get("name") or description or "안내점").strip(),
                    "description": description or None,
                    "point_type": point_type or None,
                    "turn_type": properties.get("turnType"),
                    "distance_m": int(to_number(properties.get("distance"), 0)) if properties.get("distance") is not None else None,
                    "duration_min": max(1, int(round(to_number(properties.get("time"), 0) / 60))) if properties.get("time") is not None else None,
                })

    total_distance_m = to_number((summary_props or {}).get("totalDistance"), None)
    total_time_sec = to_number((summary_props or {}).get("totalTime"), None)
    if total_distance_m is None or total_time_sec is None:
        return None

    duration_min = max(1, int(round(total_time_sec / 60)))
    distance_km = round(total_distance_m / 1000, 2)
    return {
        "distance_km": distance_km,
        "duration_min": duration_min,
        "route_summary": f"자동차 약 {duration_min}분",
        "path": path,
        "path_segments": path_segments if path_segments else ([{"type": "car", "style": "solid", "points": path}] if path else []),
        "steps": steps,
        "payment": int(to_number((summary_props or {}).get("totalFare"), 0)) if (summary_props or {}).get("totalFare") is not None else None,
        "taxi_fare": int(to_number((summary_props or {}).get("taxiFare"), 0)) if (summary_props or {}).get("taxiFare") is not None else None,
        "car_total_distance_m": int(round(total_distance_m)),
        "car_total_time_sec": int(round(total_time_sec)),
        "car_departure_time": (summary_props or {}).get("departureTime"),
        "car_arrival_time": (summary_props or {}).get("arrivalTime"),
    }



def _route_unavailable(mode: str, reason: str, route_status: str | None = None) -> dict:
    return {
        "route_type": "unavailable",
        "mode": mode,
        "route_status": route_status or ("TRANSIT_ROUTE_FAILED" if mode == "transit" else "CAR_ROUTE_FAILED"),
        "distance_km": None,
        "duration_min": None,
        "route_summary": reason,
        "path": [],
        "path_segments": [],
        "steps": [],
        "payment": None,
        "bus_transit_count": 0,
        "subway_transit_count": 0,
        "subway_section_count": 0,
        "total_walk_m": 0,
        "_debug": {
            "cache_used": False,
            "cache_valid": False,
            "route_quality": "UNAVAILABLE",
            "invalid_cache_ignored": False,
            "route_source": "TRANSIT_FAILED" if mode == "transit" else "CAR_FAILED",
            "odsay_called": False,
            "odsay_http_status": None,
            "odsay_error_code": None,
            "odsay_error_message": None,
        },
    }


def _route_quality(route: dict) -> str:
    route_type = route.get("route_type")
    if route_type == "unavailable":
        return "UNAVAILABLE"
    if route_type == "walk":
        return "WALK_ONLY"
    if route.get("path_segments"):
        return "FULL_PATH"
    if route.get("duration_min") is not None and route.get("distance_km") not in (None, 0):
        return "PARTIAL_ROUTE"
    return "UNKNOWN"


def _cache_route_source(route: dict) -> str:
    provider = str(route.get("route_provider") or "").lower()
    if route.get("route_type") == "transit":
        if provider == "odsay":
            return "ROUTE_CACHE_ODSAY"
        if provider == "tmap":
            return "ROUTE_CACHE_TMAP"
    if provider in {"tmap_time_machine", "tmap"}:
        return "ROUTE_CACHE_TMAP"
    return "ROUTE_CACHE"


def _with_route_debug(route: dict, *, cache_used: bool, cache_valid: bool, invalid_cache_ignored: bool = False) -> dict:
    enriched = dict(route)
    debug_meta = dict(enriched.get("_debug") or {})
    debug_meta["cache_used"] = bool(cache_used)
    debug_meta["cache_valid"] = bool(cache_valid)
    debug_meta["invalid_cache_ignored"] = bool(invalid_cache_ignored)
    debug_meta["route_quality"] = _route_quality(enriched)
    if cache_used:
        debug_meta["route_source"] = _cache_route_source(enriched)
        debug_meta["odsay_called"] = False
        debug_meta.setdefault("odsay_http_status", None)
        debug_meta.setdefault("odsay_error_code", None)
        debug_meta.setdefault("odsay_error_message", None)
    else:
        debug_meta.setdefault("route_source", "UNKNOWN")
        debug_meta.setdefault("odsay_called", False)
        debug_meta.setdefault("odsay_http_status", None)
        debug_meta.setdefault("odsay_error_code", None)
        debug_meta.setdefault("odsay_error_message", None)
    enriched["_debug"] = debug_meta
    return enriched


def build_odsay_request_config(start: dict, end: dict) -> dict:
    return {
        "url": ODSAY_SEARCH_URL,
        "params": {
            "SX": start["lng"],
            "SY": start["lat"],
            "EX": end["lng"],
            "EY": end["lat"],
            "apiKey": ODSAY_API_KEY,
        },
        "headers": {},
        "timeout": TRANSIT_REQUEST_TIMEOUT,
    }


def build_tmap_transit_request_config(start: dict, end: dict) -> dict:
    return {
        "url": TMAP_TRANSIT_URL,
        "headers": {
            "accept": "application/json",
            "content-type": "application/json",
            "appKey": TMAP_TRANSIT_APP_KEY,
        },
        "json": {
            "startX": str(start["lng"]),
            "startY": str(start["lat"]),
            "endX": str(end["lng"]),
            "endY": str(end["lat"]),
            "lang": 0,
            "format": "json",
            "count": 10,
        },
        "timeout": TRANSIT_REQUEST_TIMEOUT,
    }


def _extract_vertex_points(vertices: list) -> list[dict]:
    points = []
    for index in range(0, len(vertices or []), 2):
        lng = to_number(vertices[index], None)
        lat = to_number(vertices[index + 1], None) if index + 1 < len(vertices) else None
        if lat is None or lng is None:
            continue
        points.append({"lat": float(lat), "lng": float(lng)})
    return points


def _append_points(target: list[dict], points: list[dict]) -> None:
    if not points:
        return
    if not target:
        target.extend(points)
        return
    if target[-1] == points[0]:
        target.extend(points[1:])
        return
    target.extend(points)


def _parse_tmap_linestring(linestring: str | None) -> list[dict]:
    if not linestring:
        return []
    points = []
    for pair in str(linestring).split():
        if "," not in pair:
            continue
        lng_raw, lat_raw = pair.split(",", 1)
        lng = to_number(lng_raw, None)
        lat = to_number(lat_raw, None)
        if lat is None or lng is None:
            continue
        points.append({"lat": float(lat), "lng": float(lng)})
    return points


def _wgs84_to_wcong(lng: float, lat: float) -> tuple[int, int]:
    x, y = WGS84_TO_WCONG.transform(lng, lat)
    return round(x * 2.5), round(y * 2.5)


def _parse_duration_minutes(raw: str | None) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    hours = 0
    minutes = 0
    hour_match = re.search(r"(\d+)\s*시간", text)
    minute_match = re.search(r"(\d+)\s*분", text)
    if hour_match:
        hours = int(hour_match.group(1))
    if minute_match:
        minutes = int(minute_match.group(1))
    total = (hours * 60) + minutes
    if total > 0:
        return total
    digits = re.findall(r"\d+", text)
    return int(digits[0]) if digits else None


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _normalize_route_name(value: str | None) -> str:
    text = normalize_text(value)
    text = text.replace("수도권", "")
    return text


def _display_cache_key(start: dict, end: dict, top_n: int = 10) -> str:
    return _route_cache_key(start, end, "display", f"kakao_pubtrans:v{ROUTE_DISPLAY_VERSION}:top{top_n}")


def _find_cached_kakao_display_routes(start: dict, end: dict) -> list[dict]:
    start_key = f"{round(start['lat'], 6)},{round(start['lng'], 6)}"
    end_key = f"{round(end['lat'], 6)},{round(end['lng'], 6)}"
    needle = f":{start_key}:{end_key}"
    with ROUTE_CACHE_LOCK:
        for key, value in ROUTE_CACHE.items():
            if not str(key).startswith("display:kakao_pubtrans"):
                continue
            if needle not in str(key):
                continue
            if isinstance(value, list) and value:
                return value
    return []


def _has_winerror_10013(exception: Exception | None) -> bool:
    if exception is None:
        return False
    if getattr(exception, "winerror", None) == 10013:
        return True
    inner = getattr(exception, "__cause__", None) or getattr(exception, "__context__", None)
    while inner is not None:
        if getattr(inner, "winerror", None) == 10013:
            return True
        inner = getattr(inner, "__cause__", None) or getattr(inner, "__context__", None)
    return "10013" in str(exception)


def _fetch_kakao_transit_routes_with_status(start: dict, end: dict, top_n: int = 10) -> tuple[list[dict], dict]:
    cache_key = _display_cache_key(start, end, top_n)
    start_x, start_y = _wgs84_to_wcong(start["lng"], start["lat"])
    end_x, end_y = _wgs84_to_wcong(end["lng"], end["lat"])
    with KAKAO_TRANSIT_FETCH_LOCK:
        try:
            response = requests.get(
                KAKAO_PUBTRANS_URL,
                params={
                    "inputCoordSystem": "WCONGNAMUL",
                    "outputCoordSystem": "WCONGNAMUL",
                    "service": "map.daum.net",
                    "callback": "cb",
                    "sX": start_x,
                    "sY": start_y,
                    "sName": "출발",
                    "sid": "",
                    "eX": end_x,
                    "eY": end_y,
                    "eName": "도착",
                    "eid": "",
                },
                headers={
                    "Referer": "https://map.kakao.com/",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=TRANSIT_REQUEST_TIMEOUT,
            )
            http_status = response.status_code
            if http_status != 200:
                cached_routes = _find_cached_kakao_display_routes(start, end)
                if cached_routes:
                    return cached_routes[:top_n], {"kakao_live_status": "HTTP_REJECTED", "kakao_cache_fallback_used": True}
                return [], {"kakao_live_status": "HTTP_REJECTED", "kakao_cache_fallback_used": False}
            text = response.text.strip()
            matched = re.search(r"cb\((.*)\)\s*$", text, re.DOTALL)
            payload = json.loads(matched.group(1)) if matched else {}
        except Exception as exc:
            cached_routes = _find_cached_kakao_display_routes(start, end)
            if cached_routes:
                live_status = "ENV_NETWORK_BLOCKED" if _has_winerror_10013(exc) else "REQUEST_EXCEPTION"
                return cached_routes[:top_n], {"kakao_live_status": live_status, "kakao_cache_fallback_used": True}
            live_status = "ENV_NETWORK_BLOCKED" if _has_winerror_10013(exc) else "REQUEST_EXCEPTION"
            return [], {"kakao_live_status": live_status, "kakao_cache_fallback_used": False}

    routes = (((payload.get("in_local") or {}).get("routes")) or [])[:top_n]
    parsed_routes = []
    for route in routes:
        steps = []
        for step in route.get("steps") or []:
            raw_type = str(step.get("type") or "").upper()
            if raw_type not in {"SUBWAY", "BUS", "WALKING"}:
                continue
            step_type = {"SUBWAY": "subway", "BUS": "bus", "WALKING": "walk"}[raw_type]
            vehicles = step.get("vehicles") or [{}]
            first_vehicle = vehicles[0] if isinstance(vehicles, list) and vehicles else {}
            points = _kakao_pubtrans_polyline_to_points(step.get("polyline"))
            steps.append({
                "type": step_type,
                "name": str(first_vehicle.get("name") or step.get("routeName") or "").strip(),
                "start": str(((step.get("startLocation") or {}).get("name")) or "").strip(),
                "end": str(((step.get("endLocation") or {}).get("name")) or "").strip(),
                "duration_min": _parse_duration_minutes((step.get("time") or {}).get("text")),
                "distance_m": _parse_distance_meters((step.get("distance") or {}).get("text")),
                "points": points,
            })
        parsed_routes.append({
            "ranking": route.get("ranking"),
            "duration_min": _parse_duration_minutes(((route.get("time") or {}).get("text"))),
            "transfer_count": _safe_int(route.get("transfers")),
            "route_type": _kakao_route_mode_signature(steps),
            "steps": steps,
        })

    if parsed_routes:
        _route_cache_set(cache_key, parsed_routes)
        return parsed_routes, {"kakao_live_status": "OK", "kakao_cache_fallback_used": False}
    cached_routes = _find_cached_kakao_display_routes(start, end)
    if cached_routes:
        return cached_routes[:top_n], {"kakao_live_status": "EMPTY_LIVE_RESPONSE", "kakao_cache_fallback_used": True}
    return [], {"kakao_live_status": "EMPTY_LIVE_RESPONSE", "kakao_cache_fallback_used": False}


def _parse_distance_meters(raw: str | None) -> int | None:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return None
    km_match = re.search(r"(\d+(?:\.\d+)?)\s*km", text, re.IGNORECASE)
    if km_match:
        return int(round(float(km_match.group(1)) * 1000))
    meter_match = re.search(r"(\d+(?:\.\d+)?)\s*m", text, re.IGNORECASE)
    if meter_match:
        return int(round(float(meter_match.group(1))))
    digits = re.findall(r"\d+(?:\.\d+)?", text)
    return int(round(float(digits[0]))) if digits else None


def _kakao_pubtrans_polyline_to_points(polyline_str: str | None) -> list[dict]:
    if not polyline_str:
        return []
    try:
        numbers = [int(value) for value in str(polyline_str).split("|") if str(value).strip()]
    except Exception:
        return []
    points = []
    for index in range(0, len(numbers), 2):
        if index + 1 >= len(numbers):
            break
        lng, lat = WCONG_TO_WGS84.transform(numbers[index] / 2.5, numbers[index + 1] / 2.5)
        points.append({"lat": round(float(lat), 6), "lng": round(float(lng), 6)})
    return points


def _kakao_route_mode_signature(route_steps: list[dict]) -> str:
    has_bus = any(str((step or {}).get("type") or "") == "bus" for step in route_steps)
    has_subway = any(str((step or {}).get("type") or "") == "subway" for step in route_steps)
    if has_bus and has_subway:
        return "BUS_AND_SUBWAY"
    if has_subway:
        return "SUBWAY"
    if has_bus:
        return "BUS"
    return "WALK"


def _normalize_route_step_mode(step: dict | None) -> str:
    raw = str((step or {}).get("type") or (step or {}).get("mode") or (step or {}).get("trafficType") or "").strip().lower()
    if raw in {"bus", "subway", "train", "metro", "walk"}:
        return raw
    return raw


def _compute_transfer_count_from_steps(route_steps: list[dict]) -> int:
    ride_signatures: list[str] = []
    for step in route_steps or []:
        mode = _normalize_route_step_mode(step)
        if mode in {"bus", "subway", "train", "metro"}:
            signature = "|".join([
                mode,
                _normalize_route_name(step.get("name")),
                str(step.get("start") or step.get("from") or ""),
                str(step.get("end") or step.get("to") or ""),
            ])
            if not ride_signatures or ride_signatures[-1] != signature:
                ride_signatures.append(signature)
    return max(len(ride_signatures) - 1, 0)


def _kakao_route_line_names(route_steps: list[dict]) -> list[str]:
    names = []
    for step in route_steps:
        if str((step or {}).get("type") or "") == "walk":
            continue
        normalized = _normalize_route_name(step.get("name"))
        if normalized:
            names.append(normalized)
    return sorted(set(names))


def _route_signature_for_cache(route_steps: list[dict]) -> str:
    mode = _kakao_route_mode_signature(route_steps)
    line_names = ",".join(_kakao_route_line_names(route_steps))
    return f"{mode}:{line_names}"


def _kakao_display_route_score(
    original_steps: list[dict],
    original_duration_min: int | None,
    original_transfer_count: int | None,
    candidate: dict,
) -> dict | None:
    route_steps = [step for step in (candidate.get("steps") or []) if str((step or {}).get("type") or "") in {"walk", "bus", "subway"}]
    renderable_steps = [step for step in route_steps if len((step or {}).get("points") or []) >= 2]
    if not renderable_steps:
        return None

    original_transit_steps = [step for step in original_steps if str((step or {}).get("type") or "") in {"walk", "bus", "subway"}]
    original_mode = _kakao_route_mode_signature(original_transit_steps)
    display_mode = _kakao_route_mode_signature(route_steps)
    original_lines = _kakao_route_line_names(original_transit_steps)
    display_lines = _kakao_route_line_names(route_steps)
    line_overlap = sorted(set(original_lines) & set(display_lines))
    display_duration = _safe_int(candidate.get("duration_min"))
    display_transfer = _compute_transfer_count_from_steps(route_steps)
    original_duration = _safe_int(original_duration_min)
    original_transfer = _compute_transfer_count_from_steps(original_steps)
    duration_diff = abs((display_duration or 0) - (original_duration or 0))
    transfer_diff = abs((display_transfer or 0) - (original_transfer or 0))
    step_diff = abs(len(renderable_steps) - len(original_transit_steps))
    first_type_match = bool(route_steps and original_transit_steps and str((route_steps[0] or {}).get("type") or "") == str((original_transit_steps[0] or {}).get("type") or ""))
    last_type_match = bool(route_steps and original_transit_steps and str((route_steps[-1] or {}).get("type") or "") == str((original_transit_steps[-1] or {}).get("type") or ""))
    mode_match = display_mode == original_mode

    score = 100
    score += 40 if mode_match else -50
    if transfer_diff == 0:
        score += 20
    elif transfer_diff >= 2:
        score -= 30
    if duration_diff <= 5:
        score += 15
    elif duration_diff <= 10:
        score += 8
    elif duration_diff > 15:
        score -= 20
    if line_overlap:
        score += 30
    if first_type_match:
        score += 5
    if last_type_match:
        score += 5
    if not mode_match:
        if original_mode == "SUBWAY" and display_mode == "BUS":
            score -= 50
        elif original_mode == "BUS" and display_mode == "SUBWAY":
            score -= 50

    acceptable = mode_match and transfer_diff <= 1 and duration_diff <= 10 and (bool(line_overlap) or not (original_lines or display_lines)) and score >= 40

    return {
        "score": score,
        "acceptable": acceptable,
        "mode_match": mode_match,
        "original_mode_signature": original_mode,
        "display_mode_signature": display_mode,
        "original_lines": original_lines,
        "display_lines": display_lines,
        "line_overlap": line_overlap,
        "original_duration_min": original_duration,
        "display_duration_min": display_duration,
        "original_transfer_count": original_transfer,
        "display_transfer_count": display_transfer,
        "duration_diff_min": duration_diff,
        "transfer_diff": transfer_diff,
        "step_diff": step_diff,
        "first_type_match": first_type_match,
        "last_type_match": last_type_match,
        "route_steps": route_steps,
        "renderable_step_count": len(renderable_steps),
    }


def _rank_kakao_display_routes(
    kakao_routes: list[dict],
    original_steps: list[dict],
    original_duration: int | None,
    original_transfer: int | None,
) -> list[dict]:
    ranked = []
    for route in kakao_routes:
        score_meta = _kakao_display_route_score(original_steps, original_duration, original_transfer, route)
        if not score_meta:
            continue
        ranked.append({
            "route": route,
            "score": score_meta["score"],
            "acceptable": score_meta["acceptable"],
            "meta": score_meta,
        })
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def fetch_kakao_transit_routes(start: dict, end: dict, top_n: int = 10) -> list[dict]:
    routes, _ = _fetch_kakao_transit_routes_with_status(start, end, top_n=top_n)
    return routes


def _choose_kakao_transit_route(kakao_routes: list[dict], steps: list[dict], duration_min: int, transfer_count: int) -> dict | None:
    if not kakao_routes:
        return None
    ranked = _rank_kakao_display_routes(kakao_routes, steps, duration_min, transfer_count)
    if not ranked:
        return kakao_routes[0] if kakao_routes else None
    return ranked[0]["route"]


def _normalize_hex_color(raw_color: str | None, fallback: str) -> str:
    color = str(raw_color or "").strip()
    if not color:
        return fallback
    if not color.startswith("#"):
        color = f"#{color}"
    return color if len(color) == 7 else fallback


def _walking_cache_key(start: dict, end: dict) -> str:
    return _route_cache_key(start, end, "walkpoly")


def fetch_walking_path(start: dict, end: dict) -> list[dict]:
    if not KAKAO_REST_API_KEY:
        return []
    cache_key = _walking_cache_key(start, end)
    cached = _route_cache_get(cache_key)
    if isinstance(cached, dict) and cached.get("points"):
        return cached["points"]
    try:
        response = requests.get(
            KAKAO_WALKING_DIRECTIONS_URL,
            params={
                "origin": f"{start['lng']},{start['lat']}",
                "destination": f"{end['lng']},{end['lat']}",
                "priority": "DISTANCE",
                "summary": "false",
            },
            headers={
                "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}",
                "Content-Type": "application/json",
                "service": "commute-ai",
            },
            timeout=WALKING_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    routes = payload.get("routes") or []
    if not routes:
        return []
    sections = (routes[0] or {}).get("sections") or []
    points: list[dict] = []
    for section in sections:
        for road in section.get("roads") or []:
            _append_points(points, _extract_vertex_points(road.get("vertexes") or []))
    if points:
        _route_cache_set(cache_key, {"points": points})
    return points


def _section_anchor(section: dict, prefix: str) -> dict | None:
    x = to_number(section.get(f"{prefix}X"), None)
    y = to_number(section.get(f"{prefix}Y"), None)
    if x is None or y is None:
        return None
    return {"lat": float(y), "lng": float(x)}


def _fallback_transit_points(section: dict) -> list[dict]:
    stations = (((section or {}).get("passStopList") or {}).get("stations")) or []
    points = []
    for station in stations:
        lng = to_number(station.get("x"), None)
        lat = to_number(station.get("y"), None)
        if lat is None or lng is None:
            continue
        points.append({"lat": float(lat), "lng": float(lng)})
    start_anchor = _section_anchor(section, "start")
    end_anchor = _section_anchor(section, "end")
    if start_anchor and (not points or points[0] != start_anchor):
        points.insert(0, start_anchor)
    if end_anchor and (not points or points[-1] != end_anchor):
        points.append(end_anchor)
    return points


def fetch_transit_lane_segments(map_obj: str | None, sub_paths: list[dict]) -> list[dict]:
    if not map_obj or not ODSAY_API_KEY:
        return []
    cache_key = f"lane:{map_obj}"
    cached = _route_cache_get(cache_key)
    if isinstance(cached, list) and cached:
        lane_entries = cached
    else:
        try:
            response = requests.get(
                ODSAY_LANE_URL,
                params={"mapObject": f"0:0@{map_obj}", "apiKey": ODSAY_API_KEY},
                timeout=TRANSIT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            lane_entries = ((response.json().get("result") or {}).get("lane")) or []
            if lane_entries:
                _route_cache_set(cache_key, lane_entries)
        except Exception:
            lane_entries = []

    segments = []
    lane_index = 0
    for section in sub_paths:
        traffic_type = section.get("trafficType")
        if traffic_type not in (1, 2):
            continue
        lane_entry = lane_entries[lane_index] if lane_index < len(lane_entries) else {}
        lane_index += 1
        if not isinstance(lane_entry, dict):
            lane_entry = {}
        line_name = _odsay_lane_name(section)
        points = []
        sections = lane_entry.get("section") or []
        if isinstance(sections, dict):
            sections = [sections]
        if not sections:
            sections = [lane_entry]

        for lane_section in sections:
            if not isinstance(lane_section, dict):
                continue
            graph_pos = lane_section.get("graphPos") or []
            section_points = []
            if graph_pos and isinstance(graph_pos[0], dict):
                for graph in graph_pos:
                    lng = to_number(graph.get("x"), None)
                    lat = to_number(graph.get("y"), None)
                    if lat is None or lng is None:
                        continue
                    section_points.append({"lat": float(lat), "lng": float(lng)})
            elif graph_pos and isinstance(graph_pos[0], (int, float, str)):
                section_points.extend(_extract_vertex_points(graph_pos))
            _append_points(points, section_points)

        if len(points) < 2:
            points = _fallback_transit_points(section)
        if len(points) < 2:
            continue
        segment_type = "subway" if traffic_type == 1 else "bus"
        color_hint = _odsay_lane_color_hint(section)
        segments.append(
            {
                "type": segment_type,
                "style": "solid",
                "name": line_name or ("지하철" if segment_type == "subway" else "버스"),
                "line": line_name or ("지하철" if segment_type == "subway" else "버스"),
                "category_hint": color_hint,
                "color": _display_segment_color(segment_type, line_name, color_hint),
                "points": points,
            }
        )
    return segments


def _walk_leg_endpoints(sub_paths: list[dict], index: int, start: dict, end: dict) -> tuple[dict | None, dict | None]:
    prev_transit = None
    next_transit = None
    for pointer in range(index - 1, -1, -1):
        if sub_paths[pointer].get("trafficType") in (1, 2):
            prev_transit = sub_paths[pointer]
            break
    for pointer in range(index + 1, len(sub_paths)):
        if sub_paths[pointer].get("trafficType") in (1, 2):
            next_transit = sub_paths[pointer]
            break

    walk_start = _section_anchor(prev_transit, "end") if prev_transit else {"lat": start["lat"], "lng": start["lng"]}
    walk_end = _section_anchor(next_transit, "start") if next_transit else {"lat": end["lat"], "lng": end["lng"]}
    return walk_start, walk_end


def _points_from_endpoints(start_point: dict | None, end_point: dict | None) -> list[dict]:
    if not start_point or not end_point:
        return []
    return [
        {"lat": float(start_point["lat"]), "lng": float(start_point["lng"])},
        {"lat": float(end_point["lat"]), "lng": float(end_point["lng"])},
    ]


def _display_segment_color(step_type: str, line_name: str = "", color_hint: str = "") -> str:
    normalized_name = _normalize_route_name(line_name)
    normalized_hint = normalize_text(color_hint)
    if step_type == "walk":
        return "#8b949e"
    if step_type == "bus":
        normalized = normalized_name.upper() or normalized_hint.upper()
        if not normalized:
            return "#15803d"
        if normalized.startswith("M") or re.match(r"^9\d{3,}$", normalized):
            return "#c62828"
        if re.match(r"^N\d+", normalized):
            return "#1f2937"
        if re.match(r"^(직행|광역|급행)", normalized):
            return "#c62828"
        if "공항" in normalized or "airport" in normalized:
            return "#00acc1"
        if "심야" in normalized or "night" in normalized:
            return "#1f2937"
        if "순환" in normalized or "circular" in normalized:
            return "#f59e0b"
        if "마을" in normalized or "village" in normalized:
            return "#16a34a"
        if "간선" in normalized or "blue" in normalized:
            return "#2563eb"
        if "지선" in normalized or "green" in normalized:
            return "#16a34a"
        if "광역" in normalized or "red" in normalized:
            return "#c62828"
        if re.match(r"^(공항|600\d)", normalized):
            return "#00acc1"
        if re.match(r"^(순환|01|02|03|04|05|06|07|08)", normalized):
            return "#f59e0b"
        if re.match(r"^\d{3}$", normalized) or re.match(r"^[67]\d{2,3}$", normalized):
            return "#2563eb"
        if re.match(r"^[1234]\d{3}$", normalized) or re.match(r"^[1-4]\d{2,3}$", normalized):
            return "#16a34a"
        return "#15803d"
    if step_type == "subway":
        subway_colors = [
            (("1호선", "1호"), "#0d3692"),
            (("2호선", "2호"), "#33a23d"),
            (("3호선", "3호"), "#fe5b10"),
            (("4호선", "4호"), "#32a1c8"),
            (("5호선", "5호"), "#8b50a4"),
            (("6호선", "6호"), "#c55c1d"),
            (("7호선", "7호"), "#54640d"),
            (("8호선", "8호"), "#f14c82"),
            (("9호선", "9호"), "#aa9872"),
            (("경의중앙선", "경의중앙"), "#77c4a3"),
            (("공항철도", "공항"), "#0090d2"),
            (("경춘선", "경춘"), "#178c72"),
            (("수인분당선", "수인분당"), "#f5a200"),
            (("신분당선", "신분당"), "#d4003b"),
            (("우이신설선", "우이신설"), "#b7c450"),
            (("서해선", "서해"), "#81a914"),
            (("신림선", "신림"), "#6789ca"),
            (("김포골드라인", "김포골드"), "#a17800"),
            (("용인경전철", "용인경전철"), "#6fb245"),
            (("의정부경전철", "의정부경전철"), "#f08200"),
            (("인천1호선", "인천1"), "#7ca8d5"),
            (("인천2호선", "인천2"), "#ed8b00"),
        ]
        for aliases, color in subway_colors:
            if any(alias in normalized_name for alias in aliases):
                return color
        return "#ea580c"
    return "#2b6ef3"


def build_kakao_display_route(start: dict, end: dict, original_route: dict | None = None) -> dict:
    original_steps = (original_route or {}).get("steps") or []
    original_duration = _safe_int((original_route or {}).get("duration_min"))
    original_transfer = _compute_transfer_count_from_steps(original_steps)
    original_signature = _route_signature_for_cache([step for step in original_steps if str((step or {}).get("type") or "") in {"walk", "bus", "subway"}])
    cache_key = _route_cache_key(start, end, "display", f"kakao_pubtrans_match:v{ROUTE_DISPLAY_VERSION}:top10:{original_signature}")
    cached = _route_cache_get(cache_key)
    if isinstance(cached, dict) and cached.get("display_path_segments") is not None:
        return cached

    routes, live_status = _fetch_kakao_transit_routes_with_status(start, end, top_n=10)
    if not routes:
        result = {
            "provider": "KAKAO_PUBTRANS",
            "match_method": "no_routes",
            "duration_min": None,
            "transfer_count": None,
            "route_match_score": None,
            "selected_match_score": None,
            "kakao_top_n": 10,
            "kakao_live_status": live_status.get("kakao_live_status"),
            "kakao_cache_fallback_used": bool(live_status.get("kakao_cache_fallback_used")),
            "summary_source": "RECOMMENDATION_ENGINE",
            "geometry_provider": "KAKAO_PUBTRANS_UNAVAILABLE",
            "polyline_rendered": False,
            "route_display_mode": "kakao",
            "kakao_candidates": [],
            "original_duration_min": _safe_int((original_route or {}).get("duration_min")),
            "display_duration_min": None,
            "original_transfer_count": original_transfer,
            "display_transfer_count": None,
            "original_mode_signature": _kakao_route_mode_signature([step for step in ((original_route or {}).get("steps") or []) if str((step or {}).get("type") or "") in {"walk", "bus", "subway"}]),
            "display_mode_signature": None,
            "selected_kakao_mode_signature": None,
            "selected_kakao_duration_min": None,
            "selected_kakao_transfer_count": None,
            "display_steps": [],
            "display_path_segments": [],
            "error": "상세 경로를 불러오지 못했습니다.",
        }
        _route_cache_set(cache_key, result)
        return result
    ranked_routes = _rank_kakao_display_routes(routes, original_steps, original_duration, original_transfer)
    selected_entry = ranked_routes[0] if ranked_routes else None
    selected = selected_entry["route"] if selected_entry else (_choose_kakao_transit_route(routes, original_steps, original_duration or 9999, original_transfer or 9999) or routes[0])
    score_meta = (selected_entry or {}).get("meta") or _kakao_display_route_score(original_steps, original_duration, original_transfer, selected) or {}
    route_steps = [step for step in (selected.get("steps") or []) if str((step or {}).get("type") or "") in {"walk", "bus", "subway"}]
    original_mode = _kakao_route_mode_signature([step for step in original_steps if str((step or {}).get("type") or "") in {"walk", "bus", "subway"}])
    display_mode = _kakao_route_mode_signature(route_steps)
    score_value = score_meta.get("score")
    acceptable = bool(score_meta.get("acceptable"))
    reject_reason = None
    if not score_meta:
        reject_reason = "상세 경로를 불러오지 못했습니다."
    elif not acceptable:
        reject_reason = "상세 경로를 표시할 수 없습니다. 추천 경로와 Kakao route가 일치하지 않습니다."
    display_steps = []
    display_path_segments = []
    for step in route_steps:
        step_type = str(step.get("type") or "")
        line_name = str(step.get("name") or "").strip()
        duration_value = _safe_int(step.get("duration_min"))
        distance_value = _safe_int(step.get("distance_m"))
        coordinates = [[point.get("lng"), point.get("lat")] for point in (step.get("points") or []) if point.get("lat") is not None and point.get("lng") is not None]
        if len(coordinates) < 2:
            continue
        display_steps.append({
            "type": step_type,
            "mode": step_type,
            "name": line_name,
            "line": line_name,
            "start": str(step.get("start") or "").strip(),
            "end": str(step.get("end") or "").strip(),
            "from": str(step.get("start") or "").strip(),
            "to": str(step.get("end") or "").strip(),
            "duration_min": duration_value,
            "distance_m": distance_value,
            "distance_text": f"{distance_value}m" if distance_value is not None else "",
            "geometry_source": "kakao_pubtrans_polyline",
        })
        points = [{"lat": lat, "lng": lng} for lng, lat in coordinates]
        display_path_segments.append({
            "type": step_type,
            "mode": step_type,
            "name": line_name,
            "line": line_name,
            "from": str(step.get("start") or "").strip(),
            "to": str(step.get("end") or "").strip(),
            "style": "walk" if step_type == "walk" else "solid",
            "color": _display_segment_color(step_type, line_name),
            "geometry_source": "kakao_pubtrans_polyline",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "points": points,
            })
    selected_duration_min = _safe_int(selected.get("duration_min"))
    selected_transfer_count = _compute_transfer_count_from_steps(route_steps)
    result = {
        "provider": "KAKAO_PUBTRANS",
        "match_method": "accepted_route" if acceptable else "route_too_different",
        "route_match_score": score_value,
        "selected_match_score": score_value,
        "kakao_top_n": 10,
        "kakao_live_status": live_status.get("kakao_live_status"),
        "kakao_cache_fallback_used": bool(live_status.get("kakao_cache_fallback_used")),
        "summary_source": "RECOMMENDATION_ENGINE",
        "geometry_provider": "KAKAO_PUBTRANS_MATCHED_ROUTE" if acceptable and display_path_segments else "KAKAO_PUBTRANS_UNAVAILABLE",
        "polyline_rendered": bool(acceptable and display_path_segments),
        "route_display_mode": "kakao",
        "kakao_candidates": [
            {
                "rank": index + 1,
                "duration_min": entry["meta"].get("display_duration_min"),
                "transfer_count": entry["meta"].get("display_transfer_count"),
                "mode_signature": entry["meta"].get("display_mode_signature"),
                "lines": entry["meta"].get("display_lines") or [],
                "match_score": entry["score"],
                "selected": entry is selected_entry,
                "reject_reason": None if entry.get("acceptable") else (
                    "mode_signature_mismatch" if not entry["meta"].get("mode_match") else
                    "transfer_count_or_duration_mismatch"
                ),
            }
            for index, entry in enumerate(ranked_routes)
        ],
        "route_mode_match": score_meta.get("mode_match"),
        "original_duration_min": original_duration,
        "display_duration_min": selected_duration_min,
        "original_transfer_count": original_transfer,
        "display_transfer_count": selected_transfer_count,
        "original_mode_signature": original_mode,
        "display_mode_signature": display_mode,
        "selected_kakao_mode_signature": display_mode,
        "selected_kakao_duration_min": selected_duration_min,
        "selected_kakao_transfer_count": selected_transfer_count,
        "duration_min": selected_duration_min if acceptable else original_duration,
        "transfer_count": selected_transfer_count if acceptable else original_transfer,
        "display_steps": display_steps if acceptable else [],
        "display_path_segments": display_path_segments if acceptable else [],
        "error": None if acceptable and display_path_segments else (reject_reason or "상세 경로를 불러오지 못했습니다."),
    }
    _route_cache_set(cache_key, result)
    return result


def _build_odsay_display_route(
    start: dict,
    end: dict,
    *,
    steps: list[dict],
    sub_paths: list[dict],
    info: dict,
) -> dict:
    display_segments = []
    map_obj = str(info.get("mapObject") or info.get("mapObj") or info.get("map_object") or info.get("mapobj") or "").strip() or None
    if map_obj:
        display_segments = fetch_transit_lane_segments(map_obj, sub_paths)
    if not display_segments:
        for section in sub_paths:
            traffic_type = int(to_number(section.get("trafficType"), 0))
            if traffic_type not in (1, 2, 3):
                continue
            points = _fallback_transit_points(section)
            if len(points) < 2:
                continue
            if traffic_type == 1:
                segment_type = "subway"
            elif traffic_type == 2:
                segment_type = "bus"
            else:
                segment_type = "walk"
            line_name = _odsay_lane_name(section) or ("도보" if segment_type == "walk" else "이동")
            color_hint = _odsay_lane_color_hint(section)
            display_segments.append({
                "type": segment_type,
                "mode": segment_type,
                "name": line_name,
                "line": line_name,
                "from": section.get("startName") or "",
                "to": section.get("endName") or "",
                "style": "walk" if segment_type == "walk" else "solid",
                "category_hint": color_hint,
                "color": _display_segment_color(segment_type, line_name, color_hint),
                "geometry_source": "odsay_lane" if segment_type in {"bus", "subway"} else "odsay_walk",
                "points": points,
            })

    display_steps = [
        {
            "type": step.get("type"),
            "mode": step.get("type"),
            "name": step.get("name") or ("도보" if step.get("type") == "walk" else ""),
            "line": step.get("name") or ("도보" if step.get("type") == "walk" else ""),
            "start": step.get("start") or "",
            "end": step.get("end") or "",
            "from": step.get("start") or "",
            "to": step.get("end") or "",
            "duration_min": step.get("duration_min"),
            "distance_m": step.get("distance_m"),
            "distance_text": f"{step.get('distance_m')}m" if step.get("distance_m") is not None else "",
            "geometry_source": "odsay_route",
        }
        for step in steps
    ]

    total_walk_m = int(sum((step.get("distance_m") or 0) for step in steps if step.get("type") == "walk"))
    walk_time_min = int(sum((step.get("duration_min") or 0) for step in steps if step.get("type") == "walk"))
    transfer_count = _compute_transfer_count_from_steps(steps)
    mode_signature = _kakao_route_mode_signature(steps)
    return {
        "provider": "ODSAY",
        "match_method": "odsay_display_mode",
        "route_match_score": None,
        "selected_match_score": None,
        "kakao_top_n": 0,
        "kakao_live_status": None,
        "kakao_cache_fallback_used": False,
        "summary_source": "RECOMMENDATION_ENGINE",
        "geometry_provider": "ODSAY_TRANSIT",
        "polyline_rendered": bool(display_segments),
        "route_display_mode": "odsay",
        "kakao_candidates": [],
        "original_duration_min": _safe_int(info.get("totalTime")) or None,
        "display_duration_min": _safe_int(info.get("totalTime")) or None,
        "original_transfer_count": transfer_count,
        "display_transfer_count": transfer_count,
        "original_mode_signature": mode_signature,
        "display_mode_signature": mode_signature,
        "selected_kakao_mode_signature": None,
        "selected_kakao_duration_min": None,
        "selected_kakao_transfer_count": None,
        "duration_min": _safe_int(info.get("totalTime")) or None,
        "transfer_count": transfer_count,
        "display_steps": display_steps,
        "display_path_segments": display_segments,
        "error": None if display_segments else "상세 경로를 불러오지 못했습니다.",
    }


def _walking_only_transit_route(
    start: dict,
    end: dict,
    *,
    failure_detail: str = "walkable_before_transit",
    route_quality: str = "WALKABLE_DIRECT",
) -> dict:
    distance_km = round(haversine_km(start["lat"], start["lng"], end["lat"], end["lng"]), 2)
    distance_m = max(1, int(round(distance_km * 1000)))
    duration_min = max(1, int(round(distance_m / 67)))
    walk_points = fetch_walking_path(start, end)
    route = _with_route_debug({
        "route_type": "walk",
        "mode": "transit",
        "route_status": "WALKABLE_NO_TRANSIT",
        "distance_km": distance_km,
        "duration_min": duration_min,
        "route_summary": f"도보 약 {duration_min}분 · {distance_m}m",
        "path": walk_points,
        "path_segments": [{"type": "walk", "style": "walk", "color": "#5b7cfa", "points": walk_points}] if len(walk_points) >= 2 else [],
        "steps": [
            {
                "type": "walk",
                "distance_m": distance_m,
                "duration_min": duration_min,
            }
        ],
        "payment": 0,
        "bus_transit_count": 0,
        "subway_transit_count": 0,
        "subway_section_count": 0,
        "total_walk_m": distance_m,
        "first_walk_m": distance_m,
        "first_walk_min": duration_min,
        "last_walk_m": 0,
        "last_walk_min": 0,
        "walk_distance_m": distance_m,
        "walk_time_min": duration_min,
        "_debug": {
            "route_source": "WALKABLE_FALLBACK",
            "odsay_called": False,
            "odsay_http_status": None,
            "odsay_error_code": None,
            "odsay_error_message": None,
        },
    }, cache_used=False, cache_valid=True)
    route["failure_detail"] = failure_detail
    route["_debug"]["route_quality"] = route_quality
    return route


def _estimated_transit_route(start: dict, end: dict, *, failure_detail: str = "vercel_lite_estimate") -> dict:
    def interpolate_point(a: dict, b: dict, ratio: float) -> dict:
        return {
            "lat": float(a["lat"]) + (float(b["lat"]) - float(a["lat"])) * ratio,
            "lng": float(a["lng"]) + (float(b["lng"]) - float(a["lng"])) * ratio,
        }

    distance_km = round(haversine_km(start["lat"], start["lng"], end["lat"], end["lng"]), 2)
    distance_m = max(1, int(round(distance_km * 1000)))
    duration_min = max(1, int(round(distance_km / 18.0 * 60)) + 8)
    walk_in_m = max(120, min(500, int(distance_m * 0.18)))
    walk_out_m = max(120, min(450, int(distance_m * 0.14)))
    transfer_count = 1 if distance_km >= 2.0 else 0
    bus_name = "273번"
    subway_name = "2호선"
    if distance_km >= 5.0:
        bus_name = "M버스"
        subway_name = "신분당선"
    elif distance_km >= 3.0:
        bus_name = "간선버스"
        subway_name = "9호선"

    start_point = {"lat": float(start["lat"]), "lng": float(start["lng"])}
    end_point = {"lat": float(end["lat"]), "lng": float(end["lng"])}
    p1 = interpolate_point(start_point, end_point, 0.22)
    p2 = interpolate_point(start_point, end_point, 0.62)
    path_segments = [
        {"type": "walk", "style": "walk", "points": [start_point, p1]},
        {"type": "bus", "style": "solid", "color": _display_segment_color("bus", bus_name), "points": [p1, p2]},
        {"type": "subway", "style": "solid", "color": _display_segment_color("subway", subway_name), "points": [p2, end_point]},
    ]
    path = []
    for segment in path_segments:
        _append_points(path, segment.get("points") or [])

    steps = [
        {"type": "walk", "distance_m": walk_in_m, "duration_min": max(1, int(round(walk_in_m / 67)))},
        {"type": "bus", "name": bus_name, "start": "출발지 인근", "end": "환승/도착 구간", "duration_min": max(1, int(round(duration_min * 0.45)))},
        {"type": "subway", "name": subway_name, "start": "환승역", "end": "도착역", "duration_min": max(1, int(round(duration_min * 0.35)))},
        {"type": "walk", "distance_m": walk_out_m, "duration_min": max(1, int(round(walk_out_m / 67)))},
    ]
    display_steps = [
        {
            "type": step.get("type"),
            "mode": step.get("type"),
            "name": step.get("name") or ("도보" if step.get("type") == "walk" else ""),
            "line": step.get("name") or ("도보" if step.get("type") == "walk" else ""),
            "start": step.get("start") or "",
            "end": step.get("end") or "",
            "from": step.get("start") or "",
            "to": step.get("end") or "",
            "duration_min": step.get("duration_min"),
            "distance_m": step.get("distance_m"),
            "distance_text": f"{step.get('distance_m')}m" if step.get("distance_m") is not None else "",
            "geometry_source": "local_estimate",
        }
        for step in steps
    ]
    display_path_segments = [
        {"type": segment.get("type"), "style": segment.get("style"), "color": segment.get("color"), "points": segment.get("points") or []}
        for segment in path_segments
    ]
    total_walk_m = walk_in_m + walk_out_m
    walk_time_min = int(sum((step.get("duration_min") or 0) for step in steps if step.get("type") == "walk"))
    return _with_route_debug({
        "route_type": "transit",
        "mode": "transit",
        "route_status": "TRANSIT_OK",
        "route_provider": "LOCAL_ESTIMATE",
        "route_geometry_provider": "LOCAL_ESTIMATE",
        "route_display_version": ROUTE_DISPLAY_VERSION,
        "route_display_mode": ROUTE_DISPLAY_MODE,
        "summary_source": "RECOMMENDATION_ENGINE",
        "distance_km": distance_km,
        "duration_min": duration_min,
        "route_summary": f"총 {duration_min}분(추정) · 환승 {transfer_count}회 · 도보 {total_walk_m}m",
        "path": path,
        "path_segments": [],
        "steps": steps,
        "original_duration_min": duration_min,
        "display_duration_min": duration_min,
        "original_transfer_count": transfer_count,
        "display_transfer_count": transfer_count,
        "original_mode_signature": _kakao_route_mode_signature(steps),
        "display_mode_signature": _kakao_route_mode_signature(steps),
        "route_match_score": None,
        "display_total_walk_m": total_walk_m,
        "display_walk_time_min": walk_time_min,
        "display_path_segments": display_path_segments,
        "display_steps": display_steps,
        "display_route_provider": "LOCAL_ESTIMATE",
        "display_route_match_method": "local_estimate",
        "display_route_error": None,
        "selected_kakao_mode_signature": None,
        "selected_kakao_duration_min": None,
        "selected_kakao_transfer_count": None,
        "polyline_rendered": bool(display_path_segments),
        "payment": 0,
        "bus_transit_count": 1,
        "subway_transit_count": 1,
        "subway_section_count": 1,
        "transfer_count": transfer_count,
        "total_walk_m": total_walk_m,
        "first_walk_m": walk_in_m,
        "first_walk_min": max(1, int(round(walk_in_m / 67))),
        "last_walk_m": walk_out_m,
        "last_walk_min": max(1, int(round(walk_out_m / 67))),
        "walk_distance_m": total_walk_m,
        "walk_time_min": walk_time_min,
        "failure_detail": failure_detail,
        "_debug": {
            "route_source": "LOCAL_ESTIMATE",
            "odsay_called": False,
            "odsay_http_status": None,
            "odsay_error_code": None,
            "odsay_error_message": None,
            "route_geometry_provider": "LOCAL_ESTIMATE",
            "route_display_version": ROUTE_DISPLAY_VERSION,
            "route_display_mode": ROUTE_DISPLAY_MODE,
            "summary_source": "RECOMMENDATION_ENGINE",
            "display_step_count": len(display_steps),
            "display_segment_count": len(display_path_segments),
            "polyline_rendered": bool(display_path_segments),
            "route_quality": "ESTIMATED",
        },
    }, cache_used=False, cache_valid=True)


def _walking_only_car_route(start: dict, end: dict, route_status: str) -> dict:
    distance_km = round(haversine_km(start["lat"], start["lng"], end["lat"], end["lng"]), 2)
    distance_m = max(1, int(round(distance_km * 1000)))
    duration_min = max(1, int(round(distance_m / 67)))
    walk_points = fetch_walking_path(start, end)
    fallback_walk_points = walk_points if len(walk_points) >= 2 else [
        {"lat": float(start["lat"]), "lng": float(start["lng"])},
        {"lat": float(end["lat"]), "lng": float(end["lng"])},
    ]
    summary = "직장/학교와 가까워 도보 이동이 가능한 후보" if route_status == "NEAR_DESTINATION" else f"도보 약 {duration_min}분 · {distance_m}m"
    return _with_route_debug({
        "route_type": "walk",
        "mode": "car",
        "route_status": route_status,
        "distance_km": distance_km,
        "duration_min": duration_min,
        "route_summary": summary,
        "path": fallback_walk_points,
        "path_segments": [{"type": "walk", "style": "walk", "color": "#5b7cfa", "points": fallback_walk_points}],
        "steps": [
            {
                "type": "walk",
                "distance_m": distance_m,
                "duration_min": duration_min,
            }
        ],
        "payment": 0,
        "bus_transit_count": 0,
        "subway_transit_count": 0,
        "subway_section_count": 0,
        "total_walk_m": distance_m,
        "first_walk_m": distance_m,
        "first_walk_min": duration_min,
        "last_walk_m": 0,
        "last_walk_min": 0,
        "walk_distance_m": distance_m,
        "walk_time_min": duration_min,
        "_debug": {
            "route_source": "WALKABLE_FALLBACK",
            "odsay_called": False,
            "odsay_http_status": None,
            "odsay_error_code": None,
            "odsay_error_message": None,
        },
    }, cache_used=False, cache_valid=True)


def _estimated_car_route(
    start: dict,
    end: dict,
    *,
    requested_provider: str,
    route_time_basis: str | None,
    route_direction: str,
    car_time_profile: dict | None,
    failure_detail: str | None,
    response_received: bool = False,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    profile = _normalize_car_time_profile(car_time_profile)
    prediction_type, prediction_time = _tmap_prediction_type_and_time(profile)
    distance_km = round(haversine_km(start["lat"], start["lng"], end["lat"], end["lng"]), 2)
    distance_m = max(1, int(round(distance_km * 1000)))
    duration_min = max(1, int(round(distance_km / 28.0 * 60)))
    path = [
        {"lat": float(start["lat"]), "lng": float(start["lng"])},
        {"lat": float(end["lat"]), "lng": float(end["lng"])},
    ]
    route = {
        "route_type": "car",
        "mode": "car",
        "route_status": "CAR_OK",
        "distance_km": distance_km,
        "duration_min": duration_min,
        "route_summary": f"자동차 약 {duration_min}분(추정)",
        "path": path,
        "path_segments": [{"type": "car", "style": "solid", "points": path}],
        "steps": [
            {
                "type": "car",
                "name": "추정 경로",
                "distance_m": distance_m,
                "duration_min": duration_min,
            }
        ],
        "payment": None,
        "bus_transit_count": 0,
        "subway_transit_count": 0,
        "subway_section_count": 0,
        "total_walk_m": 0,
        "route_provider": "LOCAL_ESTIMATE",
        "car_route_provider": "LOCAL_ESTIMATE",
        "car_route_requested_provider": requested_provider,
        "car_route_time_basis": "selected_but_not_applied" if profile.get("enabled") else route_time_basis,
        "car_route_time_label": _car_route_time_label(profile),
        "car_route_time_supported": False if profile.get("enabled") else None,
        "car_route_direction": route_direction,
        "selected_car_time": profile.get("selected_car_time"),
        "car_route_prediction_type": prediction_type if profile.get("enabled") else None,
        "car_route_prediction_time": prediction_time if profile.get("enabled") else None,
        "car_time_profile": profile,
        "car_route_http_status": None,
        "car_route_response_received": bool(response_received),
        "car_route_error_code": error_code or "LOCAL_ESTIMATE",
        "car_route_error_message": error_message or failure_detail,
        "car_route_failure_detail": failure_detail,
        "car_route_fallback_used": True,
        "_debug": {
            "route_source": "LOCAL_ESTIMATE",
            "odsay_called": False,
            "odsay_http_status": None,
            "odsay_error_code": None,
            "odsay_error_message": None,
            "car_route_provider": "LOCAL_ESTIMATE",
            "car_route_requested_provider": requested_provider,
            "car_route_time_basis": "selected_but_not_applied" if profile.get("enabled") else route_time_basis,
            "car_route_time_label": _car_route_time_label(profile),
            "car_route_time_supported": False if profile.get("enabled") else None,
            "car_route_direction": route_direction,
            "selected_car_time": profile.get("selected_car_time"),
            "car_route_prediction_type": prediction_type if profile.get("enabled") else None,
            "car_route_prediction_time": prediction_time if profile.get("enabled") else None,
            "car_time_profile": profile,
            "car_route_http_status": None,
            "car_route_response_received": bool(response_received),
            "car_route_error_code": error_code or "LOCAL_ESTIMATE",
            "car_route_error_message": error_message or failure_detail,
            "car_route_failure_detail": failure_detail,
            "car_route_fallback_used": True,
        },
    }
    return _with_route_debug(route, cache_used=False, cache_valid=True)


def fetch_car_route(start: dict, end: dict, car_time_profile: dict | None = None) -> dict:
    profile = _normalize_car_time_profile(car_time_profile)
    request_start, request_end = _car_route_endpoints(start, end, profile)
    requested_provider = _car_route_requested_provider_label(profile)
    route_time_basis = _car_route_time_basis_label(profile)
    route_time_label = _car_route_time_label(profile)
    route_direction = _car_route_direction_label(profile)
    time_sentence = _car_time_basis_sentence(profile)
    prediction_type, prediction_time = _tmap_prediction_type_and_time(profile)
    selected_car_time = profile.get("selected_car_time")

    def _annotate(route: dict, actual_provider: str, *, http_status: int | None = None, failure_detail: str | None = None, fallback_used: bool = False) -> dict:
        time_supported = bool(profile.get("enabled")) and actual_provider == "TMAP_TIME_MACHINE" and not fallback_used
        route_time_status = route_time_basis if time_supported else ("selected_but_not_applied" if profile.get("enabled") else route_time_basis)
        annotated = dict(route)
        annotated.setdefault("route_provider", actual_provider)
        annotated.setdefault("car_route_provider", actual_provider)
        annotated["car_route_requested_provider"] = requested_provider
        annotated["car_route_time_basis"] = route_time_status
        annotated["car_route_time_label"] = route_time_label
        annotated["car_route_time_supported"] = time_supported if profile.get("enabled") else None
        annotated["car_route_direction"] = route_direction
        annotated["car_route_prediction_type"] = prediction_type if profile.get("enabled") else None
        annotated["car_route_prediction_time"] = prediction_time if profile.get("enabled") else None
        annotated["selected_car_time"] = selected_car_time if profile.get("enabled") else None
        annotated.setdefault("car_route_departure_lat", request_start["lat"])
        annotated.setdefault("car_route_departure_lng", request_start["lng"])
        annotated.setdefault("car_route_destination_lat", request_end["lat"])
        annotated.setdefault("car_route_destination_lng", request_end["lng"])
        annotated["car_time_profile"] = profile
        annotated.setdefault("car_route_http_status", http_status)
        annotated.setdefault("car_route_response_received", http_status is not None)
        annotated.setdefault("car_route_error_code", None if http_status is not None else ("MISSING_KEY" if actual_provider == "LOCAL_ESTIMATE" else None))
        annotated.setdefault("car_route_error_message", failure_detail)
        annotated.setdefault("car_route_failure_detail", failure_detail)
        annotated.setdefault("car_route_fallback_used", bool(fallback_used))
        annotated.setdefault("_debug", {})
        annotated["_debug"].update({
            "route_source": actual_provider,
            "car_route_provider": actual_provider,
            "car_route_requested_provider": requested_provider,
            "car_route_time_basis": route_time_status,
            "car_route_time_label": route_time_label,
            "car_route_time_supported": time_supported if profile.get("enabled") else None,
            "car_route_direction": route_direction,
            "car_route_prediction_type": prediction_type if profile.get("enabled") else None,
            "car_route_prediction_time": prediction_time if profile.get("enabled") else None,
            "selected_car_time": selected_car_time if profile.get("enabled") else None,
            "car_route_departure_lat": request_start["lat"],
            "car_route_departure_lng": request_start["lng"],
            "car_route_destination_lat": request_end["lat"],
            "car_route_destination_lng": request_end["lng"],
            "car_time_profile": profile,
            "car_route_http_status": http_status,
            "car_route_response_received": http_status is not None,
            "car_route_error_code": None if http_status is not None else ("MISSING_KEY" if actual_provider == "LOCAL_ESTIMATE" else None),
            "car_route_error_message": failure_detail,
            "car_route_failure_detail": failure_detail,
            "car_route_fallback_used": bool(fallback_used),
        })
        return annotated
    fallback_failure_detail = None
    fallback_used = False
    invalid_cache_ignored = False
    tmap_response_received = False
    tmap_error_code = None
    tmap_error_message = None

    if profile.get("enabled") and requested_provider == "TMAP_TIME_MACHINE":
        cache_key = _route_cache_key(request_start, request_end, "car", "tmap_time_machine")
        cached = _route_cache_get(cache_key)
        if cached and not _is_invalid_car_cache_entry(cached):
            cached = dict(cached)
            cached.setdefault("route_status", "CAR_OK")
            cached.setdefault("route_type", "car")
            cached.setdefault("car_route_requested_provider", requested_provider)
            duration_min = to_number(cached.get("duration_min"), None)
            if duration_min is not None:
                cached["route_summary"] = cached.get("route_summary") or f"자동차 약 {int(round(duration_min))}분"
            cached = _annotate(cached, "TMAP_TIME_MACHINE", http_status=200, fallback_used=False)
            cached.setdefault("path", [])
            cached.setdefault("path_segments", [])
            return _with_route_debug(cached, cache_used=True, cache_valid=True)
        if cached and _is_invalid_car_cache_entry(cached):
            _route_cache_delete(cache_key)
            invalid_cache_ignored = True

        if TMAP_APP_KEY:
            request_config = _tmap_car_route_request_config(request_start, request_end, profile)
            response = None
            last_error = None
            for timeout in (CAR_REQUEST_TIMEOUT, CAR_REQUEST_TIMEOUT + 5):
                try:
                    response = requests.post(
                        request_config["url"],
                        params={"version": "1", "resCoordType": "WGS84GEO", "reqCoordType": "WGS84GEO", "sort": "index"},
                        json=request_config["json"],
                        headers=request_config["headers"],
                        timeout=timeout,
                    )
                    break
                except RequestException as exc:
                    last_error = exc
                    response = None

            if response is not None:
                http_status = response.status_code
                tmap_response_received = True
                try:
                    payload = response.json()
                except Exception:
                    payload = None
                parsed = _parse_tmap_car_route_payload(payload or {})
                if response.ok and parsed:
                    result = {
                        "route_type": "car",
                        "mode": "car",
                        "route_status": "CAR_OK",
                        "distance_km": parsed["distance_km"],
                        "duration_min": parsed["duration_min"],
                        "route_summary": parsed["route_summary"],
                        "path": parsed["path"],
                        "path_segments": parsed["path_segments"],
                        "steps": parsed["steps"],
                        "payment": parsed["payment"],
                        "bus_transit_count": 0,
                        "subway_transit_count": 0,
                        "subway_section_count": 0,
                        "total_walk_m": 0,
                        "route_provider": "TMAP_TIME_MACHINE",
                        "car_route_provider": "TMAP_TIME_MACHINE",
                        "car_route_requested_provider": requested_provider,
                        "car_route_time_basis": route_time_basis,
                        "car_route_time_label": route_time_label,
                        "car_route_time_supported": True,
                        "car_route_direction": route_direction,
                        "car_route_prediction_type": prediction_type,
                        "car_route_prediction_time": prediction_time,
                        "selected_car_time": selected_car_time,
                        "car_route_departure_lat": request_start["lat"],
                        "car_route_departure_lng": request_start["lng"],
                        "car_route_destination_lat": request_end["lat"],
                        "car_route_destination_lng": request_end["lng"],
                        "car_time_profile": profile,
                        "car_route_http_status": http_status,
                        "car_route_response_received": True,
                        "car_route_error_code": None,
                        "car_route_error_message": None,
                        "car_route_failure_detail": None,
                        "car_route_fallback_used": False,
                        "car_total_distance_m": parsed["car_total_distance_m"],
                        "car_total_time_sec": parsed["car_total_time_sec"],
                        "car_departure_time": parsed["car_departure_time"],
                        "car_arrival_time": parsed["car_arrival_time"],
                        "taxi_fare": parsed["taxi_fare"],
                        "_debug": {
                            "route_source": "TMAP_TIME_MACHINE",
                            "odsay_called": False,
                            "odsay_http_status": None,
                            "odsay_error_code": None,
                            "odsay_error_message": None,
                            "car_route_provider": "TMAP_TIME_MACHINE",
                            "car_route_requested_provider": requested_provider,
                            "car_route_time_basis": route_time_basis,
                            "car_route_time_label": route_time_label,
                            "car_route_time_supported": True,
                            "car_route_direction": route_direction,
                            "car_route_prediction_type": prediction_type,
                            "car_route_prediction_time": prediction_time,
                            "selected_car_time": selected_car_time,
                            "car_route_departure_lat": request_start["lat"],
                            "car_route_departure_lng": request_start["lng"],
                            "car_route_destination_lat": request_end["lat"],
                            "car_route_destination_lng": request_end["lng"],
                            "car_time_profile": profile,
                            "car_route_http_status": http_status,
                            "car_route_response_received": True,
                            "car_route_error_code": None,
                            "car_route_error_message": None,
                            "car_route_failure_detail": None,
                            "car_route_fallback_used": False,
                        },
                    }
                    _route_cache_set(cache_key, result)
                    return _with_route_debug(result, cache_used=False, cache_valid=True, invalid_cache_ignored=invalid_cache_ignored)

                error_code, error_message = _tmap_route_error_info(http_status, payload)
                fallback_failure_detail = f"TMAP Time Machine 경로 실패: {_tmap_route_error_detail(http_status, payload)}"
                tmap_error_code = error_code
                tmap_error_message = error_message
            else:
                detail = str(last_error) if last_error else "요청 실패"
                fallback_failure_detail = f"TMAP Time Machine 경로 실패: {detail}"
                tmap_error_code = "CONNECTION_ERROR"
                tmap_error_message = detail
        else:
            fallback_failure_detail = "TMAP Time Machine API 키가 없습니다."
            tmap_error_code = "MISSING_KEY"
            tmap_error_message = fallback_failure_detail
        fallback_used = True

    if not KAKAO_REST_API_KEY:
        return _estimated_car_route(
            request_start,
            request_end,
            requested_provider=requested_provider,
            route_time_basis=route_time_basis,
            route_direction=route_direction,
            car_time_profile=profile,
            failure_detail=fallback_failure_detail or "자동차 경로 API 키가 없습니다.",
            response_received=False,
            error_code="MISSING_KEY",
            error_message=fallback_failure_detail or "자동차 경로 API 키가 없습니다.",
        )

    cache_key = _route_cache_key(request_start, request_end, "car", "live_kakao")
    cached = _route_cache_get(cache_key)
    if cached and not _is_invalid_car_cache_entry(cached):
        cached = dict(cached)
        cached.setdefault("route_status", "CAR_OK")
        cached.setdefault("route_type", "car")
        duration_min = to_number(cached.get("duration_min"), None)
        if duration_min is not None:
            cached["route_summary"] = cached.get("route_summary") or f"자동차 약 {int(round(duration_min))}분"
        cached = _annotate(cached, "LIVE_KAKAO", http_status=200, failure_detail=fallback_failure_detail, fallback_used=fallback_used)
        cached.setdefault("path", [])
        cached.setdefault("path_segments", [])
        return _with_route_debug(cached, cache_used=True, cache_valid=True, invalid_cache_ignored=invalid_cache_ignored)
    if cached and _is_invalid_car_cache_entry(cached):
        _route_cache_delete(cache_key)
        invalid_cache_ignored = True

    response = None
    last_error = None
    for timeout in (CAR_REQUEST_TIMEOUT, CAR_REQUEST_TIMEOUT + 5):
        try:
            response = requests.get(
                KAKAO_MOBILITY_DIRECTIONS_URL,
                params={
                    "origin": f"{request_start['lng']},{request_start['lat']}",
                    "destination": f"{request_end['lng']},{request_end['lat']}",
                    "priority": "RECOMMEND",
                },
                headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
                timeout=timeout,
            )
            response.raise_for_status()
            break
        except RequestException as exc:
            last_error = exc
            response = None

    if response is None:
        detail = str(last_error) if last_error else "요청 실패"
        combined_detail = fallback_failure_detail or detail
        return _estimated_car_route(
            request_start,
            request_end,
            requested_provider=requested_provider,
            route_time_basis=route_time_basis,
            route_direction=route_direction,
            car_time_profile=profile,
            failure_detail=f"자동차 경로를 불러오지 못했습니다. ({combined_detail})",
            response_received=tmap_response_received,
            error_code=tmap_error_code,
            error_message=tmap_error_message or combined_detail,
        )
    routes = response.json().get("routes", [])
    if not routes:
        combined_detail = fallback_failure_detail or "자동차 경로를 찾지 못했습니다."
        return _estimated_car_route(
            request_start,
            request_end,
            requested_provider=requested_provider,
            route_time_basis=route_time_basis,
            route_direction=route_direction,
            car_time_profile=profile,
            failure_detail=f"자동차 경로를 찾지 못했습니다. ({combined_detail})",
            response_received=tmap_response_received,
            error_code=tmap_error_code,
            error_message=tmap_error_message or combined_detail,
        )

    route = routes[0]
    summary = route.get("summary", {})
    sections = route.get("sections", []) or []
    duration_sec = to_number(summary.get("duration"), 0)
    distance_m = to_number(summary.get("distance"), 0)
    duration_min = max(1, int(round(duration_sec / 60)))
    straight_distance_km = haversine_km(request_start["lat"], request_start["lng"], request_end["lat"], request_end["lng"])

    path = []
    steps = []
    for section in sections:
        for road in section.get("roads", []) or []:
            vertices = road.get("vertexes", []) or []
            road_points = []
            for index in range(0, len(vertices), 2):
                lng = to_number(vertices[index], None)
                lat = to_number(vertices[index + 1], None) if index + 1 < len(vertices) else None
                if lat is None or lng is None:
                    continue
                point = {"lat": float(lat), "lng": float(lng)}
                path.append(point)
                road_points.append(point)
            if road_points:
                steps.append(
                    {
                        "type": "car",
                        "name": road.get("name") or "도로 이동",
                        "distance_m": int(to_number(road.get("distance"), 0)),
                        "duration_min": max(1, int(round(to_number(road.get("duration"), 0) / 60))) if road.get("duration") is not None else None,
                    }
                )

    if distance_m <= 0 or (straight_distance_km >= 1.0 and duration_min <= 2):
        combined_detail = fallback_failure_detail or "자동차 경로를 정상적으로 계산하지 못했습니다."
        return _estimated_car_route(
            request_start,
            request_end,
            requested_provider=requested_provider,
            route_time_basis=route_time_basis,
            route_direction=route_direction,
            car_time_profile=profile,
            failure_detail=f"자동차 경로를 정상적으로 계산하지 못했습니다. ({combined_detail})",
            response_received=tmap_response_received,
            error_code=tmap_error_code,
            error_message=tmap_error_message or combined_detail,
        )

    result = {
        "route_type": "car",
        "mode": "car",
        "route_status": "CAR_OK",
        "distance_km": round(distance_m / 1000, 2),
        "duration_min": duration_min,
        "route_summary": f"자동차 약 {duration_min}분",
        "path": path,
        "path_segments": [{"type": "car", "style": "solid", "points": path}] if path else [],
        "steps": steps,
        "payment": None,
        "bus_transit_count": 0,
        "subway_transit_count": 0,
        "subway_section_count": 0,
        "total_walk_m": 0,
        "route_provider": "LIVE_KAKAO",
        "car_route_provider": "LIVE_KAKAO",
        "car_route_requested_provider": requested_provider,
        "car_route_time_basis": route_time_basis,
        "car_route_direction": route_direction,
        "car_route_prediction_type": prediction_type if profile.get("enabled") else None,
        "car_route_prediction_time": prediction_time if profile.get("enabled") else None,
        "car_route_departure_lat": request_start["lat"],
        "car_route_departure_lng": request_start["lng"],
        "car_route_destination_lat": request_end["lat"],
        "car_route_destination_lng": request_end["lng"],
        "car_time_profile": profile,
        "car_route_http_status": response.status_code,
        "car_route_failure_detail": fallback_failure_detail,
        "car_route_fallback_used": fallback_used,
        "_debug": {
            "route_source": "LIVE_KAKAO",
            "odsay_called": False,
            "odsay_http_status": None,
            "odsay_error_code": None,
            "odsay_error_message": None,
            "car_route_provider": "LIVE_KAKAO",
            "car_route_requested_provider": requested_provider,
            "car_route_time_basis": route_time_basis,
            "car_route_direction": route_direction,
            "car_route_prediction_type": prediction_type if profile.get("enabled") else None,
            "car_route_prediction_time": prediction_time if profile.get("enabled") else None,
            "car_route_departure_lat": request_start["lat"],
            "car_route_departure_lng": request_start["lng"],
            "car_route_destination_lat": request_end["lat"],
            "car_route_destination_lng": request_end["lng"],
            "car_time_profile": profile,
            "car_route_http_status": response.status_code,
            "car_route_failure_detail": fallback_failure_detail,
            "car_route_fallback_used": fallback_used,
        },
    }
    _route_cache_set(cache_key, result)
    return _with_route_debug(result, cache_used=False, cache_valid=True, invalid_cache_ignored=invalid_cache_ignored)


def _odsay_error_info(payload: dict) -> tuple[str | None, str | None]:
    error = payload.get("error")
    if isinstance(error, list) and error:
        first = error[0] or {}
        return str(first.get("code", "")).strip() or None, str(first.get("message") or first.get("msg") or "").strip() or None
    if isinstance(error, dict) and error:
        return str(error.get("code", "")).strip() or None, str(error.get("message") or error.get("msg") or "").strip() or None
    return None, None


def _odsay_lane_name(section: dict) -> str:
    lane = section.get("lane")
    if isinstance(lane, list) and lane:
        first = lane[0] or {}
        return str(first.get("busNo") or first.get("name") or first.get("subwayCode") or "").strip()
    if isinstance(lane, dict):
        return str(lane.get("busNo") or lane.get("name") or lane.get("subwayCode") or "").strip()
    return ""


def _odsay_lane_color_hint(section: dict) -> str:
    lane = section.get("lane")
    candidates = []
    if isinstance(lane, list):
        candidates = [item for item in lane if isinstance(item, dict)]
    elif isinstance(lane, dict):
        candidates = [lane]

    text_parts = []
    for candidate in candidates:
        for key in ("busNo", "name", "subwayCode", "type", "class", "busType", "busClass", "routeType"):
            value = candidate.get(key)
            if value is not None:
                text_parts.append(str(value))
    text_parts.extend([
        str(section.get("routeType") or ""),
        str(section.get("trafficType") or ""),
        str(section.get("laneType") or ""),
        str(section.get("type") or ""),
    ])
    return normalize_text(" ".join(text_parts))


def _odsay_walking_points(sub_paths: list[dict], index: int, start: dict, end: dict) -> list[dict]:
    walk_start, walk_end = _walk_leg_endpoints(sub_paths, index, start, end)
    if not walk_start or not walk_end:
        return []
    return fetch_walking_path(walk_start, walk_end)


def fetch_transit_route(start: dict, end: dict) -> dict:
    direct_distance_m = int(round(haversine_km(start["lat"], start["lng"], end["lat"], end["lng"]) * 1000))
    if direct_distance_m <= WALKABLE_AUTO_MAX_M:
        return _walking_only_transit_route(
            start,
            end,
            failure_detail="walkable_before_transit",
            route_quality="WALKABLE_DIRECT",
        )
    if not ODSAY_API_KEY:
        return _with_route_debug(
            _route_unavailable("transit", "ODSAY 대중교통 API 키가 없습니다.", "TRANSIT_ROUTE_FAILED"),
            cache_used=False,
            cache_valid=False,
        )

    cache_key = _route_cache_key(start, end, "transit", "odsay")
    cached = _route_cache_get(cache_key)
    invalid_cache_ignored = bool(isinstance(cached, dict) and cached.get("route_type") == "unavailable")
    low_fidelity_cache_ignored = bool(_is_low_fidelity_transit_route(cached))
    stale_provider_cache_ignored = bool(
        isinstance(cached, dict)
        and cached.get("route_type") == "transit"
        and cached.get("route_provider") != "odsay"
    )
    stale_geometry_cache_ignored = bool(
        isinstance(cached, dict)
        and cached.get("route_type") == "transit"
        and cached.get("route_geometry_provider") not in {
            None,
            "kakao_pubtrans",
            "kakao_pubtrans_unavailable",
            "KAKAO_PUBTRANS_MATCHED_ROUTE",
            "KAKAO_PUBTRANS_UNAVAILABLE",
            "ODSAY_TRANSIT",
            "odsay_transit",
        }
    )
    stale_display_cache_ignored = bool(_is_stale_transit_display_cache(cached))
    if cached and (
        cached.get("path_segments")
        or (cached.get("route_type") == "walk" and (cached.get("path") or cached.get("path_segments")))
    ) and not low_fidelity_cache_ignored and not stale_provider_cache_ignored and not stale_geometry_cache_ignored and not stale_display_cache_ignored:
        return _with_route_debug(dict(cached), cache_used=True, cache_valid=True)
    if low_fidelity_cache_ignored or stale_provider_cache_ignored or stale_geometry_cache_ignored or stale_display_cache_ignored:
        _route_cache_delete(cache_key)

    http_status = None
    request_config = build_odsay_request_config(start, end)
    try:
        response = requests.get(
            request_config["url"],
            params=request_config["params"],
            headers=request_config["headers"],
            timeout=request_config["timeout"],
        )
        http_status = response.status_code
        response.raise_for_status()
    except RequestException:
        if direct_distance_m <= WALKABLE_AUTO_MAX_M:
            return _walking_only_transit_route(
                start,
                end,
                failure_detail="odsay_failed_but_walkable",
                route_quality="WALKABLE_FALLBACK",
            )
        unavailable = _route_unavailable("transit", "ODSAY 대중교통 경로를 불러오지 못했습니다.", "TRANSIT_ROUTE_FAILED")
        unavailable["_debug"].update({
            "route_source": "TRANSIT_FAILED",
            "odsay_called": True,
            "odsay_http_status": http_status,
        })
        return _with_route_debug(
            unavailable,
            cache_used=False,
            cache_valid=False,
            invalid_cache_ignored=invalid_cache_ignored,
        )

    try:
        payload = response.json()
    except Exception:
        if direct_distance_m <= WALKABLE_AUTO_MAX_M:
            return _walking_only_transit_route(
                start,
                end,
                failure_detail="odsay_failed_but_walkable",
                route_quality="WALKABLE_FALLBACK",
            )
        unavailable = _route_unavailable("transit", "ODSAY 대중교통 응답을 해석하지 못했습니다.", "TRANSIT_ROUTE_FAILED")
        unavailable["_debug"].update({
            "route_source": "TRANSIT_FAILED",
            "odsay_called": True,
            "odsay_http_status": http_status,
        })
        return _with_route_debug(
            unavailable,
            cache_used=False,
            cache_valid=False,
            invalid_cache_ignored=invalid_cache_ignored,
        )

    error_code, error_msg = _odsay_error_info(payload)
    if error_code or error_msg:
        error_text = (error_msg or "")
        if "700m" in error_text or direct_distance_m <= WALKABLE_AUTO_MAX_M:
            return _walking_only_transit_route(
                start,
                end,
                failure_detail="odsay_failed_but_walkable",
                route_quality="WALKABLE_FALLBACK",
            )
        unavailable = _route_unavailable("transit", f"ODSAY 대중교통 경로 오류: {error_msg or error_code or '알 수 없음'}", "TRANSIT_ROUTE_FAILED")
        unavailable["_debug"].update({
            "route_source": "TRANSIT_FAILED",
            "odsay_called": True,
            "odsay_http_status": http_status,
            "odsay_error_code": error_code,
            "odsay_error_message": error_msg or None,
        })
        return _with_route_debug(
            unavailable,
            cache_used=False,
            cache_valid=False,
            invalid_cache_ignored=invalid_cache_ignored,
        )

    result_root = payload.get("result") or {}
    path_candidates = result_root.get("path") or []
    if not path_candidates:
        if direct_distance_m <= WALKABLE_AUTO_MAX_M:
            return _walking_only_transit_route(
                start,
                end,
                failure_detail="odsay_failed_but_walkable",
                route_quality="WALKABLE_FALLBACK",
            )
        unavailable = _route_unavailable("transit", "ODSAY 대중교통 경로를 찾지 못했습니다.", "TRANSIT_ROUTE_FAILED")
        unavailable["_debug"].update({
            "route_source": "TRANSIT_FAILED",
            "odsay_called": True,
            "odsay_http_status": http_status,
        })
        return _with_route_debug(
            unavailable,
            cache_used=False,
            cache_valid=False,
            invalid_cache_ignored=invalid_cache_ignored,
        )

    try:
        best = min(path_candidates, key=lambda item: to_number(((item.get("info") or {}).get("totalTime")), 999999))
    except Exception:
        if direct_distance_m <= WALKABLE_AUTO_MAX_M:
            return _walking_only_transit_route(
                start,
                end,
                failure_detail="odsay_failed_but_walkable",
                route_quality="WALKABLE_FALLBACK",
            )
        unavailable = _route_unavailable("transit", "ODSAY 대중교통 경로를 해석하지 못했습니다.", "TRANSIT_ROUTE_FAILED")
        unavailable["_debug"].update({
            "route_source": "TRANSIT_FAILED",
            "odsay_called": True,
            "odsay_http_status": http_status,
        })
        return _with_route_debug(
            unavailable,
            cache_used=False,
            cache_valid=False,
            invalid_cache_ignored=invalid_cache_ignored,
        )

    info = best.get("info") or {}
    sub_paths = best.get("subPath") or []
    steps = []
    bus_section_count = 0
    subway_section_count = 0

    for index, section in enumerate(sub_paths):
        traffic_type = int(to_number(section.get("trafficType"), 0))
        section_time = max(1, int(round(to_number(section.get("sectionTime"), 0))))
        section_distance = int(to_number(section.get("distance"), 0))
        if traffic_type == 3:
            steps.append({
                "type": "walk",
                "distance_m": section_distance,
                "duration_min": section_time,
            })
        elif traffic_type == 2:
            bus_section_count += 1
            steps.append({
                "type": "bus",
                "name": _odsay_lane_name(section) or "버스",
                "start": section.get("startName") or "",
                "end": section.get("endName") or "",
                "duration_min": section_time,
            })
        elif traffic_type == 1:
            subway_section_count += 1
            steps.append({
                "type": "subway",
                "name": _odsay_lane_name(section) or "지하철",
                "start": section.get("startName") or "",
                "end": section.get("endName") or "",
                "duration_min": section_time,
            })

    duration_min = max(1, int(round(to_number(info.get("totalTime"), 0))))
    total_walk_m = int(to_number(info.get("totalWalk"), 0) or to_number(info.get("totalWalkDistance"), 0))
    walk_steps = [step for step in steps if step.get("type") == "walk"]
    first_walk_m = int(walk_steps[0].get("distance_m", 0)) if walk_steps else 0
    first_walk_min = int(walk_steps[0].get("duration_min", 0)) if walk_steps else 0
    last_walk_m = int(walk_steps[-1].get("distance_m", 0)) if walk_steps else 0
    last_walk_min = int(walk_steps[-1].get("duration_min", 0)) if walk_steps else 0
    transfer_count = _compute_transfer_count_from_steps(steps)
    payment = int(to_number(info.get("payment"), 0))
    display_mode = ROUTE_DISPLAY_MODE
    if display_mode == "kakao":
        display_route = build_kakao_display_route(start, end, {
            "steps": steps,
            "duration_min": duration_min,
            "transfer_count": transfer_count,
        })
    else:
        display_route = _build_odsay_display_route(
            start,
            end,
            steps=steps,
            sub_paths=sub_paths,
            info=info,
        )
    display_steps = display_route.get("display_steps") or []
    display_path_segments = display_route.get("display_path_segments") or []
    geometry_provider = display_route.get("geometry_provider") or ("KAKAO_PUBTRANS_MATCHED_ROUTE" if display_mode == "kakao" and display_path_segments else "ODSAY_TRANSIT" if display_path_segments else "KAKAO_PUBTRANS_UNAVAILABLE")
    display_duration_min = _safe_int(display_route.get("display_duration_min"))
    display_transfer_count = _safe_int(display_route.get("display_transfer_count"))
    display_total_walk_m = int(sum((step.get("distance_m") or 0) for step in display_steps if step.get("type") == "walk")) if display_steps else total_walk_m
    display_walk_time_min = int(sum((step.get("duration_min") or 0) for step in display_steps if step.get("type") == "walk")) if display_steps else int(sum((step.get("duration_min") or 0) for step in walk_steps))
    path = []
    for segment in display_path_segments:
        _append_points(path, segment.get("points") or [])

    result = {
        "route_type": "transit",
        "mode": "transit",
        "route_status": "TRANSIT_OK",
        "route_provider": "odsay",
        "route_geometry_provider": geometry_provider,
        "route_display_version": ROUTE_DISPLAY_VERSION,
        "route_display_mode": display_mode,
        "summary_source": display_route.get("summary_source") or "RECOMMENDATION_ENGINE",
        "kakao_live_status": display_route.get("kakao_live_status"),
        "kakao_cache_fallback_used": display_route.get("kakao_cache_fallback_used"),
        "distance_km": round(haversine_km(start["lat"], start["lng"], end["lat"], end["lng"]), 2),
        "duration_min": duration_min,
        "route_summary": f"총 {duration_min}분 · {'환승 없음' if transfer_count == 0 else f'환승 {transfer_count}회'} · 도보 {total_walk_m}m",
        "path": path,
        "path_segments": [],
        "steps": steps,
        "original_duration_min": duration_min,
        "display_duration_min": display_duration_min,
        "original_transfer_count": transfer_count,
        "display_transfer_count": display_transfer_count,
        "original_mode_signature": _kakao_route_mode_signature(steps),
        "display_mode_signature": display_route.get("display_mode_signature"),
        "route_match_score": display_route.get("route_match_score"),
        "display_total_walk_m": display_total_walk_m,
        "display_walk_time_min": display_walk_time_min,
        "display_path_segments": display_path_segments,
        "display_steps": display_steps,
        "display_route_provider": display_route.get("provider"),
        "display_route_match_method": display_route.get("match_method"),
        "display_route_error": display_route.get("error"),
        "selected_kakao_mode_signature": display_route.get("selected_kakao_mode_signature"),
        "selected_kakao_duration_min": display_route.get("selected_kakao_duration_min"),
        "selected_kakao_transfer_count": display_route.get("selected_kakao_transfer_count"),
        "polyline_rendered": bool(display_path_segments),
        "payment": payment,
        "bus_transit_count": bus_section_count,
        "subway_transit_count": subway_section_count,
        "subway_section_count": subway_section_count,
        "transfer_count": transfer_count,
        "total_walk_m": total_walk_m,
        "first_walk_m": first_walk_m,
        "first_walk_min": first_walk_min,
        "last_walk_m": last_walk_m,
        "last_walk_min": last_walk_min,
        "walk_distance_m": total_walk_m,
        "walk_time_min": int(sum((step.get("duration_min") or 0) for step in walk_steps)),
        "_debug": {
            "route_source": "LIVE_ODSAY",
            "odsay_called": True,
            "odsay_http_status": http_status,
            "odsay_error_code": None,
            "odsay_error_message": None,
            "route_geometry_provider": geometry_provider,
            "route_display_version": ROUTE_DISPLAY_VERSION,
            "route_display_mode": display_mode,
            "summary_source": display_route.get("summary_source") or "RECOMMENDATION_ENGINE",
            "kakao_live_status": display_route.get("kakao_live_status"),
            "kakao_cache_fallback_used": display_route.get("kakao_cache_fallback_used"),
            "original_duration_min": duration_min,
            "display_duration_min": display_duration_min,
            "original_transfer_count": transfer_count,
            "display_transfer_count": display_transfer_count,
            "original_mode_signature": _kakao_route_mode_signature(steps),
            "display_mode_signature": display_route.get("display_mode_signature"),
            "selected_kakao_mode_signature": display_route.get("selected_kakao_mode_signature"),
            "selected_kakao_duration_min": display_route.get("selected_kakao_duration_min"),
            "selected_kakao_transfer_count": display_route.get("selected_kakao_transfer_count"),
            "route_match_score": display_route.get("route_match_score"),
            "display_route_provider": display_route.get("provider"),
            "display_route_match_method": display_route.get("match_method"),
            "display_route_error": display_route.get("error"),
            "display_step_count": len(display_steps or []),
            "display_segment_count": len(display_path_segments or []),
            "polyline_rendered": bool(display_path_segments),
        },
    }
    _route_cache_set(cache_key, result)
    return _with_route_debug(result, cache_used=False, cache_valid=True, invalid_cache_ignored=invalid_cache_ignored)


def fetch_route(start: dict, end: dict, transport_mode: str, car_time_profile: dict | None = None) -> dict:
    if IS_VERCEL_DEPLOYMENT:
        if transport_mode == "car":
            return _estimated_car_route(
                start,
                end,
                requested_provider=_car_route_requested_provider_label(car_time_profile),
                route_time_basis=_car_route_time_basis_label(car_time_profile),
                route_direction=_car_route_direction_label(car_time_profile),
                car_time_profile=car_time_profile,
                failure_detail="vercel_lite_estimate",
                response_received=False,
                error_code="LOCAL_ESTIMATE",
                error_message="Vercel 배포용 추정 경로",
            )
        return _estimated_transit_route(start, end, failure_detail="vercel_lite_estimate")
    if transport_mode == "car":
        return fetch_car_route(start, end, car_time_profile)
    return fetch_transit_route(start, end)


def normalize_request_state(request_state: dict | None) -> dict:
    raw = request_state or {}
    transport = raw.get("transport", {}) or {}
    hard = raw.get("hard_constraints", {}) or {}
    soft = raw.get("soft_preferences", {}) or {}
    route_preferences = raw.get("route_preferences", {}) or {}
    geo_constraints = raw.get("geo_constraints", {}) or {}
    tradeoff_policy = raw.get("tradeoff_policy", {}) or {}
    living_preferences = raw.get("living_preferences", {}) or {}
    car_time_profile = _normalize_car_time_profile(raw.get("car_time_profile"))
    unsupported_preferences = raw.get("unsupported_preferences", []) or []
    reference_preferences = raw.get("reference_preferences", []) or []
    deal_type = raw.get("deal_type") or hard.get("deal_type")

    transport_mode = raw.get("transport_mode") or transport.get("primary_mode") or DEFAULT_TRANSPORT_MODE
    if hard.get("max_commute_minutes") is None:
        hard["max_commute_minutes"] = _safe_int(raw.get("max_commute_minutes"))
    if hard.get("deposit_max") is None:
        hard["deposit_max"] = _safe_int(raw.get("deposit_max"))
    if hard.get("rent_max") is None:
        hard["rent_max"] = _safe_int(raw.get("rent_max"))
    hard["deal_type"] = deal_type

    return {
        "deal_type": deal_type,
        "transport_mode": transport_mode,
        "transport": {
            "primary_mode": transport_mode,
            "secondary_modes": transport.get("secondary_modes", []),
            "transit_detail": transport.get("transit_detail", []),
        },
        "hard_constraints": hard,
        "soft_preferences": {
            "commute_time_priority": soft.get("commute_time_priority", "medium"),
            "walking_distance_priority": soft.get("walking_distance_priority", "low"),
            "transfer_count_priority": soft.get("transfer_count_priority", "low"),
            "budget_priority": soft.get("budget_priority", "medium"),
            "transit_access_priority": soft.get("transit_access_priority", "none"),
        },
        "route_preferences": {
            "avoid_subway": bool(route_preferences.get("avoid_subway", False)),
        },
        "geo_constraints": {
            "excluded_districts": geo_constraints.get("excluded_districts", []) or [],
            "preferred_districts": geo_constraints.get("preferred_districts", []) or [],
            "avoid_remote_area": geo_constraints.get("avoid_remote_area"),
        },
        "tradeoff_policy": {
            "pay_more_for_commute": tradeoff_policy.get("pay_more_for_commute"),
            "accept_longer_walk_for_lower_rent": tradeoff_policy.get("accept_longer_walk_for_lower_rent"),
        },
        "living_preferences": living_preferences,
        "car_time_profile": car_time_profile,
        "unsupported_preferences": unsupported_preferences,
        "priority_focus": raw.get("priority_focus") or "none",
        "reference_preferences": reference_preferences,
        "workplace": raw.get("workplace"),
    }


def coalesce_constraints(parsed: dict, query: dict) -> dict:
    merged = normalize_request_state(parsed)
    hard = merged["hard_constraints"]

    def _merge_lower_limit(existing, candidate):
        existing_value = _safe_int(existing)
        candidate_value = _safe_int(candidate)
        if existing_value is None:
            return candidate_value
        if candidate_value is None:
            return existing_value
        return min(existing_value, candidate_value)

    transport_mode = query.get("transport_mode", [""])[0].strip()
    if transport_mode:
        merged["transport_mode"] = transport_mode
        merged["transport"]["primary_mode"] = transport_mode

    deposit_max = query.get("deposit_max", [""])[0].strip()
    rent_max = query.get("rent_max", [""])[0].strip()
    max_commute = query.get("max_commute_minutes", [""])[0].strip()
    deal_type = query.get("deal_type", [""])[0].strip()
    if deposit_max:
        hard["deposit_max"] = _merge_lower_limit(hard.get("deposit_max"), deposit_max)
    if rent_max:
        hard["rent_max"] = _merge_lower_limit(hard.get("rent_max"), rent_max)
    if max_commute:
        hard["max_commute_minutes"] = _merge_lower_limit(hard.get("max_commute_minutes"), max_commute)
    if deal_type:
        merged["deal_type"] = deal_type
        hard["deal_type"] = deal_type

    transport_json = query.get("transport_json", [""])[0].strip()
    if transport_json:
        try:
            transport = json.loads(transport_json)
            merged["transport"].update(transport)
            if transport.get("primary_mode"):
                merged["transport_mode"] = transport["primary_mode"]
                merged["transport"]["primary_mode"] = transport["primary_mode"]
        except Exception:
            pass

    soft_json = query.get("soft_preferences_json", [""])[0].strip()
    if soft_json:
        try:
            merged["soft_preferences"].update(json.loads(soft_json))
        except Exception:
            pass

    route_json = query.get("route_preferences_json", [""])[0].strip()
    if route_json:
        try:
            merged["route_preferences"].update(json.loads(route_json))
        except Exception:
            pass

    geo_json = query.get("geo_constraints_json", [""])[0].strip()
    if geo_json:
        try:
            merged["geo_constraints"].update(json.loads(geo_json))
        except Exception:
            pass

    tradeoff_json = query.get("tradeoff_policy_json", [""])[0].strip()
    if tradeoff_json:
        try:
            merged["tradeoff_policy"].update(json.loads(tradeoff_json))
        except Exception:
            pass

    living_json = query.get("living_preferences_json", [""])[0].strip()
    if living_json:
        try:
            merged["living_preferences"] = json.loads(living_json)
        except Exception:
            pass

    car_time_profile_json = query.get("car_time_profile_json", [""])[0].strip()
    if car_time_profile_json:
        try:
            merged["car_time_profile"] = _normalize_car_time_profile(json.loads(car_time_profile_json))
        except Exception:
            pass

    selected_car_time = query.get("selected_car_time", [""])[0].strip()
    car_route_direction = query.get("car_route_direction", [""])[0].strip()
    car_route_time_basis = query.get("car_route_time_basis", [""])[0].strip()
    car_trip_type = query.get("car_trip_type", [""])[0].strip()
    car_time_profile_key = query.get("car_time_profile", [""])[0].strip()
    if selected_car_time or car_route_direction or car_route_time_basis or car_trip_type or car_time_profile_key:
        car_time_profile = dict(merged.get("car_time_profile") or {})
        if car_time_profile_key in {"commute_to_work", "commute_from_work"}:
            car_trip_type = car_trip_type or car_time_profile_key
        elif car_time_profile_key == "custom":
            car_time_profile["profile_key"] = "custom"
        elif car_time_profile_key == "weekday_evening_6":
            car_time_profile["profile_key"] = "weekday_evening_6"
            car_trip_type = car_trip_type or "commute_from_work"
            selected_car_time = selected_car_time or "18:00"
        elif car_time_profile_key == "weekday_morning_8":
            car_time_profile["profile_key"] = "weekday_morning_8"
            car_trip_type = car_trip_type or "commute_to_work"
            selected_car_time = selected_car_time or "08:00"
        if selected_car_time:
            car_time_profile["time"] = selected_car_time
            car_time_profile["selected_car_time"] = selected_car_time
        if car_route_direction:
            car_time_profile["route_direction"] = "from_work" if car_route_direction in {"from_work", "work_to_home"} else "to_work"
            car_time_profile["trip_type"] = "commute_from_work" if car_route_direction in {"from_work", "work_to_home"} else "commute_to_work"
        if car_trip_type:
            car_time_profile["trip_type"] = car_trip_type
            car_time_profile["route_direction"] = "from_work" if car_trip_type == "commute_from_work" else "to_work"
        if car_route_time_basis:
            car_time_profile["time_basis"] = car_route_time_basis
        if not selected_car_time and car_time_profile.get("trip_type") in {"commute_to_work", "commute_from_work"}:
            selected_car_time = "18:00" if car_time_profile["trip_type"] == "commute_from_work" else "08:00"
            car_time_profile["time"] = selected_car_time
            car_time_profile["selected_car_time"] = selected_car_time
        car_time_profile["enabled"] = bool(car_time_profile)
        merged["car_time_profile"] = _normalize_car_time_profile(car_time_profile)

    transport_car_time_profile = (merged.get("transport") or {}).get("car_time_profile")
    if transport_car_time_profile:
        merged["car_time_profile"] = _normalize_car_time_profile(transport_car_time_profile)

    unsupported_json = query.get("unsupported_preferences_json", [""])[0].strip()
    if unsupported_json:
        try:
            merged["unsupported_preferences"] = json.loads(unsupported_json)
        except Exception:
            pass

    priority_focus = query.get("priority_focus", [""])[0].strip()
    if priority_focus:
        merged["priority_focus"] = priority_focus

    reference_json = query.get("reference_preferences_json", [""])[0].strip()
    if reference_json:
        try:
            merged["reference_preferences"] = json.loads(reference_json)
        except Exception:
            pass

    return merged


def build_meta_warnings(primary_mode: str) -> list[str]:
    warnings = []
    if not KAKAO_JS_KEY:
        warnings.append("카카오 지도 키가 없어 지도가 표시되지 않을 수 있습니다.")
    if not KAKAO_REST_API_KEY:
        warnings.append("카카오 REST API 키가 없어 직장 검색과 자동차 경로 계산이 제한됩니다.")
    if primary_mode == "transit" and not ODSAY_API_KEY:
        warnings.append("ODSAY 대중교통 API 키가 없어 대중교통 경로 계산이 제한됩니다.")
    return warnings


def bounded_score(value: float | None, good: float, bad: float) -> float:
    if value is None:
        return 0.0
    if value <= good:
        return 100.0
    if value >= bad:
        return 0.0
    ratio = (value - good) / (bad - good)
    return max(0.0, 100.0 - ratio * 100.0)


def larger_is_better_score(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 0.0
    if value >= high:
        return 100.0
    if value <= low:
        return 0.0
    ratio = (value - low) / (high - low)
    return max(0.0, min(100.0, ratio * 100.0))


def is_meaningful_car_time_diff(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    gap = abs(float(a) - float(b))
    base = max(min(float(a), float(b)), 1.0)
    return gap >= 3.0 and (gap / base) >= 0.15


def car_time_band(car_minutes: float | None, max_commute_minutes: float | None) -> str:
    if car_minutes is None:
        return "unknown"
    if max_commute_minutes:
        ratio = float(car_minutes) / float(max_commute_minutes)
        if ratio <= 0.35:
            return "very_relaxed"
        if ratio <= 0.55:
            return "relaxed"
        if ratio <= 0.75:
            return "comfortable"
        if ratio <= 1.0:
            return "within_limit"
        return "over_limit"
    if car_minutes <= 10:
        return "very_relaxed"
    if car_minutes <= 20:
        return "relaxed"
    if car_minutes <= 30:
        return "comfortable"
    return "within_limit"


def car_distance_band(distance_km: float | None) -> str:
    if distance_km is None:
        return "unknown"
    if distance_km <= 2:
        return "very_near"
    if distance_km <= 5:
        return "near"
    if distance_km <= 8:
        return "normal"
    if distance_km <= 12:
        return "far"
    return "very_far"


def budget_pressure_metrics(
    deposit: float | None,
    rent: float | None,
    deposit_limit: float | None,
    rent_limit: float | None,
) -> dict:
    rent_usage_ratio = None
    deposit_usage_ratio = None
    weighted_parts: list[float] = []

    if rent is not None and rent_limit:
        rent_usage_ratio = float(rent) / float(rent_limit)
        weighted_parts.append(rent_usage_ratio * 0.65)
    if deposit is not None and deposit_limit:
        deposit_usage_ratio = float(deposit) / float(deposit_limit)
        weighted_parts.append(deposit_usage_ratio * 0.35)

    if weighted_parts:
        pressure = sum(weighted_parts)
    else:
        pressure = None

    rent_margin_ratio = (1.0 - rent_usage_ratio) if rent_usage_ratio is not None else None
    deposit_margin_ratio = (1.0 - deposit_usage_ratio) if deposit_usage_ratio is not None else None
    return {
        "rent_usage_ratio": rent_usage_ratio,
        "deposit_usage_ratio": deposit_usage_ratio,
        "rent_margin_ratio": rent_margin_ratio,
        "deposit_margin_ratio": deposit_margin_ratio,
        "budget_pressure_score": pressure,
    }


def budget_band(rent_usage_ratio: float | None, deposit_usage_ratio: float | None) -> str:
    weighted_parts: list[float] = []
    if rent_usage_ratio is not None:
        weighted_parts.append(float(rent_usage_ratio) * 0.65)
    if deposit_usage_ratio is not None:
        weighted_parts.append(float(deposit_usage_ratio) * 0.35)
    if not weighted_parts:
        return "unknown"
    pressure = sum(weighted_parts)
    if pressure <= 0.45:
        return "very_light"
    if pressure <= 0.65:
        return "light"
    if pressure <= 0.85:
        return "moderate"
    if pressure <= 1.0:
        return "tight"
    return "over_budget"


def car_commute_margin_score(car_minutes: float | None, max_commute_minutes: float | None) -> float:
    if car_minutes is None or not max_commute_minutes:
        return 0.0
    limit = float(max_commute_minutes)
    minutes = float(car_minutes)
    margin_ratio = (limit - minutes) / limit
    if margin_ratio >= 0:
        return max(55.0, min(100.0, 55.0 + margin_ratio * 45.0))
    overflow_ratio = abs(margin_ratio)
    return max(0.0, 40.0 - overflow_ratio * 70.0)


def car_transport_score(
    car_minutes: float | None,
    distance_km: float | None,
    max_commute_minutes: float | None,
) -> tuple[float, str, str, float | None, float | None]:
    time_band = car_time_band(car_minutes, max_commute_minutes)
    distance_band_name = car_distance_band(distance_km)
    margin_minutes = None if car_minutes is None or not max_commute_minutes else float(max_commute_minutes) - float(car_minutes)
    margin_ratio = None if margin_minutes is None or not max_commute_minutes else margin_minutes / float(max_commute_minutes)
    time_score = CAR_TIME_BAND_SCORE.get(time_band, 0.0)
    distance_score = CAR_DISTANCE_BAND_SCORE.get(distance_band_name, 0.0)
    margin_score = car_commute_margin_score(car_minutes, max_commute_minutes)
    transport = round(time_score * 0.60 + distance_score * 0.25 + margin_score * 0.15, 2)
    return transport, time_band, distance_band_name, margin_minutes, margin_ratio


def target_proximity_score(distance_km: float | None) -> float:
    if distance_km is None:
        return 0.0
    if distance_km <= 0.7:
        return 100.0
    if distance_km <= 1.5:
        return 85.0
    if distance_km <= 3.0:
        return 65.0
    if distance_km <= 5.0:
        return 45.0
    if distance_km <= 8.0:
        return 20.0
    return 0.0


def compute_weight_map(state: dict) -> dict:
    soft = state["soft_preferences"]
    primary_mode = state["transport"].get("primary_mode")
    transit_detail = state["transport"].get("transit_detail", []) or []
    priority_focus = state.get("priority_focus") or "none"
    geo_constraints = state.get("geo_constraints", {}) or {}
    tradeoff = state.get("tradeoff_policy", {}) or {}
    has_secondary_transit = primary_mode == "car" and (
        "transit" in state["transport"].get("secondary_modes", []) or soft.get("transit_access_priority", "none") != "none"
    )
    weights = {
        "commute": BASE_WEIGHT_MAP["commute"] * PRIORITY_MULTIPLIER.get(soft.get("commute_time_priority", "medium"), 1.0),
        "walking": BASE_WEIGHT_MAP["walking"] * PRIORITY_MULTIPLIER.get(soft.get("walking_distance_priority", "low"), 0.75),
        "transfer": BASE_WEIGHT_MAP["transfer"] * PRIORITY_MULTIPLIER.get(soft.get("transfer_count_priority", "low"), 0.75),
        "budget": BASE_WEIGHT_MAP["budget"] * PRIORITY_MULTIPLIER.get(soft.get("budget_priority", "medium"), 1.0),
        "transit_access": BASE_WEIGHT_MAP["transit_access"] * PRIORITY_MULTIPLIER.get(soft.get("transit_access_priority", "none"), 0.45),
        "infra": 0.0,
        "geo_preference": 0.0,
        "target_proximity": 0.0,
        "car_distance": 0.0,
        "area": 0.0,
        "subway": 0.0,
    }
    if tradeoff.get("pay_more_for_commute") is True:
        weights["commute"] *= 1.2
        weights["budget"] *= 0.85
    elif tradeoff.get("pay_more_for_commute") is False:
        weights["commute"] *= 0.9
        weights["budget"] *= 1.1
    if tradeoff.get("accept_longer_walk_for_lower_rent") is True:
        weights["walking"] *= 0.8
        weights["budget"] *= 1.05
    preferred_districts = [str(item).strip() for item in (geo_constraints.get("preferred_districts") or []) if str(item).strip()]
    if preferred_districts:
        weights["geo_preference"] = 8.0
    if geo_constraints.get("avoid_remote_area"):
        weights["geo_preference"] = max(weights["geo_preference"], 6.0)
    if state["route_preferences"].get("avoid_subway"):
        weights["subway"] = 20.0
    elif primary_mode == "transit" and "subway" in transit_detail:
        weights["subway"] = 8.0
    if primary_mode == "car":
        weights["commute"] = 35.0 * PRIORITY_MULTIPLIER.get(soft.get("commute_time_priority", "medium"), 1.0)
        weights["walking"] = 0.0
        weights["transfer"] = 0.0
        weights["budget"] = 22.0 * PRIORITY_MULTIPLIER.get(soft.get("budget_priority", "medium"), 1.0)
        weights["target_proximity"] = 20.0
        weights["car_distance"] = 6.0
        weights["area"] = 10.0
        if not has_secondary_transit:
            weights["transit_access"] = 0.0
            weights["subway"] = 0.0
        else:
            weights["transit_access"] = 5.0 * PRIORITY_MULTIPLIER.get(soft.get("transit_access_priority", "none"), 0.45)
    if state.get("living_preferences"):
        focus_infra_weight = {
            "living": 24.0,
            "balanced": 16.0,
            "transport": 8.0,
            "none": 5.0,
        }
        weights["infra"] = focus_infra_weight.get(priority_focus, 5.0)
    return weights


def build_feature_row(row: dict, primary_route: dict, secondary_transit_route: dict | None, secondary_car_route: dict | None, state: dict) -> dict:
    hard = state["hard_constraints"]
    primary_mode = state["transport"].get("primary_mode")
    deposit_limit = hard.get("deposit_max")
    rent_limit = hard.get("rent_max")
    max_commute_minutes = hard.get("max_commute_minutes")
    deposit_value = row.get("deposit_manwon")
    rent_value = row.get("monthly_rent_manwon")

    commute_time = primary_route.get("duration_min")
    route_distance_km = to_number(primary_route.get("distance_km"), 0)
    direct_distance_km = to_number(row.get("rough_distance_km"), route_distance_km)
    area_sqm = to_number(row.get("area_sqm"), 0)
    walk_m = int(primary_route.get("total_walk_m", 0) or 0)
    first_walk_m = int(primary_route.get("first_walk_m", 0) or 0)
    first_walk_min = int(primary_route.get("first_walk_min", 0) or 0)
    last_walk_m = int(primary_route.get("last_walk_m", 0) or 0)
    last_walk_min = int(primary_route.get("last_walk_min", 0) or 0)
    transfer_count = _compute_transfer_count_from_steps(primary_route.get("steps") or [])
    if not (primary_route.get("steps") or []) and primary_route.get("transfer_count") is not None:
        transfer_count = int(primary_route.get("transfer_count") or 0)
    subway_count = int(primary_route.get("subway_section_count", 0) or 0)

    commute_score = bounded_score(commute_time, good=20, bad=max(80, (hard.get("max_commute_minutes") or 60) + 20))
    walking_score = bounded_score(walk_m, good=100, bad=1500)
    transfer_score = bounded_score(transfer_count, good=0, bad=4)
    subway_score = 50.0
    if state["route_preferences"].get("avoid_subway"):
        subway_score = 100.0 if subway_count == 0 else max(0.0, 70.0 - subway_count * 25.0)
    elif "subway" in (state["transport"].get("transit_detail") or []):
        subway_score = 100.0 if subway_count >= 1 else 40.0

    budget_metrics = budget_pressure_metrics(deposit_value, rent_value, deposit_limit, rent_limit)
    budget_band_name = budget_band(budget_metrics["rent_usage_ratio"], budget_metrics["deposit_usage_ratio"])
    budget_score = BUDGET_BAND_SCORE.get(budget_band_name, 60.0)
    if row.get("has_price_info"):
        if budget_band_name == "unknown":
            budget_score = 100.0
            if deposit_limit is not None and deposit_value is not None:
                budget_score = min(budget_score, bounded_score(deposit_value, good=deposit_limit * 0.5, bad=deposit_limit))
            if rent_limit is not None and rent_value is not None:
                budget_score = min(budget_score, bounded_score(rent_value, good=max(1, rent_limit * 0.5), bad=rent_limit))

    transit_access_score = 50.0
    if secondary_transit_route:
        access_transfer = int(secondary_transit_route.get("bus_transit_count", 0) or 0) + int(secondary_transit_route.get("subway_transit_count", 0) or 0)
        transit_access_score = (
            bounded_score(secondary_transit_route.get("duration_min"), good=20, bad=90) * 0.5
            + bounded_score(secondary_transit_route.get("total_walk_m"), good=100, bad=1500) * 0.3
            + bounded_score(access_transfer, good=0, bad=4) * 0.2
        )

    target_proximity = target_proximity_score(direct_distance_km)
    car_distance_score = bounded_score(route_distance_km, good=2.0, bad=max(12.0, (hard.get("max_commute_minutes") or 30) * 0.5))
    area_score = larger_is_better_score(area_sqm, low=16.0, high=40.0)
    living_eval = evaluate_living_preferences(row["rough_geo"], state.get("living_preferences"))
    geo_constraints = state.get("geo_constraints", {}) or {}
    preferred_districts = {str(item).strip() for item in (geo_constraints.get("preferred_districts") or []) if str(item).strip()}
    avoid_remote_area = bool(geo_constraints.get("avoid_remote_area"))
    if preferred_districts and row.get("district") in preferred_districts:
        geo_preference_score = 100.0
    elif avoid_remote_area and direct_distance_km is not None and direct_distance_km > 5.0:
        geo_preference_score = 35.0
    elif avoid_remote_area and direct_distance_km is not None and direct_distance_km <= 5.0:
        geo_preference_score = 75.0
    else:
        geo_preference_score = 50.0

    car_transport = None
    car_time_band_name = "unknown"
    car_distance_band_name = car_distance_band(route_distance_km)
    commute_margin_minutes = None
    commute_margin_ratio = None
    if primary_mode == "car":
        (
            car_transport,
            car_time_band_name,
            car_distance_band_name,
            commute_margin_minutes,
            commute_margin_ratio,
        ) = car_transport_score(commute_time, route_distance_km, max_commute_minutes)
        commute_score = car_transport
        car_distance_score = CAR_DISTANCE_BAND_SCORE.get(car_distance_band_name, car_distance_score)

    return {
        "route_status": primary_route.get("route_status"),
        "commute_time": commute_time,
        "route_distance_km": route_distance_km,
        "direct_distance_km": direct_distance_km,
        "area_sqm": area_sqm,
        "walk_m": walk_m,
        "first_walk_m": first_walk_m,
        "first_walk_min": first_walk_min,
        "last_walk_m": last_walk_m,
        "last_walk_min": last_walk_min,
        "transfer_count": transfer_count,
        "subway_count": subway_count,
        "commute_score": commute_score,
        "walking_score": walking_score,
        "transfer_score": transfer_score,
        "budget_score": budget_score,
        "subway_score": subway_score,
        "transit_access_score": transit_access_score,
        "infra_score": living_eval["infra_score"],
        "geo_preference_score": geo_preference_score,
        "target_proximity_score": target_proximity,
        "car_distance_score": car_distance_score,
        "car_transport_score": car_transport,
        "car_time_band": car_time_band_name,
        "car_distance_band": car_distance_band_name,
        "commute_margin_minutes": commute_margin_minutes,
        "commute_margin_ratio": commute_margin_ratio,
        "budget_band": budget_band_name,
        "budget_pressure_score": budget_metrics["budget_pressure_score"],
        "rent_usage_ratio": budget_metrics["rent_usage_ratio"],
        "deposit_usage_ratio": budget_metrics["deposit_usage_ratio"],
        "rent_margin_ratio": budget_metrics["rent_margin_ratio"],
        "deposit_margin_ratio": budget_metrics["deposit_margin_ratio"],
        "area_score": area_score,
        "living_matches": living_eval["matches"],
        "living_details": living_eval["details"],
        "living_reference_tags": living_eval.get("reference_tags") or [],
        "secondary_transit_route": secondary_transit_route,
        "secondary_car_route": secondary_car_route,
    }


def compute_final_score(feature_row: dict, weights: dict) -> float:
    return round(
        feature_row["commute_score"] * weights["commute"]
        + feature_row["walking_score"] * weights["walking"]
        + feature_row["transfer_score"] * weights["transfer"]
        + feature_row["budget_score"] * weights["budget"]
        + feature_row["transit_access_score"] * weights["transit_access"]
        + feature_row["infra_score"] * weights["infra"]
        + feature_row["target_proximity_score"] * weights["target_proximity"]
        + feature_row["car_distance_score"] * weights["car_distance"]
        + feature_row["area_score"] * weights["area"]
        + feature_row["subway_score"] * weights["subway"],
        2,
    )


def build_score_breakdown(feature_row: dict, weights: dict) -> dict:
    total = compute_final_score(feature_row, weights)
    factors = []
    for key, meta in FACTOR_META.items():
        weight = float(weights.get(key, 0.0) or 0.0)
        raw_score = float(feature_row.get(meta["score_key"], 0.0) or 0.0)
        contribution = round(raw_score * weight, 2)
        factors.append(
            {
                "key": key,
                "label": meta["label"],
                "raw_score": round(raw_score, 2),
                "weight": round(weight, 2),
                "contribution": contribution,
                "share_pct": round((contribution / total) * 100, 1) if total > 0 else 0.0,
            }
        )
    factors.sort(key=lambda item: (-item["contribution"], -item["raw_score"], item["label"]))
    return {"total": total, "factors": factors}


def _car_time_basis_sentence(state: dict) -> str | None:
    profile = _normalize_car_time_profile((state or {}).get("car_time_profile"))
    if not profile.get("enabled"):
        return None
    if profile.get("trip_type") == "commute_from_work":
        return "자동차 통근시간은 평일 오후 6시 퇴근 기준으로 계산했어요."
    return "자동차 통근시간은 평일 오전 8시 출근 기준으로 계산했어요."


def build_reason_lines(feature_row: dict, state: dict) -> list[str]:
    reasons = []
    primary_mode = state["transport"]["primary_mode"]
    route_status = feature_row.get("route_status")
    living_matches = feature_row.get("living_matches") or []
    living_references = feature_row.get("living_reference_tags") or []
    if route_status in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        reasons.append(f"직장/학교까지 도보 {feature_row['commute_time']}분 정도라 이동이 꽤 편해요.")
        if feature_row.get("walk_m"):
            reasons.append("걸어서 이동 가능한 거리라 매일 이동 부담이 크지 않아요.")
        return reasons
    if primary_mode == "transit":
        reasons.append(f"대중교통 기준으로 총 {feature_row['commute_time']}분 정도 걸려요.")
        detail_bits = []
        if feature_row.get("first_walk_min") is not None:
            detail_bits.append(f"집에서 역/정류장까지 도보 {feature_row['first_walk_min']}분")
        if feature_row.get("walk_m") is not None:
            detail_bits.append(f"총 도보 {feature_row['walk_m']}m")
        if feature_row.get("transfer_count") is not None:
            transfer_count = int(feature_row['transfer_count'] or 0)
            detail_bits.append("환승 없음" if transfer_count <= 0 else f"환승 {transfer_count}회")
        if detail_bits:
            reasons.append(" ".join(detail_bits) + "이라 이동 흐름이 무난해요.")
        secondary_car = feature_row.get("secondary_car_route")
        if secondary_car:
            reasons.append(f"자동차로는 약 {secondary_car['duration_min']}분 정도라 비교용으로도 볼 수 있어요.")
        if state["route_preferences"].get("avoid_subway"):
            reasons.append(f"지하철 이용 구간은 {feature_row['subway_count']}개예요.")
    else:
        time_basis_sentence = _car_time_basis_sentence(state)
        if time_basis_sentence:
            reasons.append(time_basis_sentence)
        reasons.append(f"자동차 기준으로 {feature_row['commute_time']}분 정도예요.")
        secondary = feature_row.get("secondary_transit_route")
        if secondary:
            sec_transfer = int(secondary.get("transfer_count") or (int(secondary.get("bus_transit_count", 0) or 0) + int(secondary.get("subway_transit_count", 0) or 0)))
            if sec_transfer <= 0:
                reasons.append(f"대중교통으로는 약 {secondary['duration_min']}분 정도이고, 환승은 없어요.")
            else:
                reasons.append(f"대중교통으로는 약 {secondary['duration_min']}분 정도이고, 환승은 {sec_transfer}회예요.")
    if living_matches:
        details = []
        for match in living_matches[:2]:
            if match.get("distance_m") is None:
                details.append(f"{match['label']} 조건이 확인됐어요.")
            else:
                walk_minutes = max(1, int(round(float(match["distance_m"]) / 70.0)))
                details.append(f"{match['label']}는 도보 약 {walk_minutes}분({int(match['distance_m'])}m) 거리에 있어요.")
        if details:
            reasons.append(" ".join(details))
    if living_references:
        reasons.append(f"주변에 {living_references[0]['label']}로 분류되는 시설도 함께 확인됐어요.")
    return reasons


def build_explanation(row: dict, feature_row: dict, state: dict) -> str:
    primary_mode = state["transport"]["primary_mode"]
    route_status = feature_row.get("route_status")
    budget_score = float(feature_row.get("budget_score") or 0)
    transfer_count = int(feature_row.get("transfer_count") or 0)
    first_walk_min = feature_row.get("first_walk_min")
    living_matches = feature_row.get("living_matches") or []
    living_references = feature_row.get("living_reference_tags") or []
    direct_distance_km = to_number(feature_row.get("direct_distance_km"), None)
    has_living_merit = bool(living_matches or living_references)

    if route_status in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        if budget_score >= 75:
            base = "직장/학교와 가까운 점에 예산 안정성까지 함께 볼 수 있어 먼저 보기 좋아요."
        else:
            base = "직장/학교와 가까워 이동 부담을 줄이기 좋아요."
        if living_matches:
            first_match = living_matches[0]
            if first_match.get("distance_m") is not None:
                walk_minutes = max(1, int(round(float(first_match["distance_m"]) / 70.0)))
                base += f" {first_match['label']}도 도보 약 {walk_minutes}분({int(first_match['distance_m'])}m) 거리에 있어 생활 편의도 함께 챙기기 좋아요."
        return base
    if primary_mode == "transit":
        if budget_score >= 80 and transfer_count <= 1:
            base = "통근과 예산 조건을 함께 안정적으로 맞추기 좋아 상위권에서 먼저 보기 좋아요."
        elif has_living_merit and budget_score >= 70:
            base = "통근 조건에 생활 편의와 예산 안정성까지 함께 보면 균형이 괜찮아요."
        elif transfer_count >= 3 or (first_walk_min is not None and int(first_walk_min) >= 11):
            base = "기본 조건은 맞지만 이동 동선은 상위 후보와 한 번 더 비교해보면 좋아요."
        elif budget_score >= 75:
            base = "예산 부담을 크게 키우지 않으면서 통근 조건도 무난하게 맞춰요."
        else:
            base = "대중교통 통근 기준으로 기본 조건을 무난하게 맞춰요."
        if living_matches:
            first_match = living_matches[0]
            if first_match.get("distance_m") is not None:
                walk_minutes = max(1, int(round(float(first_match["distance_m"]) / 70.0)))
                base += f" {first_match['label']}는 도보 약 {walk_minutes}분({int(first_match['distance_m'])}m) 거리에 있어 생활 편의도 함께 챙길 수 있어요."
        return base
    time_basis_sentence = _car_time_basis_sentence(state)
    if direct_distance_km is not None and direct_distance_km <= NEAR_DESTINATION_MAX_KM and budget_score >= 75:
        base = "직주 거리와 예산 조건을 함께 안정적으로 맞추기 좋아 먼저 보기 좋아요."
    elif direct_distance_km is not None and direct_distance_km <= NEAR_DESTINATION_MAX_KM:
        base = "직장/학교와 가까운 편이라 자동차 이동 부담을 줄이기 좋아요."
    elif budget_score >= 80:
        base = "예산 부담을 상대적으로 덜고 싶을 때 보기 좋아요."
    elif direct_distance_km is not None and direct_distance_km >= FAR_DISTANCE_THRESHOLD_KM:
        base = "예산이나 면적 조건은 볼 만하지만 거리 메리트는 상위 후보보다 약할 수 있어요."
    elif has_living_merit:
        base = "자동차 이동 조건에 생활 편의까지 함께 보면 균형이 괜찮아요."
    else:
        base = "자동차 이동 기준으로 기본 조건을 무난하게 맞춰요."
    if living_matches:
        first_match = living_matches[0]
        if first_match.get("distance_m") is not None:
            walk_minutes = max(1, int(round(float(first_match["distance_m"]) / 70.0)))
            base += f" {first_match['label']}도 도보 약 {walk_minutes}분({int(first_match['distance_m'])}m) 거리에 있어 생활 편의도 함께 볼 수 있어요."
    if time_basis_sentence:
        base = f"{time_basis_sentence} {base}"
    return base


def _numeric_diff(current_value, other_value, digits: int = 1) -> float | None:
    if current_value is None or other_value is None:
        return None
    return round(float(current_value) - float(other_value), digits)


def _build_pairwise_comparison(current: dict, other: dict | None) -> dict | None:
    if not other:
        return None

    current_factors = {item["key"]: item for item in current["score_breakdown"]["factors"]}
    other_factors = {item["key"]: item for item in other["score_breakdown"]["factors"]}
    factor_deltas = []
    for key, meta in FACTOR_META.items():
        current_factor = current_factors.get(key)
        other_factor = other_factors.get(key)
        if not current_factor or not other_factor:
            continue
        factor_deltas.append(
            {
                "key": key,
                "label": meta["label"],
                "raw_score_delta": round(current_factor["raw_score"] - other_factor["raw_score"], 2),
                "contribution_delta": round(current_factor["contribution"] - other_factor["contribution"], 2),
            }
        )

    factor_deltas.sort(key=lambda item: abs(item["contribution_delta"]), reverse=True)
    return {
        "other_rank": other.get("rank"),
        "other_title": other.get("title"),
        "score_gap": round((current.get("score") or 0.0) - (other.get("score") or 0.0), 2),
        "commute_time_gap_min": _numeric_diff(current.get("duration_min"), other.get("duration_min"), 1),
        "distance_km_gap": _numeric_diff(current.get("distance_km"), other.get("distance_km"), 2),
        "meaningful_car_time_diff": is_meaningful_car_time_diff(current.get("duration_min"), other.get("duration_min")),
        "same_car_time_band": current.get("car_time_band") == other.get("car_time_band"),
        "same_budget_band": current.get("budget_band") == other.get("budget_band"),
        "same_distance_band": current.get("car_distance_band") == other.get("car_distance_band"),
        "car_time_band_changed": current.get("car_time_band") != other.get("car_time_band"),
        "budget_pressure_gap": _numeric_diff(current.get("budget_pressure_score"), other.get("budget_pressure_score"), 3),
        "area_gap_sqm": _numeric_diff(current.get("area_sqm"), other.get("area_sqm"), 1),
        "walk_gap_m": _numeric_diff(current.get("total_walk_m"), other.get("total_walk_m"), 0),
        "transfer_gap": _numeric_diff(
            (current.get("transfer_count") or ((current.get("bus_transit_count") or 0) + (current.get("subway_transit_count") or 0))),
            (other.get("transfer_count") or ((other.get("bus_transit_count") or 0) + (other.get("subway_transit_count") or 0))),
            0,
        ),
        "deposit_gap_manwon": _numeric_diff(current.get("deposit_manwon"), other.get("deposit_manwon"), 0),
        "rent_gap_manwon": _numeric_diff(current.get("monthly_rent_manwon"), other.get("monthly_rent_manwon"), 0),
        "top_factor_deltas": factor_deltas[:3],
    }


def enrich_ranking_context(results: list[dict], state: dict, weights: dict | None = None) -> list[dict]:
    if not results:
        return results

    primary_mode = state["transport"]["primary_mode"]
    weights = weights or {}
    selected_living_categories = sorted([key for key, value in (state.get("living_preferences") or {}).items() if isinstance(value, dict) and value.get("selected")])
    commute_values = [float(item.get("duration_min")) for item in results if item.get("duration_min") is not None]
    rent_values = [float(item.get("monthly_rent_manwon")) for item in results if item.get("monthly_rent_manwon") is not None]
    deposit_values = [float(item.get("deposit_manwon")) for item in results if item.get("deposit_manwon") is not None]
    car_time_band_counts: dict[str, int] = {}
    car_distance_band_counts: dict[str, int] = {}
    budget_band_counts: dict[str, int] = {}
    for item in results:
        time_band = str(item.get("car_time_band") or "unknown")
        distance_band = str(item.get("car_distance_band") or "unknown")
        budget_band_name = str(item.get("budget_band") or "unknown")
        car_time_band_counts[time_band] = car_time_band_counts.get(time_band, 0) + 1
        car_distance_band_counts[distance_band] = car_distance_band_counts.get(distance_band, 0) + 1
        budget_band_counts[budget_band_name] = budget_band_counts.get(budget_band_name, 0) + 1
    ranking_summary = {
        "min_commute_time_min": min(commute_values) if commute_values else None,
        "min_monthly_rent_manwon": min(rent_values) if rent_values else None,
        "min_deposit_manwon": min(deposit_values) if deposit_values else None,
        "top_commute_time_min": results[0].get("duration_min") if results else None,
        "top_monthly_rent_manwon": results[0].get("monthly_rent_manwon") if results else None,
        "top_deposit_manwon": results[0].get("deposit_manwon") if results else None,
        "rank_1_car_time_band": results[0].get("car_time_band") if results else None,
        "rank_1_budget_band": results[0].get("budget_band") if results else None,
        "rank_1_car_distance_band": results[0].get("car_distance_band") if results else None,
        "car_time_band_counts": car_time_band_counts,
        "car_distance_band_counts": car_distance_band_counts,
        "budget_band_counts": budget_band_counts,
    }
    for index, item in enumerate(results):
        item["rank"] = index + 1
        built_year = item.get("built_year")
        building_age = None
        if built_year:
            try:
                building_age = max(0, int(datetime.now().year - int(float(built_year))))
            except Exception:
                building_age = None
        factors = [factor for factor in item["score_breakdown"]["factors"] if (factor.get("weight") or 0) > 0]
        strong_points = [factor["label"] for factor in factors if factor["raw_score"] >= 75][:3]
        strong_point_keys = [factor["key"] for factor in factors if factor["raw_score"] >= 75][:3]
        weak_points = [factor["label"] for factor in sorted(factors, key=lambda factor: factor["raw_score"]) if factor["raw_score"] < 60][:2]
        weak_point_keys = [factor["key"] for factor in sorted(factors, key=lambda factor: factor["raw_score"]) if factor["raw_score"] < 60][:2]
        item["explanation_context"] = {
            "rank": index + 1,
            "primary_mode": primary_mode,
            "secondary_modes": state["transport"].get("secondary_modes", []),
            "car_time_profile": item.get("car_time_profile"),
            "car_route_requested_provider": item.get("car_route_requested_provider"),
            "car_route_provider": item.get("car_route_provider"),
            "car_route_time_basis": item.get("car_route_time_basis"),
            "car_route_time_label": item.get("car_route_time_label"),
            "car_route_time_supported": item.get("car_route_time_supported"),
            "car_route_direction": item.get("car_route_direction"),
            "selected_car_time": item.get("selected_car_time"),
            "car_route_http_status": item.get("car_route_http_status"),
            "car_route_failure_detail": item.get("car_route_failure_detail"),
            "car_route_fallback_used": item.get("car_route_fallback_used"),
            "primary_metrics": {
                "commute_time_min": item.get("duration_min"),
                "route_status": item.get("route_status"),
                "distance_m": item.get("direct_distance_m"),
                "distance_km": item.get("distance_km"),
                "walk_m": item.get("total_walk_m"),
                "walk_time_min": item.get("walk_time_min"),
                "first_walk_m": item.get("first_walk_m"),
                "first_walk_min": item.get("first_walk_min"),
                "last_walk_m": item.get("last_walk_m"),
                "last_walk_min": item.get("last_walk_min"),
                "car_time_band": item.get("car_time_band"),
                "car_distance_band": item.get("car_distance_band"),
                "car_transport_score": item.get("car_transport_score"),
                "commute_margin_minutes": item.get("commute_margin_minutes"),
                "commute_margin_ratio": item.get("commute_margin_ratio"),
                "transfer_count": item.get("transfer_count") or ((item.get("bus_transit_count") or 0) + (item.get("subway_transit_count") or 0)),
                "bus_count": item.get("bus_transit_count"),
                "subway_section_count": item.get("subway_section_count"),
            },
            "secondary_metrics": {
                "car_commute_time_min": (item.get("secondary_car") or {}).get("duration_min"),
                "transit_commute_time_min": (item.get("secondary_transit") or {}).get("duration_min"),
                "transit_walk_m": (item.get("secondary_transit") or {}).get("total_walk_m"),
                "transit_transfer_count": ((item.get("secondary_transit") or {}).get("transfer_count") or ((((item.get("secondary_transit") or {}).get("bus_transit_count") or 0) + ((item.get("secondary_transit") or {}).get("subway_transit_count") or 0)))),
            },
            "budget": {
                "deal_type": item.get("deal_type"),
                "deposit_manwon": item.get("deposit_manwon"),
                "monthly_rent_manwon": item.get("monthly_rent_manwon"),
                "built_year": item.get("built_year"),
                "building_age": building_age,
                "area_pyeong": item.get("area_pyeong"),
                "area_sqm": item.get("area_sqm"),
                "budget_band": item.get("budget_band"),
                "budget_pressure_score": item.get("budget_pressure_score"),
                "rent_usage_ratio": item.get("rent_usage_ratio"),
                "deposit_usage_ratio": item.get("deposit_usage_ratio"),
                "rent_margin_ratio": item.get("rent_margin_ratio"),
                "deposit_margin_ratio": item.get("deposit_margin_ratio"),
            },
            "constraints": {
                "deposit_max": state["hard_constraints"].get("deposit_max"),
                "rent_max": state["hard_constraints"].get("rent_max"),
                "max_commute_minutes": state["hard_constraints"].get("max_commute_minutes"),
            },
            "geo_constraints": state.get("geo_constraints", {}) or {},
            "tradeoff_policy": state.get("tradeoff_policy", {}) or {},
            "requested_living_preferences": state.get("living_preferences", {}) or {},
            "ranking_summary": ranking_summary,
            "selected_living_categories": selected_living_categories,
            "strong_points": strong_points,
            "strong_point_keys": strong_point_keys,
            "weak_points": weak_points,
            "weak_point_keys": weak_point_keys,
        }

    for index, item in enumerate(results):
        prev_item = results[index - 1] if index > 0 else None
        next_item = results[index + 1] if index + 1 < len(results) else None
        item["explanation_context"]["vs_prev"] = _build_pairwise_comparison(item, prev_item)
        item["explanation_context"]["vs_next"] = _build_pairwise_comparison(item, next_item)
        item["explanation_context"]["rank1_reference"] = {
            "rank": results[0].get("rank"),
            "car_time_band": results[0].get("car_time_band"),
            "car_distance_band": results[0].get("car_distance_band"),
            "budget_band": results[0].get("budget_band"),
            "area_sqm": results[0].get("area_sqm"),
            "area_pyeong": results[0].get("area_pyeong"),
            "distance_km": results[0].get("distance_km"),
            "direct_distance_m": results[0].get("direct_distance_m"),
            "duration_min": results[0].get("duration_min"),
            "first_walk_min": results[0].get("first_walk_min"),
            "total_walk_min": results[0].get("walk_time_min"),
            "transfer_count": results[0].get("transfer_count"),
            "subway_count": results[0].get("subway_section_count"),
            "built_year": results[0].get("built_year"),
            "building_age": max(0, int(datetime.now().year - int(float(results[0].get("built_year"))))) if results[0].get("built_year") not in (None, "") else None,
            "deposit_manwon": results[0].get("deposit_manwon"),
            "monthly_rent_manwon": results[0].get("monthly_rent_manwon"),
            "rent_usage_ratio": results[0].get("rent_usage_ratio"),
            "deposit_usage_ratio": results[0].get("deposit_usage_ratio"),
            "commute_margin_ratio": results[0].get("commute_margin_ratio"),
            "deposit_margin_ratio": results[0].get("deposit_margin_ratio"),
            "rent_margin_ratio": results[0].get("rent_margin_ratio"),
        }
        item["explanation_context"]["prev_reference"] = {
            "rank": prev_item.get("rank") if prev_item else None,
            "car_time_band": prev_item.get("car_time_band") if prev_item else None,
            "car_distance_band": prev_item.get("car_distance_band") if prev_item else None,
            "budget_band": prev_item.get("budget_band") if prev_item else None,
            "area_sqm": prev_item.get("area_sqm") if prev_item else None,
            "area_pyeong": prev_item.get("area_pyeong") if prev_item else None,
            "distance_km": prev_item.get("distance_km") if prev_item else None,
            "direct_distance_m": prev_item.get("direct_distance_m") if prev_item else None,
            "duration_min": prev_item.get("duration_min") if prev_item else None,
            "first_walk_min": prev_item.get("first_walk_min") if prev_item else None,
            "total_walk_min": prev_item.get("walk_time_min") if prev_item else None,
            "transfer_count": prev_item.get("transfer_count") if prev_item else None,
            "subway_count": prev_item.get("subway_section_count") if prev_item else None,
            "built_year": prev_item.get("built_year") if prev_item else None,
            "building_age": max(0, int(datetime.now().year - int(float(prev_item.get("built_year"))))) if prev_item and prev_item.get("built_year") not in (None, "") else None,
            "deposit_manwon": prev_item.get("deposit_manwon") if prev_item else None,
            "monthly_rent_manwon": prev_item.get("monthly_rent_manwon") if prev_item else None,
            "rent_usage_ratio": prev_item.get("rent_usage_ratio") if prev_item else None,
            "deposit_usage_ratio": prev_item.get("deposit_usage_ratio") if prev_item else None,
            "commute_margin_ratio": prev_item.get("commute_margin_ratio") if prev_item else None,
            "deposit_margin_ratio": prev_item.get("deposit_margin_ratio") if prev_item else None,
            "rent_margin_ratio": prev_item.get("rent_margin_ratio") if prev_item else None,
        }
        rank1 = item["explanation_context"]["rank1_reference"]
        item["explanation_context"]["vs_rank1"] = {
            "rank1_duration_diff_min": _numeric_diff(item.get("duration_min"), rank1.get("duration_min"), 1),
            "rank1_walk_to_station_diff_min": _numeric_diff(item.get("first_walk_min"), rank1.get("first_walk_min"), 1),
            "rank1_total_walk_diff_min": _numeric_diff(item.get("walk_time_min"), rank1.get("total_walk_min"), 1),
            "rank1_transfer_diff": _numeric_diff(item.get("transfer_count"), rank1.get("transfer_count"), 0),
            "rank1_deposit_diff_manwon": _numeric_diff(item.get("deposit_manwon"), rank1.get("deposit_manwon"), 0),
            "rank1_rent_diff_manwon": _numeric_diff(item.get("monthly_rent_manwon"), rank1.get("monthly_rent_manwon"), 0),
            "rank1_area_diff_pyeong": _numeric_diff(item.get("area_pyeong"), rank1.get("area_pyeong"), 1),
            "rank1_building_age_diff": _numeric_diff(item["explanation_context"]["budget"].get("building_age"), rank1.get("building_age"), 0),
        }
        score_map = {factor["key"]: factor for factor in (item.get("score_breakdown", {}) or {}).get("factors", [])}
        hard_filter = _hard_filter_trace(item, state)
        item["ranking_trace"] = {
            "route_status": _route_status_label(item, primary_mode),
            "route_pool_reasons": _route_pool_reasons(item.get("candidate_tags")),
            "route_pool_rank": item.get("route_pool_rank"),
            "hard_filter": hard_filter,
            "metrics": {
                "duration_min": item.get("duration_min"),
                "distance_m": item.get("direct_distance_m"),
                "walk_to_station_min": item.get("first_walk_min"),
                "total_walk_min": item.get("walk_time_min"),
                "transfer_count": item.get("transfer_count"),
                "subway_count": item.get("subway_section_count"),
                "bus_count": item.get("bus_transit_count"),
                "deposit_manwon": item.get("deposit_manwon"),
                "monthly_rent_manwon": item.get("monthly_rent_manwon") if item.get("deal_type") != "전세" else None,
                "area_pyeong": item.get("area_pyeong"),
                "built_year": item.get("built_year"),
                "building_age": item["explanation_context"]["budget"].get("building_age"),
            },
            "score_breakdown": {
                "commute_score": item.get("commute_score") or score_map.get("commute", {}).get("raw_score"),
                "walking_score": item.get("walking_score") or score_map.get("walking", {}).get("raw_score"),
                "transfer_score": item.get("transfer_score") or score_map.get("transfer", {}).get("raw_score"),
                "subway_score": item.get("subway_score") or score_map.get("subway", {}).get("raw_score"),
                "budget_score": item.get("budget_score") or score_map.get("budget", {}).get("raw_score"),
                "area_score": item.get("area_score") or score_map.get("area", {}).get("raw_score"),
                "infra_score": item.get("infra_score") or score_map.get("infra", {}).get("raw_score"),
                "geo_preference_score": item.get("geo_preference_score") or score_map.get("geo_preference", {}).get("raw_score"),
                "total_score": item.get("score"),
            },
            "weight_breakdown": {
                "commute": weights.get("commute"),
                "walking": weights.get("walking"),
                "transfer": weights.get("transfer"),
                "subway": weights.get("subway"),
                "budget": weights.get("budget"),
                "area": weights.get("area"),
                "infra": weights.get("infra"),
                "geo_preference": weights.get("geo_preference"),
            },
            "sort_reasons": _sort_reasons_for_item(item, primary_mode, index + 1),
            "vs_rank1": {
                "duration_diff_min": item["explanation_context"]["vs_rank1"].get("rank1_duration_diff_min"),
                "walk_to_station_diff_min": item["explanation_context"]["vs_rank1"].get("rank1_walk_to_station_diff_min"),
                "total_walk_diff_min": item["explanation_context"]["vs_rank1"].get("rank1_total_walk_diff_min"),
                "transfer_diff": item["explanation_context"]["vs_rank1"].get("rank1_transfer_diff"),
                "deposit_diff_manwon": item["explanation_context"]["vs_rank1"].get("rank1_deposit_diff_manwon"),
                "rent_diff_manwon": item["explanation_context"]["vs_rank1"].get("rank1_rent_diff_manwon"),
                "area_diff_pyeong": item["explanation_context"]["vs_rank1"].get("rank1_area_diff_pyeong"),
                "building_age_diff": item["explanation_context"]["vs_rank1"].get("rank1_building_age_diff"),
            },
            "vs_prev": {
                "duration_diff_min": _numeric_diff(item.get("duration_min"), (prev_item or {}).get("duration_min"), 1) if prev_item else None,
                "walk_to_station_diff_min": _numeric_diff(item.get("first_walk_min"), (prev_item or {}).get("first_walk_min"), 1) if prev_item else None,
                "total_walk_diff_min": _numeric_diff(item.get("walk_time_min"), (prev_item or {}).get("walk_time_min"), 1) if prev_item else None,
                "transfer_diff": _numeric_diff(item.get("transfer_count"), (prev_item or {}).get("transfer_count"), 0) if prev_item else None,
                "deposit_diff_manwon": _numeric_diff(item.get("deposit_manwon"), (prev_item or {}).get("deposit_manwon"), 0) if prev_item else None,
                "rent_diff_manwon": _numeric_diff(item.get("monthly_rent_manwon"), (prev_item or {}).get("monthly_rent_manwon"), 0) if prev_item else None,
                "area_diff_pyeong": _numeric_diff(item.get("area_pyeong"), (prev_item or {}).get("area_pyeong"), 1) if prev_item else None,
                "building_age_diff": _numeric_diff(item["explanation_context"]["budget"].get("building_age"), (item["explanation_context"]["prev_reference"] or {}).get("building_age"), 0) if prev_item else None,
            },
        }
    return results


def _clone_state(state: dict) -> dict:
    return json.loads(json.dumps(state))


def _prepare_workplace(workplace_address: str, state: dict) -> dict | None:
    workplace = state.get("workplace")
    if workplace and workplace.get("lat") is not None and workplace.get("lng") is not None:
        return {
            "address": workplace_address,
            "lat": float(workplace["lat"]),
            "lng": float(workplace["lng"]),
            "source": workplace.get("source", "selected-workplace"),
        }
    geo = geocode_address(workplace_address)
    if not geo:
        return None
    return {"address": workplace_address, "lat": geo["lat"], "lng": geo["lng"], "source": geo["source"]}


def _apply_hard_filters(listings: pd.DataFrame, state: dict) -> pd.DataFrame:
    hard = state["hard_constraints"]
    geo = state.get("geo_constraints", {}) or {}
    excluded_districts = {str(item).strip() for item in (geo.get("excluded_districts") or []) if str(item).strip()}
    filtered = listings.copy()
    deal_type = hard.get("deal_type")
    if deal_type == "jeonse":
        filtered = filtered[filtered["deal_type"].astype(str) == "전세"].copy()
    elif deal_type == "monthly":
        filtered = filtered[filtered["deal_type"].astype(str) == "월세"].copy()
    if hard.get("deposit_max") is not None or hard.get("rent_max") is not None:
        filtered = filtered[filtered["has_price_info"]].copy()
    if hard.get("deposit_max") is not None:
        filtered = filtered[filtered["deposit_manwon"].fillna(999999) <= hard["deposit_max"]]
    if hard.get("rent_max") is not None:
        filtered = filtered[filtered["monthly_rent_manwon"].fillna(999999) <= hard["rent_max"]]
    if excluded_districts:
        filtered = filtered[~filtered["district"].astype(str).isin(excluded_districts)].copy()
    return filtered


def _select_route_candidates(filtered: pd.DataFrame, primary_mode: str) -> pd.DataFrame:
    if filtered.empty:
        return filtered

    axis_limit = TRANSIT_ROUTE_AXIS_LIMIT if primary_mode == "transit" else CAR_ROUTE_AXIS_LIMIT
    pool_limit = TRANSIT_ROUTE_POOL_LIMIT if primary_mode == "transit" else CAR_ROUTE_POOL_LIMIT

    working = filtered.reset_index(drop=True).copy()
    working["_row_id"] = working.index
    picked_ids: set[int] = set()
    tag_map: dict[int, set[str]] = {}

    def add_rows(row_ids: list[int], tag: str) -> None:
        for row_id in row_ids:
            row_id = int(row_id)
            picked_ids.add(row_id)
            tag_map.setdefault(row_id, set()).add(tag)

    if primary_mode == "car":
        protected_walkable_ids = working[working["rough_distance_km"] <= (WALKABLE_AUTO_MAX_M / 1000)].sort_values(
            ["budget_rank_key", "area_sqm", "rough_distance_km"],
            ascending=[True, False, True],
        )["_row_id"].tolist()
        protected_near_ids = working[working["rough_distance_km"] <= NEAR_DESTINATION_MAX_KM].sort_values(
            ["budget_rank_key", "area_sqm", "rough_distance_km"],
            ascending=[True, False, True],
        ).head(axis_limit)["_row_id"].tolist()
        add_rows(protected_walkable_ids, "protected_walkable")
        add_rows(protected_near_ids, "protected_near")

    distance_ids = working.sort_values(
        ["rough_distance_km", "budget_rank_key", "area_sqm"],
        ascending=[True, True, False],
    ).head(axis_limit)["_row_id"].tolist()
    budget_ids = working.sort_values(
        ["budget_rank_key", "rough_distance_km", "area_sqm"],
        ascending=[True, True, False],
    ).head(axis_limit)["_row_id"].tolist()
    area_ids = working.sort_values(
        ["area_sqm", "rough_distance_km", "budget_rank_key"],
        ascending=[False, True, True],
    ).head(axis_limit)["_row_id"].tolist()
    add_rows(distance_ids, "distance_axis")
    add_rows(budget_ids, "budget_axis")
    add_rows(area_ids, "area_axis")

    selected = working[working["_row_id"].isin(picked_ids)].copy()
    if selected.empty:
        return working.head(pool_limit).drop(columns=["_row_id"], errors="ignore")

    selected["distance_rank"] = selected["rough_distance_km"].rank(method="dense", ascending=True)
    selected["budget_rank"] = selected["budget_rank_key"].rank(method="dense", ascending=True)
    selected["area_rank"] = selected["area_sqm"].rank(method="dense", ascending=False)
    selected["route_pool_rank"] = selected["distance_rank"] + selected["budget_rank"] + selected["area_rank"]
    selected = selected.sort_values(
        ["route_pool_rank", "rough_distance_km", "budget_rank_key", "area_sqm"],
        ascending=[True, True, True, False],
    ).head(pool_limit)
    selected["candidate_tags"] = selected["_row_id"].apply(lambda row_id: ",".join(sorted(tag_map.get(int(row_id), set()))))
    selected["route_pool_rank"] = selected["route_pool_rank"].astype(float)
    return selected.drop(columns=["_row_id", "distance_rank", "budget_rank", "area_rank"], errors="ignore")


def _count_matches(base_listings: pd.DataFrame, workplace: dict, state: dict) -> int:
    filtered = _apply_hard_filters(base_listings, state)
    if filtered.empty:
        return 0
    filtered = filtered.copy()
    filtered["rough_geo"] = filtered.apply(lambda row: listing_geo(row), axis=1)
    filtered = filtered[filtered["rough_geo"].notna()].copy()
    if filtered.empty:
        return 0
    filtered["rough_distance_km"] = filtered["rough_geo"].apply(lambda geo: haversine_km(geo["lat"], geo["lng"], workplace["lat"], workplace["lng"]))
    filtered["budget_rank_key"] = filtered["deposit_manwon"].fillna(999999) + filtered["monthly_rent_manwon"].fillna(999999) * 100
    filtered = _select_route_candidates(filtered, state["transport"]["primary_mode"])
    car_time_profile = state.get("car_time_profile") if state["transport"]["primary_mode"] == "car" else None
    count = 0
    for row in filtered.to_dict(orient="records"):
        route = fetch_route(row["rough_geo"], workplace, state["transport"]["primary_mode"], car_time_profile)
        if route["route_type"] == "unavailable":
            continue
        max_commute = state["hard_constraints"].get("max_commute_minutes")
        if max_commute is not None and route["duration_min"] > max_commute:
            continue
        count += 1
    return count


def build_relaxation_suggestions(base_listings: pd.DataFrame, workplace: dict, state: dict) -> list[dict]:
    hard = state["hard_constraints"]
    suggestions = []
    trials = []
    if hard.get("max_commute_minutes") is not None:
        trials.extend([("max_commute_minutes", hard["max_commute_minutes"] + 10), ("max_commute_minutes", hard["max_commute_minutes"] + 20)])
    if hard.get("deposit_max") is not None:
        trials.extend([("deposit_max", hard["deposit_max"] + 500), ("deposit_max", hard["deposit_max"] + 1000)])
    if hard.get("rent_max") is not None:
        trials.extend([("rent_max", hard["rent_max"] + 10), ("rent_max", hard["rent_max"] + 20)])

    seen = set()
    for key, value in trials:
        relaxed = _clone_state(state)
        relaxed["hard_constraints"][key] = value
        count = _count_matches(base_listings, workplace, relaxed)
        if count <= 0:
            continue
        marker = (key, str(value))
        if marker in seen:
            continue
        seen.add(marker)
        if key == "max_commute_minutes":
            label = f"통근시간을 {hard['max_commute_minutes']}분에서 {value}분으로 늘리면 후보가 {count}개 있습니다."
        elif key == "deposit_max":
            label = f"보증금을 {int(hard['deposit_max'])}만원에서 {int(value)}만원으로 늘리면 후보가 {count}개 있습니다."
        elif key == "rent_max":
            label = f"월세 상한을 {int(hard['rent_max'])}만원에서 {int(value)}만원으로 늘리면 후보가 {count}개 있습니다."
        else:
            label = f"조건을 완화하면 후보가 {count}개 있습니다."
        suggestions.append({"field": key, "value": value, "count": count, "label": label})
        if len(suggestions) >= 2:
            break
    return suggestions


def build_mixed_relaxation_suggestions(
    base_listings: pd.DataFrame,
    workplace: dict,
    state: dict,
    over_limit_count: int,
    route_failure_count: int,
    walkable_count: int,
) -> list[dict]:
    auto_suggestions = build_relaxation_suggestions(base_listings, workplace, state)
    by_field = {item.get("field"): item for item in auto_suggestions if item.get("field")}
    suggestions: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(item: dict | None) -> None:
        if not item or len(suggestions) >= 3:
            return
        marker = (str(item.get("field")), str(item.get("value")))
        if marker in seen:
            return
        suggestions.append(item)
        seen.add(marker)

    if over_limit_count > 0:
        add(
            by_field.get("max_commute_minutes")
            or {
                "field": "max_commute_minutes",
                "value": int((state.get("hard_constraints") or {}).get("max_commute_minutes") or 0) + 10,
                "count": 0,
                "label": f"통근시간을 {int((state.get('hard_constraints') or {}).get('max_commute_minutes') or 0)}분에서 {int((state.get('hard_constraints') or {}).get('max_commute_minutes') or 0) + 10}분으로 늘려 보기",
            }
        )

    if by_field.get("rent_max"):
        add(by_field.get("rent_max"))
    elif by_field.get("deposit_max"):
        add(by_field.get("deposit_max"))
    else:
        hard = state.get("hard_constraints") or {}
        if hard.get("rent_max") is not None:
            add({
                "field": "rent_max",
                "value": int(hard["rent_max"]) + 10,
                "count": 0,
                "label": f"월세 상한을 {int(hard['rent_max'])}만원에서 {int(hard['rent_max']) + 10}만원으로 높여 보기",
            })
        elif hard.get("deposit_max") is not None:
            add({
                "field": "deposit_max",
                "value": int(hard["deposit_max"]) + 500,
                "count": 0,
                "label": f"보증금 상한을 {int(hard['deposit_max'])}만원에서 {int(hard['deposit_max']) + 500}만원으로 높여 보기",
            })

    if walkable_count > 0:
        add({
            "field": "include_walkable_candidates",
            "value": 1,
            "count": walkable_count,
            "label": f"도보권 후보 {walkable_count}개를 함께 보기",
        })

    secondary_modes = ((state.get("transport") or {}).get("secondary_modes")) or []
    primary_mode = ((state.get("transport") or {}).get("primary_mode")) or "transit"
    if route_failure_count > 0 and primary_mode == "transit" and "car" not in secondary_modes:
        add({
            "field": "add_secondary_mode",
            "value": "car",
            "count": 0,
            "label": "자동차 기준도 함께 보기",
        })

    return suggestions[:3]


def _score_factor_map(score_breakdown: dict | None) -> dict[str, dict]:
    factors = ((score_breakdown or {}).get("factors")) or []
    return {str(item.get("key")): item for item in factors if item.get("key")}


def _protected_pool_value(candidate_tags: str | None) -> str:
    tags = [tag.strip() for tag in str(candidate_tags or "").split(",") if tag.strip()]
    protected = [tag for tag in tags if tag.startswith("protected_")]
    return ",".join(protected)


def _route_pool_reasons(candidate_tags: str | None) -> list[str]:
    labels = {
        "distance_axis": "직선거리 축 상위 후보로 route 계산 대상에 포함",
        "budget_axis": "예산 축 상위 후보로 route 계산 대상에 포함",
        "area_axis": "면적 축 상위 후보로 route 계산 대상에 포함",
        "protected_walkable": "직장/학교와 가까운 도보권 보호 후보로 포함",
        "protected_near": "근거리 보호 후보로 포함",
    }
    tags = [tag.strip() for tag in str(candidate_tags or "").split(",") if tag.strip()]
    return [labels[tag] for tag in tags if tag in labels]


def _route_status_label(item: dict, primary_mode: str) -> str:
    route_status = str(item.get("route_status") or "")
    if route_status in {"WALKABLE_NO_TRANSIT", "WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        return route_status
    return "CAR" if primary_mode == "car" else "TRANSIT"


def _sort_reasons_for_item(item: dict, primary_mode: str, rank: int) -> list[str]:
    reasons = []
    route_status = str(item.get("route_status") or "")
    if primary_mode == "transit" and route_status == "WALKABLE_NO_TRANSIT":
        reasons.append("도보권 후보라 일반 대중교통 후보보다 먼저 정렬")
    if primary_mode == "car" and route_status in {"WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"}:
        reasons.append("근거리 보호 후보라 자동차 후보군에서 우선 검토")
    reasons.append(f"최종 점수 {round(float(item.get('score') or 0.0), 2)} 기준으로 상위권 유지")
    if primary_mode == "transit":
        if item.get("duration_min") is not None:
            reasons.append(f"대중교통 통근시간 {int(float(item.get('duration_min')))}분 반영")
        if item.get("transfer_count") is not None:
            reasons.append(f"환승 {int(float(item.get('transfer_count') or 0))}회 반영")
    else:
        if item.get("duration_min") is not None:
            reasons.append(f"자동차 통근시간 {int(float(item.get('duration_min')))}분 반영")
        if item.get("distance_km") is not None:
            reasons.append(f"주행거리 {float(item.get('distance_km')):.2f}km 반영")
    if rank > 1:
        reasons.append(f"{rank-1}위 후보와 비교한 tie-break 결과 반영")
    return reasons


def _hard_filter_trace(item: dict, state: dict) -> dict:
    hard = state.get("hard_constraints", {}) or {}
    deal_type = str(item.get("deal_type") or "")
    requested_deal_type = str(hard.get("deal_type") or "")
    deposit = item.get("deposit_manwon")
    rent = item.get("monthly_rent_manwon")
    deposit_limit = hard.get("deposit_max")
    rent_limit = hard.get("rent_max")
    return {
        "deal_type_passed": (not requested_deal_type) or deal_type == ("전세" if requested_deal_type == "jeonse" else "월세" if requested_deal_type == "monthly" else deal_type),
        "deposit_passed": deposit_limit is None or deposit is None or float(deposit) <= float(deposit_limit),
        "rent_passed": rent_limit is None or deal_type == "전세" or rent is None or float(rent) <= float(rent_limit),
    }


def _transit_sort_priority(item: dict) -> tuple[int, float]:
    route_status = str(item.get("route_status") or "")
    protected_pool = str(item.get("protected_pool") or "")
    direct_distance_km = float(item.get("direct_distance_km") or 999999)
    if route_status == "WALKABLE_NO_TRANSIT":
        return (0, direct_distance_km)
    if "protected_walkable" in protected_pool or "protected_near" in protected_pool:
        return (1, direct_distance_km)
    if direct_distance_km <= NEAR_DESTINATION_MAX_KM:
        return (2, direct_distance_km)
    return (3, direct_distance_km)


def _transit_sort_priority_value(item: dict) -> int:
    return int(_transit_sort_priority(item)[0])


def _listing_key(address: str | None, deposit_manwon, monthly_rent_manwon, area_sqm) -> str:
    return "|".join(
        [
            str(address or ""),
            str(_safe_int(deposit_manwon) if deposit_manwon is not None else ""),
            str(_safe_int(monthly_rent_manwon) if monthly_rent_manwon is not None else ""),
            str(round(float(area_sqm), 1) if area_sqm is not None else ""),
        ]
    )


def _reason_code_for_candidate(
    *,
    primary_mode: str,
    route_status: str | None,
    route_quality: str | None,
    candidate_tags: str | None,
    score_breakdown: dict | None,
    direct_distance_km: float | None,
    route_debug: dict | None,
) -> str:
    route_debug = route_debug or {}
    if route_debug.get("invalid_cache_ignored"):
        return "INVALID_ROUTE_CACHE_IGNORED"
    if route_status == "WALKABLE_NO_TRANSIT":
        return "WALKABLE_NO_TRANSIT"
    if route_status == "WALKABLE_IN_CAR_MODE":
        return "WALKABLE_IN_CAR_MODE"
    if route_status == "NEAR_DESTINATION":
        return "NEAR_DESTINATION"
    if route_quality == "PARTIAL_ROUTE":
        return "PARTIAL_ROUTE_ACCEPTED"

    factor_map = _score_factor_map(score_breakdown)
    strongest = None
    strongest_contribution = -1.0
    for key, factor in factor_map.items():
        contribution = float(factor.get("contribution") or 0.0)
        if contribution > strongest_contribution:
            strongest = key
            strongest_contribution = contribution

    if primary_mode == "car" and direct_distance_km is not None and direct_distance_km > FAR_DISTANCE_THRESHOLD_KM:
        if strongest == "budget":
            return "FAR_BUT_STRONG_BUDGET"
        if strongest == "area":
            return "FAR_BUT_STRONG_AREA"
    if strongest == "budget":
        return "BUDGET_STRONG"
    if strongest == "commute":
        return "CAR_TIME_STRONG" if primary_mode == "car" else "COMMUTE_STRONG"
    if strongest == "target_proximity":
        return "NEAR_DESTINATION_PROTECTED"
    if strongest == "area":
        return "AREA_STRONG"
    if strongest == "car_distance":
        return "CAR_DISTANCE_STRONG"
    if "protected_walkable" in str(candidate_tags or "") or "protected_near" in str(candidate_tags or ""):
        return "NEAR_DESTINATION_PROTECTED"
    return "GENERAL_INCLUDED"


def _build_debug_snapshot(
    *,
    address: str,
    status: str,
    rank: int | None = None,
    row: dict | None = None,
    route: dict | None = None,
    feature_row: dict | None = None,
    final_score: float | None = None,
    candidate_tags: str | None = None,
    reason: str | None = None,
    reason_code: str | None = None,
    failure_detail: str | None = None,
) -> dict:
    row = row or {}
    route = route or {}
    feature_row = feature_row or {}
    route_debug = route.get("_debug") or {}
    direct_distance_km = to_number(feature_row.get("direct_distance_km"), row.get("rough_distance_km"))
    direct_distance_m = None if direct_distance_km is None else int(round(float(direct_distance_km) * 1000))
    protected_pool = _protected_pool_value(candidate_tags)
    listing_key = _listing_key(address, row.get("deposit_manwon"), row.get("monthly_rent_manwon"), row.get("area_sqm"))
    snapshot = {
        "listing_key": listing_key,
        "rank": rank,
        "address": address,
        "status": status,
        "reason": reason,
        "reason_code": reason_code,
        "failure_detail": failure_detail or route.get("failure_detail"),
        "route_status": route.get("route_status"),
        "route_quality": route_debug.get("route_quality"),
        "direct_distance_m": direct_distance_m,
        "car_duration_min": route.get("duration_min") if route.get("mode") == "car" else (feature_row.get("secondary_car_route") or {}).get("duration_min"),
        "car_distance_km": route.get("distance_km") if route.get("mode") == "car" else (feature_row.get("secondary_car_route") or {}).get("distance_km"),
        "transit_duration_min": route.get("duration_min") if route.get("mode") == "transit" else (feature_row.get("secondary_transit_route") or {}).get("duration_min"),
        "walk_time_min": feature_row.get("walk_time_min") or route.get("walk_time_min") or route.get("duration_min"),
        "budget_score": feature_row.get("budget_score"),
        "area_score": feature_row.get("area_score"),
        "target_proximity_score": feature_row.get("target_proximity_score"),
        "final_score": final_score,
        "protected_pool": protected_pool,
        "sort_priority": _transit_sort_priority_value({
            "route_status": route.get("route_status"),
            "protected_pool": protected_pool,
            "direct_distance_km": direct_distance_km,
        }) if route.get("mode") == "transit" else None,
        "candidate_tags": candidate_tags or "",
        "route_source": route_debug.get("route_source"),
        "route_provider": route.get("route_provider"),
        "car_time_profile": route.get("car_time_profile"),
        "car_route_requested_provider": route.get("car_route_requested_provider"),
        "car_route_provider": route.get("car_route_provider"),
        "car_route_time_basis": route.get("car_route_time_basis"),
        "car_route_time_label": route.get("car_route_time_label"),
        "car_route_time_supported": route.get("car_route_time_supported"),
        "car_route_direction": route.get("car_route_direction"),
        "selected_car_time": route.get("selected_car_time"),
        "car_route_prediction_type": route.get("car_route_prediction_type"),
        "car_route_prediction_time": route.get("car_route_prediction_time"),
        "car_route_departure_lat": route.get("car_route_departure_lat"),
        "car_route_departure_lng": route.get("car_route_departure_lng"),
        "car_route_destination_lat": route.get("car_route_destination_lat"),
        "car_route_destination_lng": route.get("car_route_destination_lng"),
        "car_route_http_status": route.get("car_route_http_status"),
        "car_route_response_received": route.get("car_route_response_received"),
        "car_route_error_code": route.get("car_route_error_code"),
        "car_route_error_message": route.get("car_route_error_message"),
        "car_route_failure_detail": route.get("car_route_failure_detail"),
        "car_route_fallback_used": route.get("car_route_fallback_used"),
        "odsay_called": route_debug.get("odsay_called"),
        "odsay_http_status": route_debug.get("odsay_http_status"),
        "odsay_error_code": route_debug.get("odsay_error_code"),
        "cache_used": route_debug.get("cache_used"),
        "cache_valid": route_debug.get("cache_valid"),
    }
    return snapshot


def summarize_debug_failures(debug_items: list[dict]) -> dict:
    route_failure_count = 0
    over_limit_count = 0
    included_count = 0
    walkable_count = 0
    failure_reasons: dict[str, int] = {}

    for item in debug_items:
        status = item.get("status")
        reason = item.get("reason") or ""
        if status == "included":
            included_count += 1
            if item.get("route_status") == "WALKABLE_NO_TRANSIT":
                walkable_count += 1
            continue
        if "실제 경로 계산 실패" in reason:
            route_failure_count += 1
            detail = item.get("failure_detail") or item.get("route_summary") or "실제 경로 계산 실패"
            failure_reasons[detail] = failure_reasons.get(detail, 0) + 1
        elif "기준 초과" in reason:
            over_limit_count += 1

    return {
        "route_failure_count": route_failure_count,
        "over_limit_count": over_limit_count,
        "included_count": included_count,
        "walkable_count": walkable_count,
        "excluded_count": len(debug_items) - included_count,
        "failure_reasons": failure_reasons,
    }


def far_candidate_allowed(feature_row: dict, row: dict, near_reference: dict | None, state: dict) -> bool:
    if state["transport"].get("primary_mode") != "car":
        return True
    direct_distance_km = to_number(feature_row.get("direct_distance_km"), 0)
    if direct_distance_km <= FAR_DISTANCE_THRESHOLD_KM:
        return True
    if near_reference is None:
        return True

    far_commute = to_number(feature_row.get("commute_time"), None)
    near_commute = to_number(near_reference.get("commute_time"), None)
    if far_commute is not None and near_commute is not None and near_commute - far_commute >= 10:
        return True

    far_rent = to_number(row.get("monthly_rent_manwon"), None)
    near_rent = to_number(near_reference.get("monthly_rent_manwon"), None)
    if far_rent is not None and near_rent is not None and near_rent - far_rent >= 15:
        return True

    far_deposit = to_number(row.get("deposit_manwon"), None)
    near_deposit = to_number(near_reference.get("deposit_manwon"), None)
    if far_deposit is not None and near_deposit is not None and near_deposit - far_deposit >= 700:
        return True

    far_area = to_number(row.get("area_sqm"), None)
    near_area = to_number(near_reference.get("area_sqm"), None)
    if far_area is not None and near_area is not None and (far_area - near_area) >= (2 * 3.3058):
        return True

    return False


def recommend(workplace_address: str, request_state: dict, selected_districts: list[str] | None = None) -> dict:
    state = normalize_request_state(request_state)
    primary_mode = state["transport"]["primary_mode"]

    workplace = _prepare_workplace(workplace_address, state)
    if not workplace:
        return {
            "workplace": None,
            "recommendations": [],
            "meta": {"message": "직장 위치를 찾지 못했습니다.", "warnings": build_meta_warnings(primary_mode)},
            "debug": [],
        }

    listings = read_listing_frames()
    if listings.empty:
        return {
            "workplace": workplace,
            "recommendations": [],
            "meta": {"message": "매물 데이터가 없습니다.", "warnings": build_meta_warnings(primary_mode)},
            "debug": [],
        }

    filtered = _apply_hard_filters(listings, state)
    if filtered.empty:
        suggestions = build_relaxation_suggestions(listings, workplace, state)
        return {
            "workplace": workplace,
            "recommendations": [],
            "meta": {
                "message": "현재 조건에 맞는 매물이 없습니다.",
                "relaxation_suggestions": suggestions,
                "warnings": build_meta_warnings(primary_mode),
                "total_candidates": 0,
                "transport_mode": primary_mode,
            },
            "debug": [],
        }

    filtered = filtered.copy()
    filtered["rough_geo"] = filtered.apply(lambda row: listing_geo(row), axis=1)
    filtered = filtered[filtered["rough_geo"].notna()].copy()
    if filtered.empty:
        return {
            "workplace": workplace,
            "recommendations": [],
            "meta": {"message": "매물 좌표 정보가 부족해 추천을 만들지 못했습니다.", "warnings": build_meta_warnings(primary_mode)},
            "debug": [],
        }

    filtered["rough_distance_km"] = filtered["rough_geo"].apply(lambda geo: haversine_km(geo["lat"], geo["lng"], workplace["lat"], workplace["lng"]))
    filtered["budget_rank_key"] = filtered["deposit_manwon"].fillna(999999) + filtered["monthly_rent_manwon"].fillna(999999) * 100
    base_for_relaxation = filtered.copy()
    filtered = _select_route_candidates(filtered, primary_mode)
    if IS_VERCEL_DEPLOYMENT:
        filtered = filtered.head(TRANSIT_ROUTE_POOL_LIMIT if primary_mode == "transit" else CAR_ROUTE_POOL_LIMIT).copy()
    car_time_profile = state.get("car_time_profile") if primary_mode == "car" else None

    weights = compute_weight_map(state)
    need_secondary_transit = (not IS_VERCEL_DEPLOYMENT) and primary_mode == "car" and (
        "transit" in state["transport"].get("secondary_modes", []) or state["soft_preferences"].get("transit_access_priority") != "none"
    )
    need_secondary_car = (not IS_VERCEL_DEPLOYMENT) and primary_mode == "transit" and ("car" in state["transport"].get("secondary_modes", []))

    def finalize_response(payload: dict) -> dict:
        flush_route_cache()
        return payload

    rows = filtered.to_dict(orient="records")
    results = []
    debug_items = []

    def evaluate_candidate(index: int, row: dict) -> tuple[dict | None, dict]:
        primary_route = fetch_route(row["rough_geo"], workplace, primary_mode, car_time_profile)
        if primary_route["route_type"] == "unavailable":
            if primary_mode == "car":
                straight_distance_km = haversine_km(row["rough_geo"]["lat"], row["rough_geo"]["lng"], workplace["lat"], workplace["lng"])
                if straight_distance_km <= (WALKABLE_AUTO_MAX_M / 1000):
                    primary_route = _walking_only_car_route(row["rough_geo"], workplace, "WALKABLE_IN_CAR_MODE")
                elif straight_distance_km <= NEAR_DESTINATION_MAX_KM:
                    primary_route = _walking_only_car_route(row["rough_geo"], workplace, "NEAR_DESTINATION")
                else:
                    return None, _build_debug_snapshot(
                        address=row["address"],
                        status="excluded",
                        row=row,
                        route=primary_route,
                        candidate_tags=row.get("candidate_tags", ""),
                        reason="실제 경로 계산 실패",
                        reason_code="ROUTE_UNAVAILABLE",
                        failure_detail=primary_route.get("route_summary"),
                    )
            else:
                return None, _build_debug_snapshot(
                    address=row["address"],
                    status="excluded",
                    row=row,
                    route=primary_route,
                    candidate_tags=row.get("candidate_tags", ""),
                    reason="실제 경로 계산 실패",
                    reason_code="ROUTE_UNAVAILABLE",
                    failure_detail=primary_route.get("route_summary"),
                )

        max_commute = state["hard_constraints"].get("max_commute_minutes")
        if (
            max_commute is not None
            and primary_route["duration_min"] > max_commute
            and not (primary_mode == "car" and primary_route.get("route_status") in {"WALKABLE_IN_CAR_MODE", "NEAR_DESTINATION"})
        ):
            return None, _build_debug_snapshot(
                address=row["address"],
                status="excluded",
                row=row,
                route=primary_route,
                candidate_tags=row.get("candidate_tags", ""),
                reason=f"통근시간 {primary_route['duration_min']}분으로 기준 초과",
                reason_code="COMMUTE_LIMIT_EXCEEDED",
            )

        secondary_transit_route = None
        if need_secondary_transit and index < SECONDARY_TRANSIT_SAMPLE_LIMIT:
            secondary_transit_route = fetch_route(row["rough_geo"], workplace, "transit")
            if secondary_transit_route["route_type"] == "unavailable":
                secondary_transit_route = None

        secondary_car_route = None
        if need_secondary_car:
            secondary_car_route = fetch_route(row["rough_geo"], workplace, "car")
            if secondary_car_route["route_type"] == "unavailable":
                secondary_car_route = None

        feature_row = build_feature_row(row, primary_route, secondary_transit_route, secondary_car_route, state)
        final_score = compute_final_score(feature_row, weights)
        score_breakdown = build_score_breakdown(feature_row, weights)
        reason_code = _reason_code_for_candidate(
            primary_mode=primary_mode,
            route_status=primary_route.get("route_status"),
            route_quality=(primary_route.get("_debug") or {}).get("route_quality"),
            candidate_tags=row.get("candidate_tags", ""),
            score_breakdown=score_breakdown,
            direct_distance_km=feature_row.get("direct_distance_km"),
            route_debug=primary_route.get("_debug"),
        )
        return (
            {
                "listing_key": _listing_key(row["address"], row.get("deposit_manwon"), row.get("monthly_rent_manwon"), row.get("area_sqm")),
                "title": row["display_name"],
                "district": row["district"],
                "address": row["address"],
                "candidate_tags": row.get("candidate_tags", ""),
                "route_pool_rank": row.get("route_pool_rank"),
                "house_type": row["house_type"],
                "deal_type": row["deal_type"] or ("전세" if (row.get("monthly_rent_manwon") or 0) == 0 else "월세"),
                "deposit_manwon": int(row["deposit_manwon"]) if row.get("deposit_manwon") is not None else None,
                "monthly_rent_manwon": int(row["monthly_rent_manwon"]) if row.get("monthly_rent_manwon") is not None else None,
                "built_year": int(row["built_year"]) if row.get("built_year") is not None else None,
                "area_sqm": round(to_number(row["area_sqm"], 0), 1),
                "area_pyeong": sqm_to_display_pyeong(row.get("area_sqm")),
                "floor": row["floor"],
                "contract_date": row["contract_date"],
                "lat": row["rough_geo"]["lat"],
                "lng": row["rough_geo"]["lng"],
                "direct_distance_km": feature_row["direct_distance_km"],
                "distance_km": primary_route["distance_km"],
                "duration_min": primary_route["duration_min"],
                "route_type": primary_route["route_type"],
                "route_status": primary_route.get("route_status"),
                "route_provider": primary_route.get("route_provider"),
                "car_time_profile": primary_route.get("car_time_profile"),
                "car_route_requested_provider": primary_route.get("car_route_requested_provider"),
                "car_route_provider": primary_route.get("car_route_provider"),
                "car_route_time_basis": primary_route.get("car_route_time_basis"),
                "car_route_time_label": primary_route.get("car_route_time_label"),
                "car_route_time_supported": primary_route.get("car_route_time_supported"),
                "car_route_direction": primary_route.get("car_route_direction"),
                "selected_car_time": primary_route.get("selected_car_time"),
                "car_route_prediction_type": primary_route.get("car_route_prediction_type"),
                "car_route_prediction_time": primary_route.get("car_route_prediction_time"),
                "car_route_departure_lat": primary_route.get("car_route_departure_lat"),
                "car_route_departure_lng": primary_route.get("car_route_departure_lng"),
                "car_route_destination_lat": primary_route.get("car_route_destination_lat"),
                "car_route_destination_lng": primary_route.get("car_route_destination_lng"),
                "car_route_http_status": primary_route.get("car_route_http_status"),
                "car_route_response_received": primary_route.get("car_route_response_received"),
                "car_route_error_code": primary_route.get("car_route_error_code"),
                "car_route_error_message": primary_route.get("car_route_error_message"),
                "car_route_failure_detail": primary_route.get("car_route_failure_detail"),
                "car_route_fallback_used": primary_route.get("car_route_fallback_used"),
                "route_summary": primary_route["route_summary"],
                "path": primary_route["path"],
                "path_segments": primary_route["path_segments"],
                "display_path_segments": primary_route.get("display_path_segments") or primary_route["path_segments"],
                "payment": primary_route["payment"],
                "bus_transit_count": primary_route["bus_transit_count"],
                "subway_transit_count": primary_route["subway_transit_count"],
                "subway_section_count": primary_route["subway_section_count"],
                "transfer_count": primary_route.get("transfer_count"),
                "total_walk_m": primary_route["total_walk_m"],
                "first_walk_m": primary_route.get("first_walk_m"),
                "first_walk_min": primary_route.get("first_walk_min"),
                "last_walk_m": primary_route.get("last_walk_m"),
                "last_walk_min": primary_route.get("last_walk_min"),
                "walk_distance_m": primary_route.get("walk_distance_m"),
                "walk_time_min": primary_route.get("walk_time_min"),
                "car_time_band": feature_row.get("car_time_band"),
                "car_distance_band": feature_row.get("car_distance_band"),
                "car_transport_score": feature_row.get("car_transport_score"),
                "commute_margin_minutes": feature_row.get("commute_margin_minutes"),
                "commute_margin_ratio": feature_row.get("commute_margin_ratio"),
                "budget_band": feature_row.get("budget_band"),
                "budget_pressure_score": feature_row.get("budget_pressure_score"),
                "rent_usage_ratio": feature_row.get("rent_usage_ratio"),
                "deposit_usage_ratio": feature_row.get("deposit_usage_ratio"),
                "rent_margin_ratio": feature_row.get("rent_margin_ratio"),
                "deposit_margin_ratio": feature_row.get("deposit_margin_ratio"),
                "infra_score": feature_row.get("infra_score"),
                "living_matches": feature_row.get("living_matches"),
                "living_details": feature_row.get("living_details"),
                "living_reference_tags": feature_row.get("living_reference_tags"),
                "selected_living_categories": sorted([key for key, value in (state.get("living_preferences") or {}).items() if isinstance(value, dict) and value.get("selected")]),
                "steps": primary_route["steps"],
                "display_steps": primary_route.get("display_steps") or primary_route["steps"],
                "display_duration_min": primary_route.get("display_duration_min", primary_route.get("duration_min")),
                "display_transfer_count": primary_route.get("display_transfer_count", primary_route.get("transfer_count")),
                "display_total_walk_m": primary_route.get("display_total_walk_m", primary_route.get("total_walk_m")),
                "display_walk_time_min": primary_route.get("display_walk_time_min", primary_route.get("walk_time_min")),
                "route_geometry_provider": primary_route.get("route_geometry_provider"),
                "route_display_version": primary_route.get("route_display_version"),
                "secondary_transit": secondary_transit_route,
                "secondary_car": secondary_car_route,
                "score": final_score,
                "score_breakdown": score_breakdown,
                "route_source": (primary_route.get("_debug") or {}).get("route_source"),
                "odsay_called": (primary_route.get("_debug") or {}).get("odsay_called"),
                "odsay_http_status": (primary_route.get("_debug") or {}).get("odsay_http_status"),
                "odsay_error_code": (primary_route.get("_debug") or {}).get("odsay_error_code"),
                "failure_detail": primary_route.get("failure_detail"),
                "car_time_profile": primary_route.get("car_time_profile"),
                "car_route_requested_provider": primary_route.get("car_route_requested_provider"),
                "car_route_provider": primary_route.get("car_route_provider"),
                "car_route_time_basis": primary_route.get("car_route_time_basis"),
                "car_route_time_label": primary_route.get("car_route_time_label"),
                "car_route_time_supported": primary_route.get("car_route_time_supported"),
                "car_route_direction": primary_route.get("car_route_direction"),
                "selected_car_time": primary_route.get("selected_car_time"),
                "car_route_prediction_type": primary_route.get("car_route_prediction_type"),
                "car_route_prediction_time": primary_route.get("car_route_prediction_time"),
                "car_route_departure_lat": primary_route.get("car_route_departure_lat"),
                "car_route_departure_lng": primary_route.get("car_route_departure_lng"),
                "car_route_destination_lat": primary_route.get("car_route_destination_lat"),
                "car_route_destination_lng": primary_route.get("car_route_destination_lng"),
                "car_route_http_status": primary_route.get("car_route_http_status"),
                "car_route_response_received": primary_route.get("car_route_response_received"),
                "car_route_error_code": primary_route.get("car_route_error_code"),
                "car_route_error_message": primary_route.get("car_route_error_message"),
                "car_route_failure_detail": primary_route.get("car_route_failure_detail"),
                "car_route_fallback_used": primary_route.get("car_route_fallback_used"),
                "route_quality": (primary_route.get("_debug") or {}).get("route_quality"),
                "cache_used": (primary_route.get("_debug") or {}).get("cache_used"),
                "cache_valid": (primary_route.get("_debug") or {}).get("cache_valid"),
                "protected_pool": _protected_pool_value(row.get("candidate_tags", "")),
                "sort_priority": _transit_sort_priority_value({
                    "route_status": primary_route.get("route_status"),
                    "protected_pool": _protected_pool_value(row.get("candidate_tags", "")),
                    "direct_distance_km": feature_row.get("direct_distance_km"),
                }) if primary_mode == "transit" else None,
                "reason_code": reason_code,
                "reason": build_explanation(row, feature_row, state),
                "score_reasons": build_reason_lines(feature_row, state),
            },
            _build_debug_snapshot(
                address=row["address"],
                status="included",
                row=row,
                route=primary_route,
                feature_row=feature_row,
                final_score=final_score,
                candidate_tags=row.get("candidate_tags", ""),
                reason_code=reason_code,
            ),
        )

    max_workers = CAR_ROUTE_WORKERS if primary_mode == "car" else TRANSIT_ROUTE_WORKERS
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(evaluate_candidate, index, row): (index, row["address"])
            for index, row in enumerate(rows)
        }
        for future in as_completed(future_map):
            _, address = future_map[future]
            try:
                item, debug_item = future.result()
            except Exception:
                item, debug_item = None, {
                    "rank": None,
                    "address": address,
                    "status": "excluded",
                    "reason": "실제 경로 계산 실패",
                    "reason_code": "CANDIDATE_EVALUATION_EXCEPTION",
                    "route_status": f"{primary_mode.upper()}_ROUTE_FAILED",
                    "route_quality": "UNAVAILABLE",
                    "failure_detail": "후보 평가 중 예외 발생",
                    "route_source": f"{primary_mode.upper()}_FAILED",
                    "odsay_called": primary_mode == "transit",
                    "odsay_http_status": None,
                    "odsay_error_code": None,
                    "cache_used": False,
                    "cache_valid": False,
                }
            debug_items.append(debug_item)
            if item is not None:
                results.append(item)

    if primary_mode == "car" and results:
        near_candidates = [item for item in results if to_number(item.get("direct_distance_km"), 999) <= 1.0]
        near_reference = None
        if near_candidates:
            near_reference = max(
                near_candidates,
                key=lambda item: (
                    float(item.get("score") or 0),
                    -float(item.get("direct_distance_km") or 999),
                    -float(item.get("duration_min") or 999),
                ),
            )
        gated_results = []
        for item in results:
            if far_candidate_allowed(item, item, near_reference, state):
                gated_results.append(item)
                continue
            debug_items.append(
                {
                    "rank": item.get("rank"),
                    "address": item.get("address"),
                    "status": "excluded",
                    "reason": "근거리 후보 대비 우세 근거 부족",
                    "reason_code": "FAR_CANDIDATE_REJECTED",
                    "route_status": item.get("route_status"),
                    "route_quality": item.get("route_quality"),
                    "route_source": item.get("route_source"),
                    "candidate_tags": item.get("candidate_tags", ""),
                    "protected_pool": item.get("protected_pool"),
                    "direct_distance_m": int(round(float(item.get("direct_distance_km") or 0) * 1000)),
                    "car_distance_km": item.get("distance_km"),
                    "car_duration_min": item.get("duration_min"),
                    "budget_score": _score_factor_map(item.get("score_breakdown")).get("budget", {}).get("raw_score"),
                    "area_score": _score_factor_map(item.get("score_breakdown")).get("area", {}).get("raw_score"),
                    "target_proximity_score": _score_factor_map(item.get("score_breakdown")).get("target_proximity", {}).get("raw_score"),
                    "final_score": item.get("score"),
                    "odsay_called": item.get("odsay_called"),
                    "odsay_http_status": item.get("odsay_http_status"),
                    "odsay_error_code": item.get("odsay_error_code"),
                    "cache_used": item.get("cache_used"),
                    "cache_valid": item.get("cache_valid"),
                }
            )
        results = gated_results

    if primary_mode == "car":
        results.sort(
            key=lambda item: (
                -item["score"],
                item.get("direct_distance_km") or 999999,
                item["duration_min"],
                item.get("monthly_rent_manwon") or 999999,
                -(item.get("area_sqm") or 0),
            )
        )
    else:
        results.sort(
            key=lambda item: (
                0 if str(item.get("route_status") or "") == "WALKABLE_NO_TRANSIT" else 1,
                -item["score"],
                _transit_sort_priority_value(item),
                item["duration_min"],
                item.get("direct_distance_km") or 999999,
                item.get("deposit_manwon") or 999999,
                item.get("monthly_rent_manwon") or 999999,
            )
        )
    results = enrich_ranking_context(results, state, weights)
    rank_by_key = {item.get("listing_key"): item.get("rank") for item in results if item.get("listing_key")}
    for debug_item in debug_items:
        listing_key = debug_item.get("listing_key")
        if listing_key and listing_key in rank_by_key:
            debug_item["rank"] = rank_by_key[listing_key]

    if not results:
        failure_meta = summarize_debug_failures(debug_items)
        route_failure_count = failure_meta["route_failure_count"]
        over_limit_count = failure_meta["over_limit_count"]
        walkable_count = failure_meta["walkable_count"]
        total_debug_count = len(debug_items)
        route_label = "자동차" if primary_mode == "car" else "대중교통"
        actual_car_route_provider = next((item.get("car_route_provider") for item in debug_items if item.get("car_route_provider")), None)
        if not actual_car_route_provider and primary_mode == "car":
            actual_car_route_provider = _car_route_requested_provider_label(state.get("car_time_profile"))

        suggestions = []
        relaxation_mode = "none"
        message = "현재 조건에 맞는 실제 경로 후보를 찾지 못했습니다."
        if total_debug_count and route_failure_count == total_debug_count:
            relaxation_mode = "route_failure"
            message = f"조건에 맞는 후보는 있었지만 {route_label} 경로를 불러오지 못했습니다. 잠시 뒤 다시 시도해 주세요."
        elif total_debug_count and over_limit_count == total_debug_count:
            relaxation_mode = "commute_limit"
            message = "예산 조건에 맞는 후보는 있었지만 통근시간 기준을 넘겼습니다."
            suggestions = build_relaxation_suggestions(base_for_relaxation, workplace, state)
        else:
            relaxation_mode = "mixed"
            if walkable_count > 0 and over_limit_count > 0 and route_failure_count == 0:
                message = "도보로 볼 수 있는 가까운 후보가 있지만, 나머지는 통근시간 기준을 넘겼습니다."
                suggestions = build_relaxation_suggestions(base_for_relaxation, workplace, state)
            elif walkable_count > 0 and route_failure_count > 0:
                message = f"일부 후보는 직장/학교와 가까워 도보 이동으로 보는 것이 더 자연스럽고, 일부 후보는 {route_label} 경로를 불러오지 못했습니다."
            elif over_limit_count > 0 and route_failure_count == 0:
                suggestions = build_relaxation_suggestions(base_for_relaxation, workplace, state)
            elif route_failure_count > 0 and over_limit_count > 0:
                message = "현재 조건에서는 일부 후보는 경로를 확인하기 어렵고, 일부 후보는 설정한 통근시간을 넘었습니다. 통근시간이나 예산 조건을 조금 완화하면 더 많은 후보를 볼 수 있습니다."
                suggestions = build_mixed_relaxation_suggestions(
                    base_for_relaxation,
                    workplace,
                    state,
                    over_limit_count,
                    route_failure_count,
                    walkable_count,
                )
            elif route_failure_count > 0:
                message = f"후보 일부는 {route_label} 경로를 불러오지 못해 추천으로 이어지지 못했습니다."
        return finalize_response({
            "workplace": workplace,
            "recommendations": [],
            "meta": {
                "message": message,
                "relaxation_suggestions": suggestions,
                "warnings": build_meta_warnings(primary_mode),
                "total_candidates": int(len(filtered)),
                "checked_candidates": int(len(debug_items)),
                "transport_mode": primary_mode,
                "max_commute_minutes": state["hard_constraints"].get("max_commute_minutes"),
                "car_time_profile": state.get("car_time_profile"),
                "car_route_requested_provider": _car_route_requested_provider_label(state.get("car_time_profile")) if primary_mode == "car" else None,
                "car_route_provider": actual_car_route_provider if primary_mode == "car" else None,
                "car_route_time_basis": next((item.get("car_route_time_basis") for item in debug_items if item.get("car_route_time_basis") is not None), None) if primary_mode == "car" else None,
                "car_route_time_label": _car_route_time_label(state.get("car_time_profile")) if primary_mode == "car" else None,
                "car_route_time_supported": next((item.get("car_route_time_supported") for item in debug_items if item.get("car_route_time_supported") is not None), None) if primary_mode == "car" else None,
                "car_route_direction": next((item.get("car_route_direction") for item in debug_items if item.get("car_route_direction")), None) if primary_mode == "car" else None,
                "selected_car_time": (state.get("car_time_profile") or {}).get("selected_car_time") if primary_mode == "car" else None,
                "car_route_prediction_type": next((item.get("car_route_prediction_type") for item in debug_items if item.get("car_route_prediction_type") is not None), None),
                "car_route_prediction_time": next((item.get("car_route_prediction_time") for item in debug_items if item.get("car_route_prediction_time") is not None), None),
                "car_route_departure_lat": next((item.get("car_route_departure_lat") for item in debug_items if item.get("car_route_departure_lat") is not None), None),
                "car_route_departure_lng": next((item.get("car_route_departure_lng") for item in debug_items if item.get("car_route_departure_lng") is not None), None),
                "car_route_destination_lat": next((item.get("car_route_destination_lat") for item in debug_items if item.get("car_route_destination_lat") is not None), None),
                "car_route_destination_lng": next((item.get("car_route_destination_lng") for item in debug_items if item.get("car_route_destination_lng") is not None), None),
                "car_route_http_status": next((item.get("car_route_http_status") for item in debug_items if item.get("car_route_http_status") is not None), None),
                "car_route_response_received": any(item.get("car_route_response_received") is True for item in debug_items),
                "car_route_error_code": next((item.get("car_route_error_code") for item in debug_items if item.get("car_route_error_code")), None),
                "car_route_error_message": next((item.get("car_route_error_message") for item in debug_items if item.get("car_route_error_message")), None),
                "car_route_failure_detail": next((item.get("car_route_failure_detail") for item in debug_items if item.get("car_route_failure_detail")), None),
                "car_route_fallback_used": any(bool(item.get("car_route_fallback_used")) for item in debug_items),
                "weights": weights,
                "relaxation_mode": relaxation_mode,
                **failure_meta,
            },
            "debug": debug_items,
        })

    return finalize_response({
        "workplace": workplace,
        "recommendations": results[:TOP_N],
            "meta": {
                "message": "통근시간, 경로, 면적, 예산 조건을 반영해 추천했습니다.",
                "warnings": build_meta_warnings(primary_mode),
                "total_candidates": int(len(filtered)),
                "checked_candidates": int(len(debug_items)),
                "transport_mode": primary_mode,
                "max_commute_minutes": state["hard_constraints"].get("max_commute_minutes"),
                "car_time_profile": state.get("car_time_profile"),
                "car_route_requested_provider": _car_route_requested_provider_label(state.get("car_time_profile")) if primary_mode == "car" else None,
                "car_route_provider": next((item.get("car_route_provider") for item in results if item.get("car_route_provider")), None)
                if primary_mode == "car"
                else None,
                "car_route_time_basis": next((item.get("car_route_time_basis") for item in results if item.get("car_route_time_basis") is not None), None) if primary_mode == "car" else None,
                "car_route_time_label": _car_route_time_label(state.get("car_time_profile")) if primary_mode == "car" else None,
                "car_route_time_supported": next((item.get("car_route_time_supported") for item in results if item.get("car_route_time_supported") is not None), None) if primary_mode == "car" else None,
                "car_route_direction": next((item.get("car_route_direction") for item in results if item.get("car_route_direction")), None) if primary_mode == "car" else None,
                "selected_car_time": (state.get("car_time_profile") or {}).get("selected_car_time") if primary_mode == "car" else None,
                "car_route_prediction_type": next((item.get("car_route_prediction_type") for item in debug_items if item.get("car_route_prediction_type") is not None), None),
                "car_route_prediction_time": next((item.get("car_route_prediction_time") for item in debug_items if item.get("car_route_prediction_time") is not None), None),
                "car_route_departure_lat": next((item.get("car_route_departure_lat") for item in debug_items if item.get("car_route_departure_lat") is not None), None),
                "car_route_departure_lng": next((item.get("car_route_departure_lng") for item in debug_items if item.get("car_route_departure_lng") is not None), None),
                "car_route_destination_lat": next((item.get("car_route_destination_lat") for item in debug_items if item.get("car_route_destination_lat") is not None), None),
                "car_route_destination_lng": next((item.get("car_route_destination_lng") for item in debug_items if item.get("car_route_destination_lng") is not None), None),
                "car_route_http_status": next((item.get("car_route_http_status") for item in debug_items if item.get("car_route_http_status") is not None), None),
                "car_route_response_received": any(item.get("car_route_response_received") is True for item in debug_items),
                "car_route_error_code": next((item.get("car_route_error_code") for item in debug_items if item.get("car_route_error_code")), None),
                "car_route_error_message": next((item.get("car_route_error_message") for item in debug_items if item.get("car_route_error_message")), None),
                "car_route_failure_detail": next((item.get("car_route_failure_detail") for item in debug_items if item.get("car_route_failure_detail")), None),
                "car_route_fallback_used": any(bool(item.get("car_route_fallback_used")) for item in debug_items),
                "weights": weights,
                "candidate_compression": "distance_budget_area_union",
            },
            "debug": debug_items,
    })

