import os
from pathlib import Path


def _load_local_env_file():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    _load_local_env_file()


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DATA_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

INPUT_FILE = DATA_RAW_DIR / "seoul_gukyoubuji.xlsx"
OUTPUT_FILE = DATA_OUTPUT_DIR / "seoul_youth_housing_candidates.xlsx"
MAP_FILE = DATA_OUTPUT_DIR / "seoul_youth_housing_candidates_map.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
KAKAO_MAP_JS_KEY = os.getenv("KAKAO_MAP_JS_KEY", "")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
TMAP_APP_KEY = os.getenv("TMAP_APP_KEY", "")

DEFAULT_TOP_N = 10

COLUMN_CANDIDATES = {
    "candidate_id": ["후보지번호", "관리번호", "필지번호", "candidate_id"],
    "district": ["구", "자치구", "district"],
    "dong": ["동", "neighborhood", "dong"],
    "lot_number": ["지번", "lot", "lot_number"],
    "address": ["소재지(지번)", "소재지", "주소", "address"],
    "asset_type": ["재산종류", "asset_type", "refilter_asset_type"],
    "asset_class": ["재산구분", "asset_class"],
    "land_category": ["지목", "land_category"],
    "area": ["대장면적(단위:㎡)", "면적", "토지면적", "면적(㎡)", "area_sqm", "area"],
    "lat": ["위도", "lat", "latitude"],
    "lon": ["경도", "lng", "lon", "longitude"],
    "use_main": ["청년주택_가능용도지역", "용도지역", "용도지역1", "youth_zone", "zone_main"],
    "use_sub": ["기타확인_용도지역", "용도지역2", "세부용도지역", "extra_zone", "zone_sub"],
    "special_district": ["특별지구", "보호지구", "특별지구/보호지구", "special_zone", "special_zone_raw"],
    "nearest_station": [
        "재필터_근접역명_점기준",
        "nearest_station_point",
        "가장가까운_역명",
        "근접역명",
    ],
    "nearest_station_distance": [
        "재필터_근접역거리_m_점기준",
        "nearest_station_point_distance_m",
        "지하철출입구_최단거리_m",
        "역거리",
        "가장가까운_역거리",
    ],
    "station_image_available": [
        "재필터_근접역_범위이미지보유",
        "nearest_station_image_available",
        "역범위이미지보유",
    ],
    "station_status": [
        "역세권판정상태",
        "station_zone_status",
        "역세권상태",
    ],
    "station_basis": [
        "역세권판정근거",
        "station_zone_basis",
        "역세권메모",
        "refilter_policy_note",
    ],
}

EXCLUDED_LAND_CATEGORIES = {
    "도로",
    "하천",
    "구거",
    "철도용지",
    "학교용지",
    "수도용지",
    "제방",
}
