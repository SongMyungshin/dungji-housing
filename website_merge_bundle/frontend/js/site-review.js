const POLICY_COLORS = {
  "청년안심주택형 유리": "#5B6CFF",
  "조건부 개발 가능": "#2A9D8F",
  "리모델링 가능": "#F4A261",
  "필지결합형": "#E76FAD",
  "기타 후보지": "#8A94A6",
};

const DEFAULT_KAKAO_MAP_JS_KEY = "89d5a8b6ef1bc8512e595bc9ffa22608";
const FILTER_DEFAULTS = Object.freeze({
  districts: [],
  candidate_scope: "both",
  station_scope: "include_conditional",
  min_area_sqm: null,
  merge_preference: "include",
  policy_need_filter: "keep",
  worker_market_filter: "keep",
});

const state = {
  options: null,
  overview: null,
  response: null,
  selectedId: null,
  lastQuery: "",
  map: null,
  mapMode: null,
  mapLayerGroup: null,
  mapFeatureIndex: new Map(),
  mapObjects: [],
  kakaoLoaderPromise: null,
  mapFallbackReason: "",
};

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function $(id) {
  return document.getElementById(id);
}

function normalizeId(value) {
  return value == null ? "" : String(value);
}

function currentFilters() {
  const districtSelect = $("districtSelect");
  const selectedDistricts = Array.from(districtSelect.selectedOptions).map((option) => option.value);
  return {
    districts: selectedDistricts,
    candidate_scope: $("candidateScope").value,
    station_scope: $("stationScope").value,
    min_area_sqm: $("minArea").value ? Number($("minArea").value) : null,
    merge_preference: $("mergePreference").value,
    policy_need_filter: $("policyNeedFilter").value,
    worker_market_filter: $("workerMarketFilter").value,
  };
}

function normalizeFiltersForCompare(filters = {}) {
  return {
    districts: [...(filters.districts || [])].sort(),
    candidate_scope: filters.candidate_scope || FILTER_DEFAULTS.candidate_scope,
    station_scope: filters.station_scope || FILTER_DEFAULTS.station_scope,
    min_area_sqm: filters.min_area_sqm == null || filters.min_area_sqm === "" ? null : Number(filters.min_area_sqm),
    merge_preference: filters.merge_preference || FILTER_DEFAULTS.merge_preference,
    policy_need_filter: filters.policy_need_filter || FILTER_DEFAULTS.policy_need_filter,
    worker_market_filter: filters.worker_market_filter || FILTER_DEFAULTS.worker_market_filter,
  };
}

function filtersDifferFromDefaults(filters = currentFilters()) {
  const left = normalizeFiltersForCompare(filters);
  const right = normalizeFiltersForCompare(FILTER_DEFAULTS);
  return JSON.stringify(left) !== JSON.stringify(right);
}

function applyFiltersToForm(filters = FILTER_DEFAULTS) {
  const normalized = normalizeFiltersForCompare(filters);
  const districts = new Set(normalized.districts);
  Array.from($("districtSelect").options).forEach((option) => {
    option.selected = districts.has(option.value);
  });
  $("candidateScope").value = normalized.candidate_scope;
  $("stationScope").value = normalized.station_scope;
  $("minArea").value = normalized.min_area_sqm == null ? "" : String(normalized.min_area_sqm);
  $("mergePreference").value = normalized.merge_preference;
  $("policyNeedFilter").value = normalized.policy_need_filter;
  $("workerMarketFilter").value = normalized.worker_market_filter;
}

function effectiveFilterTokens(filters = {}) {
  const normalized = normalizeFiltersForCompare(filters);
  const tokens = [];
  const hasManualOverride = filtersDifferFromDefaults(normalized);

  if (normalized.districts.length) {
    tokens.push(normalized.districts.join(", "));
  } else if (hasManualOverride) {
    tokens.push("서울 전체");
  }

  const stationLabels = {
    core_only: "역세권 250m",
    include_conditional: "역세권 350m",
    all: "생활권 전체",
  };
  tokens.push(stationLabels[normalized.station_scope] || "역세권 350m");

  if (normalized.candidate_scope === "land") {
    tokens.push("신규 개발형");
  } else if (normalized.candidate_scope === "building") {
    tokens.push("기존 건축물 활용형");
  }

  if (normalized.min_area_sqm != null) {
    tokens.push(`최소 면적 ${normalized.min_area_sqm}㎡`);
  }

  if (normalized.merge_preference === "exclude") {
    tokens.push("필지결합 제외");
  } else if (normalized.merge_preference === "merge_only") {
    tokens.push("필지결합형만");
  } else if (normalized.merge_preference === "include" && hasManualOverride) {
    tokens.push("필지결합 포함");
  }

  if (normalized.policy_need_filter === "high") {
    tokens.push("청년수요 높음");
  } else if (normalized.policy_need_filter === "high_or_medium") {
    tokens.push("청년수요 높음/보통");
  }

  if (normalized.worker_market_filter === "high") {
    tokens.push("직장수요 높음");
  } else if (normalized.worker_market_filter === "high_or_medium") {
    tokens.push("직장수요 높음/보통");
  }

  return tokens;
}

