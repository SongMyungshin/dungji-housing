from __future__ import annotations

from itertools import combinations
from math import atan2, cos, radians, sin, sqrt

import pandas as pd

from .classifiers import classify_policy_type, classify_station_access, normalize_text


MERGE_DISTANCE_M = 30.0
MERGE_MAX_PARCELS = 3
MERGE_MIN_AREA_SQM = 200.0
MERGE_RECOMMENDED_AREA_SQM = 300.0
MERGE_STRONG_AREA_SQM = 500.0


def _to_float(value):
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _to_bool(value):
    if isinstance(value, bool):
        return value
    text = normalize_text(value).lower()
    return text in {"true", "1", "y", "yes"}


def _haversine_distance_m(lat1, lon1, lat2, lon2):
    radius_m = 6371000
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)

    a = (
        sin(delta_phi / 2) ** 2
        + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_m * atan2(sqrt(a), sqrt(1 - a))


def _area_band(area_sqm):
    if area_sqm < MERGE_MIN_AREA_SQM:
        return "제외", 0
    if area_sqm < MERGE_RECOMMENDED_AREA_SQM:
        return "결합 후보", 10
    if area_sqm < MERGE_STRONG_AREA_SQM:
        return "우수 결합 후보", 20
    return "매우 우수 결합 후보", 30


def _final_grade_from_score(score):
    if score >= 150:
        return "우선검토"
    if score >= 130:
        return "조건부 검토"
    return "추가검토"


def _eligible_land_rows(result_df, column_map):
    asset_col = column_map.get("asset_type")
    district_col = column_map.get("district")
    dong_col = column_map.get("dong")
    area_col = column_map.get("area")
    lat_col = column_map.get("lat")
    lon_col = column_map.get("lon")
    candidate_col = column_map.get("candidate_id")

    if not all([asset_col, district_col, dong_col, area_col, lat_col, lon_col, candidate_col]):
        return pd.DataFrame()

    work_df = result_df.copy()
    work_df = work_df[
        work_df[asset_col].astype(str).str.contains("토지", regex=False, na=False)
    ].copy()

    work_df["_merge_area_sqm"] = pd.to_numeric(work_df[area_col], errors="coerce")
    work_df["_merge_lat"] = pd.to_numeric(work_df[lat_col], errors="coerce")
    work_df["_merge_lon"] = pd.to_numeric(work_df[lon_col], errors="coerce")
    work_df = work_df.dropna(
        subset=[
            district_col,
            dong_col,
            candidate_col,
            "_merge_area_sqm",
            "_merge_lat",
            "_merge_lon",
        ]
    ).copy()

    if "후보유형" in work_df.columns:
        work_df = work_df[work_df["후보유형"] != "필지결합형"].copy()

    small_lot_mask = work_df["_merge_area_sqm"] < MERGE_MIN_AREA_SQM
    if "small_lot_for_merge" in work_df.columns:
        small_lot_mask = small_lot_mask | work_df["small_lot_for_merge"].apply(_to_bool)

    return work_df[small_lot_mask].copy()


def find_nearby_parcels(
    result_df,
    column_map,
    max_distance_m=MERGE_DISTANCE_M,
):
    small_land_df = _eligible_land_rows(result_df, column_map)
    if small_land_df.empty:
        return []

    district_col = column_map["district"]
    dong_col = column_map["dong"]
    candidate_col = column_map["candidate_id"]

    parcel_groups = []
    for (district, dong), group_df in small_land_df.groupby([district_col, dong_col]):
        records = group_df.to_dict("records")
        if len(records) < 2:
            continue

        adjacency = {record[candidate_col]: set() for record in records}
        record_map = {record[candidate_col]: record for record in records}
        distance_map = {}

        for left, right in combinations(records, 2):
            distance_m = _haversine_distance_m(
                left["_merge_lat"],
                left["_merge_lon"],
                right["_merge_lat"],
                right["_merge_lon"],
            )
            key = tuple(sorted((left[candidate_col], right[candidate_col])))
            distance_map[key] = distance_m
            if distance_m <= max_distance_m:
                adjacency[left[candidate_col]].add(right[candidate_col])
                adjacency[right[candidate_col]].add(left[candidate_col])

        visited = set()
        for node, neighbors in adjacency.items():
            if node in visited or not neighbors:
                continue

            stack = [node]
            component_ids = []
            visited.add(node)
            while stack:
                current = stack.pop()
                component_ids.append(current)
                for neighbor in adjacency[current]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)

            component_records = [record_map[record_id] for record_id in sorted(component_ids)]
            parcel_groups.append(
                {
                    "district": district,
                    "dong": dong,
                    "records": component_records,
                    "distance_map": distance_map,
                }
            )

    return parcel_groups


