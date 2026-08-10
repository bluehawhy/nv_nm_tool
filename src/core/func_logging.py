import os
import subprocess
import re
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timedelta
from ..core import func_record_image
from ..utils import configus, loggas

logging = loggas.logger

class AndroidLogManager:
    def __init__(self, device, folder_path=None):
        """
        안드로이드 디바이스별 독립된 로그 수집 및 모니터링을 담당하는 클래스
        """
        self.device = device
        self.device_obj = device.get('ppadb_device')  # ppadb 객체 추출
        self.serial = device.get('serial')
        
        # 설정 로드
        self.config = configus.load_config('resources/configs/config.json')
        
        # 기본 저장 경로 설정
        if folder_path is None:
            folder_path = self.config.get('local_path', './')
        self.log_dir = os.path.join(folder_path, "logs")
        os.makedirs(self.log_dir, exist_ok=True)

        # 실시간 수집 상태 관리 변수
        self.file_count = 0
        self.overlap_lines = []
        self.current_log_path = ""
        self.current_filter_path = ""
        self.last_screenshot_time = 0.0
        
        # 스레드 통신용 이벤트
        self.stop_event = None

    def close_connection_by_error(self):
        """UI단 또는 외부에 의해 에러가 감지되었을 때 수집 스레드 및 커넥션을 완전히 강제 종료합니다."""
        logging.info(f"[{self.serial}] UI 단 요청으로 인한 커넥션 및 수집 종료 처리 시작")
        
        # 1. 스레드 종료 이벤트 발생
        self.stop_live_logging()
        
        # 2. ppadb socket connection 강제 닫기 (Socket 속성이 존재하는 경우)
        try:
            if hasattr(self.device_obj, 'connection') and self.device_obj.connection:
                self.device_obj.connection.close()
                logging.info(f"[{self.serial}] ppadb connection socket 강제 종료 완료")
        except Exception as e:
            logging.debug(f"[{self.serial}] Connection socket 종료 중 예외 (이미 닫힘): {e}")

        # 3. 추가적인 ADB process cleaning이 필요하다면 실행
        try:
            subprocess.run(["adb", "-s", self.serial, "logcat", "-c"], capture_output=True)
        except Exception:
            pass


    def _update_paths(self):
        """실시간 로그 및 필터 로그의 저장 경로를 갱신합니다."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{self.file_count}" if self.file_count > 0 else ""
        self.current_log_path = os.path.join(self.log_dir, f"log_{timestamp}{suffix}.txt")
        self.current_filter_path = os.path.join(self.log_dir, f"log_{timestamp}{suffix}_filtered.txt")

    def _expand_log_buffer(self):
        """디바이스의 로그 버퍼 사이즈를 100M로 확대합니다."""
        try:
            self.device_obj.shell("logcat -G 100M")
        except Exception as e:
            logging.info(f"[{self.serial}] 로그 버퍼 설정 실패: {e}")

    def _update_overlap_context(self):
        """파일 교체 시 문맥 보존을 위해 마지막 10줄을 갱신합니다."""
        try:
            with open(self.current_log_path, "r", encoding="utf-8", errors="replace") as rf:
                lines = rf.readlines()
                self.overlap_lines = lines[-10:] if len(lines) >= 10 else lines
        except Exception:
            self.overlap_lines = []

    def start_live_logging(self, debounce_time=3.0):
        """
        실시간 로그 수집 및 100MB 분할 저장을 백그라운드 스레드에서 시작합니다.
        (기존 call_logs_before 및 call_logs 통합본)
        """
        self._expand_log_buffer()
        self._update_paths()

        self.stop_event = threading.Event()
        log_thread = threading.Thread(
            target=self._live_log_worker, 
            args=(debounce_time,), 
            daemon=True
        )
        log_thread.start()

        logging.info(f"[*] [{self.serial}] 로그 수집 시작 (스크린샷 디바운스 대기 타임: {debounce_time}초)")
        return self.stop_event

    def stop_live_logging(self):
        """실시간 로그 수집 스레드를 안전하게 중지시킵니다."""
        if self.stop_event is not None and not self.stop_event.is_set():
            logging.info(f"[{self.serial}] 로그 수집 중지 요청 중...")
            self.stop_event.set()
            return True
        return False

    def _live_log_worker(self, debounce_time):
        """로그캣 스트림 접속을 유지하고 재시작을 제어하는 메인 워커 루프"""
        try:
            while not self.stop_event.is_set():
                # handler 방식으로 실행 (이 명령은 핸들러가 리턴될 때까지 블로킹됩니다)
                self.device_obj.shell(
                    "logcat -v threadtime", 
                    handler=lambda conn: self._live_log_stream_handler(conn, debounce_time)
                )
                
                if self.stop_event.is_set():
                    break
                    
                logging.info(f"[{self.serial}] 로그 파일 교체 및 수집 재시작...")
        except Exception as e:
            logging.error(f"[{self.serial}] 로그 수집 워커 에러: {e}")
        finally:
            logging.info(f"[{self.serial}] 로그 수집 쓰레드 최종 종료")

    def _live_log_stream_handler(self, connection, debounce_time):
        """소켓 스트림으로부터 바이너리 데이터를 받아 파일 처리 및 필터링을 수행합니다."""
        is_snapshot_enabled = self.config.get('snapshop_log', False) 
        filter_keywords = self.config.get('log_filter', [])
        is_filter_active = is_snapshot_enabled and isinstance(filter_keywords, list) and len(filter_keywords) > 0

        screenshot_keywords = self.config.get('log_screenshot_filter', [])
        is_screenshot_filter_active = isinstance(screenshot_keywords, list) and len(screenshot_keywords) > 0

        try:
            while not self.stop_event.is_set():
                with ExitStack() as stack:
                    # 1. 파일 오픈
                    f = stack.enter_context(open(self.current_log_path, "w", encoding="utf-8", buffering=1024*1024))
                    f_filter = None
                    if is_filter_active:
                        f_filter = stack.enter_context(open(self.current_filter_path, "w", encoding="utf-8"))
                        f_filter.write(f"=== Filter Active: {filter_keywords} ===\n\n")

                    # 2. 이전 파일 문맥 기록
                    if self.overlap_lines:
                        f.write("\n" + "="*50 + "\n=== Previous Context ===\n")
                        f.writelines(self.overlap_lines)
                        f.write("="*50 + "\n\n")

                    # 3. 데이터 읽기 루프
                    while not self.stop_event.is_set():
                        chunk = connection.read(8192)  # 8KB씩 읽기
                        if not chunk:
                            return  # 스트림이 끊기면 핸들러 종료 후 워커에서 재연결 유도
                        
                        text = chunk.decode('utf-8', errors='replace')
                        f.write(text)
                        
                        # 줄 단위 분할 및 필터링 검사
                        if (is_filter_active and f_filter) or is_screenshot_filter_active:
                            for line in text.splitlines():
                                line_upper = line.upper()
                                
                                # 일반 로그 텍스트 필터 저장
                                if is_filter_active and f_filter:
                                    if any(word.upper() in line_upper for word in filter_keywords):
                                        f_filter.write(line + "\n")
                                
                                # 스크린샷 캡처 필터 감지
                                if is_screenshot_filter_active:
                                    if any(word.upper() in line_upper for word in screenshot_keywords):
                                        current_time = time.time()
                                        
                                        if current_time - self.last_screenshot_time >= debounce_time:
                                            self.last_screenshot_time = current_time
                                            logging.info(f"📸 [{self.serial}] [스크린샷 조건 충족 - 캡처 실행]: {line.strip()}")
                                            try:
                                                func_record_image.record_screenshot(self.device)
                                            except Exception as screenshot_err:
                                                logging.error(f"스크린샷 캡처 중 오류 발생: {screenshot_err}")
                                        else:
                                            remaining = debounce_time - (current_time - self.last_screenshot_time)
                                            logging.debug(f"⏳ [디바운스 중] 스크린샷 무시됨 (남은 시간: {remaining:.1f}초): {line.strip()}")

                        if is_filter_active and f_filter:
                            f_filter.flush()

                        # 100MB 용량 체크 및 파일 로테이션 트리거
                        if os.path.getsize(self.current_log_path) > 100 * 1024 * 1024:
                            f.flush()
                            break 

                # 루프 탈출 시(100MB 도달) 정보 갱신 후 핸들러 리턴 -> 메인 워커에서 logcat 재호출
                self._update_overlap_context()
                self.file_count += 1
                self._update_paths()
                return 

        except Exception as e:
            logging.error(f"[{self.serial}] 핸들러 실행 중 오류: {e}")
        finally:
            connection.close()

    def get_snapshot_logs(self, folder_path=None, duration_sec=3):
        """
        현재 시점 기준 [과거 duration_sec ~ 미래 duration_sec]의 로그 스냅샷을 수집하고 필터링합니다.
        (기존 get_snapshot_logs 기능)
        """
        if folder_path is None:
            log_dir = os.path.join(self.config.get('local_path', './'), "logs", "snapshot")
        else:
            log_dir = folder_path
        os.makedirs(log_dir, exist_ok=True)

        log_filter = self.config.get('log_filter', [])
        is_filter_active = isinstance(log_filter, list) and len(log_filter) > 0

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(log_dir, f"Screenshot_{timestamp}.txt")
        filtered_file_path = os.path.join(log_dir, f"Screenshot_{timestamp}_filtered.txt")

        past_time = (datetime.now() - timedelta(seconds=duration_sec)).strftime("%m-%d %H:%M:%S.000")
        logging.info(f"[*] [{self.serial}] 스냅샷 로그 수집 시작 (시작지점: {past_time})")
        
        try:
            with ExitStack() as stack:
                f = stack.enter_context(open(file_path, "w", encoding="utf-8"))
                f_filter = None
                if is_filter_active:
                    f_filter = stack.enter_context(open(filtered_file_path, "w", encoding="utf-8"))
                    f_filter.write(f"=== Log Filter Active: {log_filter} ===\n\n")

                # 1. 과거 로그 긁어오기
                logging.info(f"[{self.serial}] [1/2] 과거 {duration_sec}초 로그 복사 및 필터링 중...")
                past_process = subprocess.run(
                    ["adb", "-s", self.serial, "logcat", "-v", "threadtime", "-t", past_time],
                    capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
                
                for line in past_process.stdout.splitlines():
                    f.write(line + "\n")
                    if is_filter_active and f_filter:
                        if any(word.upper() in line.upper() for word in log_filter):
                            f_filter.write(line + "\n")

                separator = f"\n{'='*50}\n=== PAST LOG END / REAL-TIME START AT {datetime.now()} ===\n{'='*50}\n\n"
                f.write(separator)
                if f_filter: 
                    f_filter.write(separator)

                # 2. 실시간 로그 수집 시작
                logging.info(f"[{self.serial}] [2/2] 실시간 로그 수집 중 ({duration_sec}초간)...")
                process = subprocess.Popen(
                    ["adb", "-s", self.serial, "logcat", "-v", "threadtime"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace"
                )

                start_time = time.time()
                while time.time() - start_time < duration_sec:
                    line = process.stdout.readline()
                    if not line: 
                        break
                    
                    f.write(line)
                    if is_filter_active and f_filter:
                        if any(word.upper() in line.upper() for word in log_filter):
                            f_filter.write(line)
                            f_filter.flush()

                # 프로세스 종료
                if process.poll() is None:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True)
                    process.wait()

            logging.info(f"[+] [{self.serial}] 스냅샷 저장 완료: {file_path}")
            if is_filter_active:
                logging.info(f"[+] [{self.serial}] 필터링된 스냅샷 저장 완료: {filtered_file_path}")
            return True
            
        except Exception as e:
            logging.error(f"[!] [{self.serial}] 스냅샷 수집 중 에러: {e}")
            return False

    def get_log_from_list(self, search_patterns, file_path, result_dict=None, timeout_seconds=300):
        """
        정규식 패턴 세트가 완전히 매칭될 때까지 실시간으로 로그를 추적하여 파일에 저장합니다.
        (기존 get_log_from_list 기능)
        """
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 중복 실행 방지 검사
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                if all(f"{key}:" in content for key in search_patterns.keys()):
                    stop_event = threading.Event()
                    stop_event.set()
                    return stop_event

        def _pattern_worker(stop_event):
            found_versions = {}
            start_time = time.time()
            compiled_patterns = {key: re.compile(pattern) for key, pattern in search_patterns.items()}

            def _pattern_handler(connection):
                try:
                    while not stop_event.is_set():
                        if time.time() - start_time > timeout_seconds:
                            logging.warning(f"[{self.serial}] {' TIMEOUT: STOPPING MONITOR ':=^50}")
                            break

                        data = connection.read(4096)
                        if not data:
                            break
                        
                        chunk = data.decode('utf-8', errors='replace')
                        for line in chunk.splitlines():
                            for key, pattern_re in compiled_patterns.items():
                                if key not in found_versions:
                                    match = pattern_re.search(line)
                                    if match:
                                        extracted_value = (match.group(1) if match.groups() else match.group()).strip()
                                        found_versions[key] = extracted_value
                                        
                                        try:
                                            with open(file_path, "a", encoding="utf-8") as f:
                                                f.write(f"{key}: {extracted_value}\n")
                                                f.flush()
                                            logging.info(f"[{self.serial}] 패턴 기록 완료! [{key}] -> {extracted_value}")
                                            
                                            if result_dict is not None:
                                                result_dict[key] = extracted_value
                                        except Exception as file_err:
                                            logging.error(f"파일 기록 오류: {file_err}")

                            if len(found_versions) >= len(search_patterns):
                                logging.info(f"[{self.serial}] {' ALL ITEMS FOUND ':=^50}")
                                return  # 핸들러 리턴 종료

                except Exception as e:
                    logging.error(f"[{self.serial}] 패턴 핸들러 내부 오류: {e}")
                finally:
                    connection.close()

            try:
                self.device_obj.shell("logcat -c")  # 기존 버퍼 비우기
                logging.info(f"[{self.serial}] {' LOG PATTERN MONITORING START ':=^50}")
                self.device_obj.shell("logcat -v threadtime", handler=_pattern_handler)
                stop_event.set()
            except Exception as e:
                logging.error(f"[{self.serial}] 패턴 수집 중 에러 발생: {e}")
            finally:
                logging.info(f"[{self.serial}] 패턴 수집 스레드 종료.")

        stop_event = threading.Event()
        pattern_thread = threading.Thread(target=_pattern_worker, args=(stop_event,), daemon=True)
        pattern_thread.start()
        return stop_event


# ================================== load config data ==================================
config = configus.load_config('resources/configs/config.json')
# ================================== logging. ==================================

def call_logs_before(device, folder_path=None):
    """
    ppadb 객체와 handler를 이용한 실시간 로그 수집 및 100MB 단위 분할 저장
    """
    device_obj = device['ppadb_device'] # ppadb 객체 추출
    if folder_path is None:
        # config 변수는 외부에서 정의되어 있다고 가정합니다.
        folder_path = config.get('local_path', './')
        
    log_dir = os.path.join(folder_path, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # 1. 로그 버퍼 사이즈 확대
    try:
        device_obj.shell("logcat -G 100M")
    except Exception as e:
        logging.info(f"로그 버퍼 설정 실패: {e}")

    is_snapshot_enabled = config.get('snapshop_log', False) 
    filter_keywords = config.get('log_filter', [])
    is_filter_active = is_snapshot_enabled and isinstance(filter_keywords, list) and len(filter_keywords) > 0

    def log_worker(stop_event):
        # 변수 초기화
        state = {
            'file_count': 0,
            'overlap_lines': [],
            'current_log_path': "",
            'current_filter_path': ""
        }

        def update_paths():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = f"_{state['file_count']}" if state['file_count'] > 0 else ""
            state['current_log_path'] = os.path.join(log_dir, f"log_{timestamp}{suffix}.txt")
            state['current_filter_path'] = os.path.join(log_dir, f"log_{timestamp}{suffix}_filtered.txt")

        # 최초 경로 설정
        update_paths()

        # [핵심] 실시간 로그 데이터를 처리할 핸들러
        def log_handler(connection):
            try:
                while not stop_event.is_set():
                    with ExitStack() as stack:
                        # 1. 파일 오픈
                        f = stack.enter_context(open(state['current_log_path'], "w", encoding="utf-8", buffering=1024*1024))
                        f_filter = None
                        if is_filter_active:
                            f_filter = stack.enter_context(open(state['current_filter_path'], "w", encoding="utf-8"))
                            f_filter.write(f"=== Filter Active: {filter_keywords} ===\n\n")

                        # 2. 이전 파일 문맥 기록
                        if state['overlap_lines']:
                            f.write("\n" + "="*50 + "\n=== Previous Context ===\n")
                            f.writelines(state['overlap_lines'])
                            f.write("="*50 + "\n\n")

                        # 3. 소켓으로부터 데이터 읽기 루프
                        line_counter = 0
                        
                        # connection.read()는 데이터를 덩어리(chunk)로 가져옵니다.
                        while not stop_event.is_set():
                            chunk = connection.read(8192) # 8KB씩 읽기
                            if not chunk:
                                break
                            
                            text = chunk.decode('utf-8', errors='replace')
                            f.write(text)
                            
                            # 필터링 처리 (줄 단위로 쪼개서 검사)
                            if is_filter_active and f_filter:
                                for line in text.splitlines():
                                    if any(word.upper() in line.upper() for word in filter_keywords):
                                        f_filter.write(line + "\n")
                                f_filter.flush()

                            # 100MB 용량 체크 및 파일 로테이션
                            if os.path.getsize(state['current_log_path']) > 100 * 1024 * 1024:
                                # Overlap 추출 후 루프 탈출하여 새 파일 생성
                                f.flush()
                                break 

                        # 4. 루프를 빠져나왔다면(파일 교체 시점), 오버랩 데이터 갱신
                        try:
                            with open(state['current_log_path'], "r", encoding="utf-8", errors="replace") as rf:
                                lines = rf.readlines()
                                state['overlap_lines'] = lines[-10:] if len(lines) >= 10 else lines
                        except:
                            state['overlap_lines'] = []

                        state['file_count'] += 1
                        update_paths()
                        
                        # 파일 용량 때문에 break 된 거라면 핸들러 안에서 계속 돌아야 하지만, 
                        # logcat 명령 자체가 다시 실행되어야 하므로 핸들러를 종료하고 
                        # 밖의 while 루프에서 device.shell을 재호출하게 유도합니다.
                        return 

            except Exception as e:
                logging.error(f"핸들러 실행 중 오류: {e}")
            finally:
                connection.close()

        # 메인 루프: 핸들러가 종료되면(파일 로테이션 등) 다시 실행
        try:
            while not stop_event.is_set():
                # handler 방식으로 실행 (이 명령은 핸들러가 return될 때까지 블로킹됩니다)
                device_obj.shell("logcat -v threadtime", handler=log_handler)
                
                # 만약 파일 용량 때문에 return 된 게 아니라 stop_event 때문이라면 종료
                if stop_event.is_set():
                    break
                    
                logging.info(f"[{device_obj.serial}] 로그 파일 교체 및 수집 재시작...")
                
        except Exception as e:
            logging.error(f"로그 수집 워커 에러: {e}")
        finally:
            logging.info(f"[{device_obj.serial}] 로그 수집 쓰레드 최종 종료")

    # 스레드 시작
    stop_event = threading.Event()
    log_thread = threading.Thread(target=log_worker, args=(stop_event,), daemon=True)
    log_thread.start()

    logging.info(f"[*] 로그 수집 시작 (필터링 활성: {is_filter_active})")
    return stop_event

def call_logs(device, folder_path=None):
    """
    ppadb 객체와 handler를 이용한 실시간 로그 수집 및 100MB 단위 분할 저장
    
    :param DEBOUNCE_TIME: 스크린샷 연속 캡처를 방지하는 대기 시간 (단위: 초)
    """
    # ---------------------------------------------------------------------
    # 수정 가능한 설정 변수 (수정 필요 시 여기만 바꾸세요)
    # ---------------------------------------------------------------------
    DEBOUNCE_TIME = 3.0  # *초 동안 동일 명령 무시
    # ---------------------------------------------------------------------

    device_obj = device['ppadb_device'] 
    if folder_path is None:
        folder_path = config.get('local_path', './')
        
    log_dir = os.path.join(folder_path, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # 1. 로그 버퍼 사이즈 확대
    try:
        device_obj.shell("logcat -G 100M")
    except Exception as e:
        logging.info(f"로그 버퍼 설정 실패: {e}")

    # --- [필터 설정 로드 섹션] ---
    is_snapshot_enabled = config.get('snapshop_log', False) 
    
    filter_keywords = config.get('log_filter', [])
    is_filter_active = is_snapshot_enabled and isinstance(filter_keywords, list) and len(filter_keywords) > 0

    screenshot_keywords = config.get('log_screenshot_filter', [])
    is_screenshot_filter_active = isinstance(screenshot_keywords, list) and len(screenshot_keywords) > 0
    # ----------------------------

    def log_worker(stop_event):
        # 변수 초기화
        state = {
            'file_count': 0,
            'overlap_lines': [],
            'current_log_path': "",
            'current_filter_path': "",
            'last_screenshot_time': 0.0  # 마지막 스크린샷 타임스탬프
        }

        def update_paths():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = f"_{state['file_count']}" if state['file_count'] > 0 else ""
            state['current_log_path'] = os.path.join(log_dir, f"log_{timestamp}{suffix}.txt")
            state['current_filter_path'] = os.path.join(log_dir, f"log_{timestamp}{suffix}_filtered.txt")

        # 최초 경로 설정
        update_paths()

        # 실시간 로그 데이터를 처리할 핸들러
        def log_handler(connection):
            try:
                while not stop_event.is_set():
                    with ExitStack() as stack:
                        # 1. 파일 오픈
                        f = stack.enter_context(open(state['current_log_path'], "w", encoding="utf-8", buffering=1024*1024))
                        f_filter = None
                        if is_filter_active:
                            f_filter = stack.enter_context(open(state['current_filter_path'], "w", encoding="utf-8"))
                            f_filter.write(f"=== Filter Active: {filter_keywords} ===\n\n")

                        # 2. 이전 파일 문맥 기록
                        if state['overlap_lines']:
                            f.write("\n" + "="*50 + "\n=== Previous Context ===\n")
                            f.writelines(state['overlap_lines'])
                            f.write("="*50 + "\n\n")

                        # 3. 소켓으로부터 데이터 읽기 루프
                        while not stop_event.is_set():
                            chunk = connection.read(8192) 
                            if not chunk:
                                break
                            
                            text = chunk.decode('utf-8', errors='replace')
                            f.write(text)
                            
                            # 줄 단위 분할 및 필터링 검사 처리
                            if (is_filter_active and f_filter) or is_screenshot_filter_active:
                                for line in text.splitlines():
                                    line_upper = line.upper()
                                    
                                    # [기존 기능] 일반 로그 텍스트 필터 저장
                                    if is_filter_active and f_filter:
                                        if any(word.upper() in line_upper for word in filter_keywords):
                                            f_filter.write(line + "\n")
                                    
                                    # [신규 기능] 스크린샷 캡처 필터 감지 (설정된 DEBOUNCE_TIME 적용)
                                    if is_screenshot_filter_active:
                                        if any(word.upper() in line_upper for word in screenshot_keywords):
                                            current_time = time.time()
                                            
                                            # 마지막 캡처 시점으로부터 설정한 시간(DEBOUNCE_TIME)이 지났는지 검사
                                            if current_time - state['last_screenshot_time'] >= DEBOUNCE_TIME:
                                                state['last_screenshot_time'] = current_time  # 시각 업데이트
                                                logging.info(f"📸 [스크린샷 조건 충족 - 캡처 실행]: {line.strip()}")
                                                try:
                                                    func_record_image.record_screenshot(device)
                                                except Exception as screenshot_err:
                                                    logging.error(f"스크린샷 캡처 중 오류 발생: {screenshot_err}")
                                            else:
                                                # 설정 시간 이내에 연속으로 들어온 로그는 무시
                                                remaining = DEBOUNCE_TIME - (current_time - state['last_screenshot_time'])
                                                logging.debug(f"⏳ [디바운스 중] 스크린샷 무시됨 (남은 시간: {remaining:.1f}초): {line.strip()}")

                                if is_filter_active and f_filter:
                                    f_filter.flush()

                            # 100MB 용량 체크 및 파일 로테이션
                            if os.path.getsize(state['current_log_path']) > 100 * 1024 * 1024:
                                f.flush()
                                break 

                        # 4. 루프를 빠져나왔다면, 오버랩 데이터 갱신
                        try:
                            with open(state['current_log_path'], "r", encoding="utf-8", errors="replace") as rf:
                                lines = rf.readlines()
                                state['overlap_lines'] = lines[-10:] if len(lines) >= 10 else lines
                        except:
                            state['overlap_lines'] = []

                        state['file_count'] += 1
                        update_paths()
                        return 

            except Exception as e:
                logging.error(f"핸들러 실행 중 오류: {e}")
            finally:
                connection.close()

        # 메인 루프
        try:
            while not stop_event.is_set():
                device_obj.shell("logcat -v threadtime", handler=log_handler)
                if stop_event.is_set():
                    break
                logging.info(f"[{device_obj.serial}] 로그 파일 교체 및 수집 재시작...")
                
        except Exception as e:
            logging.error(f"로그 수집 워커 에러: {e}")
        finally:
            logging.info(f"[{device_obj.serial}] 로그 수집 쓰레드 최종 종료")

    # 스레드 시작
    stop_event = threading.Event()
    log_thread = threading.Thread(target=log_worker, args=(stop_event,), daemon=True)
    log_thread.start()

    logging.info(f"[*] 로그 수집 시작 (스크린샷 디바운스 대기 타임: {DEBOUNCE_TIME}초)")
    return stop_event

# ================================== etc logging. ==================================
# 1. 로그 중지 함수
def stop_logging(stop_event):
    if stop_event is not None:
        logging.info("로그 수집 중지 요청 중...")
        stop_event.set()  # 스레드 내부의 while 루프를 종료시킴
        return True
    return False

def get_snapshot_logs(device, folder_path=None, duration_sec=3):
    """
    # 4. 동작 시기 전후의 로그만 받는 함수
    현재 시점 기준 [과거 duration_sec ~ 미래 duration_sec]의 로그를 수집하고 필터링된 파일을 별도 생성하는 함수
    """
    # input 된 folder_path 가 없으면 config.json에 폴더 받아서 함.
    if folder_path == None:
        folder_path = config['local_path']
        log_dir = os.path.join(folder_path, "logs", "snapshot")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
    else:
        log_dir = folder_path
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
    # 필터 리스트 호출 및 유효성 검사
    log_filter = config.get('log_filter', [])
    is_filter_active = isinstance(log_filter, list) and len(log_filter) > 0


    # 파일명 생성
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join(log_dir, f"Screenshot_{timestamp}.txt")
    filtered_file_path = os.path.join(log_dir, f"Screenshot_{timestamp}_filtered.txt")

    past_time = (datetime.now() - timedelta(seconds=duration_sec)).strftime("%m-%d %H:%M:%S.000")
    
    logging.info(f"[*] 스냅샷 로그 수집 시작 (시작지점: {past_time})")
    
    try:
        # 두 개의 파일을 동시에 엽니다 (필터가 활성화된 경우만 두 번째 파일 오픈)
        from contextlib import ExitStack
        with ExitStack() as stack:
            f = stack.enter_context(open(file_path, "w", encoding="utf-8"))
            f_filter = None
            if is_filter_active:
                f_filter = stack.enter_context(open(filtered_file_path, "w", encoding="utf-8"))
                f_filter.write(f"=== Log Filter Active: {log_filter} ===\n\n")

            # 2. 과거 로그 긁어오기
            logging.info(f"[1/2] 과거 {duration_sec}초 로그 복사 및 필터링 중...")
            # 과거 로그는 subprocess.run의 결과를 변수에 담아서 처리해야 필터링이 가능합니다.
            past_process = subprocess.run(
                ["adb", "-s", device['serial'], "logcat", "-v", "threadtime", "-t", past_time],
                capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            
            for line in past_process.stdout.splitlines():
                f.write(line + "\n")
                if is_filter_active:
                    if any(word.upper() in line.upper() for word in log_filter):
                        f_filter.write(line + "\n")

            separator = "\n" + "="*50 + f"\n=== PAST LOG END / REAL-TIME START AT {datetime.now()} ===\n" + "="*50 + "\n\n"
            f.write(separator)
            if f_filter: f_filter.write(separator)

            # 3. 실시간 로그 수집 시작
            logging.info(f"[2/2] 실시간 로그 수집 중 ({duration_sec}초간)...")
            process = subprocess.Popen(
                ["adb", "-s", device['serial'], "logcat", "-v", "threadtime"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace"
            )

            start_time = time.time()
            while time.time() - start_time < duration_sec:
                line = process.stdout.readline()
                if not line: break
                
                # 전체 로그 기록
                f.write(line)
                
                # 필터링 기록
                if is_filter_active:
                    if any(word.upper() in line.upper() for word in log_filter):
                        f_filter.write(line)
                        f_filter.flush() # 실시간성 보장

            # 4. 프로세스 종료
            if process.poll() is None:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True)
                process.wait()

        logging.info(f"[+] 스냅샷 저장 완료: {file_path}")
        if is_filter_active:
            logging.info(f"[+] 필터링된 스냅샷 저장 완료: {filtered_file_path}")
        
        return True
    
    except Exception as e:
        logging.error(f"[!] 스냅샷 수집 중 에러: {e}")
        return False

def get_log_from_list(device, search_patterns, file_path, result_dict=None):
    """
    ppadb의 device_obj와 handler를 사용하여 실시간 logcat을 모니터링하고 패턴을 추출합니다.
    """
    timeout_seconds = 300
    # 전달받은 device 딕셔너리에서 ppadb 객체 추출
    device_device_obj = device['ppadb_device']
    
    # 1. 초기화: 기존 파일 삭제 및 폴더 생성
    #if os.path.exists(file_path):
    #    os.remove(file_path)
    #    logging.info(f"기존 버전 정보 파일 삭제 완료: {file_path}")
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    # 이미 파일이 있고 모든 정보를 다 찾았는지 체크하는 로직 (중복 실행 방지)
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            if all(f"{key}:" in content for key in search_patterns.keys()):
                # 이미 다 찾았다면 스레드를 만들지 않고 종료
                stop_event = threading.Event()
                stop_event.set()
                return stop_event
            
    def log_worker(stop_event):
        found_versions = {}
        start_time = time.time()
        compiled_patterns = {key: re.compile(pattern) for key, pattern in search_patterns.items()}
        
        # [중요] 실시간 로그 데이터를 처리할 핸들러 함수
        def log_handler(connection):
            try:
                # connection.read()를 통해 바이너리 데이터를 덩어리로 가져옵니다.
                while not stop_event.is_set():
                    # 타임아웃 체크
                    if time.time() - start_time > timeout_seconds:
                        logging.warning(f"{' TIMEOUT: STOPPING MONITOR ':=^50}")
                        break

                    # 데이터 읽기 (소켓 스트림)
                    data = connection.read(4096)
                    if not data:
                        break
                    
                    # 문자열 변환 및 줄 단위 처리
                    chunk = data.decode('utf-8', errors='replace')
                    lines = chunk.splitlines()

                    for line in lines:
                        # 패턴 매칭
                        for key, pattern_re in compiled_patterns.items():
                            if key not in found_versions:
                                match = pattern_re.search(line)
                                if match:
                                    extracted_value = (match.group(1) if match.groups() else match.group()).strip()
                                    found_versions[key] = extracted_value
                                    
                                    # 파일 기록
                                    try:
                                        with open(file_path, "a", encoding="utf-8") as f:
                                            f.write(f"{key}: {extracted_value}\n")
                                            f.flush()
                                        logging.info(f"[{device_device_obj.serial}] 기록 완료! [{key}] -> {extracted_value}")
                                        
                                        if result_dict is not None:
                                            result_dict[key] = extracted_value
                                    except Exception as e:
                                        logging.error(f"파일 기록 오류: {e}")

                        # 모든 항목을 다 찾았다면 핸들러 종료 루프 탈출
                        if len(found_versions) >= len(search_patterns):
                            logging.info(f"{' ALL ITEMS FOUND ':=^50}")
                            return # handler 종료

            except Exception as e:
                logging.error(f"핸들러 내부 오류: {e}")
            finally:
                connection.close()

        try:
            # 1. 기존 로그 버퍼 비우기
            device_device_obj.shell("logcat -c")
            
            logging.info(f"{' LOG MONITORING START (PPADB HANDLER) ':=^50}")
            
            # 2. handler 방식으로 logcat 실행 (블로킹 모드)
            device_device_obj.shell("logcat -v threadtime", handler=log_handler)
            
            # 작업 완료 후 이벤트 설정
            stop_event.set()

        except Exception as e:
            logging.error(f"로그 수집 중 에러 발생: {e}")
        finally:
            logging.info(f"[{device_device_obj.serial}] 버전 수집 스레드 종료.")

    stop_event = threading.Event()
    log_thread = threading.Thread(target=log_worker, args=(stop_event,), daemon=True)
    log_thread.start()

    return stop_event