function renderAppliedConditionSummary(filters = null, { forceVisible = false } = {}) {
  const summary = $("activeConditionSummary");
  const textNode = $("activeConditionText");
  const tokens = filters ? effectiveFilterTokens(filters) : [];

  if ((!filters || !filtersDifferFromDefaults(filters)) && !forceVisible) {
    summary.classList.add("hidden");
    textNode.textContent = "";
    return;
  }

  textNode.textContent = tokens.length ? tokens.join(" · ") : "현재 적용된 추가 조건이 없습니다.";
  summary.classList.remove("hidden");
}

function renderOptions(options) {
  state.options = options;
  const districtSelect = $("districtSelect");
  districtSelect.innerHTML = "";
  (options.districts || []).forEach((district) => {
    const option = document.createElement("option");
    option.value = district;
    option.textContent = district;
    districtSelect.appendChild(option);
  });
}

function setFilterDrawerOpen(isOpen) {
  $("filterDrawer").classList.toggle("is-open", isOpen);
  $("filterDrawerBackdrop").classList.toggle("hidden", !isOpen);
  $("filterDrawer").setAttribute("aria-hidden", String(!isOpen));
  document.body.classList.toggle("drawer-open", isOpen);
}

async function resetToOverviewState() {
  state.response = null;
  state.selectedId = null;
  state.lastQuery = "";
  renderIdleCandidateState();
  renderDetail(null);
  await renderMap();
}

function tagClass(label = "") {
  if (label.includes("높음") || label.includes("우선")) return "green";
  if (label.includes("검토") || label.includes("조건부") || label.includes("보통")) return "orange";
  if (label.includes("낮음")) return "gray";
  return "";
}

function renderConditionCards(
  items = [],
  statusText = "질문을 해석해 검토 조건을 정리했습니다.",
  {
    open = false,
    emptyText = "아직 해석된 조건이 없습니다. 검색을 실행하면 자세한 기준이 정리됩니다.",
  } = {},
) {
  const container = $("conditionCards");
  container.innerHTML = "";

  if (!items.length) {
    container.innerHTML = `<div class="condition-empty">${emptyText}</div>`;
  } else {
    items.forEach((item) => {
      const card = document.createElement("div");
      card.className = "condition-card";
      card.innerHTML = `
        <div class="condition-label">${item.label}</div>
        <div class="condition-value">${item.value}</div>
      `;
      container.appendChild(card);
    });
  }

  $("aiStatus").textContent = statusText;
  if (open) {
    $("analysisGuide").open = true;
  }
}

function renderCandidateList(candidates = [], emptyText = "자연어 검색이나 검토 조건 적용 후 이 영역에 후보 카드가 표시됩니다.") {
  const container = $("candidateList");
  container.innerHTML = "";

  if (!candidates.length) {
    container.innerHTML = `<div class="candidate-empty">${emptyText}</div>`;
    return;
  }

  candidates.forEach((candidate) => {
    const card = document.createElement("article");
    card.className = `candidate-card${candidate.id === state.selectedId ? " is-active" : ""}`;
    card.dataset.id = candidate.id;
    const tags = (candidate.tags || [])
      .map((tag) => `<span class="tag ${tagClass(tag)}">${tag}</span>`)
      .join("");

    card.innerHTML = `
      <h3 class="candidate-address">📍 ${candidate.address}</h3>
      <div class="candidate-subline">${candidate.station}</div>
      <div class="candidate-tags">${tags}</div>
      <div class="candidate-summary">${candidate.summary}</div>
      <div class="candidate-meta">관리 ID ${candidate.managementId}</div>
    `;
    card.addEventListener("click", () => {
      void selectCandidate(candidate.id);
    });
    container.appendChild(card);
  });
}

function renderIdleCandidateState() {
  const total = Number(state.overview?.candidateCount || 0);
  $("candidateCountText").textContent = total
    ? `서울 전체 ${total}건 후보의 위치를 먼저 지도에서 확인할 수 있습니다.`
    : "초기 지도를 준비하고 있습니다.";
  renderCandidateList([], "자연어 검색이나 검토 조건 적용 후 이 영역에 후보 카드가 표시됩니다.");
}

