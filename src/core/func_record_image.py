import time
from datetime import datetime
import os
import threading

from . import location_utils
from ..utils import configus, loggas

logging = loggas.logger


def record_video(device=None, log_manager=None, duration=None, save_dir=None):
    """
    비디오 녹화 및 위치 정보 캡처 수행
    """
    config = configus.load_config('resources/configs/config.json')
    if duration is None:
        duration = config['video_recording_duration']

    device_obj = device['ppadb_device']
    device_obj_serial = device_obj.serial
    logging.info(f"[*] 비디오 녹화 시작 (기기: {device_obj_serial}, 시간: {duration}초)")

    try:
        # 0. 기존 녹화 프로세스 정리
        logging.info(f"[{device_obj_serial}] 기존 screenrecord 프로세스 정리 중...")
        device_obj.shell("pkill -9 screenrecord")
        time.sleep(0.5)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_file = f"Screen_Recording_{timestamp}.mp4"
        car_pos_file = f"Screen_Recording_{timestamp}_location.txt"
                
        res = device.get('resolution', 'unknown') 
        remote_path = f"/sdcard/{video_file}" if res == '1920x720' else f"/sdcard/DCIM/Screenshots/{video_file}"

        logging.info(f"[{device_obj_serial}] Remote path set to: {remote_path} (Res: {res})")
    
        local_dir = save_dir if save_dir else config['local_path']
        local_path = os.path.join(local_dir, video_file)
        car_pos_path = os.path.join(local_dir, car_pos_file)

        # --- [위치 정보 로그 수집 직접 수행] ---
        if log_manager:
            tmp_log_file_path = "resources/info/loca_info_video.txt"
            if os.path.exists(tmp_log_file_path):
                try:
                    os.remove(tmp_log_file_path)
                except Exception:
                    pass

            results = {}
            loca_stop_signal = log_manager.fetch_log_from_list(
                search_patterns={'car_pos': ".*win 0 SFN.*"}, 
                file_path=tmp_log_file_path, 
                result_dict=results,
                timeout_seconds=2
            )

            # 2초간 패턴 매칭 대기
            loca_stop_signal.wait(timeout=2)

            if 'car_pos' in results:
                logging.info(f"[{device_obj_serial}] Location info found: {results['car_pos']}")
                location_utils.save_loca(results, car_pos_path)
            else:
                logging.warning(f"[{device_obj_serial}] No location info captured in 2 seconds.")
                with open(car_pos_path, "w", encoding="utf-8") as f:
                    f.write("car_pos: N/A (Log not detected)")
            
            loca_stop_signal.set()
        else:
            logging.warning(f"[{device_obj_serial}] log_manager가 제공되지 않아 위치 정보를 수집하지 않습니다.")

        # 1. 비디오 녹화 시작 (백그라운드 스레드)
        logging.info(f"[{device_obj_serial}] Recording Start: {video_file}")
        
        def start_recording():
            device_obj.shell(f"screenrecord {remote_path}")

        record_thread = threading.Thread(target=start_recording, daemon=True)
        record_thread.start()

        # 2. 설정된 시간 동안 녹화 대기
        time.sleep(duration)

        # 3. 녹화 종료
        logging.info(f"[{device_obj_serial}] Stopping recording...")
        device_obj.shell("pkill -2 screenrecord")
        time.sleep(3)

        # 4. ADB Pull
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

    except Exception as e:
        logging.error(f"[{device_obj_serial}] 비디오 태스크 에러: {e}")
    finally:
        logging.info(f"[{device_obj_serial}] 비디오 작업 완료")

