"""평소 경로와 우회 경로를 고른다: 감지 → 판단 → 재탐색."""
from dataclasses import dataclass

from . import api
from .detour import same_line_variant
from .events import NO_ALTERNATIVE, issues_for
from .route import attach_realtime, parse_route
from .walking import enrich

MAX_REALTIME = 6  # 실시간 조회는 경로마다 API를 부르므로 개수 제한


@dataclass
class Plan:
    usual: object        # 이벤트가 없을 때의 1순위 경로
    options: list        # 이용 가능한 경로 (실시간 예상 시간순)
    researched: bool     # 후보가 모두 막혀 다시 검색했는지
    same_line: object = None  # 평소 경로를 같은 노선 그대로 이용하는 대안 (비교용)

    @property
    def detour_needed(self):
        return bool(self.usual.issues)


def search_routes(origin, dest, path_type=0):
    o, d = (origin["x"], origin["y"]), (dest["x"], dest["y"])
    return [parse_route(p, o, d) for p in api.odsay_routes(*o, *d, path_type)]


def _rank(routes):
    top = sorted(routes, key=lambda r: r.total_min)[:MAX_REALTIME]
    for r in top:
        enrich(r)
        attach_realtime(r)
    return sorted(top, key=lambda r: r.sort_key)


def _usable(routes, events):
    """영향 없는 경로는 그대로, 영향받는 경로는 같은 노선 대안으로 살린다."""
    usable = []
    for r in routes:
        r.issues = issues_for(r, events)
        r.notes = []
        if not r.issues:
            usable.append(r)
        elif any(i.event.kind in NO_ALTERNATIVE for i in r.issues):
            continue  # 운행 시간 밖은 같은 노선의 다른 정류소·역으로 바꿔도 해결되지 않는다
        else:
            v = same_line_variant(r, events)
            if v is not None:
                usable.append(v)
    return usable


def make_plan(routes, events, origin, dest):
    for r in routes:
        r.issues = issues_for(r, events)
    usual = sorted(routes[:MAX_REALTIME], key=lambda r: r.total_min)
    usual = _rank(usual)[0]

    options = _usable(routes, events)
    researched = False
    if not options:
        # 기본 후보가 모두 막힘 → 지하철만 / 버스만 경로로 다시 검색
        researched = True
        for path_type in (1, 2):
            try:
                options += _usable(search_routes(origin, dest, path_type), events)
            except api.ApiError:
                continue
    same_line = None
    if usual.issues:
        same_line = same_line_variant(usual, events)
        if same_line is not None:
            enrich(same_line)
            attach_realtime(same_line)
    return Plan(usual, _rank(options), researched, same_line)