function renderSearchCandidateState() {
  const candidates = state.response?.candidates || [];
  $("candidateCountText").textContent = candidates.length
    ? `총 ${candidates.length}건을 지도에 표시했습니다. 후보를 클릭하면 검토 결과가 열립니다.`
    : "조건에 맞는 후보가 없습니다.";
  renderCandidateList(candidates, "조건에 맞는 후보가 없습니다.");
}

function getDetailById(candidateId) {
  const normalizedId = normalizeId(candidateId);
  return (
    state.response?.detailById?.[normalizedId]
    || state.overview?.detailById?.[normalizedId]
    || null
  );
}

function renderDetail(detail) {
  const panel = $("detailPanel");
  const container = $("candidateDetail");
  const resultsSection = $("resultsSection");

  if (!detail) {
    panel.classList.remove("is-open");
    resultsSection.classList.remove("has-detail");
    container.className = "detail-empty";
    container.textContent = "후보를 선택하면 이 영역에 검토 결과가 표시됩니다.";
    return;
  }

  resultsSection.classList.add("has-detail");
  panel.classList.add("is-open");
  container.className = "";
  const riskItems = (detail.riskItems || []).map((item) => `<li>${item}</li>`).join("");
  container.innerHTML = `
    <div class="detail-card-head">
      <h3 class="detail-address">📍 ${detail.address}</h3>
      <div class="detail-meta">
        ${detail.station}<br />
        관리 ID ${detail.managementId}
      </div>
    </div>

    <section class="detail-section">
      <h4>정책 적합성</h4>
      <p>${detail.policyFit}</p>
    </section>

    <section class="detail-section">
      <h4>개발 가능성</h4>
      <p>${detail.feasibility}</p>
    </section>

    <section class="detail-section">
      <h4>특별지구 검토</h4>
      <p>${detail.specialZoneReview}</p>
    </section>

    <section class="detail-section">
      <h4>추가 검토 필요</h4>
      <ul>${riskItems || "<li>현재 단계에서는 추가 확인 필요사항이 없습니다.</li>"}</ul>
    </section>

    <section class="detail-section">
      <h4>종합 의견</h4>
      <p>${detail.overall}</p>
    </section>
  `;
}

function currentSearchResultIds() {
  return new Set((state.response?.searchResultIds || []).map((id) => normalizeId(id)).filter(Boolean));
}

function featureId(feature) {
  return normalizeId(feature?.properties?.id);
}

function buildFeatureLookup(features = []) {
  const lookup = new Map();
  features.forEach((feature) => {
    const id = featureId(feature);
    if (id) {
      lookup.set(id, feature);
    }
  });
  return lookup;
}

function getFeaturePosition(feature) {
  const properties = feature?.properties || {};
  const lat = Number(properties.lat);
  const lon = Number(properties.lon);
  if (Number.isFinite(lat) && Number.isFinite(lon)) {
    return { lat, lon };
  }

  if (feature?.type === "marker") {
    const coordinates = feature.geometry?.coordinates || [];
    const markerLon = Number(coordinates[0]);
    const markerLat = Number(coordinates[1]);
    if (Number.isFinite(markerLat) && Number.isFinite(markerLon)) {
      return { lat: markerLat, lon: markerLon };
    }
  }

  return null;
}

function policyColor(policyType = "") {
  return POLICY_COLORS[policyType] || POLICY_COLORS["기타 후보지"];
}

function searchFeatures() {
  return state.response?.mapFeatures || [];
}

function popupTagText(properties = {}) {
  const policyNeed = String(properties.policyNeed || "").trim();
  if (policyNeed) {
    return policyNeed
      .replace("청년 주거 수요", "청년수요")
      .replace("청년 수요", "청년수요")
      .replace(/\s+/g, " ")
      .trim();
  }
  return String(properties.policyType || "후보지").trim();
}

