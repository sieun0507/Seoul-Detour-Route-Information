"""우회·무정차 이벤트: 실시간 감지와 시뮬레이션을 같은 구조로 다룬다."""
from dataclasses import dataclass

from . import api
from .route import BUS, SUBWAY

BUS_DETOUR = "bus_detour"     # 버스 노선 우회 운행 (미정차 정류소 모름)
BUS_SKIP = "bus_skip"         # 버스가 특정 정류소에 서지 않음
SUBWAY_SKIP = "subway_skip"   # 지하철이 특정 역에 서지 않음
LAST_TRAIN = "last_train"     # 지하철 운행 종료(막차 이후·첫차 전) — 시간표 기준
BUS_LAST = "bus_last"         # 버스 승차 정류소의 운행 시간 밖(막차 이후·첫차 전) — 도착 정보의 첫차·막차 기준
# 같은 노선의 다른 정류소·역으로 바꿔도 해결되지 않는 이벤트 (대안을 만들지 않는다)
NO_ALTERNATIVE = (LAST_TRAIN, BUS_LAST)

REAL, SIMULATED, ESTIMATED, TIMETABLE = "실시간", "시뮬레이션", "실시간 추정", "시간표"


@dataclass(frozen=True)
class Event:
    kind: str
    line: str          # 버스 번호("163") 또는 지하철 호선("5호선")
    stop: str = ""     # 정류소/역 이름. BUS_DETOUR는 비어 있음
    source: str = SIMULATED

    @property
    def label(self):
        if self.kind == BUS_DETOUR:
            return f"[{self.source}] {self.line}번 버스 우회 운행 (미정차 정류소 미확인)"
        if self.kind == BUS_SKIP:
            return f"[{self.source}] {self.line}번 버스 {self.stop} 미정차"
        if self.kind == LAST_TRAIN:
            return f"[{self.source}] {self.line} {self.stop}역 운행 종료 (막차 이후 또는 첫차 전)"
        if self.kind == BUS_LAST:
            return f"[{self.source}] {self.line}번 {self.stop} 정류소 운행 시간 외 (막차 이후 또는 첫차 전)"
        return f"[{self.source}] {self.line} {self.stop}역 무정차 통과"

    def hits(self, seg):
        if self.kind in (SUBWAY_SKIP, LAST_TRAIN):
            return seg.mode == SUBWAY and seg.name == self.line
        return seg.mode == BUS and seg.name == self.line


@dataclass(frozen=True)
class Issue:
    seg_index: int     # route.segments 안의 위치
    where: str         # "start"(승차) / "end"(하차) / "all"(노선 전체)
    event: Event

    @property
    def text(self):
        e = self.event
        if self.where == "all":
            return f"{e.line}번 버스 우회 운행 중 — 미정차 정류소 미확인 ({e.source})"
        action = "승차" if self.where == "start" else "하차"
        if e.kind == LAST_TRAIN:
            return f"{e.line} {e.stop}역 운행 종료 — {action} 불가 ({e.source})"
        if e.kind == BUS_LAST:
            return f"{e.line}번 {e.stop} 정류소 운행 시간 외 — {action} 불가 ({e.source})"
        what = f"{e.line}번 {e.stop} 정류소 미정차" if e.kind == BUS_SKIP else f"{e.line} {e.stop}역 무정차"
        return f"{what} — {action} 불가 ({e.source})"


def detect_bus_detours(routes):
    """후보 경로에 포함된 버스 노선의 deTourAt(11: 우회)을 확인한다."""
    found = set()
    checked = set()
    for r in routes:
        for seg in r.rides:
            if seg.mode != BUS or not seg.ars_id or (seg.ars_id, seg.name) in checked:
                continue
            checked.add((seg.ars_id, seg.name))
            try:
                items = api.bus_arrivals(seg.ars_id)
            except api.ApiError:
                continue
            if any(i.get("rtNm") == seg.name and i.get("deTourAt") == "11" for i in items):
                found.add(Event(BUS_DETOUR, seg.name, source=REAL))
    return found


def issues_for(route, events):
    """경로가 받는 영향 목록. 서지 않는 정류소/역이 타거나 내리는(갈아타는) 곳일 때만 문제다.
    중간에 지나가기만 하는 곳이면 그대로 타고 가면 된다."""
    issues = []
    for i, seg in enumerate(route.segments):
        for ev in events:
            if not ev.hits(seg):
                continue
            if ev.kind == BUS_DETOUR:
                issues.append(Issue(i, "all", ev))
            elif ev.stop == seg.start:
                issues.append(Issue(i, "start", ev))
            elif ev.stop == seg.end:
                issues.append(Issue(i, "end", ev))
    return issues
