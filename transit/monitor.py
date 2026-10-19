"""경로 중심 실시간 점검: 사용자 경로의 버스 노선에서 운행 중인 버스가
원래 노선을 벗어났는지 보고, 어느 정류장을 건너뛰는지 판정한다.

판정 원리
- 버스 GPS가 원래 노선(실제 도로 모양)에서 threshold 이상 떨어지면 '이탈'.
- 이탈 직전에 지난 정류장(lastStnId) 다음부터, 노선에 복귀한 뒤 처음 지난 정류장 전까지 = 건너뛴 정류장.
- 아직 이탈 중이면, 버스 위치를 노선에 투영해서 이미 지나친 정류장까지를 '의심'으로 본다.
- 여러 대가 같은 정류장을 건너뛰면 '확인'.
"""
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import api

OK, SUSPECT, CONFIRMED, UNKNOWN = "정상", "미정차 의심", "미정차 확인", "확인 불가"
ICON = {OK: "✅", SUSPECT: "⚠️", CONFIRMED: "❌", UNKNOWN: "❔"}

OFF_ROUTE_M = 150         # 공식 우회(deTourAt) 노선의 이탈 기준
OFF_ROUTE_STRICT_M = 300  # 공식 표시가 없는 노선은 더 엄격하게
KEEP_SECONDS = 30 * 60    # 관측 보관 시간
WINDOW_AHEAD = 8          # 투영할 때 직전 정류장부터 몇 정류장 앞까지 볼지

M_PER_DEG_Y = 110540
M_PER_DEG_X = 111320 * math.cos(math.radians(37.55))  # 서울 위도 기준


def _m(x, y):
    return x * M_PER_DEG_X, y * M_PER_DEG_Y


