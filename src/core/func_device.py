import os
import time
import unicodedata
import threading
import xml.etree.ElementTree as ET
import re
import json
import math

from . import func_ui_class, location_utils
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
    data = configus.load_config('resources/configs/self.button_info.json')
    
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

def update_button(res, button_name, location, config_file="resources/configs/self.button_info.json"):
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


#================================== basic func ==================================
class TouchController:
    def __init__(self, device: dict):
        """
        device: {'ppadb_device': ..., 'u2_device': ...} 형태의 딕셔너리
        """
        self.ppadb = device.get('ppadb_device')
        self.u2 = device.get('u2_device')
        self.serial = self.ppadb.serial if self.ppadb else "Unknown"

    
    def one_finger_touch(self, pos):

        """단일 지점 터치 (PPADB 활용)"""
        if not self.ppadb:
            logging.error(f"[{self.serial}] PPADB 객체가 설정되지 않았습니다.")
            return -1
        x, y = str(pos['x']), str(pos['y'])
        
        # 2. 명령어 구성 (adb prefix 없이 shell 내부 명령만 전달)    
        try:
            logging.info(f"[{self.serial}] Touch: ({x}, {y})")
            self.ppadb.shell(f"input tap {x} {y}")
            return 0
        except Exception as e:
            logging.error(f"[{self.serial}] Touch Error: {e}")
            return -1

    def two_finger_touch(self, pos1: dict, pos2: dict):
        """두 지점 동시 터치 (uiautomator2 활용)"""
        if not self.u2:
            logging.error(f"[{self.serial}] u2_device 객체가 설정되지 않았습니다.")
            return
        map_view = self.u2()
        # 외부 변수 d 대신 인자로 받은 device를 사용하도록 수정
        map_view = self.u2()
        map_view.gesture(
            (pos1['x'], pos1['y']),
            (pos2['x'], pos2['y']),
            (pos1['x'], pos1['y']),
            (pos2['x'], pos2['y'])
        )

    def swipe(self, pos1: dict, pos2: dict, duration: int = 600) -> int:
        """화면 스와이프 (PPADB 활용)"""
        if not self.ppadb:
            logging.error(f"[{self.serial}] PPADB 객체가 설정되지 않았습니다.")
            return -1
        
        # 좌표 및 시간 추출
        x1, y1 = pos1['x'], pos1['y']
        x2, y2 = pos2['x'], pos2['y']
        
        # 2. 명령어 구성 (input swipe x1 y1 x2 y2 duration)
        command = f"input swipe {x1} {y1} {x2} {y2} {duration}"
        
        try:
            logging.info(f"[{self.serial}] Swipe: ({x1}, {y1}) -> ({x2}, {y2}) over {duration}ms")
            self.ppadb.shell(command)
            return 0
        except Exception as e:
            logging.error(f"[{self.serial}] Swipe Error: {e}")
            return -1

    def repeat_swipe(self, pos1: dict, pos2: dict, duration: int = 600, count: int = 1) -> int:
        """지정한 횟수만큼 반복 스와이프"""
        for i in range(count):
            res = self.swipe(pos1, pos2, duration)
            if res != 0:
                return -1
            time.sleep(0.1)
        return 0

    def input_text(self, text):
        """ppadb를 사용하여 텍스트를 입력합니다. (공백은 %s로 치환)"""
        if isinstance(text, (int, float)):
            safe_text = str(text)
        else:
            safe_text = str(text).replace(" ", "%s")

        command = f'input text "{safe_text}"'
        try:
            logging.info(f"[{self.serial}] Input Text: {text} (Encoded: {safe_text})")
            self.ppadb_dev.shell(command)
            return 0
        except Exception as e:
            logging.error(f"[{self.serial}] Input Text Error: {e}")
            return -1

    def send_keyevent(self, keycode):
        """ppadb를 사용하여 안드로이드 키 이벤트를 전송합니다."""
        command = f"input keyevent {keycode}"
        try:
            logging.info(f"[{self.serial}] Send Keyevent: {keycode}")
            self.ppadb_dev.shell(command)
            return 0
        except Exception as e:
            logging.error(f"[{self.serial}] Send Keyevent Error: {e}")
            return -1
