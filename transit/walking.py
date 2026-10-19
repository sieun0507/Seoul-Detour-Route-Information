"""도보 구간을 실제 걷는 길(OpenRouteService)로 계산하고 한국어 안내를 만든다."""
import math

from . import api
from .route import WALK

WALK_M_PER_MIN = 66   # ODsay 도보 구간 실측 평균(분당 60~70m, 신호 대기 포함)
DETOUR_FACTOR = 1.3   # API를 못 쓸 때: 직선거리 → 실제 걷는 거리 보정
SAME_PLACE_M = 30     # 이보다 가까우면 같은 곳(역 안 환승 등)으로 보고 그대로 둔다

ACTIONS = {0: "좌회전", 1: "우회전", 2: "크게 좌회전", 3: "크게 우회전", 4: "왼쪽 방향",
           5: "오른쪽 방향", 6: "직진", 7: "회전교차로 진입", 8: "회전교차로 진출",
           9: "유턴", 12: "왼쪽 길 유지", 13: "오른쪽 길 유지"}


def straight_m(a, b):
    """두 (x, y) 지점의 직선거리(m)."""
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((a[0] - b[0]) * 111320 * math.cos(lat), (a[1] - b[1]) * 110540)


def step_text(step):
    # "홍지문길, 홍지문길"처럼 같은 이름이 반복되는 경우가 있다
    name = ", ".join(dict.fromkeys(n for n in step["name"].split(", ") if n not in ("", "-")))
    dist = f"{step['distance']:.0f}m"
    if step["type"] == 11:
        return f"출발 — {name + ' 방향으로 ' if name else ''}{dist}"
    if step["type"] == 10:
        return "도착"
    action = ACTIONS.get(step["type"], "이동")
    return f"{name + ' 쪽으로 ' if name else ''}{action} → {dist}"


def measure(seg):
    """도보 구간의 거리·시간·길·안내를 양 끝점 기준으로 다시 계산한다."""
    a, b = seg.stops[0][1:], seg.stops[-1][1:]
    if straight_m(a, b) < SAME_PLACE_M:
        return
    try:
        w = api.ors_walk(round(a[0], 6), round(a[1], 6), round(b[0], 6), round(b[1], 6))
        seg.distance_m = w["distance"]
        seg.path = [tuple(c) for c in w["coords"]]
        seg.steps = [step_text(s) for s in w["steps"] if s["distance"] >= 1 or s["type"] == 10]
    except api.ApiError:
        seg.distance_m = straight_m(a, b) * DETOUR_FACTOR
        seg.path, seg.steps = [], []
    seg.minutes = max(1, math.ceil(seg.distance_m / WALK_M_PER_MIN))


def enrich(route):
    """경로의 모든 도보 구간을 실제 길 기준으로 바꾸고 총 소요 시간을 고친다. 한 번만 한다."""
    if route.walks_measured:
        return route
    for seg in route.segments:
        if seg.mode == WALK and not seg.path:
            before = seg.minutes
            measure(seg)
            route.total_min += seg.minutes - before
    route.walks_measured = True
    return route
