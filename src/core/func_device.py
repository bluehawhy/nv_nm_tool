import os
import time
import unicodedata
import threading
import xml.etree.ElementTree as ET
import re
import json

from . import func_ui_class
from ..utils import configus, loggas
logging= loggas.logger


def get_device_type_and_res(device):
    """
    해상도 문자열(예: '1920x720')을 기반으로 기기 타입을 결정합니다.
    """
    wm_size = device['ppadb_device'].shell("wm size")
    logging.info(wm_size)
    resolution = wm_size.strip().split(":")[-1].strip()
    if not resolution or 'x' not in resolution:
        return "None", 0, 0
    try:
        # 가로, 세로 크기 추출 
        parts = resolution.split('x')
        res_x, res_y = int(parts[0]), int(parts[1])
        # 1. TWD (특정 와이드 해상도)
        if resolution == "1920x720":
            return "twd", res_x, res_y
            
        # 2. Tablet (긴 쪽이 2000 이상이거나 특정 비율 이상)
        # 태블릿은 이상하가 xy 가 반대로 됨, 변경 필요
        if max(res_x, res_y) >= 2500:
            return "tablet", max(res_x, res_y) , min(res_x, res_y)
        
        # 3. 나머지는 Mobile
        return "mobile", res_x, res_y
    except (ValueError, IndexError):
        return "None", 0, 0

def call_button_info(resolution):
    # 1. 관리용으로 정리된 JSON 로드
    data = configus.load_config('resources/configs/button_info.json')
    
    # 2. 결과값을 담을 딕셔너리
    button_info = {}
    
    if 'button_layouts' in data:
        for feature, res_map in data['button_layouts'].items():
            # 해당 해상도 좌표가 존재하는지 확인
            if resolution in res_map:
                button_info[feature] = res_map[resolution]
            else:
                # 해상도 정보가 없을 경우 0,0 처리 및 로그 남기기
                button_info[feature] = {"x": 0, "y": 0}
                logging.warning(f"⚠️ [버튼 좌표 없음] '{feature}' 기능의 해상도('{resolution}') 좌표 정보가 없어 (0, 0)으로 설정합니다.")

    # --- 키보드 레이아웃 처리 (계층 유지) ---
    if 'keyboard_layouts' in data:
        keyboard_data = data['keyboard_layouts']
        
        # 1) keyboard_lang 처리
        if 'keyboard_lang' in keyboard_data:
            res_map = keyboard_data['keyboard_lang']
            if resolution in res_map:
                button_info['keyboard_lang'] = res_map[resolution]
            else:
                button_info['keyboard_lang'] = {"x": 0, "y": 0}
                logging.warning(f"⚠️ [키보드 좌표 없음] 'keyboard_lang'의 해상도('{resolution}') 좌표 정보가 없어 (0, 0)으로 설정합니다.")
            
        # 2) han_keyboard 처리 (자음/모음 계층 유지)
        if 'han_keyboard' in keyboard_data:
            button_info['han_keyboard'] = {}
            for key, res_map in keyboard_data['han_keyboard'].items():
                if resolution in res_map:
                    button_info['han_keyboard'][key] = res_map[resolution]
                else:
                    button_info['han_keyboard'][key] = {"x": 0, "y": 0}
                    logging.warning(f"⚠️ [한글 키보드 좌표 없음] han_keyboard['{key}']의 해상도('{resolution}') 좌표 정보가 없어 (0, 0)으로 설정합니다.")
    return button_info

def update_button(res, button_name, location, config_file="resources/configs/button_info.json"):
    """
    res: 기기 해상도 (예: "1752x2800")
    button_name: JSON 내 button_layouts의 자식 키 (예: "ui_guidance_off")
    location: 입력받을 좌표 딕셔너리 (예: {'x': 277, 'y': 907})
    """
    # 1. 전달받은 location 디버깅 출력
    logging.info(f"[{button_name}] 업데이트 시도 좌표: {location}")
    print(f"[{button_name}] 위치 업데이트")
    
    x_val, y_val = None, None

    # 2. {'x': 277, 'y': 907} 형태를 가장 먼저 파싱
    if isinstance(location, dict):
        if 'x' in location and 'y' in location:
            x_val = location['x']
            y_val = location['y']
        else:
            # 기존 OCR 매칭 결과인 엉뚱한 딕셔너리 형태 방어용
            first_val = next(iter(location.values()), None)
            if isinstance(first_val, (list, tuple)) and len(first_val) >= 2:
                x_val, y_val = first_val[0], first_val[1]
                
    elif isinstance(location, (list, tuple)) and len(location) >= 2:
        x_val, y_val = location[0], location[1]

    # 좌표 추출 실패 시 예외 처리
    if x_val is None or y_val is None:
        logging.info(f"❌ [{button_name}] 업데이트 실패: 유효한 x, y 좌표를 파싱할 수 없습니다.")
        return False

    # 3. JSON 설정 데이터 로드
    config_data = configus.load_config(config_file)
    if not config_data:
        logging.info(f"❌ [{button_name}] 업데이트 실패: {config_file} 파일 로드 실패.")
        return False

    try:
        # 4. JSON 트리 구조 안전하게 접근 및 생성
        if "button_layouts" not in config_data:
            config_data["button_layouts"] = {}
        if button_name not in config_data["button_layouts"]:
            config_data["button_layouts"][button_name] = {}
        if res not in config_data["button_layouts"][button_name]:
            config_data["button_layouts"][button_name][res] = {}

        # -----------------------------------------------------------------
        # [추가] 기존 설정 값과 입력 값이 동일한지 체크 (중복 업데이트 방지)
        # -----------------------------------------------------------------
        current_target = config_data["button_layouts"][button_name][res]
        if "x" in current_target and "y" in current_target:
            if current_target["x"] == int(x_val) and current_target["y"] == int(y_val):
                logging.info(f"🔄 [{res}] {button_name} 좌표가 기존 설정과 동일합니다. (x: {int(x_val)}, y: {int(y_val)}) 업데이트를 건너뜁니다.")
                return True  # 이미 반영되어 있으므로 성공으로 간주하고 리턴
        # -----------------------------------------------------------------

        # 5. 순수 정수(int)형으로 캐스팅 후 값 할당
        config_data["button_layouts"][button_name][res]["x"] = int(x_val)
        config_data["button_layouts"][button_name][res]["y"] = int(y_val)

        # 6. 파일 저장
        configus.save_config(config_data, config_file)
        logging.info(f"[{res}] {button_name} 좌표 등록 성공 -> x: {int(x_val)}, y: {int(y_val)}")
        return True

    except Exception as e:
        logging.info(f"[{button_name}] JSON 업데이트 중 예외 발생: {e}")
        return False

