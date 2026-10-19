"""지하철 막차·첫차 확인: 승차역이 지금 운행 중인지 서울교통공사 시간표로 본다.

- 역 이름으로 맞춘다. 시간표의 전철역코드(예: 9호선 선유도 4127)와 ODsay 역 ID(912)는 체계가 달라서 쓸 수 없다.
- 방향은 ODsay 구간의 wayCode(1 상행·내선, 2 하행·외선)를 쓴다. 9호선 선유도→국회의사당은 1이고, 시간표 상행 막차도 개화역 출발이라 맞는다.
- 시각은 24시 이후 표기(예: 245630 = 24시 56분 30초)를 분으로 바꿔 비교한다. 새벽 4시 이전은 전날 운행으로 본다.
- 요일은 평일 1, 토요일 2, 일·공휴 3. 공휴일은 구분하지 않는다(한계).
- 승차역만 본다. 승차 후 하차역까지 열차가 가는지는 확인하지 않는다.
"""
from datetime import datetime, timedelta

from . import api
from .events import LAST_TRAIN, TIMETABLE, Event
from .route import SUBWAY

SERVICE_DAY_START = 4 * 60  # 새벽 4시 이전은 전날 운행으로 본다
UPDN = {1: "1", 2: "2"}     # ODsay wayCode → 시간표 상행/하행 (1 상행·내선, 2 하행·외선)


def _minutes(hhmmss):
    """'245630' → 1496 (24시 56분, 초는 버림)"""
    return int(hhmmss[:2]) * 60 + int(hhmmss[2:4])


def _service_day(now):
    """(요일 코드, 운행일 기준 분). 새벽 4시 전이면 전날 운행이므로 전날 요일과 +24시간으로 본다."""
    minutes = now.hour * 60 + now.minute
    day = now
    if minutes < SERVICE_DAY_START:
        minutes += 24 * 60
        day = now - timedelta(days=1)
    dow = "1" if day.weekday() < 5 else ("2" if day.weekday() == 5 else "3")
    return dow, minutes


def service_state(seg, now=None):
    """지하철 구간 승차역의 상태: "운행" / "막차 이후" / "첫차 전". 시간표에 없으면 None."""
    now = now or datetime.now()
    updn = UPDN.get(seg.way_code)
    if updn is None or not 1 <= seg.subway_code <= 9:  # 이 데이터는 서울교통공사 1~9호선만 있다
        return None
    dow, minutes = _service_day(now)
    row = next((r for r in api.last_train_table(seg.name, updn, dow) if r["STTN"] == seg.start), None)
    if row is None:
        return None
    if minutes > _minutes(row["LSTTM_HRM"]):
        return "막차 이후"
    if minutes < _minutes(row["FSTT_HRM"]):
        return "첫차 전"
    return "운행"


def last_train_events(routes, now=None):
    """경로의 지하철 승차역이 운행 종료 상태면 이벤트로 만든다. 시간표를 못 받으면 아무것도 만들지 않는다."""
    found = set()
    for r in routes:
        for seg in r.rides:
            if seg.mode != SUBWAY:
                continue
            try:
                state = service_state(seg, now)
            except api.ApiError:
                continue
            if state in ("막차 이후", "첫차 전"):
                found.add(Event(LAST_TRAIN, seg.name, seg.start, TIMETABLE))
    return found