function ensureMapShell() {
  const root = $("siteReviewMap");
  if (root.dataset.ready === "true") return;

  root.innerHTML = `
    <div id="siteReviewMapCanvas" class="map-canvas"></div>
    <div id="mapLegend" class="map-legend">
      <div class="legend-title">후보 유형 범례</div>
      <div class="legend-row"><span class="legend-swatch" style="--swatch:#5B6CFF;"></span>청년안심주택형 유리</div>
      <div class="legend-row"><span class="legend-swatch" style="--swatch:#2A9D8F;"></span>조건부 개발 가능</div>
      <div class="legend-row"><span class="legend-swatch" style="--swatch:#F4A261;"></span>리모델링 가능</div>
      <div class="legend-row"><span class="legend-swatch" style="--swatch:#E76FAD;"></span>필지결합형</div>
      <div class="legend-row"><span class="legend-swatch" style="--swatch:#8A94A6;"></span>기타 후보지</div>
      <div class="legend-divider"></div>
      <div class="legend-row"><span class="legend-swatch"></span>기본 후보 위치 점</div>
      <div class="legend-row"><span class="legend-swatch legend-swatch-outline"></span>후보 필지 경계</div>
      <div class="legend-row"><span class="legend-swatch legend-swatch-ring250"></span>250m 핵심 역세권</div>
      <div class="legend-row"><span class="legend-swatch legend-swatch-ring350"></span>350m 확장 검토권</div>
      <div class="legend-row"><span class="legend-swatch legend-swatch-result"></span>검색 결과 강조</div>
      <div class="legend-row"><span class="legend-swatch legend-swatch-selected"></span>선택 후보 강조</div>
    </div>
  `;

  root.dataset.ready = "true";
}

function renderMapMeta() {
  return;
}

function buildMapInfoHtml(feature) {
  const properties = feature?.properties || {};

  return `
    <div class="map-info-card">
      <div class="map-info-title">📍 ${properties.address || "-"}</div>
      <div class="map-info-line">${properties.station || "-"}</div>
      <div class="map-info-tag" style="--tag-color:${policyColor(properties.policyType)};">
        [${popupTagText(properties)}]
      </div>
    </div>
  `;
}

function popupHtml(feature) {
  return buildMapInfoHtml(feature);
}

async function loadKakaoSdk(appKey) {
  if (window.kakao?.maps) {
    return window.kakao;
  }
  if (state.kakaoLoaderPromise) {
    return state.kakaoLoaderPromise;
  }

  state.kakaoLoaderPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(appKey)}&autoload=false`;
    script.onload = () => {
      window.kakao.maps.load(() => resolve(window.kakao));
    };
    script.onerror = () => {
      state.kakaoLoaderPromise = null;
      reject(new Error("카카오맵 SDK를 불러오지 못했습니다."));
    };
    document.head.appendChild(script);
  });

  return state.kakaoLoaderPromise.catch((error) => {
    state.kakaoLoaderPromise = null;
    throw error;
  });
}

function buildMarkerSvg(color, variant = "base") {
  if (variant === "selected") {
    return `
      <svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 34 34">
        <circle cx="17" cy="17" r="15" fill="${color}" fill-opacity="0.18" />
        <circle cx="17" cy="17" r="11" fill="${color}" stroke="#ffffff" stroke-width="4" />
        <circle cx="17" cy="17" r="4" fill="#ffffff" fill-opacity="0.92" />
      </svg>
    `;
  }

  if (variant === "result") {
    return `
      <svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26">
        <circle cx="13" cy="13" r="11" fill="${color}" fill-opacity="0.18" />
        <circle cx="13" cy="13" r="8" fill="${color}" stroke="#ffffff" stroke-width="4" />
      </svg>
    `;
  }

  return `
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
      <circle cx="8" cy="8" r="6" fill="${color}" stroke="#ffffff" stroke-width="2.5" />
    </svg>
  `;
}

function buildKakaoMarkerImage(color, variant = "base") {
  const size = variant === "selected" ? 34 : variant === "result" ? 26 : 16;
  const svg = buildMarkerSvg(color, variant);
  return new kakao.maps.MarkerImage(
    `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    new kakao.maps.Size(size, size),
    { offset: new kakao.maps.Point(size / 2, size / 2) }
  );
}

function extractKakaoPolygonPaths(feature) {
  const geometry = feature?.geometry || {};
  const coordinates = geometry.coordinates || [];

  const toLatLngPath = (ring) => (
    (ring || [])
      .map((coord) => {
        const lon = Number(coord[0]);
        const lat = Number(coord[1]);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
        return new kakao.maps.LatLng(lat, lon);
      })
      .filter(Boolean)
  );

  if (geometry.type === "Polygon") {
    return coordinates.length ? [toLatLngPath(coordinates[0])] : [];
  }
  if (geometry.type === "MultiPolygon") {
    return coordinates.map((polygon) => toLatLngPath(polygon?.[0] || [])).filter((path) => path.length >= 3);
  }
  return [];
}

function clearKakaoObjects() {
  state.mapObjects.forEach((item) => {
    if (item && typeof item.setMap === "function") {
      item.setMap(null);
    }
  });
  state.mapObjects = [];
}

function extendKakaoBoundsWithRadius(bounds, positionValue, radiusMeters) {
  if (!positionValue) return;

  const latDelta = radiusMeters / 111320;
  const cosLat = Math.cos((positionValue.lat * Math.PI) / 180) || 1;
  const lonDelta = radiusMeters / (111320 * cosLat);
  bounds.extend(new kakao.maps.LatLng(positionValue.lat + latDelta, positionValue.lon + lonDelta));
  bounds.extend(new kakao.maps.LatLng(positionValue.lat - latDelta, positionValue.lon - lonDelta));
}

