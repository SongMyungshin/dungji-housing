import json
import os
import mimetypes
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ai_service import GEMINI_API_KEY, GEMINI_MODEL, REQUEST_TIMEOUT, build_follow_up_question, build_summary_conditions, chat_turn_with_gemini, enrich_recommendations_with_llm, find_missing_fields, parse_query_text, parse_query_with_gemini
from check_odsay_connection import run_preflight
from recommendation_service import DEFAULT_TRANSPORT_MODE, KAKAO_JS_KEY, KAKAO_REST_API_KEY, ODSAY_API_KEY, available_districts, coalesce_constraints, recommend, search_workplaces, to_number

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
MERGE_BUNDLE_DIR = BASE_DIR / "website_merge_bundle"
MERGE_FRONTEND_DIR = MERGE_BUNDLE_DIR / "frontend"
if str(MERGE_BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(MERGE_BUNDLE_DIR))
jinja_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html", "xml"]))
TRANSIT_PREFLIGHT_CACHE = None
from backend.src.candidate_service import CandidateService, SearchFilters as SiteReviewSearchFilters  # type: ignore

site_review_service = CandidateService()


def parse_json_param(query: dict, name: str, default):
    raw = query.get(name, [""])[0].strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def route_source_distribution(payload: dict) -> dict:
    counts = {}
    seen = set()
    for item in payload.get("recommendations") or []:
        key = (item.get("listing_key"), "recommendation")
        if key in seen:
            continue
        seen.add(key)
        source = str(item.get("route_source") or "UNKNOWN")
        counts[source] = counts.get(source, 0) + 1
    for item in payload.get("debug") or []:
        key = (item.get("listing_key"), item.get("status"))
        if key in seen:
            continue
        seen.add(key)
        source = str(item.get("route_source") or "UNKNOWN")
        counts[source] = counts.get(source, 0) + 1
    return counts


def get_odsay_preflight_status() -> str:
    global TRANSIT_PREFLIGHT_CACHE
    if TRANSIT_PREFLIGHT_CACHE is None:
        try:
            TRANSIT_PREFLIGHT_CACHE = run_preflight()
        except Exception as exc:
            TRANSIT_PREFLIGHT_CACHE = {"status": f"PREFLIGHT_ERROR:{exc.__class__.__name__}"}
    return str((TRANSIT_PREFLIGHT_CACHE or {}).get("status") or "NOT_RUN")


def get_transit_preflight_provider() -> str:
    global TRANSIT_PREFLIGHT_CACHE
    if TRANSIT_PREFLIGHT_CACHE is None:
        get_odsay_preflight_status()
    return str((TRANSIT_PREFLIGHT_CACHE or {}).get("provider") or "UNKNOWN")


