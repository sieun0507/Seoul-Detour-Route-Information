"""실시간 우회 길찾기 — 길찾기 + 버스 우회·지하철 무정차 반영 화면.
실행: .venv\\Scripts\\streamlit run app.py
"""
import time

import folium
import streamlit as st
from streamlit_folium import st_folium

from transit import api
from transit.events import (BUS_DETOUR, BUS_SKIP, ESTIMATED, REAL, SUBWAY_SKIP, Event,
                            detect_bus_detours)
from transit.monitor import CONFIRMED, ICON, SUSPECT, RouteMonitor, build_line
from transit.bus_hours import bus_service_events
from transit.last_train import last_train_events
from transit.planner import make_plan, search_routes
from transit.route import BUS, SUBWAY, WALK
from transit.subway_monitor import SubwayMonitor, monitor_for

MONITOR_ROUTES = 4    # 실시간 점검할 경로 수 (빠른 순)
MONITOR_LINES = 6     # 실시간 점검할 버스 노선 최대 개수 (점검마다 노선당 API 1회)
MONITOR_SUBWAY = 3    # 실시간 점검할 지하철 구간 최대 개수 (점검마다 호선당 서울시 API 1회)
AUTO_POLL_SECONDS = 60

SUBWAY_COLORS = {1: "#0052A4", 2: "#00A84D", 3: "#EF7C1C", 4: "#00A5DE", 5: "#996CAC",
                 6: "#CD7C2F", 7: "#747F00", 8: "#E6186C", 9: "#BDB092"}
MODE_ICON = {WALK: "🚶", SUBWAY: "🚇", BUS: "🚌"}

st.set_page_config(page_title="실시간 우회 길찾기", page_icon="🚏", layout="wide")
st.title("🚏 실시간 우회 길찾기")


def search_place(query):
    """카카오 장소 검색, 실패하면 ODsay 역/정류장 검색으로 대체."""
    try:
        return api.kakao_search(query), None
    except api.ApiError as e:
        try:
            return api.odsay_station_search(query), f"{e} → 역/정류장 이름 검색으로 대체합니다."
        except api.ApiError as e2:
            return [], f"{e} / {e2}"


def place_picker(label, default):
    query = st.text_input(label, default)
    if not query:
        return None
    places, warning = search_place(query)
    if warning:
        st.caption(f"⚠️ {warning}")
    if not places:
        st.error("검색 결과가 없습니다.")
        return None
    idx = st.selectbox(f"{label} 선택", range(len(places)),
                       format_func=lambda i: f"{places[i]['name']} · {places[i]['address']}")
    return places[idx]


def segment_line(seg):
    icon = MODE_ICON[seg.mode]
    if seg.mode == WALK:
        if seg.minutes == 0:
            return None
        if seg.start == seg.end:
            return f"{icon} {seg.start}역 환승 (도보 {seg.minutes}분)"
        return f"{icon} 도보 {seg.minutes}분 ({seg.distance_m:.0f}m) — {seg.start} → {seg.end}"
    stops = len(seg.stops) - 1
    extra = f", {seg.exit_no}번 출구" if seg.exit_no else ""
    label = seg.name if seg.mode == SUBWAY else f"{seg.name}번"
    return f"{icon} **{label}** {seg.start} → {seg.end} ({stops}개 역/정류장, {seg.minutes}분{extra})"


def draw_map(route, origin, dest, monitors=None):
    m = folium.Map(location=[(origin["y"] + dest["y"]) / 2, (origin["x"] + dest["x"]) / 2],
                   zoom_start=13)
    for seg in route.segments:
        points = [(y, x) for _, x, y in seg.stops]
        if len(points) < 2:
            continue
        if seg.mode == WALK:
            if seg.path:  # 실제 걷는 길
                points = [(y, x) for x, y in seg.path]
            folium.PolyLine(points, color="#555", weight=4, dash_array="2 8" if seg.path else "6 6",
                            tooltip=f"도보 {seg.minutes}분 ({seg.distance_m:.0f}m)").add_to(m)
            continue
        color = SUBWAY_COLORS.get(seg.subway_code, "#555") if seg.mode == SUBWAY else "#3D7BE0"
        folium.PolyLine(points, color=color, weight=6, opacity=0.85, tooltip=seg.name).add_to(m)
        folium.CircleMarker(points[0], radius=6, color=color, fill=True, fill_color="white",
                            tooltip=f"승차: {seg.start}").add_to(m)
    # 이 경로의 버스 노선에서 운행 중인 버스 (빨강: 노선 이탈)
    for seg in route.rides:
        mon = (monitors or {}).get(seg.route_id)
        if mon is None:
            continue
        for veh, obs in mon.tracks.items():
            o = obs[-1]
            folium.CircleMarker((o.y, o.x), radius=5, color="#D33" if o.off else "#2A2",
                                fill=True, fill_opacity=0.9,
                                tooltip=f"{seg.name}번 {'노선 이탈 ' + str(round(o.dist)) + 'm' if o.off else '정상 운행'}"
                                ).add_to(m)
    folium.Marker((origin["y"], origin["x"]), tooltip=f"출발: {origin['name']}",
                  icon=folium.Icon(color="green", icon="play")).add_to(m)
    folium.Marker((dest["y"], dest["x"]), tooltip=f"도착: {dest['name']}",
                  icon=folium.Icon(color="red", icon="flag")).add_to(m)
    m.fit_bounds([[s[2], s[1]] for seg in route.segments for s in seg.stops])
    return m