function drawKakaoStationRanges(feature, positionValue, isSelected, searchBounds, selectedBounds) {
  if (!positionValue) return;

  const center = new kakao.maps.LatLng(positionValue.lat, positionValue.lon);
  const circle250 = new kakao.maps.Circle({
    center,
    radius: 250,
    strokeWeight: isSelected ? 3 : 2,
    strokeColor: "#4F6EF7",
    strokeOpacity: isSelected ? 0.62 : 0.38,
    fillColor: "#4F6EF7",
    fillOpacity: isSelected ? 0.08 : 0.04,
    zIndex: 1,
  });
  circle250.setMap(state.map);
  state.mapObjects.push(circle250);

  const circle350 = new kakao.maps.Circle({
    center,
    radius: 350,
    strokeWeight: isSelected ? 3 : 2,
    strokeColor: "#FF8B3D",
    strokeOpacity: isSelected ? 0.48 : 0.28,
    strokeStyle: "dash",
    fillColor: "#FF8B3D",
    fillOpacity: isSelected ? 0.04 : 0.016,
    zIndex: 1,
  });
  circle350.setMap(state.map);
  state.mapObjects.push(circle350);

  extendKakaoBoundsWithRadius(searchBounds, positionValue, 350);
  if (isSelected) {
    extendKakaoBoundsWithRadius(selectedBounds, positionValue, 350);
  }
}

function drawKakaoSearchPolygon(feature, color, isSelected, searchBounds, selectedBounds, entryBounds) {
  if (feature?.type !== "polygon") return false;

  let drawn = false;
  extractKakaoPolygonPaths(feature).forEach((path) => {
    if (path.length < 3) return;
    drawn = true;

    path.forEach((latLng) => {
      searchBounds.extend(latLng);
      entryBounds?.extend(latLng);
      if (isSelected) {
        selectedBounds.extend(latLng);
      }
    });

    const polygon = new kakao.maps.Polygon({
      path,
      strokeWeight: isSelected ? 4 : 2.5,
      strokeColor: color,
      strokeOpacity: isSelected ? 0.96 : 0.74,
      fillColor: color,
      fillOpacity: isSelected ? 0.26 : 0.12,
      zIndex: isSelected ? 3 : 2,
    });
    polygon.setMap(state.map);
    state.mapObjects.push(polygon);

    kakao.maps.event.addListener(polygon, "click", () => {
      void selectCandidate(featureId(feature));
    });
  });

  return drawn;
}

function ensureLeafletMap() {
  if (state.mapMode === "leaflet" && state.map) {
    return state.map;
  }

  state.mapMode = "leaflet";
  state.map = L.map("siteReviewMapCanvas", {
    zoomControl: true,
    attributionControl: true,
  }).setView([37.5665, 126.978], 11);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(state.map);

  state.mapLayerGroup = L.layerGroup().addTo(state.map);
  return state.map;
}

function resetLeafletMapIfNeeded() {
  if (state.mapMode === "leaflet" && state.map && typeof state.map.remove === "function") {
    state.map.remove();
    state.map = null;
    state.mapLayerGroup = null;
  }
}

async function ensureMap() {
  ensureMapShell();

  if (state.mapMode === "kakao" && state.map && window.kakao?.maps) {
    state.mapFallbackReason = "";
    return state.map;
  }

  if (state.mapMode === "leaflet" && state.map) {
    return state.map;
  }

  const kakaoAppKey = DEFAULT_KAKAO_MAP_JS_KEY || state.options?.mapConfig?.kakaoAppKey;
  if (kakaoAppKey) {
    try {
      await loadKakaoSdk(kakaoAppKey);
      resetLeafletMapIfNeeded();
      if (!state.map || state.mapMode !== "kakao") {
        state.mapMode = "kakao";
        state.map = new kakao.maps.Map($("siteReviewMapCanvas"), {
          center: new kakao.maps.LatLng(37.5665, 126.978),
          level: 8,
        });
        if (typeof state.map.setCopyrightPosition === "function") {
          state.map.setCopyrightPosition(kakao.maps.CopyrightPosition.BOTTOMRIGHT, true);
        }
      }
      state.mapFallbackReason = "";
      return state.map;
    } catch (error) {
      state.mapFallbackReason = error?.message || "알 수 없는 오류";
      console.warn("Kakao map unavailable, falling back to Leaflet.", error);
    }
  }

  return ensureLeafletMap();
}

