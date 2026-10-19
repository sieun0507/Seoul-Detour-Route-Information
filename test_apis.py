"""API 호출 테스트: 각 API를 1번씩 호출하고 응답을 data/samples/에 저장한다."""
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SUBWAY_KEY = os.getenv("SUBWAY_API_KEY")
DATA_KEY = os.getenv("DATA_GO_KR_KEY")
ODSAY_KEY = os.getenv("ODSAY_API_KEY")

SAMPLES = Path(__file__).parent / "data" / "samples"
SAMPLES.mkdir(parents=True, exist_ok=True)

ORIGIN_NAME = "선유도"
DEST_NAME = "상명대"  # POI 검색은 정류소명 위주라 "상명대입구"가 잡힘


def save(name, data):
    (SAMPLES / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def call(name, url, params):
    """API를 호출해 JSON을 저장하고 반환한다. 실패하면 None."""
    try:
        res = requests.get(url, params=params, timeout=15)
        data = res.json()
    except Exception as e:
        print(f"[실패] {name}: {e}")
        return None
    save(name, data)
    return data


def bus_api(path, **params):
    """서울시 버스 API(ws.bus.go.kr) 공통 호출. msgBody.itemList 반환."""
    data = call(path.replace("/", "_"), f"http://ws.bus.go.kr/api/rest/{path}",
                {"serviceKey": DATA_KEY, "resultType": "json", **params})
    if data is None:
        return None
    header = data.get("msgHeader", {})
    if header.get("headerCd") != "0":
        print(f"[실패] {path}: {header.get('headerCd')} {header.get('headerMsg')}")
        return None
    return (data.get("msgBody") or {}).get("itemList") or []


def test_odsay_station():
    data = call("odsay_searchStation", "https://api.odsay.com/v1/api/searchStation",
                {"apiKey": ODSAY_KEY, "stationName": ORIGIN_NAME, "CID": 1000})
    if not data or "error" in data:
        print(f"[실패] ODsay 역 검색: {data and data.get('error')}")
        return None
    st = next(s for s in data["result"]["station"] if s.get("stationClass") == 2)  # 2: 지하철역
    print(f"[성공] ODsay 역 검색: {st['stationName']} ({st['laneName']}) x={st['x']} y={st['y']}")
    return float(st["x"]), float(st["y"])


def test_location_info():
    items = bus_api("pathinfo/getLocationInfo", stSrch=DEST_NAME)
    if not items:
        return None
    poi = items[0]
    print(f"[성공] 환승경로 POI 검색: {poi['poiNm']} x={poi['gpsX']} y={poi['gpsY']} (총 {len(items)}건)")
    return float(poi["gpsX"]), float(poi["gpsY"])


def test_odsay_path(start, end):
    data = call("odsay_searchPubTransPathT", "https://api.odsay.com/v1/api/searchPubTransPathT",
                {"apiKey": ODSAY_KEY, "SX": start[0], "SY": start[1], "EX": end[0], "EY": end[1]})
    if not data or "error" in data:
        print(f"[실패] ODsay 길찾기: {data and data.get('error')}")
        return
    paths = data["result"]["path"]
    print(f"[성공] ODsay 길찾기: 후보 경로 {len(paths)}개")
    for p in paths[:3]:
        steps = []
        for s in p["subPath"]:
            if s["trafficType"] == 1:
                steps.append(f"지하철 {s['lane'][0]['name']}({s['startName']}→{s['endName']})")
            elif s["trafficType"] == 2:
                steps.append(f"버스 {s['lane'][0]['busNo']}({s['startName']}→{s['endName']})")
        print(f"    {p['info']['totalTime']}분: " + " → ".join(steps))


def test_bus_path(start, end):
    items = bus_api("pathinfo/getPathInfoByBusNSub",
                    startX=start[0], startY=start[1], endX=end[0], endY=end[1])
    if items is not None:
        print(f"[성공] 환승경로 버스+지하철: 후보 경로 {len(items)}개")


def test_subway():
    url = f"http://swopenapi.seoul.go.kr/api/subway/{SUBWAY_KEY}/json/realtimeStationArrival/0/10/{ORIGIN_NAME}"
    data = call("subway_realtimeStationArrival", url, {})
    if not data:
        return
    rows = data.get("realtimeArrivalList")
    if not rows:
        print(f"[실패] 지하철 실시간 도착: {data.get('errorMessage') or data}")
        return
    print(f"[성공] 지하철 실시간 도착: {len(rows)}건")
    for r in rows[:4]:
        print(f"    {r['trainLineNm']} | {r['arvlMsg2']}")


def test_station():
    items = bus_api("stationinfo/getStationByName", stSrch=ORIGIN_NAME)
    if not items:
        return
    st = items[0]
    print(f"[성공] 정류소 검색: {st['stNm']} (arsId {st['arsId']}), 총 {len(items)}건")
    arrivals = bus_api("stationinfo/getStationByUid", arsId=st["arsId"])
    if arrivals is None:
        return
    print(f"[성공] 정류소 도착정보: 노선 {len(arrivals)}개")
    for a in arrivals[:4]:
        print(f"    {a['rtNm']}번 | {a['arrmsg1']} / {a['arrmsg2']}")
    return arrivals[0]["busRouteId"] if arrivals else None


def test_bus_position(route_id):
    items = bus_api("buspos/getBusPosByRtid", busRouteId=route_id)
    if items is not None:
        print(f"[성공] 버스위치 (노선 {route_id}): 운행 중인 버스 {len(items)}대")


if __name__ == "__main__":
    start = test_odsay_station()
    end = test_location_info()
    if start and end:
        test_odsay_path(start, end)
        test_bus_path(start, end)
    test_subway()
    route_id = test_station()
    if route_id:
        test_bus_position(route_id)
    print(f"\n응답 저장 위치: {SAMPLES}")