#================================== intergrated func ==================================
class NaviController(TouchController):
    """
    TouchController의 기초 터치/스와이프 기능을 상속받아
    내비게이션 UI 탐색, 설정 진입, 엔지니어링 모드 활성화 등의 고도화 기능을 수행하는 클래스
    """
    def __init__(self, device: dict):
        super().__init__(device)
        self.device = device
        
        # 기기 해상도 및 설정 정보 미리 파싱
        self.device_type, self.res_x, self.res_y = get_device_type_and_res(device)
        self.button_info = call_button_info(self.device['resolution'])

    def open_navi_setting(self) -> bool:
        """내비게이션 설정 메뉴를 열고 상태를 반환합니다."""
        # 1. 설정이 이미 있는지 확인
        set_locations = func_ui_class.find_location_by_UI_class(
            self.device, ['설정', 'Settings'], package_name='navis.ncn.navi'
        )
        if set_locations and isinstance(set_locations, dict):
            logging.info(f"[{self.serial}] 이미 설정 메뉴가 열려있습니다.")
            return True

        # 2. 설정이 안 열려 있는 경우 아이콘 탐색
        location = func_ui_class.find_setting_icon_by_UI(self.device)
        if location is None:
            logging.warning(f"[{self.serial}] 설정 아이콘을 찾을 수 없습니다.")
            return False
        
        # 상속받은 touch 메서드 활용 (one_finger_touch)
        self.touch(location)
        time.sleep(0.2)

        # 3. 오픈 결과 확인
        set_locations = func_ui_class.find_location_by_UI_class(self.device, ['설정', 'Settings'])
        if not set_locations or not isinstance(set_locations, dict):
            logging.warning(f"[{self.serial}] 설정 클릭 후 메뉴 진입 실패 - '설정', 'Settings'")
            return False
            
        return True

    def go_to_eng_mode(self):
        # 1. 설정 메뉴 열기
        if not self.open_navi_setting():
            return False
            
        time.sleep(0.1)
        swipe_x = int(self.res_x * 0.25)
        swipe_start_y = int(self.res_y * 0.7)
        swipe_end_y = int(self.res_y * 0.3)
        self.swipe(
            pos1={'x': swipe_x, 'y': swipe_start_y}, 
            pos2={'x': swipe_x, 'y': swipe_end_y}, 
            duration=300
        )
        # 2. 1차 UI 검색
        locations = func_ui_class.find_location_by_UI_class(self.device, ['개발 설정', 'engineering'])
        logging.info(f"1차 매칭 결과: {locations}")

        # 3. 비활성화 상태인 경우에만 activate_eng 및 2차 재시도 실행
        if not locations or not isinstance(locations, dict):
            logging.warning("engineering mode 비활성화 상태. 활성화(activate_eng) 시도.")
            # NaviController의 메서드로 호출하도록 수정
            self.activate_eng()
            time.sleep(0.2)

            # activate_eng 수행 후 2차 확인
            if not self.open_navi_setting():
                return False
            locations = func_ui_class.find_location_by_UI_class(self.device, ['개발 설정', 'engineering'])
            if not locations or not isinstance(locations, dict):
                logging.warning("활성화 시도 후에도 메뉴를 찾지 못했습니다.")
                return False

        # 4. 🎯 위치를 찾았다면(1차든 2차든) 바로 터치 후 종료!
        eng_key = 'engineering' if 'engineering' in locations else '개발 설정'
        if eng_key in locations:
            eng_pos = locations[eng_key]
            logging.info(f"히든 메뉴 감지 ({eng_key}): {eng_pos}")
            self.one_finger_touch(eng_pos)
            # 1. 마찬가지로 스레드로 백그라운드 연산 유도
            time.sleep(0.1)  # 팝업 전환 및 버퍼링 대기시간 확보
            self.update_button_location_in_eng_mode()
            time.sleep(0.1)  # 팝업 전환 및 버퍼링 대기시간 확보
            return True
        return False

    def activate_eng(self):
        '''
        eng mode 해제.
        1. 설정 버튼 누름
        2. 안내 종료 위치 확인 및 아래 칸 위치 조정
        3. 안내 종료 밑에 빈 칸을 10번 누름
        4. 비밀번호 해제 전달 // 프로젝트 찾아야함...
        5. 확인 누름
        '''
        setting_flag = self.open_navi_setting()
        if not setting_flag:
            return False

        #스크롤 한번 하기
        swipe_x = int(self.res_x * 0.25)
        swipe_start_y = int(self.res_y * 0.7)
        swipe_end_y = int(self.res_y * 0.3)
        self.swipe(
            pos1={'x': swipe_x, 'y': swipe_start_y}, 
            pos2={'x': swipe_x, 'y': swipe_end_y}, 
            duration=300
        )

        #설정 창 들어갔으니 설정 뒤로가기 끄기 업데이트
        setting_back_off = func_ui_class.find_setting_back_off_button_by_UI(self.device)
        update_button(self.res, "ui_set_back", {"x":setting_back_off['ui_set_back']['x'],"y":setting_back_off['ui_set_back']['y']})
        update_button(self.res, "ui_set_off", {"x":setting_back_off['ui_set_off']['x'],"y":setting_back_off['ui_set_off']['y']})

        locations = func_ui_class.find_location_by_UI_class(self.device, ['개발 설정','engineering','stop navigation',"안내종료"])
        logging.info(f"매칭 결과: {locations}")

        if not locations or not isinstance(locations, dict):
            logging.warning("텍스트 인식을 실패했거나 감지된 키워드가 없습니다.")
            return False

        
        if 'engineering' in locations or '개발 설정' in locations:
            eng_key = 'engineering' if 'engineering' in locations else '개발 설정'
            eng_pos = locations[eng_key]
            logging.info(f"🚀 히든 메뉴 감지 ({eng_key}): {eng_pos}")
            update_button(self.res, "eng_hidden", {"x":eng_pos['x'],"y":eng_pos['y']})
            self.one_finger_touch(eng_pos)
            print('already eng mode activated, start check button positions')
            # 1. 마찬가지로 스레드로 백그라운드 연산 유도
            # update_button_location_in_eng_mode(device)
            # 2. 💡 스크린샷 확보를 위한 대기 타임
            self.one_finger_touch(self.button_info['eng_back'])
            self.one_finger_touch(self.button_info['ui_set_off'])
            return False
            
        # 2. Stop Navigation 또는 안내종료가 감지된 경우 (엔지니어링 모드 활성화 빌드업)
        if 'stop navigation' in locations or '안내종료' in locations:
            navi_key = 'stop navigation' if 'stop navigation' in locations else '안내종료'
            stop_navi_location = locations[navi_key]
            logging.info(f'stop_navi_location - {stop_navi_location}')
            update_button(self.res, "ui_guidance_off", stop_navi_location)
            update_button(self.res, "eng_hidden", {"x":stop_navi_location['x'],"y":stop_navi_location['y']+120})
            update_button(self.res, "eng_menu", {"x":stop_navi_location['x'],"y":stop_navi_location['y']+240})

        for _ in range(12):
            self.one_finger_touch(self.button_info['eng_hidden'])
            time.sleep(0.1)
        confdata = configus.load_config('resources/configs/config.json')
        
        text = confdata.get('activated_eng_mode', {}).get(self.device_type, '')
        #잘못된 입력 지우기 위해 추가.
        for _ in range(3):
            send_keyevent(self.device, 67)
        time.sleep(0.1)
        input_text(self.device,text)
        # ----------------------------------------------------
        # 1번째 '확인' 클릭
        # ----------------------------------------------------
        locations = func_ui_class.find_location_by_UI_class(self.device, letters=['확인'])

        # locations가 딕셔너리이고 '확인' 키가 실제로 존재하는지 안전하게 검사
        if isinstance(locations, dict) and locations.get('확인'):
            self.one_finger_touch(locations['확인'])
            logging.info(f"첫 번째 '확인' 클릭 성공: {locations['확인']}")
        else:
            logging.warning("첫 번째 '확인' 키를 찾지 못했습니다. (KeyError 방지 처리됨)")

        time.sleep(0.2)  # 팝업 전환 및 버퍼링 대기시간 확보

        # ----------------------------------------------------
        # 2번째 '확인' 클릭
        # ----------------------------------------------------
        locations = func_ui_class.find_location_by_UI_class(self.device, letters=['확인'])
        if isinstance(locations, dict) and locations.get('확인'):
            self.one_finger_touch(locations['확인'])
            logging.info(f"두 번째 '확인' 클릭 성공: {locations['확인']}")
        else:
            logging.warning("두 번째 '확인' 키를 찾지 못했습니다. (KeyError 방지 처리됨)")
            
        # 2. 💡 스크린샷 확보를 위한 대기 타임
        self.one_finger_touch(self.button_info['eng_back'])
        self.one_finger_touch(self.button_info['ui_set_off'])

    def update_button_location_in_eng_mode(self):
        res = self.device['resolution']
        target_letters = ['개발 설정', 'engineering', 'start', 'stop', 'pause', 'repeat']
        locations = func_ui_class.find_location_by_UI_class(self.device, target_letters)
        time.sleep(0.1)
        eng_locations = func_ui_class.find_eng_back_button_by_UI(self.device)
        time.sleep(0.1)
        switch_raido_location = func_ui_class.find_eng_swith_text_by_UI(self.device,['simulation speed', 'night mode'])
        time.sleep(0.1)
        
        # 1. 진입 기준점 확인 (메인 메뉴 둘 다 없으면 진행 불가하므로 이 단계만 방어)
        if '개발 설정' not in locations and 'engineering' not in locations:
            logging.error("기준 메뉴('개발 설정'/'Engineering')를 찾지 못해 업데이트를 스킵합니다.")
            return self.button_info

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
        return self.button_info

    def select_latter_eng(self,search_latter):
        eng_flag = self.go_to_eng_mode()
        if not eng_flag:
            return False  # 기존 return 0 대신 확실한 실패 플래그 반환
        scroll_flag = self.swipe_window_til_latter(search_latter)
        if not scroll_flag:
            print(f" {search_latter} UI 탐색 실패")
            return False
        location = func_ui_class.find_location_by_UI_class(self.device,letters=[search_latter])
        logging.info(f'location - {location}')
        if location is None:
            print(f'please check {search_latter} in eng mode')
            return 0
        else:
            self.one_finger_touch(location[search_latter.lower()])
            # 7. 화면 닫기 및 정리 (self.button_info 딕셔너리가 전역 또는 내부에 선언되어 있다고 가정)
        try:
            self.one_finger_touch(self.button_info['eng_back'])
            self.one_finger_touch(self.button_info['ui_set_off'])
        except NameError:
            logging.warning("self.button_info 정의를 찾을 수 없어 화면 닫기 단계를 스킵합니다.")
            return 0

    def select_latter_box_eng(self, search_latter, value):
        res = self.device['resolution']
        # 1. 초기 엔지니어 모드 진입 및 방어
        eng_flag = self.go_to_eng_mode()
        if not eng_flag:
            logging.error("엔지니어 모드 진입 실패")
            return False  
            
        # 2. 목표 메뉴 스크롤 탐색
        scroll_flag = self.swipe_window_til_latter(search_latter)
        if not scroll_flag:
            print(f"❌ {search_latter} UI 탐색 실패")
            return False
        
        # 3. 스크롤이 완료되어 화면에 확보되었으므로 스위치/텍스트 필드 정보 로드
        locations = func_ui_class.find_eng_swith_text_by_UI(self.device, letters=[search_latter])
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
        self.one_finger_touch(matched_location)  # [교정] locations 대신 매칭된 좌표 전달
        if value =='-':
            logging.info(f'radio icon -{matched_location}. finish')
            try:
                self.one_finger_touch(self.button_info['eng_back'])
                self.one_finger_touch(self.button_info['ui_set_off'])
            except NameError:
                logging.warning("self.button_info 정의를 찾을 수 없어 화면 닫기 단계를 스킵합니다.")
            return True
        #time.sleep(0.1)
        
        # Backspace(67) 3번, Delete(112) 3번으로 기존 텍스트 클리어
        for _ in range(3): send_keyevent(self.device, 67)
        for _ in range(3): send_keyevent(self.device, 112)
        
        # 6. 새 값 입력 및 엔터(66)
        input_text(self.device, value)
        send_keyevent(self.device, 66)
        #time.sleep(0.1)
        #TODO: 확인 버튼 찾아서 닫기 눌러야함.
        locations = func_ui_class.find_location_by_UI_class(self.device, letters=['확인'],package_name="com.android.inputmethod.keyboard.Key")

        # locations가 딕셔너리이고 '확인' 키가 실제로 존재하는지 안전하게 검사
        if isinstance(locations, dict) and locations.get('확인'):
            self.one_finger_touch(locations['확인'])
            logging.info(f"'확인' 클릭 성공: {locations['확인']}")
        else:
            logging.warning("'확인' 키를 찾지 못했습니다. (KeyError 방지 처리됨)")

        
        # 7. 화면 닫기 및 정리 (self.button_info 딕셔너리가 전역 또는 내부에 선언되어 있다고 가정)
        try:
            self.one_finger_touch(self.button_info['eng_back'])
            self.one_finger_touch(self.button_info['ui_set_off'])
        except NameError:
            logging.warning("self.button_info 정의를 찾을 수 없어 화면 닫기 단계를 스킵합니다.")
        return True  # 최종 성공 플래그 반환

    def set_demo_speed(self,value):
        # 엔지니어 모드 진입
        eng_flag = self.go_to_eng_mode()
        if not eng_flag:
            print('set demo speed failed')
            return 0

        self.one_finger_touch(self.button_info['simulation_speed'])
        # 기존 텍스트 지우기 및 새 값 입력 // backspace 3번 delete 3번
        send_keyevent(self.device,67)
        send_keyevent(self.device,67)
        send_keyevent(self.device,67)
        send_keyevent(self.device,112)
        send_keyevent(self.device,112)
        send_keyevent(self.device,112)
        input_text(self.device, value)
        send_keyevent(self.device,66)
        self.one_finger_touch(self.button_info['eng_back'])
        self.one_finger_touch(self.button_info['ui_set_off'])
        return 0

    def set_demo_mode(self,value):
        steps = ["START", "STOP", "PAUSE", "REPEAT"]
        if value not in steps:
            print(f"유효하지 않은 value가 입력되었습니다: {value} (지원 목록: START, STOP, PAUSE, REPEAT)")
            return 0
        self.select_latter_eng(value)
        return 0

    def set_guidance_off(self):
        self.open_navi_setting()
        self.one_finger_touch(self.button_info['ui_guidance_off'])

    def swipe_window_til_latter(self, latter, x_latters=['개발 설정', 'engineering']):
        '''
        화면을 스크롤하며 목표 UI(latter: string)를 탐색합니다.
        화면 바닥에 도달하거나 정체되면 False를, 찾으면 좌표 지점을 리턴합니다.
        '''
        # 1. 기준점(X좌표용) 탐색
        locations = func_ui_class.find_location_by_UI_class(self.device, x_latters)
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
        swipe_start_y = int(self.res_y * 0.7)
        swipe_end_y = int(self.res_y * 0.3)
        
        # 클릭 안전 y 범위 설정 (0 <= y <= res_y * 0.9)
        max_safe_y = self.res_y * 0.9
        
        prev_xml = ''
        
        while True:
            time.sleep(0.1)
            # 3. 현재 화면에서 목표 string(latter) 검색
            current_locations = func_ui_class.find_location_by_UI_class(self.device, [latter])
            
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
            self.swipe_window(
                pos1={'x': location['x'], 'y': swipe_start_y}, 
                pos2={'x': location['x'], 'y': swipe_end_y}, 
                duration=300
            )

    def zoom_in(self):
        """단순 1단계 확대"""
        screen_width, screen_height = map(int, self.device['resolution'].split("x"))
        center = {"x": screen_width // 2, "y": screen_height // 2}
        self.one_finger_touch(pos=center)
        time.sleep(0.1)
        self.one_finger_touch(pos=center)
        time.sleep(0.5)

    def zoom_out(self):
        """단순 1단계 축소 (두 손가락 간격을 100px로 넓혀 명확한 터치 처리)"""
        screen_width, screen_height = map(int, self.device['resolution'].split("x"))
        cx, cy = screen_width // 2, screen_height // 2
        p1 = {"x": cx, "y": cy}
        p2 = {"x": cx, "y": cy}
        self.two_finger_touch(pos1=p1, pos2=p2)
        time.sleep(0.1)
        self.two_finger_touch(pos1=p1, pos2=p2)
        time.sleep(0.5)

    def scroll_map_to_location(
        self, logmanager, target_location, max_attempts=50
    ):
        """도착 판정 범위 완화 및 오버슈팅(진동) 감지 시 강제 Zoom In 기능 추가"""
        THRESHOLD_METERS = 25.0  # 도달 기준 오차 완화

        screen_width, screen_height = map(int, self.device["resolution"].split("x"))
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
                        self.zoom_in()
                    elif current_scale_km < 0.01 and not has_overshot:
                        # 오버슈팅 차단 상태가 아닐 때만 Zoom Out 허용
                        logging.info(
                            f"🚀 [Final Scale] 현재({current_scale_km}km) < 0.05km -> Zoom Out"
                        )
                        self.zoom_out()
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
                self.zoom_in()
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
                        self.zoom_out()
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
                    self.zoom_in()
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

            self.swipe(pos1=pos1, pos2=pos2)
            time.sleep(0.8)

        logging.warning(
            f"⚠️ 최대 시도 횟수({max_attempts}회) 초과로 중단합니다."
        )
        return False



#--------------------------------- 키보드 및 입력 기능 함수 --------------------------------

class KeyboardController(TouchController):
    """디바이스 키보드 입력을 제어하는 클래스."""

    LANG_MAP = {
        "KR": ["한국어", "korean", "ko"],
        "EN": ["english", "영어", "en"],
        "JP": ["日本語", "japanese", "일본어", "ja"],
        "ZH": ["中文", "chinese", "중국어", "zh"],
    }
    LANG_CHAR_MAP = {
        "KR": set("ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅑㅓㅕㅗㅛㅜㅠㅡㅣㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ"),
        "EN": set("abcdefghijklmnopqrstuvwxyz"),
        "JP": set("あかさたなはまやらわアカサタナハマヤラワ"),
        "ZH": set("ㄅㄆㄇㄈㄉㄊㄋㄌ"),
    }
    SHIFT_CHAR_MAP = {
        "ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ",
        "ㅒ": "ㅐ", "ㅖ": "ㅔ",
    }
    KEYBOARD_IDENTIFIERS = ["com.samsung.android.honeyboard", "inputmethod", "keyboard"]

    def __init__(self, device: dict):
        """
        :param device_dict: 'ppadb_device', 'u2_device', 'resolution' 등을 포함하는 딕셔너리
        """
        super().__init__(device)
        self.resolution = self.device.get("resolution", "")
        self.keyboard_json_path = os.path.join("resources", "configs", "keyboard.json")
        self.current_keyboard_lang = None

    # ==========================================
    # 1. ADB / 기본 입력 동작
    # ==========================================

    def is_keyboard_present(self):
        """현재 화면에 소프트 키보드가 노출되어 있는지 검사합니다."""
        if not self.u2_dev:
            return False

        try:
            if self.u2_dev.info.get("isKeyboardShown", False):
                return True
        except Exception as e:
            logging.debug(f"u2.info 키보드 상태 확인 실패: {e}")

        try:
            xml_str = self.u2_dev.dump_hierarchy(compressed=False, pretty=True)
            check_list = self.KEYBOARD_IDENTIFIERS + ["로 변경"]
            if any(kw in xml_str.lower() for kw in check_list):
                return True
        except Exception as e:
            logging.error(f"키보드 XML 검사 중 오류: {e}")

        return False

    # ==========================================
    # 2. 키보드 상태 레이아웃 관리 (매핑 & 변경)
    # ==========================================
    def set_lang_in_keyboard(self, target_lan, max_attempts: int = 10):
        """화면 UI를 조작하여 소프트 키보드의 언어를 target_lan으로 변경합니다."""
        if not self.u2_dev:
            return -1

        target_lan = target_lan.upper()
        target_keywords = self.LANG_MAP.get(target_lan, [target_lan.lower()])
        target_chars = self.LANG_CHAR_MAP.get(target_lan, set())
        
        all_lang_keywords = [kw for sublist in self.LANG_MAP.values() for kw in sublist]
        space_identifiers = ["space", "스페이스", "공백", "空格", "espace", "espacio"]

        for attempt in range(max_attempts):
            xml_str = self.u2_dev.dump_hierarchy(compressed=False, pretty=True)
            root = ET.fromstring(xml_str)

            space_node = None
            change_btn_node = None
            all_keyboard_texts = []

            for node in root.iter("node"):
                res_id = node.attrib.get("resource-id", "").lower()
                desc = node.attrib.get("content-desc", "").lower()
                text = node.attrib.get("text", "").lower()

                if text:
                    all_keyboard_texts.append(text)

                if "로 변경" in desc or "로 변경" in text:
                    change_btn_node = node
                    continue

                is_space = (
                    any(kw in res_id or kw in desc or kw in text for kw in space_identifiers) or
                    (text and any(lang_kw in text for lang_kw in all_lang_keywords))
                )
                if is_space:
                    space_node = node

            if space_node is not None or change_btn_node is not None:
                sp_desc = space_node.attrib.get("content-desc", "").lower() if space_node else ""
                sp_text = space_node.attrib.get("text", "").lower() if space_node else ""
                sp_info = f"{sp_desc} {sp_text}".strip()

                logging.info(f"[Check {attempt+1}/{max_attempts}] 스페이스바 정보: '{sp_info}'")

                # [조건 1] 명시적 언어 텍스트 존재 확인
                if any(lang_kw in sp_info for lang_kw in all_lang_keywords):
                    if any(kw in sp_info for kw in target_keywords):
                        logging.info(f"[Success] 명시적 텍스트로 목표 언어('{target_lan}') 확인.")
                        return 0
                # [조건 2] 키 자판 문자로 추정
                else:
                    found_chars = [t for t in all_keyboard_texts if t in target_chars]
                    if len(found_chars) >= 3:
                        logging.info(f"[Success] 자판 문자 감지({found_chars[:5]})로 목표 언어('{target_lan}') 확인.")
                        return 0

                # 언어 전환 버튼 클릭
                click_node = change_btn_node if change_btn_node is not None else space_node
                bounds_str = click_node.attrib.get("bounds", "")
                nums = list(map(int, re.findall(r"\d+", bounds_str)))

                if len(nums) == 4:
                    x1, y1, x2, y2 = nums
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                    if change_btn_node is None:
                        target_x = x1 - 80
                        self.u2_dev.click(target_x, cy)
                        logging.info(f"[Click] 스페이스바 좌측 클릭: ({target_x}, {cy})")
                    else:
                        self.u2_dev.click(cx, cy)
                        logging.info(f"[Click] 언어 변경 버튼 클릭: ({cx}, {cy})")

                    time.sleep(0.1)
            else:
                logging.info("[Fail] 스페이스바 또는 언어 변경 노드를 찾지 못함.")
                return -1

        logging.error(f"[Fail] {max_attempts}회 시도 후에도 '{target_lan}' 설정 실패.")
        return -1

    def update_keyboard_key(self, lan_type):
        """현재 키보드의 키 좌표를 추출하여 keyboard.json에 저장합니다."""
        if not self.u2_dev:
            logging.error("u2_device 객체를 찾을 수 없습니다.")
            return -1

        try:
            xml_str = self.u2_dev.dump_hierarchy(compressed=False, pretty=True)
            root = ET.fromstring(xml_str)

            json_data = {}
            if os.path.exists(self.keyboard_json_path):
                with open(self.keyboard_json_path, "r", encoding="utf-8") as f:
                    try:
                        json_data = json.load(f)
                    except json.JSONDecodeError:
                        json_data = {}

            if lan_type not in json_data:
                json_data[lan_type] = {}

            extracted_count = 0
            for node in root.iter("node"):
                pkg = node.attrib.get("package", "").lower()
                res_id = node.attrib.get("resource-id", "").lower()
                cls = node.attrib.get("class", "").lower()

                if not any(ident in pkg or ident in res_id or ident in cls for ident in self.KEYBOARD_IDENTIFIERS):
                    continue

                text = node.attrib.get("text", "").strip()
                desc = node.attrib.get("content-desc", "").strip()
                bounds_str = node.attrib.get("bounds")
                key_label = text if text else desc

                if not key_label or not bounds_str:
                    continue

                nums = list(map(int, re.findall(r"\d+", bounds_str)))
                if len(nums) == 4:
                    cx, cy = (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2
                    
                    if key_label not in json_data[lan_type]:
                        json_data[lan_type][key_label] = {}

                    json_data[lan_type][key_label][self.resolution] = {"x": cx, "y": cy}
                    extracted_count += 1

            if extracted_count == 0:
                logging.warning(f"키 노드를 정합하지 못했습니다. (lan_type: {lan_type})")
                return -1

            os.makedirs(os.path.dirname(self.keyboard_json_path), exist_ok=True)
            with open(self.keyboard_json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=4)

            logging.info(f"[SUCCESS] {extracted_count}개 키 좌표 업데이트 완료 ({lan_type} / {self.resolution})")
            return 0

        except Exception as e:
            logging.error(f"update_keyboard_key 오류: {e}")
            return -1

    def press_key_by_char(self, char, lan_type="KR"):
        """json 파일의 좌표 정보를 기반으로 키 터치를 수행합니다."""
        if not self.u2_dev:
            logging.error("u2_device 객체를 찾을 수 없습니다.")
            return -1

        if not os.path.exists(self.keyboard_json_path):
            logging.error(f"'{self.keyboard_json_path}' 설정 파일이 존재하지 않습니다.")
            return -1

        try:
            with open(self.keyboard_json_path, "r", encoding="utf-8") as f:
                keyboard_data = json.load(f)

            lan_data = keyboard_data.get(lan_type, {})
            is_double_consonant = (lan_type == "KR" and char in self.SHIFT_CHAR_MAP)
            target_char = self.SHIFT_CHAR_MAP[char] if is_double_consonant else char

            # Shift 키 좌표 탐색
            shift_pos = None
            if is_double_consonant:
                for key_label, pos_map in lan_data.items():
                    if "시프트" in key_label.lower() or "shift" in key_label.lower():
                        shift_pos = pos_map.get(self.resolution)
                        break

            char_pos = lan_data.get(target_char, {}).get(self.resolution)

            if not char_pos or (char_pos.get("x") == 0 and char_pos.get("y") == 0):
                logging.warning(f"[{target_char}] 키 좌표 누락 (언어: {lan_type}, 해상도: {self.resolution})")
                return -1

            if is_double_consonant and not shift_pos:
                logging.warning(f"Shift 키 좌표를 찾을 수 없어 '{char}' 입력 불가능")
                return -1

            # 키 클릭 터치 액션 (one_finger_touch 전역함수 또는 외부 바인딩 활용)
            if is_double_consonant:
                logging.info(f"'{char}' 조합 입력을 위해 Shift 적용")
                self.one_finger_touch(shift_pos)
                self.one_finger_touch(char_pos)
                self.one_finger_touch(shift_pos)
            else:
                self.one_finger_touch(char_pos)

            logging.info(f"[SUCCESS] '{char}' 입력 완료 -> 좌표: ({char_pos['x']}, {char_pos['y']})")
            return 0

        except Exception as e:
            logging.error(f"press_key_by_char 실행 오류: {e}")
            return -1

    # ==========================================
    # 3. 고수준 입력 파이프라인
    # ==========================================
    def search_fts(self, text):
        """텍스트를 분해하고 언어를 맞추어 입력을 진행하는 메인 메소드입니다."""
        if not self.u2_dev:
            logging.error("u2_device를 찾을 수 없습니다.")
            return -1

        if not self.is_keyboard_present():
            logging.warning("화면에 키보드가 활성화되어 있지 않습니다.")
            return -1

        char_list = self.decompose_text(text)

        for char in char_list:
            lan_type = self.check_language(char)
            logging.info(f"입력 대상: '{char}' | 감지된 언어: {lan_type}")

            if lan_type == "KR":
                if self.current_keyboard_lang != lan_type:
                    self.set_lang_in_keyboard(lan_type)
                    self.update_keyboard_key(lan_type)
                    self.current_keyboard_lang = lan_type
                
                self.press_key_by_char(char, lan_type)
            elif lan_type == "EN":
                self.input_text(char)
            else:  # 특수문자, 숫자 등
                self.input_text(char)

        logging.info(f"입력 완료 - {text}")
        return 0

    # ==========================================
    # 4. 정적 유틸리티 함수 (@staticmethod)
    # ==========================================
    @staticmethod
    def decompose_text(text):
        """한글 자모/이중모음/겹받침 분해 및 문자 단위 분리 함수."""
        complex_vowels = {
            'ㅘ': ['ㅗ', 'ㅏ'], 'ㅙ': ['ㅗ', 'ㅒ'], 'ㅚ': ['ㅗ', 'ㅣ'],
            'ㅝ': ['ㅜ', 'ㅓ'], 'ㅞ': ['ㅜ', 'ㅔ'], 'ㅟ': ['ㅜ', 'ㅣ'], 'ㅢ': ['ㅡ', 'ㅣ'],
        }
        complex_consonants = {
            'ㄳ': ['ㄱ', 'ㅅ'], 'ㄵ': ['ㄴ', 'ㅈ'], 'ㄶ': ['ㄴ', 'ㅎ'],
            'ㄺ': ['ㄹ', 'ㄱ'], 'ㄻ': ['ㄹ', 'ㅁ'], 'ㄼ': ['ㄹ', 'ㅂ'],
            'ㄽ': ['ㄹ', 'ㅅ'], 'ㄾ': ['ㄹ', 'ㅌ'], 'ㄿ': ['ㄹ', 'ㅍ'],
            'ㅀ': ['ㄹ', 'ㅎ'], 'ㅄ': ['ㅂ', 'ㅅ'],
        }

        chosung = ['ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
        jungsung = ['ㅏ', 'ㅐ', 'ㅑ', 'ㅒ', 'ㅓ', 'ㅔ', 'ㅕ', 'ㅖ', 'ㅗ', 'ㅘ', 'ㅙ', 'ㅚ', 'ㅛ', 'ㅜ', 'ㅝ', 'ㅞ', 'ㅟ', 'ㅠ', 'ㅡ', 'ㅢ', 'ㅣ']
        jongsung = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']

        result = []
        for char in text:
            code = ord(char)
            if 0xAC00 <= code <= 0xD7A3:
                char_code = code - 0xAC00
                cho_idx = char_code // 588
                jung_idx = (char_code % 588) // 28
                jong_idx = char_code % 28

                cho = chosung[cho_idx]
                result.extend(complex_consonants.get(cho, [cho]))

                jung = jungsung[jung_idx]
                result.extend(complex_vowels.get(jung, [jung]))

                if jong_idx > 0:
                    jong = jongsung[jong_idx]
                    result.extend(complex_consonants.get(jong, [jong]))
            else:
                if char in complex_vowels:
                    result.extend(complex_vowels[char])
                elif char in complex_consonants:
                    result.extend(complex_consonants[char])
                else:
                    result.append(char)

        return result

    @staticmethod
    def check_language(char):
        """단일 문자의 언어 타입(KR, EN, etc)을 구별합니다."""
        try:
            name = unicodedata.name(char)
            if "HANGUL" in name:
                return "KR"
            elif "LATIN" in name:
                return "EN"
            else:
                return "etc"
        except ValueError:
            return "etc"