def _serve_binary_file(handler: BaseHTTPRequestHandler, path: Path) -> None:
    if not path.exists() or not path.is_file():
        handler.send_error(HTTPStatus.NOT_FOUND)
        return
    content_type, _ = mimetypes.guess_type(path.name)
    body = path.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", content_type or "application/octet-stream")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class AppHandler(BaseHTTPRequestHandler):
    def _json_response(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html: str, status: int = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            _serve_binary_file(self, MERGE_FRONTEND_DIR / "index.html")
            return
        if parsed.path == "/housing.html":
            template = jinja_env.get_template("index2.html")
            self._html_response(
                template.render(
                    districts=available_districts(),
                    kakao_js_key=KAKAO_JS_KEY,
                    default_transport_mode=DEFAULT_TRANSPORT_MODE,
                )
            )
            return
        if parsed.path == "/site-review.html":
            _serve_binary_file(self, MERGE_FRONTEND_DIR / "site-review.html")
            return
        if parsed.path.startswith("/css/"):
            _serve_binary_file(self, MERGE_FRONTEND_DIR / parsed.path.lstrip("/"))
            return
        if parsed.path.startswith("/js/"):
            _serve_binary_file(self, MERGE_FRONTEND_DIR / parsed.path.lstrip("/"))
            return
        if parsed.path.startswith("/assets/"):
            _serve_binary_file(self, MERGE_FRONTEND_DIR / parsed.path.lstrip("/"))
            return
        if parsed.path == "/api/recommendations":
            query = parse_qs(parsed.query)
            workplace_address = query.get("workplace_address", [""])[0].strip()
            workplace_lat = query.get("workplace_lat", [""])[0].strip()
            workplace_lng = query.get("workplace_lng", [""])[0].strip()
            query_text = query.get("query_text", [""])[0].strip()
            if query_text:
                try:
                    extracted = parse_query_with_gemini(query_text)
                except Exception:
                    extracted = parse_query_text(query_text)
            else:
                extracted = {}
            extracted = coalesce_constraints(extracted, query)
            base_summary_state = {
                "hard_constraints": {
                    "deposit_max": to_number(query.get("deposit_max", [""])[0], None) if query.get("deposit_max", [""])[0].strip() else None,
                    "rent_max": to_number(query.get("rent_max", [""])[0], None) if query.get("rent_max", [""])[0].strip() else None,
                    "max_commute_minutes": int(to_number(query.get("max_commute_minutes", [""])[0], None)) if query.get("max_commute_minutes", [""])[0].strip() else None,
                },
                "workplace": {
                    "address": workplace_address,
                } if workplace_address else None,
            }
            extracted["final_applied_conditions"] = build_summary_conditions(query_text, extracted, base_state=base_summary_state, include_base_filter=True)
            if workplace_lat and workplace_lng:
                extracted["workplace"] = {
                    "lat": float(workplace_lat),
                    "lng": float(workplace_lng),
                    "address": workplace_address,
                    "source": "selected-workplace",
                }
            if not workplace_address:
                self._json_response({"error": "직장 또는 학교 주소를 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            missing_fields = find_missing_fields(extracted, require_transport_mode=False)
            if missing_fields:
                self._json_response({"workplace": None, "recommendations": [], "debug": [], "meta": {"message": build_follow_up_question(missing_fields), "need_more_info": True, "missing_fields": missing_fields, "warnings": [], "total_candidates": 0, "transport_mode": extracted.get("transport_mode"), "parsed_query": {"query_text": query_text, "transport_mode": extracted.get("transport_mode"), "deposit_max": extracted.get("deposit_max"), "rent_max": extracted.get("rent_max"), "max_commute_minutes": extracted.get("max_commute_minutes"), "transport": extracted.get("transport", {}), "car_time_profile": extracted.get("car_time_profile", {}), "geo_constraints": extracted.get("geo_constraints", {}), "tradeoff_policy": extracted.get("tradeoff_policy", {}), "must_have": extracted.get("must_have", []), "nice_to_have": extracted.get("nice_to_have", []), "used_llm": bool(GEMINI_API_KEY)}}})
                return
            try:
                payload = recommend(workplace_address, extracted)
            except Exception as exc:
                self._json_response(
                    {
                        "workplace": None,
                        "recommendations": [],
                        "debug": [],
                        "meta": {
                            "message": f"추천 계산 중 오류가 발생했습니다: {exc}",
                            "warnings": [],
                            "total_candidates": 0,
                            "transport_mode": extracted.get("transport_mode"),
                        },
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            payload["meta"]["parsed_query"] = {
                "query_text": query_text,
                "transport_mode": extracted.get("transport_mode"),
                "deposit_max": extracted.get("deposit_max"),
                "rent_max": extracted.get("rent_max"),
                "max_commute_minutes": extracted.get("max_commute_minutes"),
                "transport": extracted.get("transport", {}),
                "car_time_profile": extracted.get("car_time_profile", {}),
                "car_route_direction": (extracted.get("car_time_profile", {}) or {}).get("route_direction"),
                "car_route_time_basis": (extracted.get("car_time_profile", {}) or {}).get("time_basis"),
                "selected_car_time": (extracted.get("car_time_profile", {}) or {}).get("selected_car_time"),
                "soft_preferences": extracted.get("soft_preferences", {}),
                "geo_constraints": extracted.get("geo_constraints", {}),
                "tradeoff_policy": extracted.get("tradeoff_policy", {}),
                "living_preferences": extracted.get("living_preferences", {}),
                "unsupported_preferences": extracted.get("unsupported_preferences", []),
                "must_have": extracted.get("must_have", []),
                "nice_to_have": extracted.get("nice_to_have", []),
                "final_applied_conditions": extracted.get("final_applied_conditions", []),
                "confidence": extracted.get("confidence"),
                "needs_clarification": extracted.get("needs_clarification", []),
                "summary_conditions": extracted.get("summary_conditions", []),
                "used_llm": bool(GEMINI_API_KEY),
            }
            payload["meta"]["route_source_distribution"] = route_source_distribution(payload)
            payload["meta"]["odsay_preflight_status"] = get_odsay_preflight_status()
            payload["meta"]["transit_preflight_status"] = get_odsay_preflight_status()
            payload["meta"]["transit_preflight_provider"] = get_transit_preflight_provider()
            with_llm = query.get("with_llm", ["true"])[0].strip().lower() != "false"
            if with_llm:
                try:
                    payload = enrich_recommendations_with_llm(payload, extracted)
                except Exception:
                    payload.setdefault("meta", {})
                    payload["meta"]["used_llm_for_explanations"] = False
            payload.setdefault("meta", {})
            payload["meta"]["route_source_distribution"] = route_source_distribution(payload)
            payload["meta"]["odsay_preflight_status"] = get_odsay_preflight_status()
            payload["meta"]["transit_preflight_status"] = get_odsay_preflight_status()
            payload["meta"]["transit_preflight_provider"] = get_transit_preflight_provider()
            self._json_response(payload)
            return
        if parsed.path == "/api/site-review/options":
            self._json_response(site_review_service.get_options())
            return
        if parsed.path == "/api/site-review/map-overview":
            self._json_response(site_review_service.get_overview_map())
            return
        if parsed.path == "/api/parse-query":
            query_text = parse_qs(parsed.query).get("q", [""])[0].strip()
            if not query_text:
                self._json_response({
                    "parsed": {
                        "transport_mode": None,
                        "deposit_max": None,
                        "rent_max": None,
                        "max_commute_minutes": None,
                        "geo_constraints": {},
                        "tradeoff_policy": {},
                        "living_preferences": {},
                        "unsupported_preferences": [],
                        "must_have": [],
                        "nice_to_have": [],
                        "confidence": None,
                        "needs_clarification": [],
                        "summary_conditions": [],
                    },
                    "missing_fields": [],
                    "message": "자연어 조건을 입력해 주세요.",
                    "used_llm": bool(GEMINI_API_KEY),
                })
                return
            try:
                extracted = parse_query_with_gemini(query_text)
            except Exception:
                extracted = parse_query_text(query_text)
            missing_fields = find_missing_fields(extracted, require_transport_mode=False)
            self._json_response({"parsed": extracted, "missing_fields": missing_fields, "message": build_follow_up_question(missing_fields) if missing_fields else "", "used_llm": bool(GEMINI_API_KEY)})
            return
        if parsed.path == "/api/chat-turn":
            query = parse_qs(parsed.query)
            current_state = {
                "transport_mode": query.get("transport_mode", [""])[0].strip() or None,
                "deposit_max": to_number(query.get("deposit_max", [""])[0], None) if query.get("deposit_max", [""])[0].strip() else None,
                "rent_max": to_number(query.get("rent_max", [""])[0], None) if query.get("rent_max", [""])[0].strip() else None,
                "max_commute_minutes": int(to_number(query.get("max_commute_minutes", [""])[0], None)) if query.get("max_commute_minutes", [""])[0].strip() else None,
                "transport": parse_json_param(query, "transport_json", {}),
                "car_time_profile": parse_json_param(query, "car_time_profile_json", {}),
                "car_route_direction": query.get("car_route_direction", [""])[0].strip() or None,
                "car_route_time_basis": query.get("car_route_time_basis", [""])[0].strip() or None,
                "selected_car_time": query.get("selected_car_time", [""])[0].strip() or None,
                "soft_preferences": parse_json_param(query, "soft_preferences_json", {}),
                "route_preferences": parse_json_param(query, "route_preferences_json", {}),
                "geo_constraints": parse_json_param(query, "geo_constraints_json", {}),
                "tradeoff_policy": parse_json_param(query, "tradeoff_policy_json", {}),
                "living_preferences": parse_json_param(query, "living_preferences_json", {}),
                "unsupported_preferences": parse_json_param(query, "unsupported_preferences_json", []),
                "hard_constraints": {
                    "deal_type": query.get("deal_type", [""])[0].strip() or None,
                },
                "conversation_flags": {
                    "deposit_decided": query.get("deposit_decided", ["false"])[0].strip().lower() == "true",
                    "rent_decided": query.get("rent_decided", ["false"])[0].strip().lower() == "true",
                },
                "deposit_decided": query.get("deposit_decided", ["false"])[0].strip().lower() == "true",
                "rent_decided": query.get("rent_decided", ["false"])[0].strip().lower() == "true",
            }
            payload = chat_turn_with_gemini(query.get("workplace_name", [""])[0].strip(), query.get("workplace_address", [""])[0].strip(), current_state, query.get("message", [""])[0].strip(), REQUEST_TIMEOUT)
            self._json_response(payload)
            return
        if parsed.path == "/api/workplace-search":
            keyword = parse_qs(parsed.query).get("q", [""])[0].strip()
            if not keyword:
                self._json_response({"results": []})
                return
            try:
                self._json_response({"results": search_workplaces(keyword)})
            except Exception as exc:
                self._json_response({"error": f"직장 검색에 실패했습니다: {exc}"}, HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path == "/api/config":
            self._json_response({
                "has_kakao_js_key": bool(KAKAO_JS_KEY),
                "has_kakao_rest_key": bool(KAKAO_REST_API_KEY),
                "has_odsay_key": bool(ODSAY_API_KEY),
                "has_gemini_key": bool(GEMINI_API_KEY),
                "gemini_model": GEMINI_MODEL,
            })
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/site-review/explore":
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                payload_data = json.loads(raw_body or "{}")
            except Exception:
                payload_data = {}
            try:
                filters_data = payload_data.get("filters") or {}
                filters = SiteReviewSearchFilters(
                    districts=filters_data.get("districts") or [],
                    candidate_scope=filters_data.get("candidate_scope") or "both",
                    station_scope=filters_data.get("station_scope") or "include_conditional",
                    min_area_sqm=filters_data.get("min_area_sqm"),
                    merge_preference=filters_data.get("merge_preference") or "include",
                    policy_need_filter=filters_data.get("policy_need_filter") or "keep",
                    worker_market_filter=filters_data.get("worker_market_filter") or "keep",
                )
                query = str(payload_data.get("query") or "")
                top_k = int(payload_data.get("top_k") or 5)
                self._json_response(site_review_service.explore(query, filters, top_k=top_k))
                return
            except Exception as exc:
                self._json_response({"error": f"site-review explore failed: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"Serving MVP at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