def compare(route, usual):
    diff = route.sort_key - usual.sort_key
    if diff >= 0.5:
        return f"+{diff:.0f}분 늦어집니다"
    if diff <= -0.5:
        return f"{-diff:.0f}분 빨라집니다"
    return "시간 차이가 거의 없습니다"


def route_title(r):
    rt = f"실시간 {r.realtime_total:.0f}분" if r.realtime_total is not None else f"예상 {r.sort_key:.0f}분(배차 기준)"
    mark = "🔁 " if r.notes else ""
    return f"{mark}{rt} (기본 {r.total_min}분) · {r.summary}"


def show_route(r):
    st.markdown(f"**첫 승차 실시간:** {r.wait_msg or '-'}  \n**요금:** {r.payment:,}원")
    for seg in r.segments:
        line = segment_line(seg)
        if not line:
            continue
        if seg.steps:
            items = "".join(f"<li>{t}</li>" for t in seg.steps)
            st.markdown(f"- {line}<details><summary>🧭 길 안내</summary><ol>{items}</ol></details>",
                        unsafe_allow_html=True)
        else:
            st.markdown(f"- {line}")
    for note in r.notes:
        st.info(f"🔁 같은 노선 유지: {note}")


def simulation_panel(routes):
    """사이드바: 실시간 감지 결과 + 시연용 시뮬레이션 이벤트 선택."""
    st.sidebar.header("🚨 운행 상황")
    with st.sidebar:
        with st.spinner("버스 우회 여부 확인 중..."):
            real = detect_bus_detours(routes[:10])
    if real:
        for ev in real:
            st.sidebar.error(ev.label)
    else:
        st.sidebar.success("실시간: 후보 경로의 버스 우회 없음")

    st.sidebar.subheader("🧪 시뮬레이션")
    sim = set()

    # 버스: 노선을 고르고, 그 노선에서 서지 않을 정류소를 고른다
    bus_stops = {}  # 버스 번호 → 후보 경로에 나오는 정류소 (노선 순서)
    for r in routes:
        for s in r.rides:
            if s.mode == BUS:
                stops = bus_stops.setdefault(s.name, [])
                stops += [n for n, _, _ in s.stops if n not in stops]
    for bus in st.sidebar.multiselect("우회 운행할 버스", sorted(bus_stops)):
        skipped = st.sidebar.multiselect(f"{bus}번이 서지 않는 정류소", bus_stops[bus],
                                         help="비워두면 '미정차 정류소 미확인'으로 노선 전체를 피합니다.")
        if skipped:
            sim |= {Event(BUS_SKIP, bus, stop) for stop in skipped}
        else:
            sim.add(Event(BUS_DETOUR, bus))

    # 지하철: 같은 역도 호선별로 무정차가 다를 수 있으니 (역, 호선)으로 고른다
    stations = sorted({(n, s.name) for r in routes for s in r.rides if s.mode == SUBWAY
                       for n in (s.start, s.end)})
    for name, line in st.sidebar.multiselect("무정차 통과할 지하철역", stations,
                                             format_func=lambda x: f"{x[0]} ({x[1]})"):
        sim.add(Event(SUBWAY_SKIP, line, name))
    return real | sim


# ---------- 버스 노선 실시간 점검 ----------

