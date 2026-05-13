import requests, re, json, os, time, hashlib
from pyproj import Transformer

HEADERS = {"Referer": "https://map.kakao.com/"}

# ── 캐시 & 레이트리밋 ─────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
MIN_INTERVAL = 0.2          # API 호출 간 최소 간격(초)
_last_call_ts = 0.0         # 마지막 실제 API 호출 시각

def _cache_key(ox, oy, dx, dy, top_n):
    raw = f"{ox},{oy},{dx},{dy},{top_n}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]

def _cache_path(key):
    return os.path.join(CACHE_DIR, f"{key}.json")

def _cache_get(key):
    p = _cache_path(key)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None

def _cache_put(key, value):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)

def _throttle():
    global _last_call_ts
    wait = MIN_INTERVAL - (time.perf_counter() - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.perf_counter()

# WGS84 ↔ WCONGNAMUL (EPSG:5181) 변환기
_to_wcong = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)
_to_wgs84 = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)

def wgs84_to_wcong(lon, lat):
    # Kakao WCONGNAMUL = EPSG:5181 with 0.4m units (×2.5)
    x, y = _to_wcong.transform(lon, lat)
    return round(x * 2.5), round(y * 2.5)

def wcong_polyline_to_wgs84(polyline_str):
    """'x1|y1|x2|y2|...' → [[lon, lat], ...]"""
    if not polyline_str:
        return []
    nums = list(map(int, polyline_str.split("|")))
    result = []
    for i in range(0, len(nums), 2):
        lon, lat = _to_wgs84.transform(nums[i] / 2.5, nums[i+1] / 2.5)
        result.append([round(lon, 6), round(lat, 6)])
    return result


def get_transit_routes(origin_lon, origin_lat,
                       dest_lon,   dest_lat,
                       top_n=3, use_cache=True):
    """
    위경도(WGS84) 입력 → 대중교통 추천 경로 + 수단별 polyline 반환
    캐시 히트 시 네트워크 호출 생략. 미스 시 호출 간 MIN_INTERVAL 만큼 throttle.
    """
    ox, oy = wgs84_to_wcong(origin_lon, origin_lat)
    dx, dy = wgs84_to_wcong(dest_lon,   dest_lat)

    key = _cache_key(ox, oy, dx, dy, top_n)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    _throttle()
    r = requests.get(
        "https://map.kakao.com/route/pubtrans.json",
        params={
            "inputCoordSystem":  "WCONGNAMUL",
            "outputCoordSystem": "WCONGNAMUL",
            "service":  "map.daum.net",
            "callback": "cb",
            "sX": ox, "sY": oy, "sName": "출발", "sid": "",
            "eX": dx, "eY": dy, "eName": "도착", "eid": "",
        },
        headers=HEADERS
    )
    data = json.loads(re.search(r'cb\((.+)\)', r.text, re.DOTALL).group(1))
    routes = data["in_local"]["routes"][:top_n]

    result = []
    for route in routes:
        segments = []
        for step in route["steps"]:
            mode = step.get("type")
            if mode not in ("SUBWAY", "BUS", "WALKING"):
                continue
            # 노선명: vehicles 배열 우선, 없으면 routeName
            line_name = (
                (step.get("vehicles") or [{}])[0].get("name")
                or step.get("routeName")
            )
            direction = (
                (step.get("vehicles") or [{}])[0].get("direction")
                or step.get("direction")
            )
            segments.append({
                "수단":     {"SUBWAY": "지하철", "BUS": "버스", "WALKING": "도보"}[mode],
                "노선명":   line_name,
                "방향":     direction,
                "승차":     step.get("startLocation", {}).get("name"),
                "하차":     step.get("endLocation",   {}).get("name"),
                "소요시간": step.get("time",     {}).get("text"),
                "거리":     step.get("distance", {}).get("text"),
                "정류장수": len(step.get("nodes", [])),
                "geometry": {
                    "type": "LineString",
                    "coordinates": wcong_polyline_to_wgs84(step.get("polyline", ""))
                }
            })

        result.append({
            "순위":     route["ranking"],
            "유형":     route["type"],
            "소요시간": route["time"]["text"],
            "요금":     route["fare"]["text"],
            "총거리":   route["distance"]["text"],
            "도보거리": route["walkingDistance"]["text"],
            "환승횟수": route["transfers"],
            "추천":     route["recommended"],
            "최소시간": route["shortestTime"],
            "최소환승": route["leastTransfer"],
            "구간":     segments,
        })

    if use_cache:
        _cache_put(key, result)
    return result


# ── 사용 예시 ──────────────────────────────────────────
if __name__ == "__main__":
    # 강남역(127.028, 37.498) → 홍대입구(126.9247, 37.5579)
    routes = get_transit_routes(
        origin_lon=127.028,  origin_lat=37.498,
        dest_lon=126.9247,   dest_lat=37.5579,
        top_n=3,
    )

    for r in routes:
        print(f"\n[경로 {r['순위']}] {r['유형']} | {r['소요시간']} | {r['요금']} | 환승 {r['환승횟수']}회")
        for seg in r["구간"]:
            pts = len(seg["geometry"]["coordinates"])
            print(f"  {seg['수단']:4s} {seg.get('노선명') or '':10s} "
                  f"{seg['승차']} → {seg['하차']} ({seg['소요시간']}, {pts}pts)")

    # GeoJSON 저장
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "경로순위": r["순위"],
                    "수단":     seg["수단"],
                    "노선명":   seg["노선명"],
                    "승차":     seg["승차"],
                    "하차":     seg["하차"],
                    "소요시간": seg["소요시간"],
                },
                "geometry": seg["geometry"]
            }
            for r in routes
            for seg in r["구간"]
            if seg["geometry"]["coordinates"]
        ]
    }
    with open("transit_routes.geojson", "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print("\nGeoJSON 저장 완료: transit_routes.geojson")