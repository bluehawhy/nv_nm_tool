
# 1. 유틸리티 (설정, 로거 등)
import re
import math
import time
from src.utils import loggas

# 2. 핵심 로직 및 디바이스 제어 모듈 (core)
from src.core import (
    func_device,
    location_utils,
)

logging = loggas.logger

import re


def parse_map_heading(logmanager):
    """
    현재 지도 표시 모드를 반환합니다.

    반환값:
    - "north_up"   : 수직 + 북쪽 방향
    - "heading_up" : 수직 + 진행 방향
    - "3d"         : 틸트가 있는 3D 모드
    - None         : 로그 형식을 해석할 수 없음
    """
    current_log = logmanager.latest_car_pos[1]
    logging.info(f"지도 방향 로그: {current_log}")

    tilt_match = re.search(
        r"\btilt\s+([-+]?\d+(?:\.\d+)?)",
        current_log,
    )
    shift_match = re.search(
        r"\bshift\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)",
        current_log,
    )

    if not tilt_match or not shift_match:
        return None

    tilt = float(tilt_match.group(1))
    shift_x = float(shift_match.group(1))
    shift_y = float(shift_match.group(2))

    # 소수 오차까지 고려
    is_vertical = abs(tilt) < 0.01
    is_north = abs(shift_x) < 0.01 and abs(shift_y) < 0.01

    if not is_vertical:
        return "3d"

    if is_north:
        return "north_up"

    return "heading_up"



def set_north_up(device, logmanager):
    """지도 방향을 북쪽으로 고정"""
    navi_contrl = func_device.NaviController(device=device)
    current_log = logmanager.latest_car_pos
    logging.info(f"지도 방향 설정: {current_log}")


    current_heading = parse_map_heading(current_log)

    if current_heading is None:
        logging.error("지도 방향 정보를 가져올 수 없습니다.")
        return False

    if abs(current_heading) > 5.0:  # 5도 이상 차이가 나면 북쪽으로 회전
        logging.info(f"지도 방향({current_heading}°)이 북쪽과 다릅니다. 북쪽으로 회전합니다.")
        navi_contrl.set_north_up()
        time.sleep(1)  # 회전 후 잠시 대기
    else:
        logging.info(f"지도 방향({current_heading}°)이 이미 북쪽과 거의 일치합니다.")

    return True


def scroll_map_to_location(device, logmanager, target_location, max_attempts=50):
    """도착 판정 범위 완화 및 오버슈팅(진동) 감지 시 강제 Zoom In 기능 추가"""
    THRESHOLD_METERS = 25.0  # 도달 기준 오차 완화

    navi_contrl = func_device.NaviController(device=device)  # NaviController 인스턴스 생성

    screen_width, screen_height = map(int, device["resolution"].split("x"))
    center_x, center_y = screen_height // 2, screen_width // 2
    center_pos = {"x": center_x, "y": center_y}
    logging.info(f"스크롤 시작: 목표 위치({target_location['latitude']}, {target_location['longitude']})")

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
            #매번 이동후 1초 대기(정확한 좌표 및 위치 체크를 위해)
            time.sleep(1)
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
        distance_m, dx_m, dy_m = location_utils.get_distance_and_bearing(
            curr_lat, curr_lon, target_lat, target_lon
        )
        logging.info(
            f"[{attempts}/{max_attempts}] 위치: ({curr_lat:.5f}, {curr_lon:.5f}) | 스케일: {current_scale_km}km | 남은거리: {distance_m/1000.0:.2f}km ({distance_m:.1f}m)"
        )

        # 3. 목표 도달 확인 및 50m(0.05km) 최종 스케일 보정
        if distance_m <= THRESHOLD_METERS:
            logging.info(
                f"🎯 목표 위치 도달! (오차: {distance_m:.1f}m <= {THRESHOLD_METERS}m) | 스케일: {current_scale_km}km -> 50m(0.05km) final scale 보정"
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
                    navi_contrl.zoom_in()
                elif current_scale_km < 0.01 and not has_overshot:
                    # 오버슈팅 차단 상태가 아닐 때만 Zoom Out 허용
                    logging.info(
                        f"🚀 [Final Scale] 현재({current_scale_km}km) < 0.05km -> Zoom Out"
                    )
                    navi_contrl.zoom_out()
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
            navi_contrl.zoom_in()
            continue  # 스케일을 낮췄으므로 바로 다음 루프에서 위치 re-check

        # 4. 남은 거리에 따른 '적정 목표 스케일' 설정
        #if distance_m >= 150000:       # 150km 이상
        #    ideal_scale = 100.0
        if distance_m >= 80000:      # 80km ~ 150km
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
                    navi_contrl.zoom_out()
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
                navi_contrl.zoom_in()
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

        navi_contrl.swipe(pos1=pos1, pos2=pos2)
        time.sleep(0.8)

    logging.warning(
        f"⚠️ 최대 시도 횟수({max_attempts}회) 초과로 중단합니다."
    )
    return False