#================================== basic func ==================================
def one_finger_touch(device, pos):
    """
    ppadb의 device_obj를 사용하여 특정 좌표(x, y)를 터치합니다.
    """
    # 1. 요청하신 대로 ppadb 객체 추출
    device_obj = device['ppadb_device']
    serial = device_obj.serial
    #logging.info(device_obj)
    x, y = str(pos['x']), str(pos['y'])
    
    # 2. 명령어 구성 (adb prefix 없이 shell 내부 명령만 전달)    
    try:
        logging.info(f"[{serial}] Touch Window: ({x}, {y})")
        
        # 3. ppadb를 통한 실행 (subprocess보다 훨씬 빠름)
        # 결과를 기다리지 않는 비동기식 클릭 (매우 빠른 연타 시 유리)
        device_obj.shell(f"input tap {x} {y}")
        return 0
    except Exception as e:
        logging.error(f"[{serial}] Touch Error: {e}")
        return -1

def two_finger_touch(device, pos1, pos2):
    '''
    두 지점을 동시에 터치하는 기능
    pos1, pos2 는 {x:, y:}, {x:, y:} 구조여야 합니다.
    '''
    d = device['u2_device']
    # 외부 변수 d 대신 인자로 받은 device를 사용하도록 수정
    map_view = d()
    
    # uiautomator2의 gesture 기능을 이용해 두 손가락 터치 수행
    map_view.gesture(
        (pos1['x'], pos1['y']), # finger1 start
        (pos2['x'], pos2['y']), # finger2 start
        (pos1['x'], pos1['y']), # finger1 end
        (pos2['x'], pos2['y'])  # finger2 end
    )

def swipe_window(device, pos1, pos2, duration : int = 600):
    """
    ppadb의 device_obj를 사용하여 화면을 스와이프합니다.
    pos1에서 pos2까지 지정된 시간(duration, ms) 동안 이동합니다.
    """
    # 1. ppadb 객체 및 시리얼 추출
    device_obj = device['ppadb_device']
    serial = device_obj.serial
    
    # 좌표 및 시간 추출
    x1, y1 = pos1['x'], pos1['y']
    x2, y2 = pos2['x'], pos2['y']
    
    # 2. 명령어 구성 (input swipe x1 y1 x2 y2 duration)
    command = f"input swipe {x1} {y1} {x2} {y2} {duration}"
    
    try:
        logging.info(f"[{serial}] Swipe Window: ({x1}, {y1}) -> ({x2}, {y2}) over {duration}ms")
        
        # 3. ppadb를 통한 실행
        # 스와이프는 동작이 끝날 때까지 기다리는 것이 흐름 제어에 유리하므로 &를 빼고 실행하는 것을 권장합니다.
        device_obj.shell(command)
        return 0
    except Exception as e:
        logging.error(f"[{serial}] 스와이프 작업 중 에러 발생: {e}")
        return -1

def repeat_swipe_window(device,pos1,pos2,duration : int = 600,cnt_reqeat : int = 0):
    cnt_scroll= 0
    while cnt_scroll < cnt_reqeat:
        cnt_scroll += 1
        swipe_window(device,pos1,pos2,duration)
        time.sleep(0.1)
    return 0

def input_text(device, text):
    """
    ppadb의 device_obj를 사용하여 텍스트를 입력합니다.
    공백은 안드로이드 input 시스템이 인식하도록 %s로 치환하여 전달합니다.
    """
    # 1. ppadb 객체 및 시리얼 추출
    device_obj = device['ppadb_device']
    serial = device_obj.serial
    
    # 2. 텍스트 안전 처리 (공백 치환)
    if isinstance(text, (int, float)):
        safe_text = str(text)
    else:
        # 안드로이드 input text는 공백을 허용하지 않으므로 %s로 예약어 치환
        # (기존에 잘 동작하던 로직 유지)
        safe_text = str(text).replace(" ", "%s")

    # 3. 명령어 구성
    # 쉘 명령어로 전달할 때는 따옴표로 감싸주는 것이 안전합니다.
    command = f'input text "{safe_text}"'
    
    try:
        logging.info(f"[{serial}] Input Text: {text} (Encoded: {safe_text})")
        
        # 4. ppadb를 통한 실행
        device_obj.shell(command)
        
        return 0
    except Exception as e:
        logging.error(f"[{serial}] Input Text Error {e}")
        return -1

def send_keyevent(device, keycode):
    """
    ppadb의 device_obj를 사용하여 안드로이드 키 이벤트를 전송합니다.
    주요 키코드:
    3  - Home
    4  - Back
    66 - Enter
    67 - Backspace
    """
    # 1. ppadb 객체 및 시리얼 추출
    device_obj = device['ppadb_device']
    serial = device_obj.serial
    
    # 2. 명령어 구성
    command = f"input keyevent {keycode}"
    
    try:
        logging.info(f"[{serial}] Send Keyevent: {keycode}")
        
        # 3. ppadb를 통한 실행
        device_obj.shell(command)
        
        return 0
    except Exception as e:
        logging.error(f"[{serial}] Send Keyevent Error: {e}")
        return -1
    
def push_file_background(device, local_file, device_file):
    device_obj = device['ppadb_device']
    serial = device_obj.serial
    
    def _run_push():
        progress_file_path = "resources/info/push_progress.txt"
        os.makedirs(os.path.dirname(progress_file_path), exist_ok=True)
        
        if os.path.exists(progress_file_path):
            os.remove(progress_file_path)

        try:
            # 1. 로컬 파일 크기 확인
            if not os.path.exists(local_file):
                return
            local_size = os.path.getsize(local_file)

            # 2. 리모트 파일 크기 확인 (동일 파일 체크용)
            remote_size_str = device_obj.shell(f"stat -c %s {device_file}").strip()
            try:
                remote_size = int(remote_size_str)
            except (ValueError, TypeError):
                remote_size = -1

            # 3. ★ 동일 용량이면 즉시 종료 (스킵 로직) ★
            if local_size == remote_size:
                with open(progress_file_path, "w", encoding="utf-8") as f:
                    f.write("Completed (Already exists)\n")
                return

            # 4. 진행률 콜백 정의 (인자 순서 보정 포함)
            def progress_callback(filename, arg1, arg2):
                current_copied = min(arg1, arg2)
                total_size = max(arg1, arg2)
                
                if total_size > 0:
                    copied_mb = round(current_copied / (1024 * 1024), 2)
                    total_mb = round(total_size / (1024 * 1024), 2)
                    percent = int((current_copied / total_size) * 100)
                    
                    log_line = f"{copied_mb}MB / {total_mb}MB / {percent}%"
                    with open(progress_file_path, "a", encoding="utf-8") as f:
                        f.write(f"{log_line}\n")

            # 5. 전송 시작 알림 기록
            with open(progress_file_path, "a", encoding="utf-8") as f:
                f.write("Pushing... 0%\n")

            # 6. 실제 전송 실행
            device_obj.push(local_file, device_file, 0o644, progress_callback)

            # 7. 최종 완료 기록
            with open(progress_file_path, "a", encoding="utf-8") as f:
                f.write("Completed\n")

        except Exception as e:
            with open(progress_file_path, "a", encoding="utf-8") as f:
                f.write(f"Error: {str(e)}\n")
        
        finally:
            # UI에서 확인 가능하도록 5초 대기 후 삭제
            time.sleep(5)
            if os.path.exists(progress_file_path):
                os.remove(progress_file_path)

    threading.Thread(target=_run_push, daemon=True).start()

