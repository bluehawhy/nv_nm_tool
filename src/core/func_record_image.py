import time
from datetime import datetime
import os
import threading

from . import func_logging
from . import location_utils

from ..utils import configus, loggas

logging= loggas.logger



# ================================== recording func ==================================
def record_video(device=None, duration=None, save_dir=None):
    config = configus.load_config('resources/configs/config.json')
    if duration is None:
        duration = config['video_recording_duration']

    device_obj = device['ppadb_device'] # ppadb 객체 추출
    device_obj_serial = device_obj.serial
    logging.info(f"[*] 비디오 녹화 시작 (기기: {device_obj_serial}, 시간: {duration}초)")
    
    video_log_results = {}
    loca_stop_signal = None

    try:
        # 0. 기존 녹화 프로세스 정리
        logging.info(f"[{device_obj_serial}] 기존 screenrecord 프로세스 정리 중...")
        device_obj.shell("pkill -9 screenrecord")
        time.sleep(0.5)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_file = f"Screen_Recording_{timestamp}.mp4"
        car_pos_file = f"Screen_Recording_{timestamp}_location.txt"
                
        res = device.get('resolution', 'unknown') 
        if res == '1920x720':
            remote_path = f"/sdcard/{video_file}"
        else:
            remote_path = f"/sdcard/DCIM/Screenshots/{video_file}"

        logging.info(f"[{device_obj_serial}] Remote path set to: {remote_path} (Res: {res})")
    
        local_dir = save_dir if save_dir else config['local_path']
        local_path = os.path.join(local_dir, video_file)

        # --- [위치 정보 로그 구간 추가 및 수정] ---
        # 1. 이전 임시 결과 파일 제거 (스크린샷 코드 동일 적용)
        log_file_path = "resources/info/loca_info_video.txt"
        if os.path.exists(log_file_path):
            os.remove(log_file_path)

        # 2. AndroidLogManager 객체 생성 후 로그 모니터링 스레드 시작
        log_manager = func_logging.AndroidLogManager(device=device)
        loca_stop_signal = log_manager.get_log_from_list(
            search_patterns={'car_pos': ".*win 0 SFN.*"}, 
            file_path=log_file_path, 
            result_dict=video_log_results
        )

        # 3. Logcat 버퍼 초기화 및 대기 (핵심!)
        time.sleep(0.1)
        device_obj.shell("logcat -c")
        logging.info("Logcat buffer cleared. Waiting for fresh location info...")

        # 4. 최대 2초 대기
        loca_stop_signal.wait(timeout=2)

        # 5. 데이터 확인 후 파일 저장 (스크린샷 코드 동일 적용)
        car_pos_path = os.path.join(local_dir, car_pos_file)
        if 'car_pos' in video_log_results:
            logging.info(f"Location info found: {video_log_results['car_pos']}")
            location_utils.save_loca(video_log_results, car_pos_path)
        else:
            logging.warning("No location info captured in 2 seconds.")
            with open(car_pos_path, "w", encoding="utf-8") as f:
                f.write("car_pos: N/A (Log not detected)")

        # 위치 모니터링 종료
        loca_stop_signal.set()
        # --- [위치 정보 로그 구간 끝] ---

        # 6. 비디오 녹화 시작 (백그라운드 스레드)
        logging.info(f"[{device_obj_serial}] Recording Start: {video_file}")
        
        def start_recording():
            device_obj.shell(f"screenrecord {remote_path}")

        record_thread = threading.Thread(target=start_recording)
        record_thread.daemon = True
        record_thread.start()

        # 7. 설정된 시간 동안 녹화 대기
        time.sleep(duration)

        # 8. 녹화 종료
        logging.info(f"[{device_obj_serial}] Stopping recording...")
        device_obj.shell("pkill -2 screenrecord")
        
        time.sleep(3)

        # 9. ADB Pull
        logging.info(f"[{device_obj_serial}] Pulling video file to: {local_path}")
        print(f"Pulling video file to: {os.path.basename(local_path)}")

        try:
            device_obj.pull(remote_path, local_path)
            if os.path.exists(local_path):
                logging.info(f"[{device_obj_serial}] Video pull successful: {local_path}")
                print(f'[Done] copy video: {local_path}')
            else:
                logging.error(f"[{device_obj_serial}] Pull failed: File not found at {local_path}")
                
        except Exception as pull_error:
            logging.error(f"[{device_obj_serial}] Pull Error during transfer: {pull_error}")

        print('[Done] copy video')

    except Exception as e:
        logging.error(f"[{device_obj_serial}] 비디오 태스크 에러: {e}")
    finally:
        if loca_stop_signal:
            loca_stop_signal.set()
        logging.info(f"[{device_obj_serial}] 비디오 작업 완료")


def record_screenshot(device, loca_log=True, save_dir=None):
    # 0. 준비: config 로드 및 객체 추출
    device_obj = device['ppadb_device'] # ppadb 객체 추출
    device_obj_serial = device_obj.serial
    config = configus.load_config('resources/configs/config.json')
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_file = f"Screenshot_{timestamp}.png"
    local_dir = save_dir if save_dir else config['local_path']

    screenshot_path = os.path.join(local_dir, screenshot_file)

    # --- [위치 정보 로그 구간 시작] ---
    if loca_log:
        car_pos_file  = f"Screenshot_{timestamp}_location.txt"
        car_pos_path = os.path.join(local_dir, car_pos_file)
        
        # 이전에 찾았던 결과는 제거
        loca_info_tmp_path = "resources/info/loca_info_screenshot.txt"
        if os.path.exists(loca_info_tmp_path):
            os.remove(loca_info_tmp_path)
        
        results = {}
        log_manager = func_logging.AndroidLogManager(device=device)
        loca_stop_signal = log_manager.get_log_from_list(
            search_patterns={'car_pos': ".*win 0 SFN.*"}, 
            file_path=loca_info_tmp_path, 
            result_dict=results
        )

        # 버퍼 클리어 및 대기
        time.sleep(0.1) 
        device_obj.shell("logcat -c")
        logging.info("Logcat buffer cleared. Waiting for fresh location info...")

        # 최대 2초 대기
        loca_stop_signal.wait(timeout=2) 

        # 데이터 확인 후 저장
        if 'car_pos' in results:
            logging.info(f"Location info found: {results['car_pos']}")
            location_utils.save_loca(results, car_pos_path)
        else:
            logging.warning("No location info captured in 2 seconds.")
            with open(car_pos_path, "w", encoding="utf-8") as f:
                f.write("car_pos: N/A (Log not detected)")
        
        # 스레드 정리
        loca_stop_signal.set()
    else:
        logging.info("Location logging is disabled (loca_log=False).")
    # --- [위치 정보 로그 구간 끝] ---

    # 2. 스크린샷 실행
    logging.info(f"Starting screenshot: {screenshot_file}")
    try:
        result = device_obj.screencap()
        with open(screenshot_path, "wb") as f:
            f.write(result)
        logging.info(f"Screenshot saved successfully: {screenshot_path}")
    except Exception as e:
        logging.error(f"Failed to take screenshot: {e}")

    return screenshot_path
