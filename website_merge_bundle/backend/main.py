from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.src.candidate_service import CandidateService, SearchFilters


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(title="청년주택 후보지 예비검토 API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

service = CandidateService()


class SearchFiltersPayload(BaseModel):
    districts: list[str] = Field(default_factory=list)
    candidate_scope: str = "both"
    station_scope: str = "include_conditional"
    min_area_sqm: int | None = None
    merge_preference: str = "include"
    policy_need_filter: str = "keep"
    worker_market_filter: str = "keep"


class ExploreRequest(BaseModel):
    query: str = ""
    top_k: int = 5
    filters: SearchFiltersPayload = Field(default_factory=SearchFiltersPayload)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/site-review/options")
def site_review_options() -> dict:
    return service.get_options()


@app.get("/api/site-review/map-overview")
def site_review_map_overview() -> dict:
    return service.get_overview_map()


@app.post("/api/site-review/explore")
def site_review_explore(payload: ExploreRequest) -> dict:
    filters = SearchFilters(
        districts=payload.filters.districts,
        candidate_scope=payload.filters.candidate_scope,
        station_scope=payload.filters.station_scope,
        min_area_sqm=payload.filters.min_area_sqm,
        merge_preference=payload.filters.merge_preference,
        policy_need_filter=payload.filters.policy_need_filter,
        worker_market_filter=payload.filters.worker_market_filter,
    )
    return service.explore(payload.query, filters, top_k=payload.top_k)


@app.get("/")
def index_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/housing.html")
def housing_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "housing.html")


@app.get("/site-review.html")
def site_review_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "site-review.html")


app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