def score_merged_candidates(records, column_map, component_distance_map):
    area_col = column_map["area"]
    candidate_col = column_map["candidate_id"]
    address_col = column_map.get("address")
    lot_col = column_map.get("lot_number")
    land_col = column_map.get("land_category")
    use_main_col = column_map.get("use_main")
    use_sub_col = column_map.get("use_sub")
    special_col = column_map.get("special_district")
    nearest_col = column_map.get("nearest_station")
    nearest_dist_col = column_map.get("nearest_station_distance")
    image_col = column_map.get("station_image_available")
    status_col = column_map.get("station_status")
    basis_col = column_map.get("station_basis")

    merged_area = sum(_to_float(record.get(area_col)) or 0 for record in records)
    area_band, area_score = _area_band(merged_area)
    if area_score == 0:
        return None

    lot_parts = []
    for record in records:
        lot_text = normalize_text(record.get(lot_col))
        if lot_text:
            lot_parts.append(lot_text)
        else:
            lot_parts.append(str(record.get(candidate_col)))

    member_ids = [str(record.get(candidate_col)) for record in records]
    unique_land_categories = sorted(
        {
            normalize_text(record.get(land_col))
            for record in records
            if normalize_text(record.get(land_col))
        }
    )
    unique_zone_main = sorted(
        {
            normalize_text(record.get(use_main_col))
            for record in records
            if use_main_col and normalize_text(record.get(use_main_col))
        }
    )
    unique_zone_sub = sorted(
        {
            normalize_text(record.get(use_sub_col))
            for record in records
            if use_sub_col and normalize_text(record.get(use_sub_col))
        }
    )
    unique_special = sorted(
        {
            normalize_text(record.get(special_col))
            for record in records
            if special_col and normalize_text(record.get(special_col))
        }
    )

    representative = min(
        records,
        key=lambda record: (
            _to_float(record.get(nearest_dist_col))
            if nearest_dist_col and _to_float(record.get(nearest_dist_col)) is not None
            else float("inf")
        ),
    )
    nearest_station = normalize_text(representative.get(nearest_col)) or "미확인"
    nearest_distance = (
        _to_float(representative.get(nearest_dist_col))
        if nearest_dist_col
        else None
    )
    explicit_status = ""
    for record in records:
        status = normalize_text(record.get(status_col)) if status_col else ""
        if status in {"포함확인", "제외확인"}:
            explicit_status = status
            break

    image_available = "Y" if any(
        normalize_text(record.get(image_col)).upper() in {"Y", "YES", "TRUE", "1"}
        for record in records
    ) else ""

    max_pair_distance = 0.0
    for left, right in combinations(records, 2):
        pair_key = tuple(sorted((left[candidate_col], right[candidate_col])))
        max_pair_distance = max(max_pair_distance, component_distance_map.get(pair_key, 0.0))

    merge_basis = (
        f"구성 필지들이 약 {max_pair_distance:.1f}m 이내에 위치하고 "
        f"합산 면적이 {merged_area:.1f}㎡입니다. "
        "본 분석은 좌표 기반 예비 결합 가능성 분석이며, 실제 필지 결합 가능 여부는 "
        "지적도, 도로 접면, 소유관계, 도시계획 규제 검토가 필요합니다."
    )

    station_result = classify_station_access(
        asset_type="토지",
        explicit_status=explicit_status,
        explicit_basis=normalize_text(representative.get(basis_col)) if basis_col else merge_basis,
        nearest_station=nearest_station,
        nearest_station_distance=nearest_distance,
        image_available=image_available,
    )
    (
        station_status,
        station_band,
        station_candidate,
        station_score,
        station_confidence,
        station_basis,
        nearest_station_name,
        image_review,
        needs_recheck,
    ) = station_result

    policy_type, policy_score = classify_policy_type(
        "토지",
        station_candidate,
        station_band,
    )

    asset_score = 25
    land_score = sum(_to_float(record.get("지목점수")) or 0 for record in records) / len(records)
    zone_score = sum(_to_float(record.get("용도지역점수")) or 0 for record in records) / len(records)
    has_special_review = any(_to_bool(record.get("특별검토필요")) for record in records) or bool(unique_special)
    special_penalty = 5 if has_special_review else 0
    merge_bonus = 4 if merged_area >= MERGE_RECOMMENDED_AREA_SQM else 0
    total_score = round(
        asset_score
        + land_score
        + zone_score
        + area_score
        + station_score
        + policy_score
        + merge_bonus
        - special_penalty
    )

    candidate_type = "필지결합형"
    review_note_parts = []
    if needs_recheck:
        review_note_parts.append("승강장 경계 기준 재판정 필요")
    if has_special_review:
        review_note_parts.append("특별지구·보호지구 등 추가 검토 필요")
    review_note_parts.append("실제 연접 여부, 도로 접면, 소유·관리 상태 확인 필요")

    district = normalize_text(records[0].get(column_map["district"]))
    dong = normalize_text(records[0].get(column_map["dong"]))
    merged_address = f"서울특별시 {district} {dong} 일대 결합 후보"
    if address_col and normalize_text(records[0].get(address_col)):
        merged_address = (
            f"{district} {dong} 일대 결합 후보 "
            f"({normalize_text(records[0].get(address_col))} 인근)"
        )

    lat_values = [_to_float(record.get(column_map["lat"])) for record in records]
    lon_values = [_to_float(record.get(column_map["lon"])) for record in records]
    lat_values = [value for value in lat_values if value is not None]
    lon_values = [value for value in lon_values if value is not None]

    reason = (
        f"단일 필지로는 개발 여지가 제한적이지만, 인접 필지 결합을 통해 약 {merged_area:.1f}㎡ "
        f"규모를 확보할 수 있어 토지 신축형 청년주거 후보로 검토할 수 있습니다."
    )

    return {
        candidate_col: "",
        column_map["district"]: district,
        column_map["dong"]: dong,
        column_map["lot_number"]: " + ".join(lot_parts) if lot_col else "",
        column_map["address"]: merged_address if address_col else merged_address,
        column_map["asset_type"]: "토지",
        column_map.get("asset_class") or "asset_class": "일반재산",
        column_map["area"]: round(merged_area, 1),
        column_map["lat"]: round(sum(lat_values) / len(lat_values), 7) if lat_values else None,
        column_map["lon"]: round(sum(lon_values) / len(lon_values), 7) if lon_values else None,
        (land_col or "land_category"): "혼합" if len(unique_land_categories) > 1 else (unique_land_categories[0] if unique_land_categories else "미확인"),
        (use_main_col or "use_main"): ", ".join(unique_zone_main),
        (use_sub_col or "use_sub"): ", ".join(unique_zone_sub),
        (special_col or "special_district"): ", ".join(unique_special),
        (nearest_col or "nearest_station"): nearest_station_name,
        (nearest_dist_col or "nearest_station_distance"): nearest_distance,
        (image_col or "station_image_available"): image_available,
        (status_col or "station_status"): station_status,
        (basis_col or "station_basis"): station_basis or merge_basis,
        "후보유형": candidate_type,
        "구성필지": " + ".join(lot_parts),
        "구성필지ID": " + ".join(member_ids),
        "결합필지수": len(records),
        "결합면적": round(merged_area, 1),
        "결합근거": merge_basis,
        "개별필지주소": " | ".join(
            normalize_text(record.get(address_col)) for record in records if address_col
        ),
        "사업유형": "토지 신축형",
        "자산유형점수": asset_score,
        "지목검토등급": "결합 검토",
        "지목점수": round(land_score, 1),
        "용도지역검토등급": "결합 검토",
        "용도지역점수": round(zone_score, 1),
        "특별검토필요": has_special_review,
        "면적검토등급": area_band,
        "면적점수": area_score,
        "역세권판정상태": station_status,
        "역세권예비구간": station_band,
        "역세권후보분류": station_candidate,
        "역세권점수": station_score,
        "공간판정신뢰도": station_confidence,
        "역세권판정근거": station_basis or merge_basis,
        "가장가까운역": nearest_station_name,
        "역범위이미지검토": image_review,
        "승강장경계재판정필요": needs_recheck,
        "정책유형": policy_type,
        "정책적합점수": policy_score,
        "결합가산점": merge_bonus,
        "최종점수": total_score,
        "최종검토등급": _final_grade_from_score(total_score),
        "AI추천사유": reason,
        "추가검토메모": ", ".join(review_note_parts),
    }


