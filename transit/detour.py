"""같은 노선을 살리는 대안 경로: 서지 않는 곳 대신 가까운 정류소/역에서 타거나 내린다."""
import copy

from .events import BUS_SKIP, SUBWAY_SKIP, issues_for
from .route import BUS, WALK, Segment
from .walking import measure


def _walk_next_to(route, i, side):
    """i번째 구간의 앞(side=-1)/뒤(+1) 도보 구간. 없으면 새로 끼워 넣는다."""
    j = i + side
    if 0 <= j < len(route.segments) and route.segments[j].mode == WALK:
        return route.segments[j]
    seg = route.segments[i]
    point = seg.stops[0] if side < 0 else seg.stops[-1]
    walk = Segment(WALK, 0, 0, start=point[0], end=point[0], stops=[("", *point[1:]), ("", *point[1:])])
    route.segments.insert(i if side < 0 else i + 1, walk)
    return walk


def _shift(route, i, where, skipped):
    """i번째 구간의 승차(start)/하차(end) 지점을 서는 곳까지 옮긴다. 성공하면 안내 문구."""
    seg = route.segments[i]
    n = len(seg.stops)
    if where == "start":
        candidates = range(1, n - 1)            # 한 정류소 더 가서 타기
    else:
        candidates = range(n - 2, 0, -1)        # 한 정류소 일찍 내리기
    k = next((k for k in candidates if seg.stops[k][0] not in skipped), None)
    if k is None:
        return None

    old, new = (seg.stops[0], seg.stops[k]) if where == "start" else (seg.stops[-1], seg.stops[k])
    per_stop = seg.minutes / (n - 1)
    moved = k if where == "start" else n - 1 - k
    ride_saved = round(per_stop * moved)

    walk = _walk_next_to(route, i, -1 if where == "start" else +1)
    walk_before = walk.minutes
    if where == "start":
        seg.stops, seg.stop_ars = seg.stops[k:], seg.stop_ars[k:]
        seg.start, seg.start_exit = new[0], ()
        seg.ars_id = seg.stop_ars[0] if seg.stop_ars else ""
        walk.stops[-1], walk.end = ("", *new[1:]), new[0]
    else:
        seg.stops, seg.stop_ars = seg.stops[:k + 1], seg.stop_ars[:k + 1]
        seg.end, seg.exit_no, seg.end_exit = new[0], "", ()
        walk.stops[0], walk.start = ("", *new[1:]), new[0]
    seg.distance_m *= (len(seg.stops) - 1) / (n - 1)
    seg.minutes = max(1, seg.minutes - ride_saved)
    measure(walk)  # 바뀐 도보 구간을 실제 걷는 길로 다시 계산
    extra_min = walk.minutes - walk_before
    route.total_min += extra_min - ride_saved

    action = "다음 정류소 " + new[0] + "에서 승차" if where == "start" else "이전 정류소 " + new[0] + "에서 하차"
    if seg.mode != BUS:
        action = action.replace("정류소", "역")
    return f"{seg.name} {old[0]} 미정차 → {action} (도보 {walk.distance_m:.0f}m, 걷는 시간 +{extra_min}분)"


def same_line_variant(route, events):
    """영향받는 경로를 같은 노선 그대로, 타고 내리는 곳만 바꿔서 살린다. 불가능하면 None."""
    v = copy.deepcopy(route)
    for _ in range(6):  # 여러 곳이 동시에 막힌 경우 하나씩 해결
        issues = issues_for(v, events)
        if not issues:
            v.issues = []
            return v
        issue = issues[0]
        if issue.where == "all":
            return None  # 미정차 정류소를 모르는 노선 우회는 피할 수밖에 없다
        seg = v.segments[issue.seg_index]
        skipped = {e.stop for e in events if e.hits(seg) and e.kind in (SUBWAY_SKIP, BUS_SKIP)}
        note = _shift(v, issue.seg_index, issue.where, skipped)
        if note is None:
            return None
        v.notes.append(note)
    return None
