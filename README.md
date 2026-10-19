# 실시간 우회 길찾기 (서울 대중교통)

버스 우회와 지하철 무정차를 반영해, 평소 경로 대신 실제 운행 상황에 맞는 경로를 추천하는 서울 대중교통 길찾기 앱입니다. 1인 학교 프로젝트입니다.

## 주요 기능
- 대중교통 후보 경로 검색과 구간별 안내, 지도 표시
- 첫 승차 지점의 실시간 대기 시간으로 경로 재정렬
- 버스 우회 감지(공식 우회 표시 + 버스 GPS로 미정차 정류장 추정)
- 지하철 무정차 감지(실시간 열차 위치의 역 순서로 추정)
- 지하철 막차·첫차, 버스 첫차·막차를 반영한 운행 시간 확인
- 미정차 지점 대신 같은 노선의 다른 정류장에서 타거나 내리는 대안 비교
- 도보 구간을 실제 길 기준으로 계산하고 한국어 안내 제공

## 사용 API
| API | 용도 |
| --- | --- |
| ODsay LAB | 대중교통 후보 경로, 버스 노선 정류장과 도로 모양 |
| 서울시 버스 정류소 도착 정보 | 실시간 도착, 우회 여부, 첫차·막차 |
| 서울시 버스 위치 정보 | 운행 버스 GPS와 지나간 정류장 |
| 서울시 지하철 실시간 | 열차 위치와 도착 예정 시간 |
| 서울교통공사 첫차·막차 | 지하철 호선·역별 첫차·막차 (1~9호선) |
| 카카오 로컬 | 장소 검색과 좌표 |
| OpenRouteService | 도보 경로 |

## 실행 방법
1. Python 가상환경을 만들고 `requests`, `python-dotenv`, `streamlit`, `folium`, `streamlit-folium`을 설치합니다.
2. 프로젝트 폴더에 `.env` 파일을 만들고 아래 키를 넣습니다. (이 저장소에는 키가 없습니다)
   ```
   ODSAY_API_KEY=
   KAKAO_REST_KEY=
   SUBWAY_API_KEY=
   SUBWAY_FIRSTLAST_KEY=
   DATA_GO_KR_KEY=
   ORS_API_KEY=
   ```
3. `실행.bat`을 실행하거나 `streamlit run app.py`를 실행합니다.
4. 브라우저에서 `http://localhost:8501`을 엽니다.

ODsay는 등록된 공인 IP에서만 호출됩니다. 다른 네트워크에서 실행하면 길찾기가 실패할 수 있습니다.

## 파일 구성
- `app.py`: Streamlit 화면
- `transit/api.py`: 외부 API 호출과 캐시
- `transit/route.py`, `transit/planner.py`: 경로 정리와 추천
- `transit/events.py`, `transit/detour.py`: 우회·무정차 이벤트와 대안 경로
- `transit/monitor.py`, `transit/subway_monitor.py`: 버스 GPS·지하철 열차 위치 점검
- `transit/last_train.py`, `transit/bus_hours.py`: 운행 시간 확인
- `transit/walking.py`: 도보 계산
- `test_*.py`: 가짜 운행 데이터로 감지 정확도를 확인하는 시뮬레이션 테스트
- `발표_정리.md`: 발표 내용과 검증 결과 정리
- `실시간우회길찾기_발표.pptx`: 발표 자료
- `screenshots/`: 앱 화면 캡처

## 한계
- 지하철 무정차는 실제 사례로 검증하지 못했고, 시뮬레이션 결과만 있습니다.
- 버스·지하철 미정차는 공식 데이터가 아니라 추정입니다.
- 공휴일별 운행 시간은 구분하지 않습니다.
- 무료 API의 일일 호출 한도가 있습니다.
