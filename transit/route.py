"""ODsay 응답을 구간 단위 공통 구조로 바꾸고, 첫 승차 지점의 실시간 대기 시간을 붙인다."""
import re
from dataclasses import dataclass, field

from . import api

WALK, SUBWAY, BUS = "walk", "subway", "bus"
TRAFFIC_TYPE = {1: SUBWAY, 2: BUS, 3: WALK}


@dataclass
class Segment:
    mode: str
    minutes: int
    distance_m: float
    name: str = ""            # 노선명: "9호선", "163"
    start: str = ""
    end: str = ""
    stops: list = field(default_factory=list)  # [(이름, x, y)] — 지도 표시용
    stop_ars: list = field(default_factory=list)  # 버스 정류소 번호 (stops와 같은 순서)
    stop_ids: list = field(default_factory=list)  # 지하철 역 ID (ODsay stationID, stops와 같은 순서)
    ars_id: str = ""          # 버스 승차 정류소 번호
    route_id: str = ""        # 버스 노선 ID(서울시)
    bus_id: int = 0           # 버스 노선 ID(ODsay) — 노선 모양 조회용
    subway_code: int = 0
    way_code: int = 0         # 지하철 방향: 1 상행·내선, 2 하행·외선 (ODsay wayCode)
    exit_no: str = ""         # 지하철 하차 출구
    start_exit: tuple = ()    # 지하철 승차 출구 좌표 (x, y)
    end_exit: tuple = ()      # 지하철 하차 출구 좌표 (x, y)
    interval: int = 0         # 배차 간격(분)
    path: list = field(default_factory=list)   # 도보: 실제 걷는 길 [(x, y)]
    steps: list = field(default_factory=list)  # 도보: 한국어 길 안내


@dataclass
class Route:
    total_min: int
    payment: int
    segments: list
    wait_min: float | None = None   # 첫 승차 실시간 대기
    wait_msg: str = ""
    realtime_total: float | None = None
    issues: list = field(default_factory=list)  # 우회·무정차 영향 (events.Issue)
    notes: list = field(default_factory=list)   # 같은 노선 대안으로 바꾼 내용
    walks_measured: bool = False                 # 도보 구간을 실제 길로 계산했는지

    @property
    def rides(self):
        return [s for s in self.segments if s.mode != WALK]

    @property
    def summary(self):
        return " → ".join(f"{s.name}" for s in self.rides)

    @property
    def sort_key(self):
        """실시간 예상 시간. 실시간 정보가 없으면 배차 간격의 절반을 평균 대기로 더한다."""
        if self.realtime_total is not None:
            return self.realtime_total
        rides = self.rides
        return self.total_min + (rides[0].interval / 2 if rides else 0)


def parse_route(path, origin, dest):
    """ODsay path 하나를 Route로 변환. origin/dest는 (x, y)."""
    segments = []
    for sp in path["subPath"]:
        mode = TRAFFIC_TYPE[sp["trafficType"]]
        seg = Segment(mode, sp["sectionTime"], sp["distance"], interval=sp.get("intervalTime", 0) or 0)
        if mode != WALK:
            lane = sp["lane"][0]
            seg.start, seg.end = sp["startName"], sp["endName"]
            seg.stops = [(s["stationName"], float(s["x"]), float(s["y"]))
                         for s in sp["passStopList"]["stations"]]
            seg.stop_ars = [s.get("arsID", "").replace("-", "") for s in sp["passStopList"]["stations"]]
            seg.stop_ids = [str(s.get("stationID", "")) for s in sp["passStopList"]["stations"]]
            if mode == BUS:
                seg.name = lane["busNo"]
                seg.ars_id = sp.get("startArsID", "").replace("-", "")
                seg.route_id = lane.get("busLocalBlID", "")
                seg.bus_id = lane.get("busID", 0)
            else:
                seg.name = lane["name"].replace("수도권 ", "")
                seg.subway_code = lane["subwayCode"]
                seg.way_code = sp.get("wayCode", 0)
                seg.exit_no = sp.get("endExitNo", "")
                if sp.get("startExitX"):
                    seg.start_exit = (float(sp["startExitX"]), float(sp["startExitY"]))
                if sp.get("endExitX"):
                    seg.end_exit = (float(sp["endExitX"]), float(sp["endExitY"]))
        segments.append(seg)

    # 도보 구간은 좌표가 없으므로 앞뒤 구간(또는 출발/도착지)으로 채운다. 지하철은 출구 좌표를 쓴다.
    for i, seg in enumerate(segments):
        if seg.mode != WALK:
            continue
        prev = (segments[i - 1].end_exit or segments[i - 1].stops[-1][1:]) if i > 0 else origin
        nxt = (segments[i + 1].start_exit or segments[i + 1].stops[0][1:]) if i + 1 < len(segments) else dest
        seg.stops = [("", *prev), ("", *nxt)]
        seg.start = segments[i - 1].end if i > 0 else "출발지"
        seg.end = segments[i + 1].start if i + 1 < len(segments) else "도착지"

    info = path["info"]
    return Route(info["totalTime"], info.get("payment", 0), segments)


