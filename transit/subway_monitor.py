"""지하철 무정차 자동 감지: 실시간 열차 위치(realtimePosition)를 주기적으로 받아
같은 열차가 경로의 역을 서지 않고 지나갔는지 추정한다.

판정 원리
- 같은 열차(호선·상하행·열차번호)의 연속된 두 관측이 역 A → 역 C이고, 사이 역 B에서 정차 기록(도착·출발)이 없으면 B를 '통과'로 본다.
- 관측 간격이 MAX_GAP_SECONDS보다 길면 중간에 정차했을 수 있어 판단하지 않는다.
- 급행(directAt=1)은 원래 일부 역을 건너뛰므로 제외한다.
- 같은 역을 통과한 열차가 2대 이상이면 '확인', 1대면 '의심' (버스 점검과 같은 기준).
- 한계: 폴링 사이에 짧게 정차한 열차는 통과로 잘못 볼 수 있다. 그래서 확인은 2대 이상을 요구한다.
- 역 목록은 길찾기 결과(ODsay 경로)에서 이미 받은 것을 쓰므로 ODsay를 추가로 부르지 않는다.
"""
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from . import api
from .monitor import CONFIRMED, OK, SUSPECT, UNKNOWN, StopStatus
from .route import SUBWAY

MAX_GAP_SECONDS = 4 * 60  # 두 관측 사이가 이보다 길면 중간 정차 여부를 알 수 없다
KEEP_SECONDS = 30 * 60    # 관측 보관 시간
STOP_STATUS = {"1", "2"}  # trainSttus: 0 진입, 1 도착, 2 출발 — 도착·출발만 정차 기록으로 본다


@dataclass
class Obs:
    t: float
    idx: int       # 역 순번 (경로의 역 목록 기준)
    status: str    # trainSttus


def _seconds(stamp, fallback):
    """recptnDt("2026-10-18 00:35:30")를 초 단위 시각으로."""
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return fallback


@dataclass
class SubwayMonitor:
    line: str                 # "9호선" (경로에 나온 이름 그대로)
    subway_id: str            # "1009" (서울시 호선 코드)
    stations: list            # [(역 ID 문자열, 역 이름)] 경로 순서
    tracks: dict = field(default_factory=lambda: defaultdict(list))
    seen: set = field(default_factory=set)
    last_poll: float = 0

    def __post_init__(self):
        self.index = {sid: i for i, (sid, _) in enumerate(self.stations)}

    def observe(self, rows, now=None):
        """열차 위치 목록(realtimePositionList 형식)을 관측으로 쌓는다."""
        now = now or time.time()
        for r in rows:
            if r.get("subwayId") != self.subway_id or r.get("directAt") == "1":
                continue
            key = (r.get("trainNo"), r.get("updnLine"), r.get("recptnDt"))
            if key in self.seen:
                continue  # 같은 위치를 여러 번 받아도 한 번만 센다
            self.seen.add(key)
            statn = r.get("statnId", "")
            sid = str(int(statn[4:])) if statn[4:].isdigit() else ""  # "1009000912" → "912"
            if sid not in self.index:
                continue
            train = (r.get("trainNo"), r.get("updnLine"))
            self.tracks[train].append(Obs(_seconds(r.get("recptnDt"), now), self.index[sid],
                                          r.get("trainSttus", "")))
        for train in list(self.tracks):
            self.tracks[train] = [o for o in self.tracks[train] if now - o.t < KEEP_SECONDS]
            if not self.tracks[train]:
                del self.tracks[train]
        self.last_poll = now

    def poll(self):
        self.observe(api.subway_positions(self.line))

    def passed_without_stop(self):
        """역 순번 → 그 역을 서지 않고 지나간 것으로 보이는 열차 키 집합."""
        passed = defaultdict(set)
        for train, obs in self.tracks.items():
            obs = sorted(obs, key=lambda o: o.t)
            stopped = {o.idx for o in obs if o.status in STOP_STATUS}
            for p, q in zip(obs, obs[1:]):
                if q.t - p.t > MAX_GAP_SECONDS or p.idx == q.idx:
                    continue
                cands = set(range(min(p.idx, q.idx) + 1, max(p.idx, q.idx)))
                if p.status == "0":
                    cands.add(p.idx)  # 진입만 찍히고 다음 역으로 넘어간 역
                for k in cands - stopped:
                    passed[k].add(train)
        return passed

    def stop_status(self):
        """역 순번 → StopStatus (버스 점검과 같은 상태 이름을 쓴다)."""
        passed = self.passed_without_stop()
        served = defaultdict(set)
        for train, obs in self.tracks.items():
            for o in obs:
                if o.status in STOP_STATUS:
                    served[o.idx].add(train)
        result = {}
        for i in range(len(self.stations)):
            k, ok = len(passed[i]), len(served[i])
            if k >= 2:
                state = CONFIRMED
            elif k == 1:
                state = SUSPECT
            elif ok:
                state = OK
            else:
                state = UNKNOWN
            result[i] = StopStatus(state, k, ok)
        return result

    def skipped_stops(self):
        """경로 판단에 쓸 무정차 역 이름 — 확인된 것만."""
        return [self.stations[i][1] for i, st in self.stop_status().items() if st.state == CONFIRMED]

    def problems(self):
        """화면 표시용: [(역 이름, StopStatus)] — 의심 이상만."""
        return [(self.stations[i][1], st) for i, st in self.stop_status().items()
                if st.state in (CONFIRMED, SUSPECT)]


def monitor_for(seg):
    """경로의 지하철 구간 하나를 점검기로 만든다. 역 ID가 없거나 1~9호선이 아니면 None."""
    if seg.mode != SUBWAY or not seg.stop_ids or not 1 <= seg.subway_code <= 9:
        return None
    names = [name for name, _, _ in seg.stops]
    return SubwayMonitor(seg.name, f"100{seg.subway_code}", list(zip(seg.stop_ids, names)))