def _project(p, a, b):
    """점 p를 선분 ab에 투영: (거리, 선분 위 비율 0~1)."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy), t


@dataclass
class Stop:
    idx: int
    name: str
    local_id: str
    ars: str
    x: float
    y: float
    seg: int = 0       # 노선 모양에서 가장 가까운 선분 번호
    along: float = 0   # 노선 시작점부터의 거리(m)


@dataclass
class BusLine:
    """노선 하나의 정류장 순서와 실제 도로 모양."""
    bus_no: str
    route_id: str
    stops: list
    pts: list          # 노선 모양 (m 좌표)
    cum: list          # 각 점까지 누적 거리
    id_to_idx: dict

    @classmethod
    def build(cls, bus_no, route_id, stations, shape):
        """stations: [{stationName, localStationID, x, y}], shape: [(x, y)]"""
        pts = [_m(x, y) for x, y in shape]
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        stops = [Stop(i, s["stationName"], str(s.get("localStationID", "")),
                      str(s.get("arsID", "")).replace("-", ""), float(s["x"]), float(s["y"]))
                 for i, s in enumerate(stations)]
        line = cls(bus_no, route_id, stops, pts, cum, {s.local_id: s.idx for s in stops})
        # 정류장을 순서대로 노선 모양에 붙인다. 왕복 노선이라 반대편 도로에 붙지 않도록 앞으로만 찾는다.
        lo = 0
        for s in stops:
            hi = min(len(pts) - 1, lo + 400)
            _, along, seg = line.project(_m(s.x, s.y), lo, hi)
            s.seg, s.along = seg, along
            lo = seg
        return line

    def project(self, p, lo, hi):
        """선분 lo~hi 범위에서 가장 가까운 지점: (거리, 누적거리, 선분 번호)."""
        best = (float("inf"), 0.0, lo)
        for i in range(lo, max(lo + 1, hi)):
            if i + 1 >= len(self.pts):
                break
            d, t = _project(p, self.pts[i], self.pts[i + 1])
            if d < best[0]:
                seg_len = self.cum[i + 1] - self.cum[i]
                best = (d, self.cum[i] + t * seg_len, i)
        return best


@dataclass
class Obs:
    t: float
    last_idx: int      # 마지막으로 지난 정류장 순번
    off: bool          # 노선 이탈 여부
    dist: float        # 노선까지 거리(m)
    ahead_idx: int     # 노선에 투영했을 때 이미 지난 정류장 순번
    x: float
    y: float


@dataclass
class StopStatus:
    state: str
    skipped_by: int = 0   # 건너뛴(것으로 보이는) 버스 대수
    served_by: int = 0    # 정상적으로 지나간 버스 대수


@dataclass
class RouteMonitor:
    line: BusLine
    official: bool = False          # deTourAt=11 공식 우회 표시 여부
    tracks: dict = field(default_factory=lambda: defaultdict(list))
    seen: set = field(default_factory=set)
    last_poll: float = 0

    @property
    def threshold(self):
        return OFF_ROUTE_M if self.official else OFF_ROUTE_STRICT_M

    def observe(self, items, now=None):
        """버스 위치 목록(getBusPosByRtid itemList 형식)을 관측으로 쌓는다."""
        now = now or time.time()
        n = len(self.line.stops)
        for it in items:
            key = (it.get("vehId"), it.get("dataTm"))
            if key in self.seen or it.get("isrunyn") == "0":
                continue
            self.seen.add(key)
            last = self.line.id_to_idx.get(str(it.get("lastStnId")))
            if last is None or last == 0 or last >= n - 1:
                continue  # 기점/종점(차고지 대기) 근처는 제외
            stops = self.line.stops
            lo = stops[max(last - 1, 0)].seg
            hi = stops[min(last + WINDOW_AHEAD, n - 1)].seg + 1
            p = _m(float(it["gpsX"]), float(it["gpsY"]))
            dist, along, _ = self.line.project(p, lo, hi)
            ahead = last
            for s in stops[last:min(last + WINDOW_AHEAD, n - 1) + 1]:
                if s.along <= along:
                    ahead = s.idx
            self.tracks[it["vehId"]].append(
                Obs(now, last, dist > self.threshold, dist, ahead, float(it["gpsX"]), float(it["gpsY"])))
        for veh in list(self.tracks):
            self.tracks[veh] = [o for o in self.tracks[veh] if now - o.t < KEEP_SECONDS]
            if not self.tracks[veh]:
                del self.tracks[veh]
        self.last_poll = now

    def poll(self):
        self.observe(api.bus_positions(self.line.route_id))

    def off_route_buses(self):
        """지금 이탈 중인 버스: [(vehId, 마지막 관측)]"""
        return [(v, obs[-1]) for v, obs in self.tracks.items() if obs[-1].off]

    def stop_status(self):
        """정류장 순번 → StopStatus"""
        skipped, served = defaultdict(set), defaultdict(set)
        for veh, obs in self.tracks.items():
            start = None
            for o in obs:
                if o.off:
                    if start is None:
                        start = o.last_idx
                    continue
                if start is not None:
                    # 노선에 복귀: 이탈 시작 다음 정류장 ~ 복귀 후 처음 지난 정류장 전까지 건너뜀
                    for s in range(start + 1, o.last_idx):
                        skipped[s].add(veh)
                    start = None
                served[o.last_idx].add(veh)
            if start is not None:
                # 아직 이탈 중: 이미 지나친 정류장(최소한 다음 정류장)을 건너뛴 것으로 본다
                for s in range(start + 1, max(obs[-1].ahead_idx, start + 1) + 1):
                    skipped[s].add(veh)

        result = {}
        for s in self.line.stops:
            k, ok = len(skipped[s.idx]), len(served[s.idx] - skipped[s.idx])
            if k >= 2:
                state = CONFIRMED
            elif k == 1:
                state = SUSPECT
            elif ok:
                state = OK
            else:
                state = UNKNOWN
            result[s.idx] = StopStatus(state, k, ok)
        return result

    def status_of(self, name, ars=""):
        """정류장 이름(가능하면 정류소 번호로 정확히)으로 상태 조회."""
        status = self.stop_status()
        for s in self.line.stops:
            if (ars and s.ars == ars) or (not ars and s.name == name):
                return status[s.idx]
        return None

    def skipped_stops(self):
        """우회 판단에 쓸 미정차 정류장 이름. 공식 우회 노선은 '의심'부터, 아니면 '확인'만."""
        accept = {CONFIRMED, SUSPECT} if self.official else {CONFIRMED}
        status = self.stop_status()
        return [self.line.stops[i].name for i, st in status.items() if st.state in accept]


def build_line(seg):
    """경로의 버스 구간(Segment)으로 노선 정보를 만든다."""
    lane = api.odsay_bus_lane(seg.bus_id)
    stations = lane["station"]
    shape = api.odsay_lane_shape(seg.bus_id, len(stations) - 1)
    return BusLine.build(seg.name, seg.route_id or lane.get("busLocalBlID", ""), stations, shape)