# ---------- 실시간 대기 시간 ----------

def _parse_seconds(msg):
    """'8분 20초 후', '3분후[2번째 전]', '곧 도착' 같은 문구를 초로."""
    if any(w in msg for w in ("곧 도착", "도착", "진입", "출발")) and "분" not in msg:
        return 30
    m = re.search(r"(\d+)분(?:\s*(\d+)초)?", msg)
    return int(m.group(1)) * 60 + int(m.group(2) or 0) if m else None


def bus_wait(seg):
    """[(대기초, 메시지)] 와 우회 여부(deTourAt)."""
    items = [i for i in api.bus_arrivals(seg.ars_id) if i.get("rtNm") == seg.name]
    if not items:
        return [], False
    it = items[0]
    waits = []
    for n in (1, 2):
        msg = it.get(f"arrmsg{n}", "")
        sec = _parse_seconds(msg)
        if sec is not None and "운행종료" not in msg:
            waits.append((sec, msg))
    return waits, it.get("deTourAt") == "11"


def subway_wait(seg):
    """다음 역 방향으로 오는 열차만 골라 [(대기초, 메시지)]."""
    rows = api.subway_arrivals(seg.start)
    if 1 <= seg.subway_code <= 9:
        rows = [r for r in rows if r["subwayId"] == f"100{seg.subway_code}"]
    if len(seg.stops) > 1:
        nxt = seg.stops[1][0]
        same_dir = [r for r in rows if f"{nxt}방면" in r["trainLineNm"]]
        rows = same_dir or rows
    waits = []
    for r in rows:
        sec = int(r["barvlDt"]) if r.get("barvlDt", "0") != "0" else _parse_seconds(r["arvlMsg2"])
        if sec is not None:
            waits.append((sec, f"{r['trainLineNm']} {r['arvlMsg2']}"))
    return sorted(waits), False


def attach_realtime(route):
    """첫 승차 구간의 실시간 도착을 반영해 realtime_total을 계산한다.
    출발지에서 승차 지점까지 걷는 동안 떠나는 차는 탈 수 없으므로 건너뛴다."""
    route.wait_min, route.wait_msg, route.realtime_total = None, "", None
    rides = route.rides
    if not rides:
        return route
    first = rides[0]
    walk_before = sum(s.minutes for s in route.segments[:route.segments.index(first)])
    try:
        waits, detour = bus_wait(first) if first.mode == BUS else subway_wait(first)
    except api.ApiError:
        return route
    catchable = [(sec, msg) for sec, msg in waits if sec / 60 >= walk_before]
    if not catchable:
        route.wait_msg = "실시간 정보 없음" if not waits else "도보 중 출발 — 다음 차 정보 없음"
        return route
    sec, msg = catchable[0]
    route.wait_min = sec / 60
    route.wait_msg = msg
    route.realtime_total = max(walk_before, route.wait_min) + (route.total_min - walk_before)
    return route
