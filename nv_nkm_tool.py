#nv_nkm_tool.py
import os
import sys
import re
from PyQt6.QtWidgets import QApplication

# 1. 유틸리티 (설정, 로거 등)
from src.utils import loggas, configus

# 2. 핵심 로직 및 디바이스 제어 모듈 (core)
from src.core import (
    func_device,
    call_device,  
    func_ios, 
    func_ui_class        
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
    ]


last_v = "v0.0"  # 기본값 백업
for item in reversed(revision_list):
    match = re.search(r'^(v\d+\.\d+)', item.strip())
    if match:
        last_v = match.group(1)
        break

# 2. 찾은 버전을 툴 이름 뒤에 붙여줍니다.
version = f'nkm Tool {last_v}'

def prod_mode():
    app = QApplication(sys.argv)
    ex = ui_nk.MainWindow(version, revision_list)
    ex.show()
    sys.exit(app.exec())

def debug_mode():
    #call_device.start_adb_server()
    devices = call_device.discover_and_connect_device()
    #ios_control = func_ios.IOSDeviceController(lockdown_device=devices[1]['lockdown_device'])
    #ios_control.download_photos_by_date('2026-07-16')
    func_device.activate_eng(devices[0], 'STOP')
    #locations = func_ui_class.find_location_by_UI_class(devices[0], letters=['확인'],package_name="com.android.inputmethod.keyboard.Key")
#
    ## locations가 딕셔너리이고 '확인' 키가 실제로 존재하는지 안전하게 검사
    #if isinstance(locations, dict) and locations.get('확인'):
    #    func_device.touch_window(devices[0], locations['확인'])
    #    logging.info(f"'확인' 클릭 성공: {locations['확인']}")
    #else:
    #    logging.warning("'확인' 키를 찾지 못했습니다. (KeyError 방지 처리됨)")


if __name__ == '__main__':
    loggas.set_debug_logging(True)
    #debug_mode()
    
    #loggas.set_debug_logging(True)
    #call_device.start_adb_server()
    prod_mode()
    