def create_merged_candidates(
    result_df,
    column_map,
    max_distance_m=MERGE_DISTANCE_M,
    max_parcels=MERGE_MAX_PARCELS,
):
    parcel_groups = find_nearby_parcels(
        result_df=result_df,
        column_map=column_map,
        max_distance_m=max_distance_m,
    )
    if not parcel_groups:
        return pd.DataFrame()

    candidate_col = column_map["candidate_id"]
    merged_rows = []

    for group_index, group in enumerate(parcel_groups, start=1):
        records = group["records"]
        distance_map = group["distance_map"]
        best_row = None

        for size in range(2, min(max_parcels, len(records)) + 1):
            for combo in combinations(records, size):
                valid_combo = True
                for left, right in combinations(combo, 2):
                    pair_key = tuple(sorted((left[candidate_col], right[candidate_col])))
                    if distance_map.get(pair_key, float("inf")) > max_distance_m:
                        valid_combo = False
                        break
                if not valid_combo:
                    continue

                scored_row = score_merged_candidates(combo, column_map, distance_map)
                if scored_row is None:
                    continue

                if best_row is None:
                    best_row = scored_row
                    continue

                if (
                    scored_row["최종점수"],
                    scored_row["결합면적"],
                    -scored_row["결합필지수"],
                ) > (
                    best_row["최종점수"],
                    best_row["결합면적"],
                    -best_row["결합필지수"],
                ):
                    best_row = scored_row

        if best_row is None:
            continue

        best_row[candidate_col] = f"MERGE-{group_index:03d}"
        merged_rows.append(best_row)

    if not merged_rows:
        return pd.DataFrame()

    merged_df = pd.DataFrame(merged_rows)
    return merged_df.sort_values(
        by=["최종점수", "결합면적"],
        ascending=[False, False],
    ).reset_index(drop=True)
