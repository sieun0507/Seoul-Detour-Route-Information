"""외부 API 호출 모음. 같은 요청은 캐싱해서 일일 호출 한도를 아낀다.
disk=True인 호출은 data/cache에 저장해 프로그램을 다시 켜도 재사용하고,
API가 실패하면(예: 일일 한도 초과) 기간이 지난 저장본이라도 대신 돌려준다."""
import hashlib
import json
import os
import time
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SUBWAY_KEY = os.getenv("SUBWAY_API_KEY")
FIRSTLAST_KEY = os.getenv("SUBWAY_FIRSTLAST_KEY")  # 서울교통공사 호선별 첫차·막차 정보
DATA_KEY = os.getenv("DATA_GO_KR_KEY")
ODSAY_KEY = os.getenv("ODSAY_API_KEY")
KAKAO_KEY = os.getenv("KAKAO_REST_KEY")


class ApiError(Exception):
    pass


CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


def ttl_cache(seconds, disk=False):
    """인자가 같으면 seconds 동안 이전 결과를 재사용한다."""
    def deco(fn):
        store = {}

        def path_for(args):
            key = hashlib.md5(repr(args).encode("utf-8")).hexdigest()
            return CACHE_DIR / fn.__name__ / f"{key}.json"

        @wraps(fn)
        def wrapper(*args):
            hit = store.get(args)
            if hit is None and disk and path_for(args).exists():
                saved = json.loads(path_for(args).read_text(encoding="utf-8"))
                hit = store[args] = (saved["t"], saved["v"])
            if hit and time.time() - hit[0] < seconds:
                return hit[1]
            try:
                value = fn(*args)
            except ApiError:
                if hit:  # 새로 못 받으면 예전 결과라도 쓴다
                    return hit[1]
                raise
            store[args] = (time.time(), value)
            if disk:
                path = path_for(args)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"t": time.time(), "v": value}, ensure_ascii=False),
                                encoding="utf-8")
            return value
        return wrapper
    return deco


def _get(url, params=None, headers=None):
    try:
        res = requests.get(url, params=params, headers=headers, timeout=15)
        return res.json()
    except Exception as e:
        raise ApiError(f"요청 실패: {e}") from e


# ---------- 장소 검색 ----------

@ttl_cache(7 * 86400, disk=True)
def kakao_search(query):
    """건물명/주소 키워드 검색. [{name, address, x, y}]"""
    data = _get("https://dapi.kakao.com/v2/local/search/keyword.json",
                {"query": query, "size": 10},
                {"Authorization": f"KakaoAK {KAKAO_KEY}"})
    if "documents" not in data:
        raise ApiError(f"카카오 검색 실패: {data.get('message', data)}")
    return [{"name": d["place_name"],
             "address": d["road_address_name"] or d["address_name"],
             "x": float(d["x"]), "y": float(d["y"])} for d in data["documents"]]


@ttl_cache(7 * 86400, disk=True)
def odsay_station_search(query):
    """카카오를 못 쓸 때의 대체 검색: 역/정류장 이름만 검색된다."""
    data = _get("https://api.odsay.com/v1/api/searchStation",
                {"apiKey": ODSAY_KEY, "stationName": query, "CID": 1000})
    if "error" in data:
        raise ApiError(f"ODsay 역 검색 실패: {data['error']}")
    kind = {1: "버스정류장", 2: "지하철역"}
    return [{"name": s["stationName"],
             "address": f"{kind.get(s['stationClass'], '')} {s.get('laneName', '')} {s.get('gu', '')}".strip(),
             "x": float(s["x"]), "y": float(s["y"])} for s in data["result"]["station"]]


# ---------- 길찾기 ----------

@ttl_cache(86400, disk=True)
def odsay_routes(sx, sy, ex, ey, path_type=0):
    """대중교통 후보 경로 목록 (ODsay path 원본). path_type 0: 전체, 1: 지하철만, 2: 버스만."""
    data = _get("https://api.odsay.com/v1/api/searchPubTransPathT",
                {"apiKey": ODSAY_KEY, "SX": sx, "SY": sy, "EX": ex, "EY": ey,
                 "SearchPathType": path_type})
    if "error" in data:
        err = data["error"]
        msg = err[0]["message"] if isinstance(err, list) else err.get("msg", err)
        raise ApiError(f"ODsay 길찾기 실패: {msg}")
    return data["result"]["path"]


@ttl_cache(86400, disk=True)
def last_train_table(line, updn, dow):
    """호선·방향·요일별 역 전체의 첫차·막차 (SearchFirstAndLastTrainbyLineServiceNew).
    line: "9호선", updn: "1"(상행·내선) / "2"(하행·외선), dow: "1" 평일 / "2" 토 / "3" 일·공휴.
    행: {STTN(역 이름), FSTT_HRM(첫차 HHMMSS), LSTTM_HRM(막차, 24시 이후는 24~27시로 표기)}"""
    url = (f"http://openapi.seoul.go.kr:8088/{FIRSTLAST_KEY}/json/"
           f"SearchFirstAndLastTrainbyLineServiceNew/1/400/{line}/{updn}/{dow}/")
    res = _get(url).get("SearchFirstAndLastTrainbyLineServiceNew")
    if not res:
        raise ApiError("첫차·막차 조회 실패: 응답 형식 오류")
    code = res["RESULT"]["CODE"]
    if code == "INFO-200":
        return []  # 해당 데이터 없음
    if code != "INFO-000":
        raise ApiError(f"첫차·막차 조회 실패: {res['RESULT']['MESSAGE']}")
    return res.get("row", [])


