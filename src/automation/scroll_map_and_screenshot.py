import os
import time
import pandas as pd

# 1. 유틸리티 (설정, 로거 등)
from src.utils import loggas

# 2. 핵심 로직 및 디바이스 제어 모듈 (core)
from src.core import (
    call_device,
    func_device,
    func_logging,
    func_record_image,
)

logging = loggas.logger


def start_simualtion(device: dict = None, file_path: str = None, colunm_loca_head: str = '좌표값', target_model: str = 'SM-X820'):
    # ---------------------------------------------------------
    # 1. Excel 파일 경로 검증 및 저장 폴더 생성
    # ---------------------------------------------------------
    if not file_path or not os.path.exists(file_path):
        logging.error(f"❌ Excel 파일이 존재하지 않거나 경로가 잘못되었습니다: {file_path}")
        return -1

    base_dir = os.path.dirname(os.path.abspath(file_path))
    save_dir = os.path.join(base_dir, "스크롤_스크린샷")
    os.makedirs(save_dir, exist_ok=True)
    logging.info(f"📂 스크린샷 저장 폴더 설정 완료: {save_dir}")

    # ---------------------------------------------------------
    # 2. 디바이스 연결 및 초기화
    # ---------------------------------------------------------
    if not device:
        devices = call_device.discover_and_connect_device()
        device = next((d for d in devices if d.get('model') == target_model), None)

    if not device:
        logging.error(f"❌ '{target_model}' 모델 디바이스를 찾을 수 없습니다.")
        return -1

    logmanager = func_logging.AndroidLogManager(device=device)
    navi_contrl = func_device.NaviController(device=device)

    logmanager.start_live_logging()
    logging.info('로그 수신을 위해 10초간 대기합니다.')
    time.sleep(10)

    # ---------------------------------------------------------
    # 3. Excel 파일 읽기 및 필수 좌표 컬럼 존재 여부 검증
    # ---------------------------------------------------------
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        logging.error(f"❌ Excel 파일을 읽는 중 오류 발생: {e}")
        return -1

    # 좌표 관련 컬럼 존재 유무 사전 검증
    has_target_col = colunm_loca_head in df.columns
    has_lat_lon_cols = ('위도' in df.columns) and ('경도' in df.columns)

    if not has_target_col and not has_lat_lon_cols:
        logging.error(f"❌ Excel 헤더에 '{colunm_loca_head}' 컬럼이나 '위도'/'경도' 컬럼이 모두 존재하지 않습니다.")
        return -1

    # 사용 가능한 파싱 방식 모드 설정 ('SINGLE_COL' 또는 'LAT_LON')
    coord_mode = 'SINGLE_COL' if has_target_col else 'LAT_LON'
    logging.info(f"🔍 좌표 추적 모드 설정: {coord_mode} (대상 컬럼: {colunm_loca_head if has_target_col else '위도/경도'})")

    # 'screenshot_path' 컬럼이 없으면 가장 마지막(우측 끝) 열에 생성
    if 'screenshot_path' not in df.columns:
        df['screenshot_path'] = None

    df['screenshot_path'] = df['screenshot_path'].astype(object)

    # ---------------------------------------------------------
    # 4. 각 행 순회 및 지도 이동 / 스크린샷 저장
    # ---------------------------------------------------------
    total_count = len(df)
    processed_count = 0  # 실제로 처리한 작업 건수 카운터

    for index, row in df.iterrows():
        # 이미 스크린샷 경로가 존재하는 경우 Pass
        current_path = row.get('screenshot_path')
        if pd.notna(current_path) and str(current_path).strip() != "":
            logging.info(f"⏩ [{index + 1}/{total_count}] 이미 완료된 항목입니다. (패스)")
            continue

        # 좌표 값 파싱
        try:
            if coord_mode == 'SINGLE_COL' and pd.notna(row[colunm_loca_head]):
                coords = str(row[colunm_loca_head]).split(',')
                lat, lon = float(coords[0].strip()), float(coords[1].strip())
            elif coord_mode == 'LAT_LON' and pd.notna(row['위도']) and pd.notna(row['경도']):
                lat = float(row['위도'])
                lon = float(row['경도'])
            else:
                raise ValueError("데이터가 비어있음(NaN)")

            target_location = {'latitude': lat, 'longitude': lon}
        except Exception as e:
            # 에러 발생 시 원본 좌표 데이터 로깅용 추출
            if coord_mode == 'SINGLE_COL':
                raw_val = row.get(colunm_loca_head, 'N/A')
            else:
                raw_val = f"(위도: {row.get('위도', 'N/A')}, 경도: {row.get('경도', 'N/A')})"

            logging.error(f"❌ [{index + 1}/{total_count}] 좌표 파싱 오류 [원래 값: {raw_val}] (사유: {e})")
            df.at[index, 'screenshot_path'] = f"좌표 오류 - 파싱 불가 (값: {raw_val})"
            processed_count += 1
            continue

        logging.info(f"[{index + 1}/{total_count}] 이동 중: {target_location}")

        # 지도 이동 및 스크린샷 촬영
        try:
            scroll_result = navi_contrl.scroll_map_to_location(logmanager, target_location, max_attempts=100)
            
            if scroll_result:
                time.sleep(2)
                screenshot_path = func_record_image.record_screenshot(
                    device=device, 
                    log_manager=logmanager, 
                    save_dir=save_dir
                )
                df.at[index, 'screenshot_path'] = screenshot_path
            else:
                df.at[index, 'screenshot_path'] = "위치 이동 못함 - 스크린샷 없음"
        except Exception as e:
            logging.error(f"❌ [{index + 1}/{total_count}] 작업 진행 중 오류 발생: {e}")
            df.at[index, 'screenshot_path'] = f"작업 중 오류 발생 ({e})"

        # 실제 작업 수행 건수 증가
        processed_count += 1

        # 10개 작업 처리할 때마다 intermediate 저장
        if processed_count % 10 == 0:
            df.to_excel(file_path, index=False)
            logging.info(f"💾 [실제 작업 {processed_count}건 완료] 중간 저장 실행: {file_path}")

    # 최종 저장
    df.to_excel(file_path, index=False)
    logging.info(f"✅ 모든 작업 완료! 최종 Excel 저장 완료 (총 {processed_count}건 신규 처리): {file_path}")
    return 0