function renderKakaoMap() {
  const overviewFeatures = state.overview?.mapFeatures || [];
  const resultFeatures = searchFeatures();
  const responseFeatureLookup = buildFeatureLookup(resultFeatures);
  const searchIds = currentSearchResultIds();
  const selectedId = normalizeId(state.selectedId);

  clearKakaoObjects();
  state.mapFeatureIndex.clear();

  const searchBounds = new kakao.maps.LatLngBounds();
  const selectedBounds = new kakao.maps.LatLngBounds();
  let selectedFeature = null;
  let selectedPosition = null;

  overviewFeatures.forEach((feature) => {
    const properties = feature.properties || {};
    const currentId = normalizeId(properties.id);
    const positionValue = getFeaturePosition(feature);
    if (!currentId || !positionValue) return;

    const resolvedFeature = responseFeatureLookup.get(currentId) || feature;
    const color = policyColor(properties.policyType);
    const isResult = searchIds.has(currentId);
    const isSelected = currentId === selectedId;
    const position = new kakao.maps.LatLng(positionValue.lat, positionValue.lon);
    const focusBounds = new kakao.maps.LatLngBounds();
    focusBounds.extend(position);
    if (isResult) {
      searchBounds.extend(position);
    }

    const markerImage = buildKakaoMarkerImage(color, isSelected ? "selected" : isResult ? "result" : "base");
    const marker = new kakao.maps.Marker({
      position,
      image: markerImage,
      clickable: true,
      zIndex: isSelected ? 5 : isResult ? 4 : 2,
    });
    marker.setMap(state.map);
    state.mapObjects.push(marker);

    kakao.maps.event.addListener(marker, "click", () => {
      void selectCandidate(currentId);
    });

    state.mapFeatureIndex.set(currentId, {
      feature: resolvedFeature,
      position,
      bounds: focusBounds,
      marker,
    });

    if (isSelected) {
      selectedFeature = resolvedFeature;
      selectedPosition = position;
      selectedBounds.extend(position);
    }
  });

  resultFeatures.forEach((feature) => {
    const currentId = featureId(feature);
    if (!currentId) return;

    const entry = state.mapFeatureIndex.get(currentId);
    const properties = feature.properties || {};
    const positionValue = getFeaturePosition(feature);
    const color = policyColor(properties.policyType);
    const isSelected = currentId === selectedId;

    drawKakaoStationRanges(feature, positionValue, isSelected, searchBounds, selectedBounds);

    const hasPolygon = drawKakaoSearchPolygon(
      feature,
      color,
      isSelected,
      searchBounds,
      selectedBounds,
      entry?.bounds || null,
    );

    if (entry) {
      entry.feature = feature;
      if (hasPolygon && isSelected) {
        entry.bounds = selectedBounds;
      }
    }

    if (isSelected) {
      selectedFeature = feature;
      if (!selectedPosition && positionValue) {
        selectedPosition = new kakao.maps.LatLng(positionValue.lat, positionValue.lon);
      }
      if (!selectedBounds.isEmpty() && entry) {
        entry.bounds = selectedBounds;
      }
    }
  });

  if (!selectedBounds.isEmpty()) {
    state.map.setBounds(selectedBounds, 110, 110, 110, 110);
  } else if (!searchBounds.isEmpty()) {
    state.map.setBounds(searchBounds, 80, 80, 80, 80);
  } else {
    state.map.setCenter(new kakao.maps.LatLng(37.5665, 126.978));
    state.map.setLevel(8);
  }

  if (selectedFeature && selectedPosition) {
    const overlay = new kakao.maps.CustomOverlay({
      position: selectedPosition,
      content: buildMapInfoHtml(selectedFeature),
      yAnchor: 1.18,
      zIndex: 8,
    });
    overlay.setMap(state.map);
    state.mapObjects.push(overlay);
  }
}

function drawLeafletStationRanges(feature, latLng, isSelected) {
  const circle250 = L.circle(latLng, {
    radius: 250,
    color: "#4F6EF7",
    weight: isSelected ? 3 : 2,
    opacity: isSelected ? 0.62 : 0.38,
    fillColor: "#4F6EF7",
    fillOpacity: isSelected ? 0.08 : 0.04,
  });
  circle250.on("click", () => {
    void selectCandidate(featureId(feature));
  });
  circle250.addTo(state.mapLayerGroup);

  const circle350 = L.circle(latLng, {
    radius: 350,
    color: "#FF8B3D",
    weight: isSelected ? 3 : 2,
    opacity: isSelected ? 0.48 : 0.28,
    dashArray: "8 6",
    fillColor: "#FF8B3D",
    fillOpacity: isSelected ? 0.04 : 0.016,
  });
  circle350.on("click", () => {
    void selectCandidate(featureId(feature));
  });
  circle350.addTo(state.mapLayerGroup);

  return circle350.getBounds();
}