def swipe_window_til_latter(device, latter, x_latters=['개발 설정', 'engineering']):
    '''
    화면을 스크롤하며 목표 UI(latter: string)를 탐색합니다.
    화면 바닥에 도달하거나 정체되면 False를, 찾으면 좌표 지점을 리턴합니다.
    '''
    # TODO: 속도 개선
    device_type, res_x, res_y = get_device_type_and_res(device)
    d = device["u2_device"]
    
    # 1. 기준점(X좌표용) 탐색
    locations = func_ui_class.find_location_by_UI_class(device, x_latters)
    logging.info(f"기준점 탐색 결과: {locations}")
    
    # 2. x_latters 중 화면에 존재하는 기준점 채택
    navi_key = None
    for target in x_latters:
        if target in locations:
            navi_key = target
            break
            
    if not navi_key:
        logging.error(f"제시된 기준 메뉴 {x_latters}를 찾지 못해 업데이트를 스킵합니다.")
        return False

    location = locations[navi_key]
    
    # 해상도 기반 스크롤 영역 계산
    swipe_start_y = int(res_y * 0.7)
    swipe_end_y = int(res_y * 0.3)
    
    # 클릭 안전 y 범위 설정 (0 <= y <= res_y * 0.9)
    max_safe_y = res_y * 0.9
    
    prev_xml = ''
    
    while True:
        time.sleep(0.1)
        # 3. 현재 화면에서 목표 string(latter) 검색
        current_locations = func_ui_class.find_location_by_UI_class(device, [latter])
        
        # [교정] 대소문자 무시하고 키 매칭 수행
        matched_key = next((k for k in current_locations if k.lower() == latter.lower()), None)

        # 4. 일치하는 키를 찾았고, Y 좌표가 화면 안전 범위(0 ~ 90%) 내에 있는 경우에만 리턴
        if matched_key:
            target_pos = current_locations[matched_key]
            target_y = target_pos.get('y', 0)
            
            if 0 <= target_y <= max_safe_y:
                logging.info(f"🎯목표 UI '{latter}' 발견! (매칭된 키: {matched_key}, Y: {target_y}) 지점 리턴: {target_pos}")
                return target_pos
            else:
                logging.info(f"👀 UI '{latter}'를 발견했지만 화면 하단 짤림 영역(Y: {target_y} > {max_safe_y})에 있어 스크롤을 더 진행합니다.")

        # 5. 스크롤 전 현재 화면의 UI XML 상태 백업
        current_xml = d.dump_hierarchy(compressed=False, pretty=True)
        
        # 6. 이전 XML과 똑같다면 화면 바닥에 도달한 것이므로 종료
        if prev_xml == current_xml:
            logging.warning(f"스크롤 종료: '{latter}'를 찾지 못하고 화면 끝에 도달했습니다.")
            print(f"스크롤 종료: '{latter}'를 찾지 못하고 화면 끝에 도달했습니다.")
            return False
            
        prev_xml = current_xml

        # 7. 찾지 못했거나 화면 범위 밖에 있으므로 스크롤 다운 수행
        logging.info(f"Searching... '{latter}' 탐색을 위해 스크롤 진행")
        swipe_window(
            device, 
            pos1={'x': location['x'], 'y': swipe_start_y}, 
            pos2={'x': location['x'], 'y': swipe_end_y}, 
            duration=300
        )

#================================== intergrated func ==================================

def update_button_location_in_eng_mode(device):
    device_type, res_x, res_y = get_device_type_and_res(device)
    res = device['resolution']
    button_info = call_button_info(res)
    target_letters = ['개발 설정', 'engineering', 'start', 'stop', 'pause', 'repeat']
    locations = func_ui_class.find_location_by_UI_class(device, target_letters)
    time.sleep(0.1)
    eng_locations = func_ui_class.find_eng_back_button_by_UI(device)
    time.sleep(0.1)
    switch_raido_location = func_ui_class.find_eng_swith_text_by_UI(device,['simulation speed', 'night mode'])
    time.sleep(0.1)
    
    # 1. 진입 기준점 확인 (메인 메뉴 둘 다 없으면 진행 불가하므로 이 단계만 방어)
    if '개발 설정' not in locations and 'engineering' not in locations:
        logging.error("기준 메뉴('개발 설정'/'Engineering')를 찾지 못해 업데이트를 스킵합니다.")
        return button_info

    # 메인 메뉴 기준 좌표 지정
    navi_key = '개발 설정' if '개발 설정' in locations else 'engineering'
    Engineering = locations[navi_key]

    # 공통/기본 메뉴 업데이트
    if eng_locations is not None:
        update_button(res, "eng_back", {"x": eng_locations['x'], "y": eng_locations['y']})

    # 데모 제어 버튼들 각각 개별 체크 후 업데이트
    if 'start' in locations:
        update_button(res, "end_demo_on", {"x": locations['start']['x'], "y": locations['start']['y']})
        
    if 'stop' in locations:
        update_button(res, "end_demo_stop", {"x": locations['stop']['x'], "y": locations['stop']['y']})
        
    if 'pause' in locations:
        update_button(res, "end_demo_pause", {"x": locations['pause']['x'], "y": locations['pause']['y']})
        
    if 'repeat' in locations:
        update_button(res, "end_demo_repeat", {"x": locations['repeat']['x'], "y": locations['repeat']['y']})

    logging.info(switch_raido_location)

    # Simulation Speed가 있을 때만 업데이트
    if 'simulation speed' in switch_raido_location:
        update_button(res, "simulation_speed", {"x": switch_raido_location['simulation speed']['x'], "y": switch_raido_location['simulation speed']['y']})

    # Night Mode가 있을 때만 업데이트
    if 'night mode' in switch_raido_location:
        update_button(res, "night_mode", {"x": switch_raido_location['night mode']['x'], "y": switch_raido_location['night mode']['y']})


    # 최종 상태 다시 불러와서 반환
    button_info = call_button_info(res)
    return button_info

