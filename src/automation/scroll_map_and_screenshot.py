#nv_nkm_tool.py
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


logging= loggas.logger

def start_simualtion(device:dict = None, file_path: str = None):
    # ---------------------------------------------------------
    # 1. Excel 파일 경로 검증 및 저장 폴더 생성
    # ---------------------------------------------------------
    if not file_path or not os.path.exists(file_path):
        logging.error(f"❌ Excel 파일이 존재하지 않거나 경로가 잘못되었습니다: {file_path}")
        return -1

    # 전달받은 file_path 기준 저장 폴더 설정 (파일 위치 / 색상유도선)
    base_dir = os.path.dirname(os.path.abspath(file_path))
    save_dir = os.path.join(base_dir, "색상유도선")
    os.makedirs(save_dir, exist_ok=True)
    logging.info(f"📂 스크린샷 저장 폴더 설정 완료: {save_dir}")

    # ---------------------------------------------------------
    # 2. 디바이스 연결 및 초기화
    # ---------------------------------------------------------
    if not device:
        logging.error(f"❌ 디바이스가 없습니다.")
        return -1

    logmanager = func_logging.AndroidLogManager(device=device)
    navi_contrl = func_device.NaviController(device=device)

    logmanager.start_live_logging()
    logging.info('로그를 받기 위해 10초간 대기합니다.')
    time.sleep(10)

    # ---------------------------------------------------------
    # 3. Excel 파일 읽기
    # ---------------------------------------------------------
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        logging.error(f"❌ Excel 파일을 읽는 중 오류 발생: {e}")
        return -1

    # 'screenshot_path' 컬럼 추가 위치 설정
    if 'screenshot_path' not in df.columns:
        if '좌표값' in df.columns:
            target_idx = df.columns.get_loc('좌표값') + 1
        elif '경도' in df.columns:
            target_idx = df.columns.get_loc('경도') + 1
        else:
            target_idx = len(df.columns)
            
        df.insert(target_idx, 'screenshot_path', None)

    df['screenshot_path'] = df['screenshot_path'].astype(object)

    # ---------------------------------------------------------
    # 4. 각 행 순회 및 지도 이동 / 스크린샷 저장
    # ---------------------------------------------------------
    total_count = len(df)
    
    for index, row in df.iterrows():
        # 이미 스크린샷 경로가 존재하는 경우 Pass
        current_path = row.get('screenshot_path')
        if pd.notna(current_path) and str(current_path).strip() != "":
            logging.info(f"⏩ [{index + 1}/{total_count}] 이미 완료된 항목입니다. (패스): {row.get('자치구', '')}")
            continue

        # 좌표 추출
        if '좌표값' in row and pd.notna(row['좌표값']):
            lat, lon = map(float, str(row['좌표값']).split(','))
        else:
            lat = float(row['위도'])
            lon = float(row['경도'])

        target_location = {'latitude': lat, 'longitude': lon}
        logging.info(f"[{index + 1}/{total_count}] 이동 중: {row.get('자치구', '')} - {target_location}")

        # 지도 이동 및 스크린샷 촬영
        scroll_result = navi_contrl.scroll_map_to_location(logmanager, target_location, max_attempts=100)
        
        if scroll_result:
            time.sleep(2)
            # save_dir 폴더로 저장 경로를 전달 (record_screenshot 내부 구현에 맞춰 매개변수 사용)
            screenshot_path = func_record_image.record_screenshot(
                device=device, 
                log_manager=logmanager, 
                save_dir=save_dir
            )
            df.at[index, 'screenshot_path'] = screenshot_path
        else:
            df.at[index, 'screenshot_path'] = "위치 이동 못함 - 스크린샷 없음"

        # 50개 항목마다 intermediate 저장
        if (index + 1) % 50 == 0:
            df.to_excel(file_path, index=False)
            logging.info(f"💾 [{index + 1}/{total_count}] 중간 저장 완료: {file_path}")

    # 최종 저장
    df.to_excel(file_path, index=False)
    logging.info(f"✅ 모든 작업 완료! 최종 Excel 저장 완료: {file_path}")
    return 0