function drawLeafletSearchPolygon(feature, color, isSelected) {
  if (feature?.type !== "polygon") return null;

  const polygonLayer = L.geoJSON(feature.geometry, {
    style: {
      color,
      weight: isSelected ? 4 : 2.5,
      opacity: isSelected ? 0.96 : 0.74,
      fillColor: color,
      fillOpacity: isSelected ? 0.26 : 0.12,
    },
  });
  polygonLayer.on("click", () => {
    void selectCandidate(featureId(feature));
  });
  polygonLayer.addTo(state.mapLayerGroup);
  return polygonLayer;
}

function renderLeafletMap() {
  const overviewFeatures = state.overview?.mapFeatures || [];
  const resultFeatures = searchFeatures();
  const responseFeatureLookup = buildFeatureLookup(resultFeatures);
  const searchIds = currentSearchResultIds();
  const selectedId = normalizeId(state.selectedId);

  state.mapLayerGroup.clearLayers();
  state.mapFeatureIndex.clear();

  let searchBounds = null;
  let selectedLayer = null;

  const extendBounds = (sourceBounds, targetBounds) => {
    if (!sourceBounds) return targetBounds;
    if (!targetBounds) return sourceBounds;
    targetBounds.extend(sourceBounds);
    return targetBounds;
  };

  overviewFeatures.forEach((feature) => {
    const properties = feature.properties || {};
    const currentId = normalizeId(properties.id);
    const positionValue = getFeaturePosition(feature);
    if (!currentId || !positionValue) return;

    const resolvedFeature = responseFeatureLookup.get(currentId) || feature;
    const color = policyColor(properties.policyType);
    const isResult = searchIds.has(currentId);
    const isSelected = currentId === selectedId;
    const latLng = L.latLng(positionValue.lat, positionValue.lon);
    const focusBounds = L.latLngBounds([latLng]);

    const marker = L.circleMarker(latLng, {
      radius: isSelected ? 11 : isResult ? 8 : 5,
      color,
      fillColor: color,
      fillOpacity: isSelected ? 1 : isResult ? 0.94 : 0.58,
      opacity: 1,
      weight: isSelected ? 4 : isResult ? 3 : 1.8,
    });
    marker.on("click", () => {
      void selectCandidate(currentId);
    });
    marker.addTo(state.mapLayerGroup);

    state.mapFeatureIndex.set(currentId, {
      feature: resolvedFeature,
      position: latLng,
      bounds: focusBounds,
      marker,
    });

    if (isResult) {
      searchBounds = extendBounds(focusBounds, searchBounds);
    }
    if (isSelected && !selectedLayer) {
      selectedLayer = marker;
    }
  });

  resultFeatures.forEach((feature) => {
    const currentId = featureId(feature);
    if (!currentId) return;

    const entry = state.mapFeatureIndex.get(currentId);
    const positionValue = getFeaturePosition(feature);
    const color = policyColor(feature.properties?.policyType);
    const isSelected = currentId === selectedId;

    if (positionValue) {
      const rangeBounds = drawLeafletStationRanges(feature, L.latLng(positionValue.lat, positionValue.lon), isSelected);
      searchBounds = extendBounds(rangeBounds, searchBounds);
      if (entry) {
        entry.bounds = entry.bounds.extend(rangeBounds);
      }
    }

    const polygonLayer = drawLeafletSearchPolygon(feature, color, isSelected);
    if (polygonLayer) {
      const polygonBounds = polygonLayer.getBounds();
      searchBounds = extendBounds(polygonBounds, searchBounds);
      if (entry && polygonBounds.isValid()) {
        entry.bounds = entry.bounds.extend(polygonBounds);
      }
      if (isSelected) {
        selectedLayer = polygonLayer;
      }
    }

    if (entry) {
      entry.feature = feature;
      if (isSelected && !selectedLayer) {
        selectedLayer = entry.marker;
      }
    }
  });

  if (state.selectedId && state.mapFeatureIndex.has(state.selectedId)) {
    const selectedFocus = state.mapFeatureIndex.get(state.selectedId);
    if (selectedFocus?.bounds) {
      state.map.fitBounds(selectedFocus.bounds.pad(0.12));
    }
    const popupLayer = selectedLayer || selectedFocus?.marker;
    if (popupLayer?.bindPopup) {
      popupLayer.bindPopup(popupHtml(selectedFocus.feature), {
        autoClose: false,
        closeButton: false,
        offset: [0, -8],
      }).openPopup();
    }
  } else if (searchBounds) {
    state.map.fitBounds(searchBounds.pad(0.1));
  } else {
    state.map.setView([37.5665, 126.978], 11);
  }

  state.map.invalidateSize();
}