def open_navi_setting(device):
    #1. 설정이 이미 있는지 확인
    set_locations = func_ui_class.find_location_by_UI_class(device, ['설정','Settings'], package_name='navis.ncn.navi')
    if set_locations and isinstance(set_locations, dict):
        print('already setting menu opened')
        return True
    #2. 설정이 안열려 있는 경우
    location = func_ui_class.find_setting_icon_by_UI(device)
    if location is None:
        print(f"not found setting icon.... ")
        logging.info("not found setting icon....")
        return False
    one_finger_touch(device,location)

    #3. 설정 아이콘이 오픈안됫을 때
    set_locations = func_ui_class.find_location_by_UI_class(device, ['설정','Settings'])
    if not set_locations or not isinstance(set_locations, dict):
        logging.warning("텍스트 인식을 실패했거나 감지된 키워드가 없습니다. - 설정','Settings")
        print('click setting icon.')
        return False
    return True

def go_to_eng_mode(device):
    # 1. 설정 메뉴 열기
    if not open_navi_setting(device):
        return False
        
    time.sleep(0.1)
    device_type, res_x, res_y = get_device_type_and_res(device)
    swipe_x = int(res_x * 0.25)
    swipe_start_y = int(res_y * 0.7)
    swipe_end_y = int(res_y * 0.3)
    swipe_window(
        device, 
        pos1={'x': swipe_x, 'y': swipe_start_y}, 
        pos2={'x': swipe_x, 'y': swipe_end_y}, 
        duration=300
    )
    # 2. 1차 UI 검색
    locations = func_ui_class.find_location_by_UI_class(device, ['개발 설정', 'engineering'])
    logging.info(f"1차 매칭 결과: {locations}")

    # 3. 비활성화 상태인 경우에만 activate_eng 및 2차 재시도 실행
    if not locations or not isinstance(locations, dict):
        logging.warning("engineering mode 비활성화 상태. 활성화(activate_eng) 시도.")
        activate_eng(device)
        time.sleep(0.2)

        # activate_eng 수행 후 2차 확인
        if not open_navi_setting(device):
            return False
        locations = func_ui_class.find_location_by_UI_class(device, ['개발 설정', 'engineering'])
        if not locations or not isinstance(locations, dict):
            logging.warning("활성화 시도 후에도 메뉴를 찾지 못했습니다.")
            return False

    # 4. 🎯 위치를 찾았다면(1차든 2차든) 바로 터치 후 종료!
    eng_key = 'engineering' if 'engineering' in locations else '개발 설정'
    if eng_key in locations:
        eng_pos = locations[eng_key]
        logging.info(f"히든 메뉴 감지 ({eng_key}): {eng_pos}")
        one_finger_touch(device, eng_pos)
        # 1. 마찬가지로 스레드로 백그라운드 연산 유도
        time.sleep(0.1)  # 팝업 전환 및 버퍼링 대기시간 확보
        update_button_location_in_eng_mode(device)
        time.sleep(0.1)  # 팝업 전환 및 버퍼링 대기시간 확보
        return True
    return False

def activate_eng(device):
    '''
    eng mode 해제.
    1. 설정 버튼 누름
    2. 안내 종료 위치 확인 및 아래 칸 위치 조정
    3. 안내 종료 밑에 빈 칸을 10번 누름
    4. 비밀번호 해제 전달 // 프로젝트 찾아야함...
    5. 확인 누름
    '''
    device_type, res_x, res_y = get_device_type_and_res(device)
    res = device["resolution"]
    button_info = call_button_info(res)
    setting_flag = open_navi_setting(device)
    if not setting_flag:
        return False

    #스크롤 한번 하기
    device_type, res_x, res_y = get_device_type_and_res(device)
    swipe_x = int(res_x * 0.25)
    swipe_start_y = int(res_y * 0.7)
    swipe_end_y = int(res_y * 0.3)
    swipe_window(
        device, 
        pos1={'x': swipe_x, 'y': swipe_start_y}, 
        pos2={'x': swipe_x, 'y': swipe_end_y}, 
        duration=300
    )

    #설정 창 들어갔으니 설정 뒤로가기 끄기 업데이트
    setting_back_off = func_ui_class.find_setting_back_off_button_by_UI(device)
    update_button(res, "ui_set_back", {"x":setting_back_off['ui_set_back']['x'],"y":setting_back_off['ui_set_back']['y']})
    update_button(res, "ui_set_off", {"x":setting_back_off['ui_set_off']['x'],"y":setting_back_off['ui_set_off']['y']})

    locations = func_ui_class.find_location_by_UI_class(device, ['개발 설정','engineering','stop navigation',"안내종료"])
    logging.info(f"매칭 결과: {locations}")

    if not locations or not isinstance(locations, dict):
        logging.warning("텍스트 인식을 실패했거나 감지된 키워드가 없습니다.")
        return False

    
    if 'engineering' in locations or '개발 설정' in locations:
        eng_key = 'engineering' if 'engineering' in locations else '개발 설정'
        eng_pos = locations[eng_key]
        logging.info(f"🚀 히든 메뉴 감지 ({eng_key}): {eng_pos}")
        update_button(res, "eng_hidden", {"x":eng_pos['x'],"y":eng_pos['y']})
        one_finger_touch(device, eng_pos)
        print('already eng mode activated, start check button positions')
        # 1. 마찬가지로 스레드로 백그라운드 연산 유도
        # update_button_location_in_eng_mode(device)
        # 2. 💡 스크린샷 확보를 위한 대기 타임
        button_info = call_button_info(res)
        one_finger_touch(device, button_info['eng_back'])
        one_finger_touch(device,button_info['ui_set_off'])
        return False
        
    # 2. Stop Navigation 또는 안내종료가 감지된 경우 (엔지니어링 모드 활성화 빌드업)
    if 'stop navigation' in locations or '안내종료' in locations:
        navi_key = 'stop navigation' if 'stop navigation' in locations else '안내종료'
        stop_navi_location = locations[navi_key]
        logging.info(f'stop_navi_location - {stop_navi_location}')
        update_button(res, "ui_guidance_off", stop_navi_location)
        update_button(res, "eng_hidden", {"x":stop_navi_location['x'],"y":stop_navi_location['y']+120})
        update_button(res, "eng_menu", {"x":stop_navi_location['x'],"y":stop_navi_location['y']+240})

    button_info = call_button_info(res)
    for _ in range(12):
        one_finger_touch(device,button_info['eng_hidden'])
        time.sleep(0.1)
    confdata = configus.load_config('resources/configs/config.json')
    
    text = confdata.get('activated_eng_mode', {}).get(device_type, '')
    #잘못된 입력 지우기 위해 추가.
    for _ in range(3):
        send_keyevent(device, 67)
    time.sleep(0.1)
    input_text(device,text)
    # ----------------------------------------------------
    # 1번째 '확인' 클릭
    # ----------------------------------------------------
    locations = func_ui_class.find_location_by_UI_class(device, letters=['확인'])

    # locations가 딕셔너리이고 '확인' 키가 실제로 존재하는지 안전하게 검사
    if isinstance(locations, dict) and locations.get('확인'):
        one_finger_touch(device, locations['확인'])
        logging.info(f"첫 번째 '확인' 클릭 성공: {locations['확인']}")
    else:
        logging.warning("첫 번째 '확인' 키를 찾지 못했습니다. (KeyError 방지 처리됨)")

    time.sleep(0.2)  # 팝업 전환 및 버퍼링 대기시간 확보

    # ----------------------------------------------------
    # 2번째 '확인' 클릭
    # ----------------------------------------------------
    locations = func_ui_class.find_location_by_UI_class(device, letters=['확인'])
    if isinstance(locations, dict) and locations.get('확인'):
        one_finger_touch(device, locations['확인'])
        logging.info(f"두 번째 '확인' 클릭 성공: {locations['확인']}")
    else:
        logging.warning("두 번째 '확인' 키를 찾지 못했습니다. (KeyError 방지 처리됨)")
        



    # 2. 💡 스크린샷 확보를 위한 대기 타임
    button_info = call_button_info(res)
    one_finger_touch(device, button_info['eng_back'])
    one_finger_touch(device,button_info['ui_set_off'])
    