def setup_monitors(routes):
    """빠른 경로들의 버스 노선마다 점검기를 만든다 (노선 모양은 하루 캐싱)."""
    monitors = {}
    for r in sorted(routes, key=lambda r: r.total_min)[:MONITOR_ROUTES]:
        for seg in r.rides:
            if (seg.mode != BUS or not seg.bus_id or not seg.route_id
                    or seg.route_id in monitors or len(monitors) >= MONITOR_LINES):
                continue
            try:
                monitors[seg.route_id] = RouteMonitor(build_line(seg))
            except (api.ApiError, KeyError):
                continue
    return monitors


def setup_subway_monitors(routes):
    """빠른 경로들의 지하철 구간마다 점검기를 만든다. ODsay를 새로 부르지 않고 경로에 받아둔 역 정보만 쓴다."""
    monitors = {}
    for r in sorted(routes, key=lambda r: r.total_min)[:MONITOR_ROUTES]:
        for seg in r.rides:
            mon = monitor_for(seg)
            if mon is None or len(monitors) >= MONITOR_SUBWAY:
                continue
            key = f"sub:{seg.name}:{seg.stop_ids[0]}-{seg.stop_ids[-1]}"
            monitors.setdefault(key, mon)
    return monitors


def poll_all(monitors):
    for mon in monitors.values():
        try:
            mon.poll()
        except api.ApiError:
            pass


@st.fragment(run_every=AUTO_POLL_SECONDS)
def auto_poll():
    """자동 점검이 켜져 있으면 1분마다 버스 위치를 새로 받고 화면 전체를 갱신한다."""
    monitors = st.session_state.get("monitors")
    if not (monitors and st.session_state.get("auto_poll")):
        return
    last = min((m.last_poll for m in monitors.values()), default=0)
    if time.time() - last >= AUTO_POLL_SECONDS - 5:
        poll_all(monitors)
        st.rerun()


def monitor_events(monitors, events):
    """점검 결과를 이벤트로 바꾼다. 미정차 정류장을 찾은 노선은 '노선 전체 우회'를 정류장 단위로 대체."""
    official = {e.line for e in events if e.kind == BUS_DETOUR and e.source == REAL}
    found = set()
    for mon in monitors.values():
        if isinstance(mon, SubwayMonitor):
            found |= {Event(SUBWAY_SKIP, mon.line, stop, ESTIMATED) for stop in mon.skipped_stops()}
            continue
        mon.official = mon.line.bus_no in official
        found |= {Event(BUS_SKIP, mon.line.bus_no, stop, ESTIMATED) for stop in mon.skipped_stops()}
    pinned = {e.line for e in found}
    kept = {e for e in events if not (e.kind == BUS_DETOUR and e.source == REAL and e.line in pinned)}
    return kept | found


def monitor_panel(monitors, shown_routes):
    """사용자 경로의 버스 정류장 상태 표시."""
    if not monitors:
        return
    last = max(m.last_poll for m in monitors.values())
    with st.expander(f"🛰️ 버스 노선 실시간 점검 — {len(monitors)}개 노선 "
                     f"(마지막 점검 {time.strftime('%H:%M:%S', time.localtime(last))})"):
        st.caption("운행 중인 버스의 GPS가 원래 노선에서 벗어났는지 보고, 건너뛴 정류장을 찾습니다. "
                   "✅ 정상  ⚠️ 미정차 의심(1대)  ❌ 미정차 확인(2대 이상)  ❔ 지나간 버스 없음")
        mine = {}  # 버스 번호 → 경로에서 타고 내리는 정류장 [(이름, 정류소번호, 역할)]
        for r in shown_routes:
            for seg in r.rides:
                if seg.mode == BUS:
                    places = mine.setdefault(seg.name, [])
                    for name, ars, role in ((seg.start, seg.stop_ars[0] if seg.stop_ars else "", "승차"),
                                            (seg.end, seg.stop_ars[-1] if seg.stop_ars else "", "하차")):
                        if (name, role) not in [(p[0], p[2]) for p in places]:
                            places.append((name, ars, role))
        for mon in monitors.values():
            if isinstance(mon, SubwayMonitor):
                st.markdown(f"**🚇 {mon.line}** — 관측 열차 {len(mon.tracks)}대")
                probs = mon.problems()
                if probs:
                    st.markdown("&nbsp;&nbsp;무정차 의심 역: " + ", ".join(
                        f"{ICON[s.state]} {n} ({s.skipped_by}대)" for n, s in probs))
                continue
            bus = mon.line.bus_no
            off = len(mon.off_route_buses())
            flag = " · 🚧 공식 우회 표시" if mon.official else ""
            st.markdown(f"**🚌 {bus}번** — 운행 버스 {len(mon.tracks)}대, 노선 이탈 {off}대{flag}")
            parts = []
            for name, ars, role in mine.get(bus, []):
                st_ = mon.status_of(name, ars)
                if st_:
                    parts.append(f"{role} {name} {ICON[st_.state]} {st_.state}")
            if parts:
                st.markdown("&nbsp;&nbsp;내 정류장: " + " · ".join(parts))
            problems = [(mon.line.stops[i].name, s) for i, s in mon.stop_status().items()
                        if s.state in (SUSPECT, CONFIRMED)]
            if problems:
                st.markdown("&nbsp;&nbsp;미정차 의심 구간: " + ", ".join(
                    f"{ICON[s.state]} {n} ({s.skipped_by}대)" for n, s in problems))