async function renderMap() {
  await ensureMap();
  renderMapMeta();

  if (state.mapMode === "kakao") {
    renderKakaoMap();
    return;
  }

  renderLeafletMap();
}

function syncCandidatePanels() {
  if (state.response) {
    renderSearchCandidateState();
  } else {
    renderIdleCandidateState();
  }

  if (state.selectedId) {
    renderDetail(getDetailById(state.selectedId));
  } else {
    renderDetail(null);
  }
}

async function selectCandidate(candidateId) {
  state.selectedId = normalizeId(candidateId);
  syncCandidatePanels();
  await renderMap();
}

async function runExplore({ queryOverride = null, allowEmptyQuery = false } = {}) {
  const query = (queryOverride ?? $("aiQuery").value ?? "").trim();
  const filters = currentFilters();
  const hasFilterOverride = filtersDifferFromDefaults(filters);
  if (!query && !allowEmptyQuery && !hasFilterOverride) return;

  state.selectedId = null;
  renderDetail(null);

  renderConditionCards([], "검토 조건을 정리하고 있습니다...", {
    emptyText: "질문을 분석하는 중입니다. 잠시만 기다려주세요.",
  });

  const payload = {
    query,
    top_k: 5,
    filters,
  };

  try {
    const response = await fetchJSON("/api/site-review/explore", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    state.response = response;
    state.selectedId = null;
    state.lastQuery = query;

    renderConditionCards(
      response.interpretedConditions || [],
      response.usedGemini
        ? "규칙 해석과 설명 보강을 반영해 검토 조건을 정리했습니다."
        : "규칙 해석을 바탕으로 검토 조건을 정리했습니다."
    );
    renderAppliedConditionSummary(response.effectiveFilters || filters, {
      forceVisible: hasFilterOverride,
    });
    syncCandidatePanels();
    $("resultsSection").classList.remove("hidden");
    await renderMap();
  } catch (error) {
    renderConditionCards([], `검색 실패: ${error.message}`, {
      open: true,
      emptyText: "검색 요청을 완료하지 못했습니다. 입력 조건과 서버 상태를 다시 확인해주세요.",
    });
  }
}

async function init() {
  const [options, overview] = await Promise.all([
    fetchJSON("/api/site-review/options"),
    fetchJSON("/api/site-review/map-overview"),
  ]);

  state.options = options;
  state.overview = overview;

  renderOptions(options);
  applyFiltersToForm(FILTER_DEFAULTS);
  renderAppliedConditionSummary(null);
  renderIdleCandidateState();
  renderDetail(null);
  $("resultsSection").classList.remove("hidden");
  await renderMap();
}

$("aiExploreForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await runExplore();
});

$("advancedFilters").addEventListener("submit", (event) => {
  event.preventDefault();
});

$("filterDrawerOpen").addEventListener("click", () => {
  setFilterDrawerOpen(true);
});

$("filterDrawerClose").addEventListener("click", () => {
  setFilterDrawerOpen(false);
});

$("filterDrawerBackdrop").addEventListener("click", () => {
  setFilterDrawerOpen(false);
});

$("filterResetButton").addEventListener("click", async () => {
  applyFiltersToForm(FILTER_DEFAULTS);
  setFilterDrawerOpen(false);
  const query = $("aiQuery").value.trim() || state.lastQuery;
  const shouldSearch = Boolean(query);
  renderAppliedConditionSummary(null);
  if (shouldSearch) {
    await runExplore({ queryOverride: query, allowEmptyQuery: false });
  } else {
    await resetToOverviewState();
  }
});

$("filterApplyButton").addEventListener("click", async () => {
  const filters = currentFilters();
  const query = $("aiQuery").value.trim() || state.lastQuery || "";
  const shouldSearch = Boolean(query) || filtersDifferFromDefaults(filters) || Boolean(state.response);
  setFilterDrawerOpen(false);
  renderAppliedConditionSummary(filters, {
    forceVisible: filtersDifferFromDefaults(filters),
  });

  if (shouldSearch) {
    await runExplore({
      queryOverride: query,
      allowEmptyQuery: filtersDifferFromDefaults(filters),
    });
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setFilterDrawerOpen(false);
  }
});

init().catch((error) => {
  renderConditionCards([], `초기화 실패: ${error.message}`, {
    open: true,
    emptyText: "초기 데이터를 불러오지 못했습니다. 서버 실행 상태를 다시 확인해주세요.",
  });
});
