from __future__ import annotations

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_NEED_FILE = PROJECT_ROOT / "data" / "external" / "policy_need_by_district.csv"


POLICY_NEED_SCORE_COL = "정책필요도점수"
POLICY_NEED_TIER_COL = "정책필요도등급"
YOUTH_SUPPLY_LABEL_COL = "청년주거수요"
POLICY_NEED_COMMENT_COL = "정책수요해석"
YOUTH_HOUSEHOLDS_COL = "청년가구수"
YOUTH_HOUSEHOLD_RATIO_COL = "청년가구비율"
YOUTH_SINGLE_HOUSEHOLDS_COL = "청년1인가구수"
YOUTH_SINGLE_RATIO_COL = "청년1인가구비율"
YOUNG_ONE_PERSON_COUNT_COL = "청년1인가구20_34"


def _load_policy_need_df() -> pd.DataFrame:
    if not DEFAULT_POLICY_NEED_FILE.exists():
        return pd.DataFrame()

    try:
        policy_df = pd.read_csv(DEFAULT_POLICY_NEED_FILE, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()

    required = {
        "district",
        "youth_households",
        "youth_household_ratio",
        "youth_single_households",
        "youth_single_ratio",
        "young_one_person_20_34",
        "policy_need_score",
        "policy_need_tier",
        "youth_supply_label",
        "policy_need_comment",
    }
    if not required.issubset(set(policy_df.columns)):
        return pd.DataFrame()
    return policy_df


def enrich_with_household_policy(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    work_df = df.copy()
    policy_df = _load_policy_need_df()

    defaults = {
        POLICY_NEED_SCORE_COL: 0,
        POLICY_NEED_TIER_COL: "확인 필요",
        YOUTH_SUPPLY_LABEL_COL: "정책 수요 확인 필요",
        POLICY_NEED_COMMENT_COL: "청년 가구·1인가구 통계 기반 정책 수요 정보는 추가 확인이 필요합니다.",
        YOUTH_HOUSEHOLDS_COL: 0,
        YOUTH_HOUSEHOLD_RATIO_COL: 0,
        YOUTH_SINGLE_HOUSEHOLDS_COL: 0,
        YOUTH_SINGLE_RATIO_COL: 0,
        YOUNG_ONE_PERSON_COUNT_COL: 0,
        "policy_need_score": 0,
        "policy_need_tier": "확인 필요",
        "youth_supply_label": "정책 수요 확인 필요",
        "policy_need_comment": "청년 가구·1인가구 통계 기반 정책 수요 정보는 추가 확인이 필요합니다.",
    }

    if policy_df.empty or "district" not in work_df.columns:
        for col, default in defaults.items():
            if col not in work_df.columns:
                work_df[col] = default
        return work_df

    rename_map = {
        "youth_households": YOUTH_HOUSEHOLDS_COL,
        "youth_household_ratio": YOUTH_HOUSEHOLD_RATIO_COL,
        "youth_single_households": YOUTH_SINGLE_HOUSEHOLDS_COL,
        "youth_single_ratio": YOUTH_SINGLE_RATIO_COL,
        "young_one_person_20_34": YOUNG_ONE_PERSON_COUNT_COL,
        "policy_need_score": POLICY_NEED_SCORE_COL,
        "policy_need_tier": POLICY_NEED_TIER_COL,
        "youth_supply_label": YOUTH_SUPPLY_LABEL_COL,
        "policy_need_comment": POLICY_NEED_COMMENT_COL,
    }
    merge_df = policy_df.rename(columns=rename_map)
    work_df = work_df.merge(merge_df, on="district", how="left")

    for col, default in defaults.items():
        if col not in work_df.columns:
            work_df[col] = default
        work_df[col] = work_df[col].fillna(default)

    work_df["policy_need_score"] = work_df[POLICY_NEED_SCORE_COL]
    work_df["policy_need_tier"] = work_df[POLICY_NEED_TIER_COL]
    work_df["youth_supply_label"] = work_df[YOUTH_SUPPLY_LABEL_COL]
    work_df["policy_need_comment"] = work_df[POLICY_NEED_COMMENT_COL]

    if "정책적합판단" in work_df.columns:
        work_df["정책적합판단"] = work_df.apply(
            lambda row: (
                f"{row['정책적합판단']} · 정책 수요 {row[POLICY_NEED_TIER_COL]}"
                if str(row.get("정책적합판단", "")).strip()
                and str(row.get(POLICY_NEED_TIER_COL, "")).strip() not in {"", "확인 필요"}
                else row.get("정책적합판단")
            ),
            axis=1,
        )

    return work_df
