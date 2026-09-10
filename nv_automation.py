import time

# 1. 유틸리티 (설정, 로거 등)
from src.core import (
    call_device,
    func_device,
    func_logging,
    func_record,
)
from src.utils import loggas
from src.automation import scroll_map_and_screenshot
from src.automation._sub import mapview_function

logging = loggas.logger


if __name__ == '__main__':
    loggas.set_debug_logging(True)
    devices = call_device.discover_and_connect_device()
    device = devices[0] if devices else None
    #logging.info("🔌 스크롤 및 스크린샷 자동화 시작")
    logmanager = func_logging.AndroidLogManager(device=device)
    logmanager.start_live_logging(enable_filter=False)
    time.sleep(10)  # 로그 수신을 위해 잠시 대기
    orientation  = mapview_function.parse_map_heading(logmanager=logmanager)
    logging.info(f"지도 방향: {orientation}")