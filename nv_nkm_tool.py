#nv_nkm_tool.py
import os
import sys
import re
import math
import time
from PyQt6.QtWidgets import QApplication


import pandas as pd


# 1. 유틸리티 (설정, 로거 등)
from src.utils import loggas, configus

# 2. 핵심 로직 및 디바이스 제어 모듈 (core)
from src.core import (
    func_device,
    call_device,  
    func_ios, 
    func_ui_class,
    func_logging,
    func_record_image,
    location_utils
)

# 3. UI 메인 윈도우 모듈 (ui)
from src.ui import ui_nk  # (기존 ui_nk)

logging= loggas.logger

def check_local_path():
    config = configus.load_config("resources/configs/config.json")
    if os.path.isdir(config['local_path']) is False:
        logging.info('no dir in local')
        config['local_path'] = os.path.join(os.path.expanduser('~'),'Desktop','NKM_Tool')
        os.makedirs(config['local_path'], exist_ok=True)
        configus.save_config(config,'resources/configs/config.json')
    return config

config = check_local_path()

revision_list=[
    'Revision list',
    'v0.1 (2026-01-29) : proto type release (beta ver.)',
    'v0.2 (2026-02-02) : merge nkm and tdw // add log function',
    'v0.3 (2026-02-10) : add location detect function.',
    'v0.31 (2026-02-10) : bug fix - wrong location detected.',
    'v0.32 (2026-02-11) : bug fix - mv debug error',
    'v0.33 (2026-02-13) : log filtering logic change',
    'v0.4  (2026-03-09) : add UI ',
    'v0.41 (2026-03-10) : bug fix + add upload file',
    'v0.42 (2026-04-01) : change demo mode, change SW checking.',
    'v0.43 (2026-04-09) : add FTS function - only ENG.',
    'v0.44 (2026-04-29) : modify demo mode function',
    'v0.5 (2026-04-29) : modify engineering mode',
    'v0.51 (2026-06-09) : modify position info',
    'v0.6 (2026-07-01) : Changed to automatically select button location',
    'v0.61 (2026-07-02) : bug fix (default folder update // s11 bug fix.)',
    'v0.62 (2026-07-06) : setting icon find function chaged- much faster',
    'v0.63 (2026-07-08) : add screenshot function with log checking',
    'v0.7 (2026-07-21) : connect/disconnect function and selecting devices',
    'v0.71 (2026-07-22) : add KOR lang input fuction',
    'v0.72 (2026-07-23) : bug fix - double consonants not input',
    'v0.73 (2026-08-04) : bug fix - demo mode setting.',
    'v0.8 (2026-08-06) : update to use on dada system',
    'v0.81 (2026-08-10) : add filter for log and screenshot to each folder',
    ]


last_v = "v0.0"  # 기본값 백업
for item in reversed(revision_list):
    match = re.search(r'^(v\d+\.\d+)', item.strip())
    if match:
        last_v = match.group(1)
        break

# 2. 찾은 버전을 툴 이름 뒤에 붙여줍니다.
version = f'nkm Tool {last_v}'


#===============================================여기 아래부터 =====================================================
def get_distance_and_bearing(lat1, lon1, lat2, lon2):
    """두 WGS84 좌표 간의 거리(m) 및 dx, dy(m) 차이 계산"""
    R = 6371000.0  # 지구 반지름 (m)

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance_m = R * c

    dy_m = delta_phi * R
    dx_m = delta_lambda * R * math.cos((phi1 + phi2) / 2)

    return distance_m, dx_m, dy_m

