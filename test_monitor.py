"""미정차 정류장 추정 정확도 테스트 (가짜 GPS 궤적).

직선 노선(정류장 15개, 400m 간격)에서 일부 정류장을 건너뛰고 옆 도로(400m 떨어짐)로
돌아가는 버스를 만들고, 60초마다 위치를 관측해 추정 결과를 정답과 비교한다.
실행: .venv\\Scripts\\python test_monitor.py
"""
import random

from transit.monitor import CONFIRMED, SUSPECT, M_PER_DEG_X, M_PER_DEG_Y, BusLine, RouteMonitor

BASE_X, BASE_Y = 126.95, 37.55
N_STOPS, GAP = 15, 400        # 정류장 수, 간격(m)
SPEED = 300                   # 버스 속도(m/분, 약 18km/h)
DETOUR_OFFSET = 400           # 우회 도로가 원래 도로에서 떨어진 거리(m)
GPS_NOISE = 10                # GPS 오차 표준편차(m)
PASS_RADIUS = 30              # 정류장 30m 안을 지나야 '지났다'고 기록 (BIS 방식 가정)


def to_deg(mx, my):
    return BASE_X + mx / M_PER_DEG_X, BASE_Y + my / M_PER_DEG_Y


def make_line():
    stations = [{"stationName": f"정류장{i}", "localStationID": str(1000 + i),
                 "x": to_deg(i * GAP, 0)[0], "y": to_deg(i * GAP, 0)[1]} for i in range(N_STOPS)]
    shape = [(s["x"], s["y"]) for s in stations]
    return BusLine.build("테스트", "TEST", stations, shape)


def bus_path(skip):
    """버스가 실제로 달리는 경로(m 좌표 꼭짓점). skip: 건너뛰는 정류장 순번 집합."""
    if not skip:
        return [(0, 0), ((N_STOPS - 1) * GAP, 0)]
    a, b = (min(skip) - 1) * GAP, (max(skip) + 1) * GAP
    return [(0, 0), (a + 100, 0), (a + 100, DETOUR_OFFSET), (b - 100, DETOUR_OFFSET),
            (b - 100, 0), ((N_STOPS - 1) * GAP, 0)]


def point_at(path, d):
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        seg = abs(x2 - x1) + abs(y2 - y1)
        if d <= seg:
            t = d / seg
            return x1 + t * (x2 - x1), y1 + t * (y2 - y1)
        d -= seg
    return path[-1]


def simulate(skip, n_buses=14, headway=3, minutes=30, official=True, seed=0, warm=20):
    """warm분 전부터 버스를 출발시켜, 관측을 시작할 때 이미 노선 곳곳에 버스가 있게 한다."""
    rng = random.Random(seed)
    line = make_line()
    mon = RouteMonitor(line, official=official)
    path = bus_path(skip)
    last_passed = {}
    for minute in range(-warm, minutes):
        items = []
        for b in range(n_buses):
            # 1분 간격으로 위치를 촘촘히 따라가며 '지난 정류장'을 기록하고, 정각 위치만 관측된다
            for sub in range(10):
                d = (minute + warm + sub / 10 - b * headway) * SPEED
                if d <= 0:
                    break
                x, y = point_at(path, d)
                for i in range(N_STOPS):
                    if abs(x - i * GAP) <= PASS_RADIUS and abs(y) <= PASS_RADIUS:
                        last_passed[b] = max(last_passed.get(b, 0), i)
            d = (minute + warm - b * headway) * SPEED
            if minute < 0 or d <= 0 or d >= (N_STOPS - 1) * GAP + 2 * DETOUR_OFFSET:
                continue  # 아직 관측 전이거나 운행 전/후
            x, y = point_at(path, d)
            x += rng.gauss(0, GPS_NOISE)
            y += rng.gauss(0, GPS_NOISE)
            gx, gy = to_deg(x, y)
            items.append({"vehId": f"bus{b}", "dataTm": str(minute), "isrunyn": "1",
                          "lastStnId": str(1000 + last_passed.get(b, 0)), "gpsX": gx, "gpsY": gy})
        mon.observe(items, now=1_000_000 + minute * 60)
    return mon


def score(mon, truth):
    status = mon.stop_status()
    confirmed = {i for i, s in status.items() if s.state == CONFIRMED}
    flagged = {i for i, s in status.items() if s.state in (CONFIRMED, SUSPECT)}
    def pr(pred):
        tp = len(pred & truth)
        precision = tp / len(pred) if pred else (1.0 if not truth else 0.0)
        recall = tp / len(truth) if truth else 1.0
        return precision, recall
    return confirmed, flagged, pr(confirmed), pr(flagged)


if __name__ == "__main__":
    cases = [
        ("정류장 1곳 미정차", {7}),
        ("정류장 3곳 연속 미정차", {5, 6, 7}),
        ("정류장 5곳 연속 미정차", {4, 5, 6, 7, 8}),
        ("우회 없음 (정상 운행)", set()),
    ]
    print(f"{'시나리오':<22}{'정답':<18}{'확인':<18}{'정밀도/재현율(확인)':<22}{'의심 포함 정밀도/재현율'}")
    for name, truth in cases:
        results = []
        for seed in range(20):  # GPS 오차를 바꿔 20번 반복
            results.append(score(simulate(truth, seed=seed), truth))
        conf, flag, (p1, r1), (p2, r2) = results[0]
        avg = lambda k, j: sum(r[k][j] for r in results) / len(results)
        print(f"{name:<22}{str(sorted(truth)):<18}{str(sorted(conf)):<18}"
              f"{avg(2, 0):.2f} / {avg(2, 1):.2f}{'':<12}{avg(3, 0):.2f} / {avg(3, 1):.2f}")

    # 검색 직후 첫 관측 한 번만으로는 얼마나 알 수 있나
    print("\n[관측 시간에 따른 변화] 정답 {5, 6, 7}")
    for minutes in (1, 2, 3, 5, 10, 20):
        conf, flag, (p1, r1), (p2, r2) = score(simulate({5, 6, 7}, minutes=minutes), {5, 6, 7})
        print(f"  {minutes:>2}분 관측: 확인 {sorted(conf)}, 의심 포함 {sorted(flag)}")
