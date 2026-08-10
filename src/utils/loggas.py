#!/usr/bin/python
# loggas.py
# -*- coding: utf-8 -*-

import os
import sys
import logging
import platform
from datetime import datetime
from pathlib import Path

__all__ = ('logger', 'makeLogger', 'input_message', 'remove_message')
__version__ = '0.3.0'
__modi_date__ = "2026-06-24"

# ==========================================
# [기존 filas.py 통합 영역] 구동 환경 및 시간 정보
# ==========================================
operation = platform.system()
now = datetime.now()

# YYYYMMDD_HHMMSS 포맷 생성
now_date_time = now.strftime('%Y%m%d_%H%M%S')

# PyInstaller(.exe) 실행 환경 검스 및 파일명/경로 추출
if getattr(sys, 'frozen', False):
    # .exe 패키징 실행 시
    main_path = os.path.dirname(sys.executable)
    # 확장자(.exe)를 제거한 실행 파일명 추출
    executed_file_name = Path(sys.executable).stem
else:
    # .py 스크립트 실행 시
    main_path = os.getcwd()
    executed_file_name = Path(sys.argv[0]).stem

# 모듈 자체의 물리적 위치 (필요시 참조)
filepath_abspath = os.path.dirname(os.path.abspath(__file__))


# ==========================================
# 유틸리티 함수 영역
# ==========================================
def remove_message(message_path=None):
    if message_path and Path(message_path).exists():
        os.remove(message_path)
    return None


def input_message(path=None, message=None, settime=True):
    if not path or message is None:
        return None
        
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(path, 'a', encoding='utf-8') as f:
        if settime:
            f.write(f"{now_str} {message}\n")
        else:
            f.write(f"{message}\n")
    return None


def set_debug_logging(is_debug):
    global logger
    if is_debug:
        formatter = logging.Formatter('[%(levelname)s|%(filename)s:%(lineno)s] %(asctime)s > %(message)s')
        streamHandler = logging.StreamHandler()
        streamHandler.setFormatter(formatter)
        logger.addHandler(streamHandler)


# ==========================================
# 로거 초기화 및 실행 영역
# ==========================================
def makeLogger(logfile_name, is_debug=False):
    # _logs 폴더 생성 (상대 경로 및 공백 경로 문제 방지)
    log_folder_path = os.path.join(main_path, 'logs')
    os.makedirs(log_folder_path, exist_ok=True)

    # 로그 파일 전체 경로
    log_file_path = os.path.join(log_folder_path, logfile_name)

    logger = logging.getLogger()
    
    # 중복 핸들러 초기화
    if logger.hasHandlers():
        logger.handlers.clear()

    level = logging.DEBUG if is_debug else logging.INFO
    logger.setLevel(level)

    formatter = logging.Formatter('[%(levelname)s|%(filename)s:%(lineno)s] %(asctime)s > %(message)s')

    # 파일 핸들러 추가
    file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logging.info(f"Logger initialized. Path: {log_file_path}")
    return logger


# --- 실 상용 로거 생성부 ---
# filas 제거 후 내부 변수(executed_file_name, now_date_time)를 직접 사용합니다.
log_file_name = f"{executed_file_name}_{now_date_time}.log"

logger = makeLogger(log_file_name, is_debug=False)