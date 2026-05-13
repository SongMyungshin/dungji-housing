from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKER_MARKET_FILE = PROJECT_ROOT / "data" / "external" / "seoul_worker_market.csv"


WORKER_MARKET_NAME_COL = "직장상권명"
YOUNG_WORKER_COUNT_COL = "청년직장인구"
WORKER_ACCESS_TIER_COL = "직주근접등급"
WORKER_MARKET_SCORE_COL = "직장상권점수"
INFRA_COMMENT_COL = "인프라해석"


def _find_worker_market_file() -> Path | None:
    if DEFAULT_WORKER_MARKET_FILE.exists():
        return DEFAULT_WORKER_MARKET_FILE
    return None


def _clean_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _normalize_station_name(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(r"\(.*?\)", "", text)
    text = text.replace("역", "")
    text = re.sub(r"\s+", "", text).strip()
    return text


def _load_worker_market_df() -> pd.DataFrame:
    csv_path = _find_worker_market_file()
    if csv_path is None or not csv_path.exists():
        return pd.DataFrame()

    try:
        worker_df = pd.read_csv(csv_path, encoding="cp949")
    except Exception:
        return pd.DataFrame()

    required = {
        "기준_년분기_코드",
        "상권_코드_명",
        "연령대_20_직장_인구_수",
        "연령대_30_직장_인구_수",
    }
    if not required.issubset(set(worker_df.columns)):
        return pd.DataFrame()

    latest_period = worker_df["기준_년분기_코드"].max()
    worker_df = worker_df[worker_df["기준_년분기_코드"] == latest_period].copy()
    worker_df["young_worker_count"] = (
        pd.to_numeric(worker_df["연령대_20_직장_인구_수"], errors="coerce").fillna(0)
        + pd.to_numeric(worker_df["연령대_30_직장_인구_수"], errors="coerce").fillna(0)
    )
    worker_df["market_name"] = worker_df["상권_코드_명"].astype(str).str.strip()
    worker_df["station_key"] = worker_df["market_name"].apply(_normalize_station_name)
    return worker_df


def _worker_market_tier(value: float) -> tuple[str, int]:
    if value >= 40000:
        return "매우 높음", 15
    if value >= 15000:
        return "높음", 12
    if value >= 5000:
        return "보통", 8
    if value >= 1000:
        return "낮음", 5
    if value > 0:
        return "제한적", 2
    return "확인 필요", 0


def _worker_market_comment(tier: str, market_name: str) -> str:
    label = market_name or "주요 직장인 상권"
    if tier == "매우 높음":
        return (
            f"20~30대 직장인구가 많은 {label} 생활권과 연계 가능성이 있어 "
            "청년 직장인 주거 수요 대응 가능성이 큽니다."
        )
    if tier == "높음":
        return (
            f"{label}와 같은 업무 밀집 생활권 접근성이 양호하여 "
            "직주근접형 청년주거 공급 검토가 가능합니다."
        )
    if tier == "보통":
        return (
            f"{label} 생활권과 연결되어 있어 일정 수준의 청년 직장인 수요를 "
            "함께 검토할 수 있습니다."
        )
    if tier == "낮음":
        return (
            f"{label} 주변 직장인 수요는 낮은 편으로 확인되어 "
            "보조적 생활권 지표로 해석할 필요가 있습니다."
        )
    if tier == "제한적":
        return (
            f"{label}와의 연결성은 확인되지만 직장인 수요 규모는 제한적이어서 "
            "참고 지표 수준으로 해석하는 것이 적절합니다."
        )
    return "직장인 상권 기반 생활권 데이터는 추가 확인이 필요합니다."


def _choose_market_row(station_name: str, worker_df: pd.DataFrame) -> pd.Series | None:
    raw_name = _clean_text(station_name)
    station_key = _normalize_station_name(station_name)
    if not raw_name and not station_key:
        return None

    if raw_name:
        exact = worker_df[worker_df["market_name"].str.contains(re.escape(raw_name), na=False)].copy()
        if not exact.empty:
            exact["match_priority"] = exact["market_name"].apply(
                lambda text: 0 if str(text).startswith(raw_name) else 1
            )
            exact = exact.sort_values(
                by=["match_priority", "young_worker_count"],
                ascending=[True, False],
            )
            return exact.iloc[0]

    if station_key:
        base = worker_df[worker_df["station_key"].str.contains(re.escape(station_key), na=False)].copy()
        if not base.empty:
            base["match_priority"] = base["station_key"].apply(
                lambda text: 0 if str(text) == station_key else 1
            )
            base = base.sort_values(
                by=["match_priority", "young_worker_count"],
                ascending=[True, False],
            )
            return base.iloc[0]

    return None


def enrich_with_worker_market(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    work_df = df.copy()
    worker_df = _load_worker_market_df()

    defaults = {
        WORKER_MARKET_NAME_COL: "",
        YOUNG_WORKER_COUNT_COL: 0,
        WORKER_ACCESS_TIER_COL: "확인 필요",
        WORKER_MARKET_SCORE_COL: 0,
        INFRA_COMMENT_COL: "직장인 상권 기반 생활권 데이터는 추가 확인이 필요합니다.",
        "worker_market_name": "",
        "young_worker_count": 0,
        "worker_access_tier": "확인 필요",
        "worker_market_score": 0,
        "infrastructure_comment": "직장인 상권 기반 생활권 데이터는 추가 확인이 필요합니다.",
    }

    if worker_df.empty or "nearest_station_point" not in work_df.columns:
        for col, value in defaults.items():
            if col not in work_df.columns:
                work_df[col] = value
        return work_df

    cache: dict[str, tuple[str, int, str, int, str]] = {}

    def _enrich_station(station_name: str) -> tuple[str, int, str, int, str]:
        key = _clean_text(station_name)
        if key in cache:
            return cache[key]

        row = _choose_market_row(key, worker_df)
        if row is None:
            cache[key] = ("", 0, "확인 필요", 0, defaults[INFRA_COMMENT_COL])
            return cache[key]

        market_name = _clean_text(row.get("market_name"))
        young_workers = int(float(row.get("young_worker_count", 0) or 0))
        tier, score = _worker_market_tier(young_workers)
        comment = _worker_market_comment(tier, market_name)
        cache[key] = (market_name, young_workers, tier, score, comment)
        return cache[key]

    enriched = work_df["nearest_station_point"].apply(_enrich_station)
    work_df[WORKER_MARKET_NAME_COL] = enriched.apply(lambda x: x[0])
    work_df[YOUNG_WORKER_COUNT_COL] = enriched.apply(lambda x: x[1])
    work_df[WORKER_ACCESS_TIER_COL] = enriched.apply(lambda x: x[2])
    work_df[WORKER_MARKET_SCORE_COL] = enriched.apply(lambda x: x[3])
    work_df[INFRA_COMMENT_COL] = enriched.apply(lambda x: x[4])

    work_df["worker_market_name"] = work_df[WORKER_MARKET_NAME_COL]
    work_df["young_worker_count"] = work_df[YOUNG_WORKER_COUNT_COL]
    work_df["worker_access_tier"] = work_df[WORKER_ACCESS_TIER_COL]
    work_df["worker_market_score"] = work_df[WORKER_MARKET_SCORE_COL]
    work_df["infrastructure_comment"] = work_df[INFRA_COMMENT_COL]

    if "정책적합판단" in work_df.columns:
        work_df["정책적합판단"] = work_df.apply(
            lambda row: (
                f"{row['정책적합판단']} · 직주근접 수요 {row[WORKER_ACCESS_TIER_COL]}"
                if _clean_text(row.get("정책적합판단"))
                and _clean_text(row.get(WORKER_ACCESS_TIER_COL)) not in {"", "확인 필요"}
                else row.get("정책적합판단")
            ),
            axis=1,
        )

    return work_df
