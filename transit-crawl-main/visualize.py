"""
transit_routes.geojson → folium 지도로 시각화
- 경로별 탭(LayerControl)으로 전환
- 수단별 색상: 지하철=파랑, 버스=초록, 도보=회색 점선
- 승하차 지점 마커 + 팝업
"""
import json, folium
from folium.plugins import Fullscreen
from crawl_transit import get_transit_routes

MODE_STYLE = {
    "지하철": {"color": "#1f6feb", "weight": 6, "opacity": 0.9, "dash_array": None},
    "버스":   {"color": "#2ea043", "weight": 6, "opacity": 0.9, "dash_array": None},
    "도보":   {"color": "#8b949e", "weight": 3, "opacity": 0.9, "dash_array": "6,6"},
}

def build_map(routes, origin, dest, out="transit_map.html"):
    # 지도 중심 = 출발·도착 중간
    center = [(origin[1] + dest[1]) / 2, (origin[0] + dest[0]) / 2]
    m = folium.Map(location=center, zoom_start=12, tiles="CartoDB positron")
    Fullscreen().add_to(m)

    # 출발/도착 마커
    folium.Marker(origin[::-1], tooltip="출발",
                  icon=folium.Icon(color="red", icon="play", prefix="fa")).add_to(m)
    folium.Marker(dest[::-1], tooltip="도착",
                  icon=folium.Icon(color="blue", icon="flag-checkered", prefix="fa")).add_to(m)

    for r in routes:
        label = f"경로{r['순위']} · {r['유형']} · {r['소요시간']} · {r['요금']} · 환승{r['환승횟수']}"
        fg = folium.FeatureGroup(name=label, show=(r["순위"] == 1))

        for seg in r["구간"]:
            coords = seg["geometry"]["coordinates"]
            if not coords:
                continue
            style = MODE_STYLE[seg["수단"]]
            # folium PolyLine은 (lat,lon) 순서
            latlon = [(c[1], c[0]) for c in coords]

            popup_html = (
                f"<b>경로 {r['순위']}</b><br>"
                f"{seg['수단']} {seg.get('노선명') or ''}<br>"
                f"{seg['승차']} → {seg['하차']}<br>"
                f"{seg['소요시간'] or ''} {seg['거리'] or ''}"
            )
            folium.PolyLine(
                latlon,
                color=style["color"],
                weight=style["weight"],
                opacity=style["opacity"],
                dash_array=style["dash_array"],
                tooltip=f"{seg['수단']} {seg.get('노선명') or ''}",
                popup=folium.Popup(popup_html, max_width=300),
            ).add_to(fg)

            # 승하차 지점 작은 원 마커 (도보 제외)
            if seg["수단"] != "도보":
                for name, pt in [(seg["승차"], latlon[0]), (seg["하차"], latlon[-1])]:
                    if name:
                        folium.CircleMarker(
                            pt, radius=5, color=style["color"], fill=True,
                            fill_color="white", fill_opacity=1, weight=2,
                            tooltip=name,
                        ).add_to(fg)

        fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # 간단 범례
    legend = """
    <div style="position:fixed; bottom:30px; left:30px; z-index:1000;
         background:white; padding:10px 14px; border:1px solid #ccc;
         border-radius:6px; font:13px sans-serif; line-height:1.7;">
      <b>수단</b><br>
      <span style="display:inline-block;width:20px;height:4px;background:#1f6feb;vertical-align:middle;"></span> 지하철<br>
      <span style="display:inline-block;width:20px;height:4px;background:#2ea043;vertical-align:middle;"></span> 버스<br>
      <span style="display:inline-block;width:20px;height:0;border-top:3px dashed #8b949e;vertical-align:middle;"></span> 도보
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    m.save(out)
    return out


if __name__ == "__main__":
    origin = (127.028, 37.498)    # 강남역
    dest   = (126.9247, 37.5579)  # 홍대입구
    routes = get_transit_routes(*origin, *dest, top_n=3)

    print(f"경로 {len(routes)}개 수집")
    for r in routes:
        seg_pts = sum(len(s["geometry"]["coordinates"]) for s in r["구간"])
        print(f"  [{r['순위']}] {r['유형']:15s} {r['소요시간']:6s} {r['요금']:8s} "
              f"환승{r['환승횟수']}회 · 구간 {len(r['구간'])}개 · 총 {seg_pts}pt")

    out = build_map(routes, origin, dest)
    print(f"\n지도 저장: {out}")