# ---------- 입력 ----------
col1, col2 = st.columns(2)
with col1:
    origin = place_picker("출발지", "선유도역")
with col2:
    dest = place_picker("도착지", "상명대학교 서울캠퍼스")

if st.button("길찾기", type="primary", disabled=not (origin and dest)):
    try:
        st.session_state.search = (search_routes(origin, dest), origin, dest)
        with st.spinner("버스 노선 정보 준비 중..."):
            st.session_state.monitors = (setup_monitors(st.session_state.search[0])
                                         | setup_subway_monitors(st.session_state.search[0]))
            poll_all(st.session_state.monitors)
    except api.ApiError as e:
        st.error(f"{e}\n\nODsay에 현재 컴퓨터의 공인 IP가 등록되어 있는지 확인하세요.")
        st.stop()

# ---------- 결과 ----------
if "search" in st.session_state:
    routes, origin, dest = st.session_state.search
    events = simulation_panel(routes)
    monitors = st.session_state.get("monitors", {})
    st.sidebar.subheader("🛰️ 실시간 점검")
    if st.sidebar.button("지금 점검", disabled=not monitors):
        poll_all(monitors)
    st.sidebar.toggle(f"{AUTO_POLL_SECONDS}초마다 자동 점검", key="auto_poll",
                      help="공공데이터 API 일일 호출 한도가 있으니 시연할 때만 켜세요.")
    auto_poll()
    events = monitor_events(monitors, events) | last_train_events(routes) | bus_service_events(routes)
    for ev in events:
        if ev.source == ESTIMATED:
            st.sidebar.warning(ev.label)
    with st.spinner("실시간 도착 정보 확인 중..."):
        plan = make_plan(routes, events, origin, dest)

    st.subheader(f"{origin['name']} → {dest['name']}")
    usual = plan.usual
    if plan.detour_needed:
        st.error("⚠️ 평소 경로가 운행 상황의 영향을 받습니다: " + " / ".join(i.text for i in usual.issues))
        if plan.options:
            best = plan.options[0]
            how = " (같은 노선 유지: " + "; ".join(best.notes) + ")" if best.notes else ""
            st.success(f"🔀 **우회 추천:** {best.summary}{how} — 평소보다 {compare(best, usual)} (실시간 예상 {best.sort_key:.0f}분)"
                       + ("  \n후보가 모두 막혀 경로를 다시 검색했습니다." if plan.researched else ""))
        else:
            st.warning("이용 가능한 경로를 찾지 못했습니다.")
        alt = plan.same_line
        if alt is not None and not (plan.options and plan.options[0].notes):
            st.info(f"🔁 **평소 노선 그대로 이용 시:** {'; '.join(alt.notes)} — 평소보다 {compare(alt, usual)} "
                    f"(실시간 예상 {alt.sort_key:.0f}분)")
    elif events:
        st.info("운행 상황이 있지만 평소 경로는 영향을 받지 않습니다.")

    with st.expander(f"평소 경로: {route_title(usual)}", expanded=not plan.options):
        show_route(usual)

    if plan.options:
        left, right = st.columns([2, 3])
        with left:
            label = "우회 경로 (실시간 예상 시간순)" if plan.detour_needed else "경로 (실시간 예상 시간순)"
            chosen = st.radio(label, range(len(plan.options)),
                              format_func=lambda i: f"{i + 1}. {route_title(plan.options[i])}")
            r = plan.options[chosen]
            show_route(r)
        with right:
            st_folium(draw_map(r, origin, dest, monitors), height=520, use_container_width=True,
                      returned_objects=[])
        monitor_panel(monitors, [usual, r])