def select_latter_eng(device,search_latter):
    device_type, res_x, res_y = get_device_type_and_res(device)
    res = device['resolution']
    button_info = call_button_info(res)
    eng_flag = go_to_eng_mode(device)
    if not eng_flag:
        return False  # 기존 return 0 대신 확실한 실패 플래그 반환
    scroll_flag = swipe_window_til_latter(device,search_latter)
    if not scroll_flag:
        print(f" {search_latter} UI 탐색 실패")
        return False
    location = func_ui_class.find_location_by_UI_class(device,letters=[search_latter])
    logging.info(f'location - {location}')
    if location is None:
        print(f'please check {search_latter} in eng mode')
        return 0
    else:
        one_finger_touch(device, location[search_latter.lower()])
        # 7. 화면 닫기 및 정리 (button_info 딕셔너리가 전역 또는 내부에 선언되어 있다고 가정)
    try:
        one_finger_touch(device, button_info['eng_back'])
        one_finger_touch(device, button_info['ui_set_off'])
    except NameError:
        logging.warning("button_info 정의를 찾을 수 없어 화면 닫기 단계를 스킵합니다.")
        return 0

def select_latter_box_eng(device, search_latter, value):
    device_type, res_x, res_y = get_device_type_and_res(device)
    res = device['resolution']
    button_info = call_button_info(res)
    # 1. 초기 엔지니어 모드 진입 및 방어
    eng_flag = go_to_eng_mode(device)
    if not eng_flag:
        logging.error("엔지니어 모드 진입 실패")
        return False  
        
    # 2. 목표 메뉴 스크롤 탐색
    scroll_flag = swipe_window_til_latter(device, search_latter)
    if not scroll_flag:
        print(f"❌ {search_latter} UI 탐색 실패")
        return False
    
    # 3. 스크롤이 완료되어 화면에 확보되었으므로 스위치/텍스트 필드 정보 로드
    locations = func_ui_class.find_eng_swith_text_by_UI(device, letters=[search_latter])
    logging.info(f"검색된 UI 목록: {locations}")
    
    # 4. [대소문자 방어 및 키워드 매칭] 
    # find_eng_swith_text_by_UI 결과가 소문자로 올 수 있으므로 유연하게 대조
    mv_key = next((k for k in locations if k.lower() == search_latter.lower()), None)

    if mv_key is None:
        logging.warning(f"❌ '{search_latter}' 관련 메뉴의 컴포넌트를 찾지 못했습니다.")
        return False
        
    # 매칭된 텍스트 필드/메뉴의 실제 좌표 추출
    matched_location = locations[mv_key]
   
    # 5. 입력창 선택 및 기존 값 청소
    one_finger_touch(device, matched_location)  # [교정] locations 대신 매칭된 좌표 전달
    if value =='-':
        logging.info(f'radio icon -{matched_location}. finish')
        try:
            one_finger_touch(device, button_info['eng_back'])
            one_finger_touch(device, button_info['ui_set_off'])
        except NameError:
            logging.warning("button_info 정의를 찾을 수 없어 화면 닫기 단계를 스킵합니다.")
        return True
    #time.sleep(0.1)
    
    # Backspace(67) 3번, Delete(112) 3번으로 기존 텍스트 클리어
    for _ in range(3): send_keyevent(device, 67)
    for _ in range(3): send_keyevent(device, 112)
    
    # 6. 새 값 입력 및 엔터(66)
    input_text(device, value)
    send_keyevent(device, 66)
    #time.sleep(0.1)
    #TODO: 확인 버튼 찾아서 닫기 눌러야함.
    locations = func_ui_class.find_location_by_UI_class(device, letters=['확인'],package_name="com.android.inputmethod.keyboard.Key")

    # locations가 딕셔너리이고 '확인' 키가 실제로 존재하는지 안전하게 검사
    if isinstance(locations, dict) and locations.get('확인'):
        one_finger_touch(device, locations['확인'])
        logging.info(f"'확인' 클릭 성공: {locations['확인']}")
    else:
        logging.warning("'확인' 키를 찾지 못했습니다. (KeyError 방지 처리됨)")

    
    # 7. 화면 닫기 및 정리 (button_info 딕셔너리가 전역 또는 내부에 선언되어 있다고 가정)
    try:
        one_finger_touch(device, button_info['eng_back'])
        one_finger_touch(device, button_info['ui_set_off'])
    except NameError:
        logging.warning("button_info 정의를 찾을 수 없어 화면 닫기 단계를 스킵합니다.")
    return True  # 최종 성공 플래그 반환

def set_demo_speed(device,value):
    device_type, res_x, res_y = get_device_type_and_res(device)
    res = device['resolution']
    button_info = call_button_info(res)
    #eng mode 진입
    # 엔지니어 모드 진입
    eng_flag = go_to_eng_mode(device)
    if not eng_flag:
        print('set demo speed failed')
        return 0

    one_finger_touch(device, button_info['simulation_speed'])
    # 기존 텍스트 지우기 및 새 값 입력 // backspace 3번 delete 3번
    send_keyevent(device,67)
    send_keyevent(device,67)
    send_keyevent(device,67)
    send_keyevent(device,112)
    send_keyevent(device,112)
    send_keyevent(device,112)
    input_text(device, value)
    send_keyevent(device,66)
    one_finger_touch(device,button_info['eng_back'])
    one_finger_touch(device,button_info['ui_set_off'])
    return 0

def set_demo_mode(device,value):
    '''
    데모모드 설정
    value = START , STOP, PAUSE, REPEAT
    
    '''
    steps = ["START", "STOP", "PAUSE", "REPEAT"]
    if value not in steps:
        print(f"유효하지 않은 value가 입력되었습니다: {value} (지원 목록: START, STOP, PAUSE, REPEAT)")
        return 0
    select_latter_eng(device,value)
    return 0

 # ================== FTS function ==================

