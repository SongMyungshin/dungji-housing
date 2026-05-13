import numpy as np
import pandas as pd

from .classifiers import (
    classify_area,
    classify_asset_project,
    classify_land_category,
    classify_policy_type,
    classify_station_access,
    classify_use_region,
)


class CandidateScorer:
    def __init__(self, df, column_map):
        self.df = df
        self.column_map = column_map
        self.filtered_df = None
        self.result_df = None

    def _asset_type_text(self, value):
        if pd.isna(value):
            return ""
        return str(value).strip()

    def _filter_asset(self, work_df):
        asset_class_col = self.column_map.get("asset_class")
        if asset_class_col:
            before = len(work_df)
            work_df = work_df[
                work_df[asset_class_col].astype(str).str.contains("일반재산", na=False)
            ].copy()
            print(f"\n일반재산 필터: {before} -> {len(work_df)}")

        asset_type_col = self.column_map.get("asset_type")
        if asset_type_col:
            before = len(work_df)
            work_df = work_df[
                work_df[asset_type_col].astype(str).str.contains("토지|건물", na=False)
            ].copy()
            print(f"토지/건물 필터: {before} -> {len(work_df)}")
        else:
            work_df["재산종류"] = "토지"
            self.column_map["asset_type"] = "재산종류"
        return work_df

    def _apply_asset_project(self, work_df):
        asset_type_col = self.column_map.get("asset_type")
        results = work_df[asset_type_col].apply(classify_asset_project)
        work_df["사업유형"] = [item[0] for item in results]
        work_df["자산유형점수"] = [item[1] for item in results]
        return work_df

    def _apply_land(self, work_df):
        asset_type_col = self.column_map.get("asset_type")
        land_col = self.column_map["land_category"]
        is_building = work_df[asset_type_col].astype(str).str.contains("건물", na=False)

        work_df["지목검토등급"] = "건물형 검토"
        work_df["지목점수"] = 20

        land_results = work_df.loc[~is_building, land_col].apply(classify_land_category)
        if not land_results.empty:
            work_df.loc[~is_building, "지목검토등급"] = [item[0] for item in land_results]
            work_df.loc[~is_building, "지목점수"] = [item[1] for item in land_results]

        before = len(work_df)
        work_df = work_df[~((~is_building) & (work_df["지목검토등급"] == "제외"))].copy()
        print(f"지목 제외 필터(토지 대상): {before} -> {len(work_df)}")
        return work_df

    def _apply_use_region(self, work_df):
        use_main_col = self.column_map.get("use_main")
        use_sub_col = self.column_map.get("use_sub")
        special_col = self.column_map.get("special_district")

        results = work_df.apply(
            lambda row: classify_use_region(
                row[use_main_col] if use_main_col else "",
                row[use_sub_col] if use_sub_col else "",
                row[special_col] if special_col else "",
            ),
            axis=1,
        )
        work_df["용도지역검토등급"] = [item[0] for item in results]
        work_df["용도지역점수"] = [item[1] for item in results]
        work_df["특별검토필요"] = [item[2] for item in results]
        return work_df

    def _apply_area(self, work_df):
        area_col = self.column_map["area"]
        numeric_area = pd.to_numeric(work_df[area_col], errors="coerce")
        work_df["면적검토등급"], work_df["면적점수"] = zip(
            *numeric_area.apply(classify_area)
        )
        before = len(work_df)
        work_df = work_df[work_df["면적검토등급"] != "제외"].copy()
        print(f"면적 제외 필터: {before} -> {len(work_df)}")
        return work_df

    def _apply_station(self, work_df):
        asset_type_col = self.column_map.get("asset_type")
        nearest_col = self.column_map.get("nearest_station")
        dist_col = self.column_map.get("nearest_station_distance")
        image_col = self.column_map.get("station_image_available")
        status_col = self.column_map.get("station_status")
        basis_col = self.column_map.get("station_basis")

        results = work_df.apply(
            lambda row: classify_station_access(
                asset_type=self._asset_type_text(row.get(asset_type_col)) if asset_type_col else "토지",
                explicit_status=row.get(status_col, "") if status_col else "",
                explicit_basis=row.get(basis_col, "") if basis_col else "",
                nearest_station=row.get(nearest_col, "") if nearest_col else "",
                nearest_station_distance=row.get(dist_col, np.nan) if dist_col else np.nan,
                image_available=row.get(image_col, "") if image_col else "",
            ),
            axis=1,
        )
        work_df["역세권판정상태"] = [item[0] for item in results]
        work_df["역세권예비구간"] = [item[1] for item in results]
        work_df["역세권후보분류"] = [item[2] for item in results]
        work_df["역세권점수"] = [item[3] for item in results]
        work_df["공간판정신뢰도"] = [item[4] for item in results]
        work_df["역세권판정근거"] = [item[5] for item in results]
        work_df["가장가까운역"] = [item[6] for item in results]
        work_df["역범위이미지검토"] = [item[7] for item in results]
        work_df["승강장경계재판정필요"] = [item[8] for item in results]
        return work_df

    def _apply_policy_type(self, work_df):
        asset_type_col = self.column_map.get("asset_type")
        results = work_df.apply(
            lambda row: classify_policy_type(
                self._asset_type_text(row.get(asset_type_col)) if asset_type_col else "토지",
                row.get("역세권후보분류", ""),
                row.get("역세권예비구간", ""),
            ),
            axis=1,
        )
        work_df["정책유형"] = [item[0] for item in results]
        work_df["정책적합점수"] = [item[1] for item in results]
        return work_df

    def prepare(self):
        work_df = self.df.copy()
        work_df = self._filter_asset(work_df)
        work_df = self._apply_asset_project(work_df)
        work_df = self._apply_land(work_df)
        work_df = self._apply_use_region(work_df)
        work_df = self._apply_area(work_df)
        work_df = self._apply_station(work_df)
        work_df = self._apply_policy_type(work_df)
        self.filtered_df = work_df
        return self.filtered_df

    @staticmethod
    def assign_final_grade(row):
        if row["지목검토등급"] == "제외" or row["면적검토등급"] == "제외":
            return "제외"

        score = row["최종점수"]
        station_candidate = row.get("역세권후보분류", "")
        project_type = row.get("사업유형", "")

        if "건물" in project_type:
            if score >= 95:
                return "우선검토"
            if score >= 75:
                return "조건부 검토"
            return "추가검토"

        if station_candidate in {"역세권 확인 후보", "역세권 유력 후보"}:
            if score >= 100 and not row["특별검토필요"]:
                return "최우선검토"
            if score >= 85:
                return "우선검토"
            if score >= 65:
                return "조건부 검토"
            return "추가검토"

        if station_candidate == "조건부 역세권 후보":
            if score >= 90:
                return "조건부 검토"
            return "추가검토"

        if station_candidate == "비역세권 후보":
            return "추가검토"

        if score >= 85:
            return "조건부 검토"
        return "추가검토"

    def score(self):
        if self.filtered_df is None:
            self.prepare()

        result_df = self.filtered_df.copy()
        result_df["최종점수"] = (
            result_df["자산유형점수"]
            + result_df["지목점수"]
            + result_df["용도지역점수"]
            + result_df["면적점수"]
            + result_df["역세권점수"]
            + result_df["정책적합점수"]
            - np.where(result_df["특별검토필요"], 5, 0)
        )
        result_df["최종검토등급"] = result_df.apply(self.assign_final_grade, axis=1)
        result_df["AI추천사유"] = ""

        result_df = result_df.sort_values(
            by=["최종점수", "역세권점수", "정책적합점수", "면적점수"],
            ascending=[False, False, False, False],
        ).reset_index(drop=True)

        self.result_df = result_df
        return self.result_df