def zoom_in_simple(device):
    """단순 1단계 확대"""
    screen_width, screen_height = map(int, device["resolution"].split("x"))
    center = {"x": screen_width // 2, "y": screen_height // 2}
    func_device.one_finger_touch(device, pos=center)
    time.sleep(0.1)
    func_device.one_finger_touch(device, pos=center)
    time.sleep(1)


def zoom_out_simple(device):
    """단순 1단계 축소 (두 손가락 간격을 100px로 넓혀 명확한 터치 처리)"""
    screen_width, screen_height = map(int, device["resolution"].split("x"))
    cx, cy = screen_width // 2, screen_height // 2
    p1 = {"x": cx, "y": cy}
    p2 = {"x": cx, "y": cy}
    func_device.two_finger_touch(device, pos1=p1, pos2=p2)
    time.sleep(0.1)
    func_device.two_finger_touch(device, pos1=p1, pos2=p2)
    time.sleep(1)


def scroll_map_to_location_old(
    device, logmanager, target_location, max_attempts=50
):
    """도착 판정 범위 완화 및 오버슈팅(진동) 감지 시 강제 Zoom In 기능 추가"""
    THRESHOLD_METERS = 25.0  # 도달 기준 오차 완화

    resolution = device["resolution"]
    screen_width, screen_height = map(int, resolution.split("x"))
    center_x, center_y = screen_width // 2, screen_height // 2
    center_pos = {"x": center_x, "y": center_y}

    attempts = 0
    target_lat = target_location["latitude"]
    target_lon = target_location["longitude"]

    # --- 오버슈팅 감지용 변수 ---
    prev_scale = None
    scale_same_count = 0
    prev_distance = None

    while attempts < max_attempts:
        attempts += 1

        # 1. 최신 위치 및 스케일 가져오기
        try:
            current_log = logmanager.latest_car_pos[1]
            current_location = location_utils.convert_nds_wgs(
                location_utils.ext_nds_pos_from_log(current_log)
            )
            current_scale_km = float(
                location_utils.parse_map_scale_km(current_log)
            )

            curr_lat = float(current_location["latitude"])
            curr_lon = float(current_location["longitude"])
        except Exception as e:
            logging.error(f"로그 추출 실패: {e}")
            return False

        # 2. 남은 거리 계산
        distance_m, dx_m, dy_m = get_distance_and_bearing(
            curr_lat, curr_lon, target_lat, target_lon
        )
        logging.info(
            f"[{attempts}/{max_attempts}] 위치: ({curr_lat:.5f}, {curr_lon:.5f}) | 스케일: {current_scale_km}km | 남은거리: {distance_m/1000.0:.2f}km ({distance_m:.1f}m)"
        )

        # 3. 목표 도달 확인 및 50m(0.05km) 최종 스케일 보정
        if distance_m <= THRESHOLD_METERS:
            logging.info(
                f"🎯 목표 위치 도달! (오차: {distance_m:.1f}m <= {THRESHOLD_METERS}m) -> 50m(0.05km) final scale 보정"
            )

            for _ in range(6):
                current_log = logmanager.latest_car_pos[1]
                current_scale_km = float(
                    location_utils.parse_map_scale_km(current_log)
                )

                if current_scale_km > 0.04:
                    logging.info(
                        f"🔍 [Final Scale] 현재({current_scale_km}km) > 0.05km -> Zoom In"
                    )
                    zoom_in_simple(device)
                elif current_scale_km < 0.03:
                    logging.info(
                        f"🚀 [Final Scale] 현재({current_scale_km}km) < 0.05km -> Zoom Out"
                    )
                    zoom_out_simple(device)
                else:
                    logging.info(
                        f"✅ 최종 50m 스케일 세팅 완료! (현재 맵 스케일: {current_scale_km}km)"
                    )
                    break

            return True

        # --- 3-1. [신규] 동일 스케일 연속 오버슈팅/진동 감지 로직 ---
        if prev_scale is not None and current_scale_km == prev_scale:
            scale_same_count += 1
        else:
            scale_same_count = 1
            prev_scale = current_scale_km

        # 동일 스케일이 3회 이상 유지되면서 거리가 더 좁혀지지 않고 주변에서 맴돌 때
        force_zoom_in = False
        if scale_same_count >= 3 and prev_distance is not None:
            # 이전 시도 대비 거리가 줄어들지 않았거나, 진동하고 있는 패턴으로 판단
            if distance_m >= prev_distance * 0.8:
                logging.warning(
                    f"⚠️ [{scale_same_count}회 연속 동일 스케일({current_scale_km}km)] "
                    f"오버슈팅 감지(남은거리: {distance_m:.1f}m) -> 강제 Zoom In 실행"
                )
                force_zoom_in = True
                scale_same_count = 0  # 카운터 초기화

        prev_distance = distance_m

        # 강제 Zoom In 발생 시 스케일을 낮추고 계속 진행
        if force_zoom_in:
            func_device.one_finger_touch(device, pos=center_pos)
            func_device.one_finger_touch(device, pos=center_pos)
            time.sleep(0.5)
            continue  # 스케일을 낮췄으므로 바로 다음 루프에서 위치 re-check

        # 4. 남은 거리에 따른 '적정 목표 스케일' 설정
        if distance_m >= 150000:       # 150km 이상
            ideal_scale = 100.0
        elif distance_m >= 80000:      # 80km ~ 150km
            ideal_scale = 50.0
        elif distance_m >= 30000:      # 30km ~ 80km
            ideal_scale = 20.0
        elif distance_m >= 10000:      # 10km ~ 30km
            ideal_scale = 5.0
        elif distance_m >= 3000:       # 3km ~ 10km
            ideal_scale = 2.0
        elif distance_m >= 1000:       # 1km ~ 3km
            ideal_scale = 0.5
        elif distance_m >= 300:        # 300m ~ 1km
            ideal_scale = 0.2
        elif distance_m >= 100:        # 100m ~ 300m
            ideal_scale = 0.1
        elif distance_m >= 50:         # 50m ~ 100m
            ideal_scale = 0.05
        else:                          # 50m 미만
            ideal_scale = 0.02

        # 5. 스케일 한 번에 쭉 올리기/내리기 (Jump Zoom)
        if current_scale_km < ideal_scale * 0.3:
            logging.info(
                f"🚀 [Scale Jump] 현재({current_scale_km}km) -> 목표({ideal_scale}km) 연속 Zoom Out"
            )
            temp_scale = current_scale_km
            tap_count = 0
            while temp_scale < ideal_scale * 0.7 and tap_count < 6:
                func_device.two_finger_touch(
                    device, pos1=center_pos, pos2=center_pos
                )
                func_device.two_finger_touch(
                    device, pos1=center_pos, pos2=center_pos
                )
                temp_scale *= 4.0
                tap_count += 1
                time.sleep(0.1)

            time.sleep(0.6)
            continue

        elif current_scale_km > ideal_scale * 2.0:
            logging.info(
                f"🔍 [Scale Jump] 현재({current_scale_km}km) -> 목표({ideal_scale}km) 연속 Zoom In"
            )
            temp_scale = current_scale_km
            tap_count = 0
            while temp_scale > ideal_scale * 1.3 and tap_count < 6:
                func_device.one_finger_touch(device, pos=center_pos)
                func_device.one_finger_touch(device, pos=center_pos)
                temp_scale /= 4.0
                tap_count += 1
                time.sleep(0.1)

            time.sleep(0.6)
            continue

        # 6. 스와이프 계산 및 픽셀 무한루프 방지
        map_scale_m = current_scale_km * 1000.0
        screen_radius_px = min(screen_width, screen_height) * 0.35

        swipe_dx = -1 * (dx_m / (map_scale_m + 1e-5)) * screen_radius_px
        swipe_dy = (dy_m / (map_scale_m + 1e-5)) * screen_radius_px

        swipe_len = math.sqrt(swipe_dx**2 + swipe_dy**2)

        if swipe_len < 15.0:
            logging.info(
                f"계산된 스와이프 거리({swipe_len:.1f}px)가 너무 작아 스와이프 생략 후 도달 판정 단계로 넘어갑니다."
            )
            distance_m = 0.0
            continue

        max_swipe_px = min(screen_width, screen_height) * 0.35
        if swipe_len > max_swipe_px:
            swipe_dx = (swipe_dx / swipe_len) * max_swipe_px
            swipe_dy = (swipe_dy / swipe_len) * max_swipe_px

        pos1 = {"x": int(center_x), "y": int(center_y)}
        pos2 = {"x": int(center_x + swipe_dx), "y": int(center_y + swipe_dy)}

        func_device.swipe_window(device, pos1=pos1, pos2=pos2)
        time.sleep(0.8)

    logging.warning(
        f"⚠️ 최대 시도 횟수({max_attempts}회) 초과로 중단합니다."
    )
    return False




def scroll_map_to_location(
    device, logmanager, target_location, max_attempts=50
):
    """도착 판정 범위 완화 및 오버슈팅(진동) 감지 시 강제 Zoom In 기능 추가"""
    THRESHOLD_METERS = 25.0  # 도달 기준 오차 완화

    resolution = device["resolution"]
    screen_width, screen_height = map(int, resolution.split("x"))
    center_x, center_y = screen_width // 2, screen_height // 2
    center_pos = {"x": center_x, "y": center_y}

    attempts = 0
    target_lat = target_location["latitude"]
    target_lon = target_location["longitude"]

    # --- 오버슈팅 감지용 변수 ---
    prev_scale = None
    scale_same_count = 0
    prev_distance = None
    has_overshot = False  # 한번이라도 오버슈팅 감지되면 Zoom Out(스케일 올리기) 차단

    while attempts < max_attempts:
        attempts += 1

        # 1. 최신 위치 및 스케일 가져오기
        try:
            current_log = logmanager.latest_car_pos[1]
            current_location = location_utils.convert_nds_wgs(
                location_utils.ext_nds_pos_from_log(current_log)
            )
            current_scale_km = float(
                location_utils.parse_map_scale_km(current_log)
            )

            curr_lat = float(current_location["latitude"])
            curr_lon = float(current_location["longitude"])
        except Exception as e:
            logging.error(f"로그 추출 실패: {e}")
            return False

        # 2. 남은 거리 계산
        distance_m, dx_m, dy_m = get_distance_and_bearing(
            curr_lat, curr_lon, target_lat, target_lon
        )
        logging.info(
            f"[{attempts}/{max_attempts}] 위치: ({curr_lat:.5f}, {curr_lon:.5f}) | 스케일: {current_scale_km}km | 남은거리: {distance_m/1000.0:.2f}km ({distance_m:.1f}m)"
        )

        # 3. 목표 도달 확인 및 50m(0.05km) 최종 스케일 보정
        if distance_m <= THRESHOLD_METERS:
            logging.info(
                f"🎯 목표 위치 도달! (오차: {distance_m:.1f}m <= {THRESHOLD_METERS}m) -> 50m(0.05km) final scale 보정"
            )

            for _ in range(6):
                current_log = logmanager.latest_car_pos[1]
                current_scale_km = float(
                    location_utils.parse_map_scale_km(current_log)
                )

                if current_scale_km > 0.04:
                    logging.info(
                        f"🔍 [Final Scale] 현재({current_scale_km}km) > 0.05km -> Zoom In"
                    )
                    zoom_in_simple(device)
                elif current_scale_km < 0.01 and not has_overshot:
                    # 오버슈팅 차단 상태가 아닐 때만 Zoom Out 허용
                    logging.info(
                        f"🚀 [Final Scale] 현재({current_scale_km}km) < 0.05km -> Zoom Out"
                    )
                    zoom_out_simple(device)
                else:
                    logging.info(
                        f"✅ 최종 50m 스케일 세팅 완료! (현재: {current_scale_km}km)"
                    )
                    break

            return True

        # --- 3-1. 동일 스케일 연속 오버슈팅/진동 감지 로직 ---
        if prev_scale is not None and current_scale_km == prev_scale:
            scale_same_count += 1
        else:
            scale_same_count = 1
            prev_scale = current_scale_km

        # 동일 스케일이 3회 이상 유지되면서 거리가 더 좁혀지지 않고 주변에서 맴돌 때
        force_zoom_in = False
        if scale_same_count >= 3 and prev_distance is not None:
            # [수정] 남은 거리가 1km 이하일 때만 오버슈팅으로 판단하도록 조건 완화
            if distance_m >= prev_distance * 0.8 and distance_m <= 1000:
                logging.warning(
                    f"⚠️ [{scale_same_count}회 연속 동일 스케일({current_scale_km}km)] "
                    f"오버슈팅 감지(남은거리: {distance_m:.1f}m) -> 강제 Zoom In 실행 및 Zoom Out 금지 설정"
                )
                force_zoom_in = True
                has_overshot = True  # 오버슈팅 발생 플래그 고정 (이후 스케일 올려서 확대/축소 진동하는 것 막음)
                scale_same_count = 0  # 카운터 초기화

        prev_distance = distance_m

        # 강제 Zoom In 발생 시 스케일을 낮추고(확대) 바로 다음 루프로
        if force_zoom_in:
            zoom_in_simple(device)
            continue  # 스케일을 낮췄으므로 바로 다음 루프에서 위치 re-check

        # 4. 남은 거리에 따른 '적정 목표 스케일' 설정
        if distance_m >= 150000:       # 150km 이상
            ideal_scale = 100.0
        elif distance_m >= 80000:      # 80km ~ 150km
            ideal_scale = 50.0
        elif distance_m >= 30000:      # 30km ~ 80km
            ideal_scale = 20.0
        elif distance_m >= 10000:      # 10km ~ 30km
            ideal_scale = 5.0
        elif distance_m >= 3000:       # 3km ~ 10km
            ideal_scale = 2.0
        elif distance_m >= 1000:       # 1km ~ 3km
            ideal_scale = 0.5
        elif distance_m >= 300:        # 300m ~ 1km
            ideal_scale = 0.2
        elif distance_m >= 100:        # 100m ~ 300m
            ideal_scale = 0.1
        elif distance_m >= 50:         # 50m ~ 100m
            ideal_scale = 0.05
        else:                          # 50m 미만
            ideal_scale = 0.02

        # 5. 스케일 한 번에 쭉 올리기/내리기 (Jump Zoom)
        if current_scale_km < ideal_scale * 0.3:
            # 오버슈팅이 한 번이라도 발생했다면 지도 스케일을 키우는(Zoom Out) 동작을 금지함
            if has_overshot:
                logging.info(
                    f"🛡️ [Overshoot Guard] 현재({current_scale_km}km) < 목표({ideal_scale}km) 이지만 오버슈팅 이력으로 Zoom Out 차단"
                )
            else:
                logging.info(
                    f"🚀 [Scale Jump] 현재({current_scale_km}km) -> 목표({ideal_scale}km) 연속 Zoom Out"
                )
                temp_scale = current_scale_km
                tap_count = 0
                while temp_scale < ideal_scale * 0.7 and tap_count < 6:
                    zoom_out_simple(device)
                    temp_scale *= 4.0
                    tap_count += 1

                time.sleep(0.6)
                continue

        elif current_scale_km > ideal_scale * 2.0:
            logging.info(
                f"🔍 [Scale Jump] 현재({current_scale_km}km) -> 목표({ideal_scale}km) 연속 Zoom In"
            )
            temp_scale = current_scale_km
            tap_count = 0
            while temp_scale > ideal_scale * 1.3 and tap_count < 6:
                zoom_in_simple(device)
                temp_scale /= 4.0
                tap_count += 1

            time.sleep(0.6)
            continue

        # 6. 스와이프 계산 및 픽셀 무한루프 방지
        map_scale_m = current_scale_km * 1000.0
        screen_radius_px = min(screen_width, screen_height) * 0.35

        swipe_dx = -1 * (dx_m / (map_scale_m + 1e-5)) * screen_radius_px
        swipe_dy = (dy_m / (map_scale_m + 1e-5)) * screen_radius_px

        swipe_len = math.sqrt(swipe_dx**2 + swipe_dy**2)

        if swipe_len < 15.0:
            logging.info(
                f"계산된 스와이프 거리({swipe_len:.1f}px)가 너무 작아 스와이프 생략 후 도달 판정 단계로 넘어갑니다."
            )
            distance_m = 0.0
            continue

        max_swipe_px = min(screen_width, screen_height) * 0.35
        if swipe_len > max_swipe_px:
            swipe_dx = (swipe_dx / swipe_len) * max_swipe_px
            swipe_dy = (swipe_dy / swipe_len) * max_swipe_px

        pos1 = {"x": int(center_x), "y": int(center_y)}
        pos2 = {"x": int(center_x + swipe_dx), "y": int(center_y + swipe_dy)}

        func_device.swipe_window(device, pos1=pos1, pos2=pos2)
        time.sleep(0.8)

    logging.warning(
        f"⚠️ 최대 시도 횟수({max_attempts}회) 초과로 중단합니다."
    )
    return False

def debug_mode():
    # call_device.start_adb_server()
    devices = call_device.discover_and_connect_device()
    # ios_control = func_ios.IOSDeviceController(lockdown_device=devices[1]['lockdown_device'])
    # ios_control.download_photos_by_date('2026-07-16')
    
    target_model = 'SM-X820'
    device = next((d for d in devices if d.get('model') == target_model), None)
    
    if not device:
        print(f"❌ 모델명이 {target_model}인 디바이스를 찾을 수 없습니다.")
        return 0

    logmanager = func_logging.AndroidLogManager(device=device)
    logmanager.start_live_logging()
    time.sleep(5)

    # 1. 다운로드 폴더 내 Excel 파일 경로 설정
    download_path = os.path.join(os.path.expanduser('~'), 'Downloads', '노면색깔유도선_WGS84_부산_위치별정리.xlsx')
    excel_file_path = download_path

    # 2. Excel 파일 읽기
    try:
        df = pd.read_excel(download_path)
    except FileNotFoundError:
        # 상대 경로 또는 현재 작업 디렉터리의 Downloads 확인
        excel_file_path = '노면색깔유도선_WGS84.xlsx'
        df = pd.read_excel(excel_file_path)

    # 3. 'screenshot_path' 컬럼 위치를 '좌표값' 또는 '경도' 바로 옆에 설정
    if 'screenshot_path' not in df.columns:
        if '좌표값' in df.columns:
            target_idx = df.columns.get_loc('좌표값') + 1
        elif '경도' in df.columns:
            target_idx = df.columns.get_loc('경도') + 1
        else:
            target_idx = len(df.columns)
            
        df.insert(target_idx, 'screenshot_path', None)

    # 컬럼의 dtype 자체를 object(문자열 가능)로 변환 (루프 시작 전 1회만 실행)
    df['screenshot_path'] = df['screenshot_path'].astype(object)

    # 4. Excel의 각 행을 순회하면서 위도/경도 추출 후 실행
    total_count = len(df)
    
    for index, row in df.iterrows():
        # 1. 이미 screenshot_path 컬럼에 값이 들어있거나, 실제 파일이 존재하는 경우 패스
        current_path = row.get('screenshot_path')
        if pd.notna(current_path) and str(current_path).strip() != "":
            # (선택 사항) 만약 파일이 '실제로 존재하는지'까지 엄격하게 검사하고 싶다면:
            # if os.path.exists(str(current_path)):
            print(f"⏩ [{index + 1}/{total_count}] 이미 완료된 항목입니다. (패스): {row.get('자치구', '')}")
            continue

        # 2. 기존 작업 수행 (좌표 추출 및 지도 이동)
        if '좌표값' in row and pd.notna(row['좌표값']):
            lat, lon = map(float, str(row['좌표값']).split(','))
        else:
            lat = float(row['위도'])
            lon = float(row['경도'])

        target_location = {'latitude': lat, 'longitude': lon}

        print(f"[{index + 1}/{total_count}] 이동 중: {row.get('자치구', '')} - {target_location}")

        # 지도 이동 및 스크린샷 촬영
        scroll_map_to_location(device, logmanager, target_location, max_attempts=100)
        time.sleep(2)
        
        screenshot_path = func_record_image.record_screenshot(device=device, log_manager=logmanager)
        
        # 스크린샷 경로 기입
        df.at[index, 'screenshot_path'] = screenshot_path

        # 50개 항목마다 Excel 저장
        if (index + 1) % 50 == 0:
            df.to_excel(excel_file_path, index=False)
            print(f"💾 [{index + 1}/{total_count}] 50개 항목 완료되어 Excel 저장됨: {excel_file_path}")

    # 전체 루프 종료 후 최종 저장
    df.to_excel(excel_file_path, index=False)
    print(f"✅ 모든 작업 완료! 최종 Excel 저장 완료: {excel_file_path}")
    return 0


def prod_mode():
    app = QApplication(sys.argv)
    ex = ui_nk.MainWindow(version, revision_list)
    ex.show()
    sys.exit(app.exec())



if __name__ == '__main__':
    loggas.set_debug_logging(True)
    # --- 사용 예시 ---
    #file_path = r"C:/Users/miskang/Downloads/서울특별시_노면색깔유도선 위치 현황_20250417.csv"
    #location_utils.convert_korea2000_to_wgs_csv(file_path)
    debug_mode()
    
    #loggas.set_debug_logging(True)
    #call_device.start_adb_server()
    #prod_mode()
    