def set_guidance_off(device):
    device_type, res_x, res_y = get_device_type_and_res(device)
    res = device['resolution']
    button_info = call_button_info(res)
    open_navi_setting(device)
    one_finger_touch(device,button_info['ui_guidance_off'])


#--------------------------------- 키보드 및 입력 기능 함수 ---------------------------------
def decompose_text(text):
    """
    한글(자모 및 이중모음/쌍자음/겹받침 분리), 영어, 숫자, 특수문자가 섞인 텍스트를 
    키보드 입력 단위(타자 단위) 리스트로 변환합니다.
    """
    # 1. 모음 분해 매핑 (이중모음 해체)
    complex_vowels = {
        #'ㅐ': ['ㅏ', 'ㅣ'],
        #'ㅒ': ['ㅑ', 'ㅣ'],
        #'ㅔ': ['ㅓ', 'ㅣ'],
        #'ㅖ': ['ㅕ', 'ㅣ'],
        'ㅘ': ['ㅗ', 'ㅏ'],
        'ㅙ': ['ㅗ', 'ㅒ'],
        'ㅚ': ['ㅗ', 'ㅣ'],
        'ㅝ': ['ㅜ', 'ㅓ'],
        'ㅞ': ['ㅜ', 'ㅔ'],
        'ㅟ': ['ㅜ', 'ㅣ'],
        'ㅢ': ['ㅡ', 'ㅣ'],
    }

    # 2. 자음 분해 매핑 (초성 쌍자음 & 종성 겹받침 해체)
    complex_consonants = {
        # 쌍자음 (초성 / 종성 공통)
        #'ㄲ': ['ㄱ', 'ㄱ'],
        #'ㄸ': ['ㄷ', 'ㄷ'],
        #'ㅃ': ['ㅂ', 'ㅂ'],
        #'ㅆ': ['ㅅ', 'ㅅ'],
        #'ㅉ': ['ㅈ', 'ㅈ'],
        
        # 겹받침 (종성)
        'ㄳ': ['ㄱ', 'ㅅ'],
        'ㄵ': ['ㄴ', 'ㅈ'],
        'ㄶ': ['ㄴ', 'ㅎ'],
        'ㄺ': ['ㄹ', 'ㄱ'],
        'ㄻ': ['ㄹ', 'ㅁ'],
        'ㄼ': ['ㄹ', 'ㅂ'],
        'ㄽ': ['ㄹ', 'ㅅ'],
        'ㄾ': ['ㄹ', 'ㅌ'],
        'ㄿ': ['ㄹ', 'ㅍ'],
        'ㅀ': ['ㄹ', 'ㅎ'],
        'ㅄ': ['ㅂ', 'ㅅ'],
    }

    # 한글 자모 인덱스 테이블
    chosung = ['ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
    jungsung = ['ㅏ', 'ㅐ', 'ㅑ', 'ㅒ', 'ㅓ', 'ㅔ', 'ㅕ', 'ㅖ', 'ㅗ', 'ㅘ', 'ㅙ', 'ㅚ', 'ㅛ', 'ㅜ', 'ㅝ', 'ㅞ', 'ㅟ', 'ㅠ', 'ㅡ', 'ㅢ', 'ㅣ']
    jongsung = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']

    result = []
    
    for char in text:
        code = ord(char)
        # 완성형 한글 범위(가 ~ 힣)
        if 0xAC00 <= code <= 0xD7A3:
            char_code = code - 0xAC00
            cho_idx = char_code // 588
            jung_idx = (char_code % 588) // 28
            jong_idx = char_code % 28

            # 1. 초성 (쌍자음 해체 적용)
            cho = chosung[cho_idx]
            result.extend(complex_consonants.get(cho, [cho]))

            # 2. 중성 (이중모음 해체 적용)
            jung = jungsung[jung_idx]
            result.extend(complex_vowels.get(jung, [jung]))

            # 3. 종성 (겹받침/쌍자음 해체 적용)
            if jong_idx > 0:
                jong = jongsung[jong_idx]
                result.extend(complex_consonants.get(jong, [jong]))
        else:
            # 영문, 숫자, 특수문자 또는 단독 자모 처리
            if char in complex_vowels:
                result.extend(complex_vowels[char])
            elif char in complex_consonants:
                result.extend(complex_consonants[char])
            else:
                result.append(char)

    return result

def check_language(char):
    try:
        name = unicodedata.name(char)
        if "HANGUL" in name:
            return "KR"
        elif "LATIN" in name:
            return "EN"
    except ValueError:
        return "etc" # 숫자나 특수문자 등
    return "etc"

def set_lang_in_keyboard(device, target_lan, max_attempts : int = 10):
    LANG_MAP = {
        'KR': ['한국어', 'korean', 'ko'],
        'EN': ['english', '영어', 'en'],
        'JP': ['日本語', 'japanese', '일본어', 'ja'],
        'ZH': ['中文', 'chinese', '중국어', 'zh']
    }
    
    # [조건 2용] 스페이스바 텍스트가 없을 때 사용할 언어별 대표 자판 문자열
    LANG_CHAR_MAP = {
        'KR': set('ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅑㅓㅕㅗㅛㅜㅠㅡㅣㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ'),
        'EN': set('abcdefghijklmnopqrstuvwxyz'),
        'JP': set('あかさたなはまやらわアカサタナハマヤラワ'),
        'ZH': set('ㄅㄆㄇㄈㄉㄊㄋㄌ') # 병음 자판 시 알파벳 사용 가능
    }

    u2 = device.get('u2_device')
    if not u2:
        return -1

    target_lan = target_lan.upper()
    target_keywords = LANG_MAP.get(target_lan, [target_lan.lower()])
    target_chars = LANG_CHAR_MAP.get(target_lan, set())
    
    all_lang_keywords = [kw for sublist in LANG_MAP.values() for kw in sublist]
    space_identifiers = ['space', '스페이스', '공백', '空格', 'espace', 'espacio']

    for attempt in range(max_attempts):
        xml_str = u2.dump_hierarchy(compressed=False, pretty=True)
        root = ET.fromstring(xml_str)

        space_node = None
        change_btn_node = None
        all_keyboard_texts = [] # 키보드 자판의 모든 문자를 모아둘 리스트

        for node in root.iter('node'):
            res_id = node.attrib.get('resource-id', '').lower()
            desc = node.attrib.get('content-desc', '').lower()
            text = node.attrib.get('text', '').lower()

            if text:
                all_keyboard_texts.append(text)

            # 1. "로 변경" 전용 버튼 감지
            if "로 변경" in desc or "로 변경" in text:
                change_btn_node = node
                continue

            # 2. 스페이스바 감지
            is_space = False
            if any(kw in res_id or kw in desc or kw in text for kw in space_identifiers):
                is_space = True
            elif text and any(lang_kw in text for lang_kw in all_lang_keywords):
                is_space = True

            if is_space:
                space_node = node

        # 3. 언어 상태 판별
        if space_node is not None or change_btn_node is not None:
            sp_desc = space_node.attrib.get('content-desc', '').lower() if space_node is not None else ''
            sp_text = space_node.attrib.get('text', '').lower() if space_node is not None else ''
            sp_info = f"{sp_desc} {sp_text}".strip()

            logging.info(f"[Check {attempt+1}/{max_attempts}] 스페이스바 감지 정보: '{sp_info}'")

            # ------------------------------------------------------------------
            # [조건 1] 스페이스바/버튼에 '한국어', 'English' 등 명시적 언어가 적힌 경우
            # ------------------------------------------------------------------
            has_explicit_lang = any(lang_kw in sp_info for lang_kw in all_lang_keywords)

            if has_explicit_lang:
                if any(kw in sp_info for kw in target_keywords):
                    logging.info(f"[Success] (조건1) 명시적 텍스트로 목표 언어('{target_lan}') 확인 완료.")
                    return 0
                else:
                    logging.info(f"[Info] 현재 스페이스바 텍스트('{sp_info}')가 목표 언어('{target_lan}')와 다름.")

            # ------------------------------------------------------------------
            # [조건 2] 스페이스바에 언어 표시가 없는 경우 (한/영 2개 언어 모드 등)
            # ------------------------------------------------------------------
            else:
                logging.info(f"[Info] 스페이스바에 언어 텍스트가 없음 -> 자판 키 문자(ㄱ, A 등)로 언어 추정 중...")
                # 화면 노드들의 text 중 목표 언어 대표 문자가 존재하는지 체크
                found_chars = [t for t in all_keyboard_texts if t in target_chars]
                
                if len(found_chars) >= 3: # 실수 방지를 위해 키가 3개 이상 들어맞을 때 성공 처리
                    logging.info(f"[Success] (조건2) 자판 문자 감지({found_chars[:5]})로 목표 언어('{target_lan}') 확인 완료.")
                    return 0

            # ------------------------------------------------------------------
            # 언어가 일치하지 않으므로 변경 버튼 클릭 진행
            # ------------------------------------------------------------------
            click_node = change_btn_node if change_btn_node is not None else space_node
            bounds_str = click_node.attrib.get('bounds', '')

            nums = list(map(int, re.findall(r'\d+', bounds_str)))
            if len(nums) == 4:
                x1, y1, x2, y2 = nums
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                if change_btn_node is None:
                    # 지구본 아이콘 / 언어 변경 키 위치인 스페이스바 좌측 클릭
                    target_x = x1 - 80 
                    u2.click(target_x, cy)
                    logging.info(f"[Click] 스페이스바 좌측 언어 변경 위치 클릭: ({target_x}, {cy})")
                else:
                    u2.click(cx, cy)
                    logging.info(f"[Click] 언어 변경 버튼 클릭: ({cx}, {cy})")
                
                time.sleep(0.1) # 키보드 레이아웃 전환 대기
        else:
            logging.info("[Fail] 스페이스바 또는 언어 변경 노드를 찾지 못함.")
            return -1

    print(f"[Fail] {max_attempts}회 시도 후에도 '{target_lan}' 설정 실패.")
    return -1

def update_keyboard_key(device, lan_type):
    keyboard_json_path = "resources/configs/keyboard.json"
    
    # 키보드 패키지/클래스/아이디 식별 키워드 리스트
    keyboard_identifiers = [
        'com.samsung.android.honeyboard', # 삼성 키보드 메인 패키지
        'inputmethod',
        'keyboard'
    ]

    res_key = device['resolution']
    u2 = device.get('u2_device')
    if not u2:
        print("[ERROR] u2_device 객체를 찾을 수 없습니다.")
        return -1
    
    try:
        # 1. 키보드 영역 XML 덤프
        xml_str = u2.dump_hierarchy(compressed=False, pretty=True)
        root = ET.fromstring(xml_str)

        # 2. 기존 resources/configs/keyboard.json 읽어오기
        json_data = {}
        if os.path.exists(keyboard_json_path):
            with open(keyboard_json_path, "r", encoding="utf-8") as f:
                try:
                    json_data = json.load(f)
                except json.JSONDecodeError:
                    json_data = {}

        if lan_type not in json_data:
            json_data[lan_type] = {}

        # 3. XML 내 노드 탐색 및 키보드 영역 필터링
        extracted_count = 0
        for node in root.iter('node'):
            # 노드의 주요 식별 정보 추출 (패키지, 리소스ID, 클래스 등)
            pkg = node.attrib.get('package', '').lower()
            res_id = node.attrib.get('resource-id', '').lower()
            cls = node.attrib.get('class', '').lower()

            # -------------------------------------------------------------
            # 필터링: package, resource-id, class 중 하나라도 키보드 식별자를 포함하는지 확인
            # -------------------------------------------------------------
            is_keyboard_node = any(
                identifier in pkg or identifier in res_id or identifier in cls
                for identifier in keyboard_identifiers
            )
            
            if not is_keyboard_node:
                continue
            # -------------------------------------------------------------

            text = node.attrib.get('text', '').strip()
            desc = node.attrib.get('content-desc', '').strip()
            bounds_str = node.attrib.get('bounds')

            # 식별할 키 값 (text 우선, 없으면 content-desc 사용)
            key_label = text if text else desc

            # 키 식별 라벨이 없거나 bounds가 없으면 패스
            if not key_label or not bounds_str:
                continue

            # bounds 파싱: "[x1,y1][x2,y2]" -> [x1, y1, x2, y2]
            nums = list(map(int, re.findall(r'\d+', bounds_str)))
            if len(nums) == 4:
                x1, y1, x2, y2 = nums
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                # JSON 데이터 구조 생성 및 좌표 할당
                if key_label not in json_data[lan_type]:
                    json_data[lan_type][key_label] = {}

                json_data[lan_type][key_label][res_key] = {
                    "x": cx,
                    "y": cy
                }
                extracted_count += 1

        if extracted_count == 0:
            logging.info(f"[WARN] XML에서 필터 조건에 맞는 키 노드가 없습니다. (lan_type: {lan_type})")
            return -1

        # 4. JSON 파일 저장
        os.makedirs(os.path.dirname(keyboard_json_path), exist_ok=True)
        with open(keyboard_json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=4)

        logging.info(f"[SUCCESS] {extracted_count}개 키 좌표가 '{keyboard_json_path}' ({lan_type} / {res_key})에 성공적으로 업데이트되었습니다.")
        return 0

    except Exception as e:
        logging.info(f"[ERROR] update_keyboard_key 실행 중 오류 발생: {e}")
        return -1

def is_keyboard_present(u2):
    """
    현재 화면에 소프트 키보드가 올라와 있는지 확인합니다.
    """
    # 1. uiautomator2 info 속성에서 키보드 노출 여부 확인
    try:
        info = u2.info
        if info.get('isKeyboardShown', False):
            return True
    except Exception as e:
        logging.debug(f"u2.info 키보드 상태 확인 실패: {e}")

    # 2. XML Hierarchy 덤프에서 삼성 키보드 패키지/입력창 존재 여부 검사 (Fallback)
    try:
        xml_str = u2.dump_hierarchy(compressed=False, pretty=True)
        keyboard_identifiers = [
            'com.samsung.android.honeyboard', # 삼성 키보드 메인 패키지
            'inputmethod',
            'keyboard',
            '로 변경' # 키보드 특유의 언어 변경 안내 문구
        ]
        if any(kw in xml_str.lower() for kw in keyboard_identifiers):
            return True
    except Exception as e:
        logging.error(f"키보드 XML 검사 중 오류: {e}")

    return False

def press_key_by_char(device, char, lan_type="KR"):
    keyboard_json_path = "resources/configs/keyboard.json"
    """
    resources/configs/keyboard.json에서 해상도 및 char에 해당하는 좌표를 찾아 터치합니다.
    쌍자음/이중모음인 경우 Shift 키를 활용하여 입력합니다.
    
    :param device: 디바이스 객체 (u2_device 포함)
    :param char: 입력할 문자 (예: "ㄲ", "ㄱ", "Space", "Done" 등)
    :param lan_type: 언어 타입 (기본값 "KR")
    :return: 0 (성공) / -1 (실패)
    """
    u2 = device.get('u2_device')
    if not u2:
        logging.info("[ERROR] u2_device 객체를 찾을 수 없습니다.")
        return -1

    # 1. JSON 파일 존재 여부 확인
    if not os.path.exists(keyboard_json_path):
        logging.info(f"[ERROR] '{keyboard_json_path}' 파일이 존재하지 않습니다. 먼저 update_keyboard_key를 실행해 주세요.")
        return -1

    # Shift 키가 필요한 문자 매핑 (쌍자음 & 일부 이중모음)
    shift_char_map = {
        'ㄲ': 'ㄱ',
        'ㄸ': 'ㄷ',
        'ㅃ': 'ㅂ',
        'ㅆ': 'ㅅ',
        'ㅉ': 'ㅈ',
        'ㅒ': 'ㅐ',
        'ㅖ': 'ㅔ'
    }

    try:
        # 2. 현재 디바이스 해상도 가져오기
        res_key = device.get('resolution', '1752x2800')

        # 3. JSON 파일 읽기
        with open(keyboard_json_path, "r", encoding="utf-8") as f:
            keyboard_data = json.load(f)

        lan_data = keyboard_data.get(lan_type, {})

        # 4. Shift 조작이 필요한 문자인지 확인
        is_double_consonant = (lan_type == "KR" and char in shift_char_map)
        target_char = shift_char_map[char] if is_double_consonant else char

        # 5. Shift 키 좌표 및 대상 키 좌표 가져오기
        # -------------------------------------------------------------
        # 변경된 부분: "시프트" 또는 "shift" 단어가 포함된 키를 가변 검색
        # -------------------------------------------------------------
        shift_pos = None
        if is_double_consonant:
            for key_label, pos_map in lan_data.items():
                # 키 라벨에 '시프트' 또는 'shift'가 들어가는지 확인 (대소문자 무시)
                if '시프트' in key_label.lower() or 'shift' in key_label.lower():
                    shift_pos = pos_map.get(res_key)
                    print(f"[INFO] Shift 키 감지됨: '{key_label}'")
                    break
        # -------------------------------------------------------------
        key_data = lan_data.get(target_char, {})
        char_pos = key_data.get(res_key)

        # 6. 좌표 유효성 검사
        if not char_pos or (char_pos.get("x", 0) == 0 and char_pos.get("y", 0) == 0):
            logging.warning(f"[WARN] '{target_char}' 키의 좌표를 찾을 수 없거나 0,0 입니다. (언어: {lan_type}, 해상도: {res_key})")
            return -1

        if is_double_consonant and (not shift_pos or (shift_pos.get("x", 0) == 0 and shift_pos.get("y", 0) == 0)):
            logging.warning(f"[WARN] Shift 키의 좌표를 찾을 수 없어 '{char}'를 입력할 수 없습니다.")
            return -1

        # 7. 터치 실행 (Shift -> 키 입력 -> Shift 해제)
        if is_double_consonant:
            logging.info(f"[INFO] '{char}' 입력을 위해 Shift 동작을 조합합니다.")
            one_finger_touch(device, shift_pos)  # Shift ON
            one_finger_touch(device, char_pos)   # 대상 키 눌림 (예: ㄱ -> ㄲ 입력됨)
            one_finger_touch(device, shift_pos)  # Shift OFF (복구)
        else:
            one_finger_touch(device, char_pos)

        logging.info(f"[SUCCESS] '{char}' 입력 완료 -> 좌표: ({char_pos['x']}, {char_pos['y']})")
        return 0

    except Exception as e:
        logging.info(f"[ERROR] press_key_by_char 실행 중 오류 발생: {e}")
        return -1

def search_fts(device, text):
    """
    텍스트를 자모로 분리한 후 언어 상태를 맞추어 키보드 터치 또는 input_text로 입력합니다.
    """
    u2 = device.get('u2_device')
    if not u2:
        logging.error("u2_device를 찾을 수 없습니다.")
        return -1
    info = u2.info
    logging.info(info)

    # ----------------------------------------------------
    # 키보드가 화면에 올라와 있는지 체크
    # ----------------------------------------------------
    if not is_keyboard_present(u2):
        print("[Fail] 화면에 키보드가 활성화되어 있지 않습니다.")
        logging.warning("키보드가 활성화되어 있지 않아 입력을 중단합니다.")
        return -1

    # 1. 텍스트 자모 분리
    char_list = decompose_text(text)
    current_keyboard_lang = None  # 이전 키보드 언어 상태 추적
    for char in char_list:
        lan_type = check_language(char)
        logging.info(f"입력 대상: '{char}' | 감지된 언어: {lan_type}")

        # 2. 언어 변경이 필요한 경우만 set_lang_in_keyboard 실행 (이전 언어와 같으면 스킵)
        if lan_type in ["KR"]:
            if current_keyboard_lang != lan_type:
                set_lang_in_keyboard(device, lan_type)
                update_keyboard_key(device, lan_type)
                current_keyboard_lang = lan_type
            else:
                logging.info(f"[Skip] 이전 언어({current_keyboard_lang})와 동일하여 키보드 언어 변경을 스킵합니다.")
        # 3. 언어별 문자의 입력 수행
        if lan_type == "KR":
            press_key_by_char(device,char, lan_type)
        elif lan_type == "EN":
            input_text(device, char)
        else:  # 특수문자, 공백 등 'etc' (이전 언어 상태 유지)
            input_text(device, char)
    print(f"입력 완료 - {text}")
    return 0