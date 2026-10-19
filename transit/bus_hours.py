"""버스 운행 시간 확인: 승차 정류소 도착 정보의 첫차(firstTm)·막차(lastTm)로 지금 운행 중인지 본다.

- 추가 호출이 없다. 실시간 도착 조회(bus_arrivals)가 이미 받은 응답을 쓴다.
- 첫차·막차는 노선·방면별로 온다. 방면을 구분하지 않고, 한 항목이라도 운행 시간 안이면 '운행'으로 본다(잘못 빼는 것을 줄이는 쪽).
- 시각은 HHMM 문자열이다. 0000은 24시(1440분)로, 새벽 4시 전은 전날 운행으로 본다(지하철과 같은 기준).
- 공휴일·요일별 차이는 구분하지 않는다(한계).
"""
import json
from datetime import datetime

from . import api
from .events import BUS_LAST, TIMETABLE, Event
from .route import BUS

SERVICE_DAY_START = 4 * 60
MAX_CHECK_ROUTES = 10  # 빠른 경로 기준으로만 확인 (실시간 도착 조회 개수 제한)


def _minutes(hhmm):
    """'0420  ' → 260, '0000' → 1440, '0030' → 1470"""
    text = hhmm.strip()
    m = int(text[:2]) * 60 + int(text[2:4])
    return m + 1440 if m < SERVICE_DAY_START else m


def _service_minutes(now):
    minutes = now.hour * 60 + now.minute
    return minutes + 1440 if minutes < SERVICE_DAY_START else minutes


SAVED = api.CACHE_DIR / "bus_windows.json"  # 정류소·노선별 첫차·막차를 하루 동안 기억 (도착 정보에서 빠졌을 때 대신 씀)


def _load_saved():
    try:
        return json.loads(SAVED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _remember(key, windows, day):
    saved = _load_saved()
    saved[key] = {"day": day, "windows": windows}
    SAVED.parent.mkdir(parents=True, exist_ok=True)
    SAVED.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")


def bus_service_state(seg, now=None):
    """승차 정류소에서 이 버스가 지금 운행 중인지: "운행" / "운행 종료" / None(정보 없음)."""
    now = now or datetime.now()
    key = f"{seg.ars_id}|{seg.name}"
    try:
        items = [i for i in api.bus_arrivals(seg.ars_id)
                 if i.get("rtNm") == seg.name and i.get("firstTm") and i.get("lastTm")]
    except api.ApiError:
        items = []  # 호출이 실패해도 저장해 둔 시간표로 판단한다
    if items:
        windows = [(_minutes(i["firstTm"]), _minutes(i["lastTm"])) for i in items]
        _remember(key, windows, now.strftime("%Y-%m-%d"))
    else:
        # 지금 도착 정보에 없으면 같은 날 저장해 둔 시간표를 쓴다 (다른 날 값은 쓰지 않는다)
        saved = _load_saved().get(key)
        if not saved or saved["day"] != now.strftime("%Y-%m-%d"):
            return None
        windows = [tuple(w) for w in saved["windows"]]
    minutes = _service_minutes(now)
    if any(first <= minutes <= last for first, last in windows):
        return "운행"
    return "운행 종료"


def bus_service_events(routes, now=None):
    """경로의 버스 승차 정류소가 운행 시간 밖이면 이벤트로 만든다. 정보를 못 받으면 아무것도 만들지 않는다."""
    found = set()
    checked = set()
    for r in sorted(routes, key=lambda r: r.total_min)[:MAX_CHECK_ROUTES]:
        for seg in r.rides:
            if seg.mode != BUS or not seg.ars_id or (seg.ars_id, seg.name) in checked:
                continue
            checked.add((seg.ars_id, seg.name))
            try:
                state = bus_service_state(seg, now)
            except api.ApiError:
                continue
            if state == "운행 종료":
                found.add(Event(BUS_LAST, seg.name, seg.start, TIMETABLE))
    return found
