import json

from config.settings import COLUMN_CANDIDATES


class ColumnMapper:
    def __init__(self, df, gemini=None, column_candidates=None):
        self.df = df
        self.gemini = gemini
        self.column_candidates = column_candidates or COLUMN_CANDIDATES
        self.column_map = {}

    def find_column(self, aliases, required=False):
        for alias in aliases:
            if alias in self.df.columns:
                return alias
        lowered = {str(col).strip().lower(): col for col in self.df.columns}
        for alias in aliases:
            key = str(alias).strip().lower()
            if key in lowered:
                return lowered[key]
        if required:
            raise KeyError(f"필수 컬럼을 찾지 못했습니다: {aliases}")
        return None

    def map_rule_based(self):
        self.column_map = {
            key: self.find_column(aliases, required=False)
            for key, aliases in self.column_candidates.items()
        }
        return self.column_map

    def map_with_gemini(self):
        missing_keys = [key for key, value in self.column_map.items() if value is None]
        if not missing_keys:
            return self.column_map
        if self.gemini is None or not self.gemini.enabled:
            print("Gemini 컬럼 보완 매핑 생략: GEMINI_API_KEY 없음")
            return self.column_map

        prompt = f"""
당신은 서울 공공 토지 데이터 컬럼 매핑 도우미다.
아래 컬럼 목록에서 각 의미에 가장 잘 맞는 실제 컬럼명을 찾아라.
없으면 null로 반환하라.
반드시 JSON 객체만 반환하라.

의미 목록:
- candidate_id: 후보지 식별번호
- district: 자치구
- dong: 동 이름
- lot_number: 지번
- address: 주소/소재지
- asset_type: 재산종류(토지/건물 등)
- asset_class: 재산구분(일반재산 등)
- land_category: 지목
- area: 면적
- lat: 위도
- lon: 경도
- use_main: 핵심 용도지역 정보
- use_sub: 보조 용도지역 정보
- special_district: 특별지구/보호지구 정보
- nearest_station: 가장 가까운 역명
- nearest_station_distance: 가장 가까운 역까지 거리(m)
- station_image_available: 역 범위 이미지 보유 여부
- station_status: 역세권 판정 상태
- station_basis: 역세권 판정 근거/메모

실제 컬럼 목록:
{json.dumps(self.df.columns.tolist(), ensure_ascii=False)}
"""
        try:
            ai_map = self.gemini.generate_json(prompt)
        except Exception as exc:
            print(f"Gemini 컬럼 매핑 실패: {exc}")
            return self.column_map

        for key in missing_keys:
            candidate = ai_map.get(key)
            if candidate in self.df.columns:
                self.column_map[key] = candidate

        return self.column_map

    def resolve(self, required=("land_category", "area")):
        self.map_rule_based()
        self.map_with_gemini()

        missing_required = [key for key in required if not self.column_map.get(key)]
        if missing_required:
            raise KeyError(f"필수 컬럼 매핑 실패: {missing_required}")

        print("\n[자동 인식 컬럼]")
        for key, value in self.column_map.items():
            print(f"{key}: {value}")
        return self.column_map
