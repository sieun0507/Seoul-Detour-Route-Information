"""지하철 무정차 감지 정확도 테스트 (가짜 열차 운행).

12개 역 노선에 열차를 일정 간격으로 보내고, 일부 역은 서지 않고 지나가게 한다.
60초마다 실시간 위치를 받는다고 가정하고(API는 열차가 마지막으로 찍힌 역·상태를 준다),
확인(2대 이상) 결과를 정답과 비교한다.

가정 (실제 API 동작은 실제 데이터로 확인이 필요)
- 정차: 도착(1) → 출발(2). 서지 않고 지나갈 때는 진입(0)만 찍힌다.
- 역 간 이동 1.5~2.5분, 정차 체류 0.3~1.0분, 열차 간격 3분.
실행: .venv\\Scripts\\python test_subway_monitor.py
"""
import random

from transit.monitor import CONFIRMED, SUSPECT
from transit.subway_monitor import SubwayMonitor

N_STOPS = 12
POLL = 60          # 초 단위 폴링 간격 (AUTO_POLL_SECONDS와 같음)
HEADWAY = 3.0      # 열차 간격(분)


def make_monitor():
    stations = [(str(100 + i), f"역{i}") for i in range(N_STOPS)]
    return SubwayMonitor("테스트선", "1001", stations)


def timeline(skip, start, rng):
    """열차 한 대의 (시각(분), 역 순번, 상태) 이벤트. 시각 오름차순."""
    t, events = start, []
    for i in range(N_STOPS):
        if i > 0:
            t += rng.uniform(1.5, 2.5)  # 역 간 이동
        if i in skip:
            events.append((t - 0.5, i, "0"))  # 진입만 찍히고 통과
        else:
            events.append((t, i, "1"))        # 도착
            t += rng.uniform(0.3, 1.0)        # 체류
            events.append((t, i, "2"))        # 출발
    return events


def simulate(skip, n_trains=8, seed=0, minutes=40):
    rng = random.Random(seed)
    mon = make_monitor()
    trains = []
    for b in range(n_trains):
        start = b * HEADWAY + rng.uniform(-0.3, 0.3)
        trains.append((b, timeline(skip, start, rng)))
    # 열차 편성마다 다른 열차 번호를 주고, 60초마다 "지금까지 마지막으로 찍힌 상태"를 받는다
    poll_t = 0.0
    while poll_t <= minutes:
        rows = []
        for b, events in trains:
            last = [e for e in events if e[0] <= poll_t]
            if not last:
                continue
            t, idx, status = last[-1]
            rows.append({"subwayId": "1001", "directAt": "0", "trainNo": f"{b:04d}", "updnLine": "1",
                         "statnId": f"1001000{100 + idx}", "trainSttus": status,
                         "recptnDt": f"2026-10-18 00:{int(poll_t // 60) % 60:02d}:{int(poll_t % 60):02d}"})
        mon.observe(rows, now=1_000_000 + poll_t * 60)
        poll_t += POLL / 60
    return mon


def score(mon, truth):
    status = mon.stop_status()
    confirmed = {i for i, s in status.items() if s.state == CONFIRMED}
    flagged = {i for i, s in status.items() if s.state in (CONFIRMED, SUSPECT)}
    return confirmed, flagged


if __name__ == "__main__":
    cases = [
        ("역 1곳 무정차", {5}),
        ("역 2곳 연속 무정차", {4, 5}),
        ("우회 없음 (정상 운행)", set()),
    ]
    print(f"{'시나리오':<20}{'정답':<12}{'확인(평균)':<26}{'확인 정확(정밀/재현)'}")
    for name, truth in cases:
        runs = [score(simulate(truth, seed=s), truth) for s in range(20)]
        conf_sets = [c for c, _ in runs]
        precision = sum(len(c & truth) for c in conf_sets) / max(1, sum(len(c) for c in conf_sets))
        recall = sum(len(c & truth) for c in conf_sets) / max(1, len(truth) * len(runs))
        avg_wrong = sum(len(c - truth) for c in conf_sets) / len(runs)
        print(f"{name:<20}{str(sorted(truth)):<12}"
              f"오탐 평균 {avg_wrong:.2f}개{'':<10}"
              f"{precision:.2f} / {recall:.2f}")

    print("\n[관측 시간에 따른 변화] 정답 {5}")
    for minutes in (3, 6, 10, 20, 40):
        conf, _ = score(simulate({5}, minutes=minutes), {5})
        print(f"  {minutes:>2}분 관측: 확인 {sorted(conf)}")