def record_screenshot(device, log_manager=None, loca_log=True, save_dir=None):
    """
    스크린샷 캡처 및 위치 정보 저장 수행
    """
    device_obj = device['ppadb_device']
    config = configus.load_config('resources/configs/config.json')
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_file = f"Screenshot_{timestamp}.png"
    local_dir = save_dir if save_dir else config['local_path']
    screenshot_path = os.path.join(local_dir, screenshot_file)

    # --- [위치 정보 로그 수집 및 저장] ---
    if loca_log:
        car_pos_file = f"Screenshot_{timestamp}_location.txt"
        car_pos_path = os.path.join(local_dir, car_pos_file)

        if log_manager:
            latest_data = getattr(log_manager, 'latest_car_pos', None)
            logging.info(
                f"[CAR_POS READ] "
                f"now={datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                f"latest={latest_data}"
            )
            
            if latest_data:
                # 튜플(pc_time, log_line) 구조 대응 및 기존 단일 문자열 호환
                if isinstance(latest_data, tuple):
                    pc_time, log_line = latest_data
                    logging.info(f"Location info captured (Recv: {pc_time}): {log_line.strip()}")
                    with open(car_pos_path, "w", encoding="utf-8") as f:
                        f.write(f"[PC Recv Time]: {pc_time}\n")
                        f.write(f"[Log Raw Line]: {log_line}\n")
                else:
                    # 단일 문자열로 저장되어 있을 때의 예외 처리
                    logging.info(f"Location info captured: {latest_data.strip()}")
                    with open(car_pos_path, "w", encoding="utf-8") as f:
                        f.write(latest_data + "\n")
            else:
                logging.warning("No location info captured yet.")
                with open(car_pos_path, "w", encoding="utf-8") as f:
                    f.write("car_pos: N/A (Log not detected)")
        else:
            logging.warning("log_manager가 전달되지 않아 위치 로그를 기록하지 못했습니다.")
            with open(car_pos_path, "w", encoding="utf-8") as f:
                f.write("car_pos: N/A (LogManager is None)")
    else:
        logging.info("Location logging is disabled (loca_log=False).")

    # --- [스크린샷 실행] ---
    logging.info(f"Starting screenshot: {screenshot_file}")
    try:
        start = time.perf_counter()
        result = device_obj.screencap()
        elapsed = time.perf_counter() - start
        logging.info(f"[SCREENCAP DONE] elapsed={elapsed:.3f}s")
        with open(screenshot_path, "wb") as f:
            f.write(result)
        logging.info(f"Screenshot saved successfully: {screenshot_path}")
    except Exception as e:
        logging.error(f"Failed to take screenshot: {e}")

    return screenshot_path

'''

def record_screenshot(device, log_manager=None, loca_log=True, save_dir=None):
    """
    스크린샷 캡처 및 위치 정보 저장 수행
    """

    device_obj = device['ppadb_device']
    config = configus.load_config('resources/configs/config.json')
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_file = f"Screenshot_{timestamp}.png"
    local_dir = save_dir if save_dir else config['local_path']
    screenshot_path = os.path.join(local_dir, screenshot_file)

    # --- [위치 정보 로그 수집 및 저장] ---
    if loca_log and log_manager:
        car_pos_file = f"Screenshot_{timestamp}_location.txt"
        car_pos_path = os.path.join(local_dir, car_pos_file)
        
        # 2초 wait 없이 메모리 변수에서 즉시 추출
        latest_log = getattr(log_manager, 'latest_car_pos', None)
        
        if latest_log:
            logging.info(f"Location info captured: {latest_log}")
            with open(car_pos_path, "w", encoding="utf-8") as f:
                f.write(latest_log + "\n")
        else:
            logging.warning("No location info captured yet.")
            with open(car_pos_path, "w", encoding="utf-8") as f:
                f.write("car_pos: N/A (Log not detected)")


    # 스크린샷 실행
    logging.info(f"Starting screenshot: {screenshot_file}")
    try:
        result = device_obj.screencap()
        with open(screenshot_path, "wb") as f:
            f.write(result)
        logging.info(f"Screenshot saved successfully: {screenshot_path}")
    except Exception as e:
        logging.error(f"Failed to take screenshot: {e}")

    return screenshot_path

'''