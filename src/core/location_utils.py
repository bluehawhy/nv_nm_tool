import re
from ..utils import loggas
import pandas as pd
from pyproj import Transformer

logging= loggas.logger

# ================================= search location ====================================
def convert_wgs_to_nds(wgs_pos):
    NDS_FACTOR = 11930464.71
    
    if not isinstance(wgs_pos, dict):
        return None
        
    try:
        # latitude / lat / x 순서로 Key 체크
        lat = (wgs_pos.get('latitude') if wgs_pos.get('latitude') is not None
               else wgs_pos.get('lat') if wgs_pos.get('lat') is not None
               else wgs_pos.get('x'))
        
        # longitude / lon / y 순서로 Key 체크
        lon = (wgs_pos.get('longitude') if wgs_pos.get('longitude') is not None
               else wgs_pos.get('lon') if wgs_pos.get('lon') is not None
               else wgs_pos.get('y'))

        if lat is not None and lon is not None:
            nds_x = int(round(float(lat) * NDS_FACTOR))
            nds_y = int(round(float(lon) * NDS_FACTOR))
            
            return {"latitude": nds_x, "longitude": nds_y}
            
    except Exception as e:
        logging.error(f"WGS -> NDS 변환 중 오류 발생: {e}")
        
    return None

def parse_map_scale_km(log_str):
    """로그 텍스트에서 'km [숫자]' 패턴을 찾아 float으로 반환합니다."""
    match = re.search(r'\bkm\s+([\d\.]+)', log_str)
    if match:
        return float(match.group(1))
    return None  # 파싱 실패 시 기본값 처리용


def parse_location(location_input):
    """
    다양한 형태의 위치 입력값을 {'latitude': float, 'longitude': float} 형태로 표준화합니다.
    
    지원하는 입력 형태:
    1. 문자열: "37.60039253324384, 127.10905251041765" 또는 "37.60, 127.10"
    2. Dict (x, y): {'x': 37.600..., 'y': 127.109...}
    3. Dict (lat, lon): {'lat': 37.600..., 'lon': 127.109...}
    4. Dict (latitude, longitude): {'latitude': 37.600..., 'longitude': 127.109...}
    """
    if location_input is None:
        return None

    # 1. 문자열인 경우 ("lat, lon" 형태)
    if isinstance(location_input, str):
        try:
            parts = location_input.split(',')
            if len(parts) == 2:
                lat = float(parts[0].strip())
                lon = float(parts[1].strip())
                return {'latitude': lat, 'longitude': lon}
        except (ValueError, AttributeError):
            return None

    # 2. Dictionary인 경우
    elif isinstance(location_input, dict):
        # latitude / lat / x 순으로 값 찾기
        lat = (location_input.get('latitude') 
               if location_input.get('latitude') is not None 
               else location_input.get('lat') 
               if location_input.get('lat') is not None 
               else location_input.get('x'))

        # longitude / lon / y 순으로 값 찾기
        lon = (location_input.get('longitude') 
               if location_input.get('longitude') is not None 
               else location_input.get('lon') 
               if location_input.get('lon') is not None 
               else location_input.get('y'))

        if lat is not None and lon is not None:
            try:
                return {'latitude': float(lat), 'longitude': float(lon)}
            except (ValueError, TypeError):
                return None
    return None

def ext_nds_pos_from_log(log_line):
    nds_pos = {'latitude': 0, 'longitude': 0}
    
    # pos 뒤의 두 숫자 덩어리 추출
    match = re.search(r'pos\s+(-?\d+)\s+(-?\d+)', log_line)
    if match:
        nds_pos['longitude'] = match.group(1)
        nds_pos['latitude'] = match.group(2)
    return nds_pos

def convert_nds_wgs(nds_pos):
    NDS_FACTOR = 11930464.71
    lat = None
    lon = None
    try:
        # x/y 키 또는 lat/lon 키 중 존재하는 값을 가져옴 (0인 경우 고려해 None 체크)
        raw_x = nds_pos.get('latitude') if nds_pos.get('latitude') is not None else nds_pos.get('x')
        raw_y = nds_pos.get('longitude') if nds_pos.get('longitude') is not None else nds_pos.get('y')

        if raw_x is not None and raw_y is not None:
            # 기존 매핑 규칙 유지: x (또는 lat) -> lat, y (또는 lon) -> lon
            lat = round(float(raw_x) / NDS_FACTOR, 7)
            lon = round(float(raw_y) / NDS_FACTOR, 7)
            
            # 구글 지도 표준 링크
            google_map_link = f"https://www.google.com/maps?q={lat},{lon}"
            
            return {"latitude": lat, "longitude": lon, "link": google_map_link}
    except Exception as e:
        logging.info(f"좌표 변환 중 오류 발생: {e}")
    return None

def conv_wgs_nds(wgs_pos):
    NDS_FACTOR = 11930464.71
    nds_x = None
    nds_y = None
    try:
        # 입력키는 lat/lon, latitude/longitude, x/y 등 상황에 맞게 dict 접근
        # lat=위도(y축 방향), lon=경도(x축 방향)
        lat = wgs_pos.get('lat') if wgs_pos.get('lat') is not None else wgs_pos.get('latitude')
        lon = wgs_pos.get('lon') if wgs_pos.get('lon') is not None else wgs_pos.get('longitude')

        if lat is not None and lon is not None:
            # NDS 좌표계는 정수형(int)으로 표현됩니다.
            nds_x = int(round(float(lat) * NDS_FACTOR))
            nds_y = int(round(float(lon) * NDS_FACTOR))

            return {"latitude": nds_x, "longitude": nds_y}
    except Exception as e:
        logging.info(f"WGS -> NDS 좌표 변환 중 오류 발생: {e}")
    return None

def save_loca(log_line,loca_local_path):
    #확인된 로그 저장
    
    # 1. 좌표 변환 과정 (기존 함수 활용)
    nds_pos = ext_nds_pos_from_log(log_line)
    conv_nds_wgs_result = convert_nds_wgs(nds_pos)
    
    with open(loca_local_path, "w", encoding="utf-8") as f:
        f.write("=== Log Results ===\n")
        f.write(str(log_line))
        
        f.write("\n\n=== Converted WGS84 Coordinates ===\n")
        if conv_nds_wgs_result:
            f.write(str({'latitude': conv_nds_wgs_result['latitude'], 'longitude': conv_nds_wgs_result['longitude']}))
            f.write(str('\n'))
            f.write(str(conv_nds_wgs_result['link']))
        else:
            f.write("No coordinate data found or conversion failed.")
    return 0

def convert_korea2000_to_wgs_csv(input_path: str, output_path: str = None):
    """Korea 2000(EPSG:5186) 좌표계 CSV를 WGS84(EPSG:4326)로 변환하여 저장합니다."""
    # 1. CSV 파일 불러오기
    df = pd.read_csv(input_path, encoding="cp949")

    # 2. EPSG:5186 -> EPSG:4326(WGS84) 변환기 생성
    transformer = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)

    # 3. 경도, 위도 변환
    df["경도"], df["위도"] = transformer.transform(
        df["X좌표"].values, df["Y좌표"].values
    )

    # 4. 문자열 결합 (apply 대비 훨씬 빠른 벡터화 방식)
    df["좌표값"] = df["위도"].astype(str) + ", " + df["경도"].astype(str)

    # 5. 저장 경로 미지정 시 기본 파일명 생성
    if not output_path:
        output_path = input_path.replace(".csv", "_WGS84.csv")

    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    logging.info(f"✅ 좌표 변환 완료! 저장 경로: {output_path}")