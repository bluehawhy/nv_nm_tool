import time
from datetime import datetime
import os
import threading

from . import func_logging
from . import location_utils

from ..utils import configus, loggas

logging= loggas.logger


#================================== recording func ==================================
def record_video(device=None, duration=None):
    config = configus.load_config('resources/configs/config.json')
    if duration is None:
        duration = config['video_recording_duration']


    device_obj = device['ppadb_device'] # ppadb 객체 추출
    device_obj_serial = device_obj.serial
    # [핵심] 이제 run_task(QThread) 안에서 돌기 때문에 이 함수 자체가 블로킹(대기)되어도 UI가 멈추지 않습니다.
    logging.info(f"[*] 비디오 녹화 시작 (기기: {device_obj_serial}, 시간: {duration}초)")
    
    
    video_log_results = {}
    loca_stop_signal = None

    try:
        # 0. 기존 녹화 프로세스 정리
        logging.info(f"[{device_obj_serial}] 기존 screenrecord 프로세스 정리 중...")
        device_obj.shell("pkill -9 screenrecord")
        time.sleep(0.5)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Screen_Recording_{timestamp}.mp4"
        locafilename = f"Screen_Recording_{timestamp}_location.txt"
                
        res = device.get('resolution', 'unknown') 
        if res == '1920x720':
            remote_path = f"/sdcard/{filename}"
        else:
            remote_path = f"/sdcard/DCIM/Screenshots/{filename}"

        logging.info(f"[{device_obj_serial}] Remote path set to: {remote_path} (Res: {res})")
    
        local_dir = config['local_path']
        local_path = os.path.join(local_dir, filename)

        # 1. 로그 모니터링 시작
        log_file_path = "resources/info/loca_info_video.txt"
        loca_stop_signal = func_logging.get_log_from_list(
            device, 
            search_patterns={'car_pos':".*win 0 SFN.*"}, 
            file_path=log_file_path, 
            result_dict=video_log_results
        )

        # 2. 로그가 찍힐 최소한의 대기
        if not video_log_results:
            loca_stop_signal.wait(timeout=2)

        # 2-1. 로그 파일 저장
        loca_local_path = os.path.join(local_dir, locafilename)
        location_utils.save_loca(video_log_results, loca_local_path)

        # 3. 비디오 녹화 시작 (ADB screenrecord 자체는 백그라운드로 던져야 하므로 이 스레드는 유지)
        logging.info(f"[{device_obj_serial}] Recording Start: {filename}")
        
        def start_recording():
            device_obj.shell(f"screenrecord {remote_path}")

        record_thread = threading.Thread(target=start_recording)
        record_thread.daemon = True
        record_thread.start()

        # 4. 설정된 시간 동안 녹화 대기 (★여기가 중요: 이 함수가 여기서 딱 잡고 기다려줍니다)
        time.sleep(duration)

        # 5. 녹화 종료
        logging.info(f"[{device_obj_serial}] Stopping recording...")
        device_obj.shell("pkill -2 screenrecord")
        
        time.sleep(3)

        # 6. ADB Pull
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
        
        # [삭제] 이제 가뿐하게 이 함수가 리턴(종료)되면 UI의 Worker 스레드가 finished를 발생시켜 버튼을 켭니다.

def record_screenshot(device, loca_log=True):

    # 0. 준비: config 로드 및 객체 추출
    device_obj = device['ppadb_device'] # ppadb 객체 추출
    device_obj_serial = device_obj.serial
    config = configus.load_config('resources/configs/config.json')
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Screenshot_{timestamp}.png"
    local_dir = config['local_path']
    local_path = os.path.join(local_dir, filename)

    # --- [위치 정보 로그 구간 시작] ---
    if loca_log:
        locafilename = f"Screenshot_{timestamp}_location.txt"
        loca_local_path = os.path.join(local_dir, locafilename)
        
        # 이전에 찾았던 결과는 제거
        loca_info_tmp_path = "resources/info/loca_info_screenshot.txt"
        if os.path.exists(loca_info_tmp_path):
            os.remove(loca_info_tmp_path)
        
        results = {}

        # 1. 로그 모니터링 시작
        loca_stop_signal = func_logging.get_log_from_list(
            device, 
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
            location_utils.save_loca(results, loca_local_path)
        else:
            logging.warning("No location info captured in 2 seconds.")
            with open(loca_local_path, "w", encoding="utf-8") as f:
                f.write("car_pos: N/A (Log not detected)")
        
        # 스레드 정리
        loca_stop_signal.set()
    else:
        logging.info("Location logging is disabled (loca_log=False).")
    # --- [위치 정보 로그 구간 끝] ---

    # 2. 스크린샷 실행
    logging.info(f"Starting screenshot: {filename}")
    try:
        result = device_obj.screencap()
        with open(local_path, "wb") as f:
            f.write(result)
        logging.info(f"Screenshot saved successfully: {local_path}")
    except Exception as e:
        logging.error(f"Failed to take screenshot: {e}")

    return local_path