# ---------- 실시간 ----------

@ttl_cache(30)
def bus_arrivals(ars_id):
    """정류소 번호(arsId)의 노선별 도착 정보 (getStationByUid itemList)."""
    data = _get("http://ws.bus.go.kr/api/rest/stationinfo/getStationByUid",
                {"serviceKey": DATA_KEY, "resultType": "json", "arsId": ars_id})
    header = data.get("msgHeader", {})
    if header.get("headerCd") not in ("0", "4"):  # 4: 결과 없음
        raise ApiError(f"버스 도착정보 실패: {header.get('headerMsg')}")
    return (data.get("msgBody") or {}).get("itemList") or []


@ttl_cache(30)
def subway_arrivals(station_name):
    """역 이름의 실시간 도착 정보 (realtimeArrivalList)."""
    url = (f"http://swopenapi.seoul.go.kr/api/subway/{SUBWAY_KEY}/json/"
           f"realtimeStationArrival/0/30/{station_name}")
    data = _get(url)
    if "realtimeArrivalList" not in data:
        return []  # 해당 역 정보 없음(서울 외 노선 등)
    return data["realtimeArrivalList"]


# ---------- 버스 노선 모양 / 위치 ----------

@ttl_cache(7 * 86400, disk=True)
def odsay_bus_lane(bus_id):
    """ODsay 버스 노선 상세: 정류장 순서·좌표·서울시 정류장 ID(localStationID)."""
    data = _get("https://api.odsay.com/v1/api/busLaneDetail", {"apiKey": ODSAY_KEY, "busID": bus_id})
    if "error" in data:
        raise ApiError(f"ODsay 노선 상세 실패: {data['error']}")
    return data["result"]


@ttl_cache(7 * 86400, disk=True)
def odsay_lane_shape(bus_id, last_idx):
    """노선 전체의 실제 도로 모양 좌표 [(x, y)]."""
    data = _get("https://api.odsay.com/v1/api/loadLane",
                {"apiKey": ODSAY_KEY, "mapObject": f"0:0@{bus_id}:1:0:{last_idx}"})
    if "error" in data:
        raise ApiError(f"ODsay 노선 모양 실패: {data['error']}")
    return [(p["x"], p["y"]) for lane in data["result"]["lane"]
            for sec in lane["section"] for p in sec["graphPos"]]


@ttl_cache(20)
def subway_positions(line_name):
    """호선(예: "1호선")의 운행 중인 열차 위치 (realtimePositionList). 서울시 API 호출이며 ODsay는 쓰지 않는다."""
    data = _get(f"http://swopenapi.seoul.go.kr/api/subway/{SUBWAY_KEY}/json/"
                f"realtimePosition/0/200/{line_name}")
    if "realtimePositionList" in data:
        return data["realtimePositionList"]
    if data.get("code") == "INFO-200":
        return []  # 지금 그 호선에 운행 중인 열차가 없음
    raise ApiError(f"지하철 위치 조회 실패: {data.get('message', data)}")


@ttl_cache(20)
def bus_positions(route_id):
    """서울시 노선 ID의 운행 중인 버스 위치 (getBusPosByRtid itemList)."""
    data = _get("http://ws.bus.go.kr/api/rest/buspos/getBusPosByRtid",
                {"serviceKey": DATA_KEY, "resultType": "json", "busRouteId": route_id})
    header = data.get("msgHeader", {})
    if header.get("headerCd") not in ("0", "4"):
        raise ApiError(f"버스 위치 조회 실패: {header.get('headerMsg')}")
    return (data.get("msgBody") or {}).get("itemList") or []


# ---------- 도보 경로 ----------

ORS_KEY = os.getenv("ORS_API_KEY")
ORS_COOLDOWN = 600      # 응답이 실패하면 이 시간(초) 동안은 호출하지 않고 바로 대체 계산을 쓴다
_ors_down_until = 0.0   # 이 시각 전까지는 ORS를 부르지 않는다


@ttl_cache(30 * 86400, disk=True)
def ors_walk(x1, y1, x2, y2):
    """OpenRouteService 도보 경로: {distance(m), coords[(x, y)], steps[{type, distance, name}]}
    응답이 없으면 한 번 실패한 뒤 ORS_COOLDOWN 동안 기다리지 않고 바로 ApiError를 낸다.
    (도보 구간마다 15초씩 기다리면 길찾기 한 번에 수 분이 걸리기 때문)"""
    global _ors_down_until
    if time.time() < _ors_down_until:
        raise ApiError("도보 경로 서버 응답 없음 (잠시 대체 계산 사용)")
    try:
        res = requests.post("https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson",
                            json={"coordinates": [[x1, y1], [x2, y2]]},
                            headers={"Authorization": ORS_KEY}, timeout=8)
        data = res.json()
    except Exception as e:
        _ors_down_until = time.time() + ORS_COOLDOWN
        raise ApiError(f"도보 경로 요청 실패: {e}") from e
    if "features" not in data:
        _ors_down_until = time.time() + ORS_COOLDOWN
        raise ApiError(f"도보 경로 실패: {data.get('error', data)}")
    f = data["features"][0]
    steps = [{"type": s["type"], "distance": s["distance"], "name": s.get("name", "")}
             for seg in f["properties"]["segments"] for s in seg["steps"]]
    return {"distance": f["properties"]["summary"].get("distance", 0),
            "coords": [tuple(c) for c in f["geometry"]["coordinates"]],
            "steps": steps}
