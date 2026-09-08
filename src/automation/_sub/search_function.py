
# 1. 유틸리티 (설정, 로거 등)
from src.utils import loggas

# 2. 핵심 로직 및 디바이스 제어 모듈 (core)
from src.core import (
    call_device,
    func_device,
    func_logging,
    func_record,
)

logging = loggas.logger


def open_fts_wedget(device: dict = None):
    #func_logging = func_logging.AndroidLogManager(device=device)
    ui_finder = func_device.UIFinder(device=device)
    navi_contrl = func_device.NaviController(device=device)
    ui_finder



if __name__ == '__main__':
    loggas.set_debug_logging(True)
    devices = call_device.discover_and_connect_device()
    device = devices[0]
    open_fts_wedget(device=device)