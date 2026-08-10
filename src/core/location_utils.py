import re
from ..utils import loggas

logging= loggas.logger

# ================================= search location ====================================
def ext_nds_pos(results):
    nds_pos = {'y': 0, 'x': 0}
    log_line = results.get('car_pos', '')
    
    # pos 뒤의 두 숫자 덩어리 추출
    match = re.search(r'pos\s+(-?\d+)\s+(-?\d+)', log_line)
    if match:
        nds_pos['y'] = match.group(1)
        nds_pos['x'] = match.group(2)
    return nds_pos

def conv_nds_wgs(nds_pos):
    NDS_FACTOR = 11930464.71
    lat = None
    lon = None
    try:
        # 0도 False가 아니라고 판단하도록 None 체크로 변경
        if nds_pos.get('x') is not None and nds_pos.get('y') is not None:
            # 0, 0 인 경우 변환하면 0.0, 0.0이 됨 (에러 방지)
            lat = round(float(nds_pos['x']) / NDS_FACTOR, 7)
            lon = round(float(nds_pos['y']) / NDS_FACTOR, 7)
            
            # 구글 지도 표준 링크 형식으로 수정 제안
            google_map_link = f"https://www.google.com/maps?q={lat},{lon}"
            
            return {"lat": lat, "lon": lon, "link": google_map_link}
    except Exception as e:
        logging.info(f"좌표 변환 중 오류 발생: {e}")
    return None

def save_loca(results,loca_local_path):
    #확인된 로그 저장
    
    # 1. 좌표 변환 과정 (기존 함수 활용)
    nds_pos = ext_nds_pos(results)
    conv_nds_wgs_result = conv_nds_wgs(nds_pos)
    
    with open(loca_local_path, "w", encoding="utf-8") as f:
        f.write("=== Log Results ===\n")
        f.write(str(results))
        
        f.write("\n\n=== Converted WGS84 Coordinates ===\n")
        if conv_nds_wgs_result:
            f.write(str({'lat': conv_nds_wgs_result['lat'], 'lon': conv_nds_wgs_result['lon']}))
            f.write(str('\n'))
            f.write(str(conv_nds_wgs_result['link']))
        else:
            f.write("No coordinate data found or conversion failed.")
    return 0