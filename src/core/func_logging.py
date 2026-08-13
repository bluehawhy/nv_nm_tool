import os
import subprocess
import re
import threading
import time
from contextlib import ExitStack
from datetime import datetime
from ..core import func_record_image
from ..utils import configus, loggas
from collections import deque



logging = loggas.logger

# ================================== load config data ==================================
class ScreenshotFilterMatcher:
    """
    로드된 config 객체(dict)에서 스크린샷 필터를 가져와
    정규표현식을 미리 컴파일하여 관리하는 필터 클래스.
    """
    def __init__(self, config_data: dict = None, filter_key: str = 'log_screenshot_filter'):
        # config_data가 전달되지 않으면 상위 scope의 config 사용
        if config_data is None:
            config_data = config if 'config' in globals() else {}
            
        self.config_data = config_data
        self.filter_key = filter_key
        self.compiled_filters = {}
        
        # 정규표현식 사전 컴파일 실행
        self._compile_filters()

    def _compile_filters(self):
        filter_dict = {}
        if isinstance(self.config_data, dict):
            filter_dict = self.config_data.get(self.filter_key, {})

        if not isinstance(filter_dict, dict):
            return

        # 정규표현식 사전 컴파일
        for folder_name, keywords in filter_dict.items():
            target_patterns = keywords if isinstance(keywords, list) else [keywords]
            compiled_list = []

            for pat in target_patterns:
                if isinstance(pat, re.Pattern):
                    compiled_list.append(pat)
                elif isinstance(pat, str):
                    try:
                        # 대소문자 무시(IGNORECASE) 적용하여 미리 컴파일
                        compiled_list.append(re.compile(pat, re.IGNORECASE))
                    except re.error as e:
                        logging.warning(f"잘못된 정규표현식 패턴 무시됨 [{pat}]: {e}")

            if compiled_list:
                self.compiled_filters[folder_name] = compiled_list

    @property
    def is_active(self) -> bool:
        """활성화된 필터 패턴이 하나라도 있는지 여부"""
        return len(self.compiled_filters) > 0

    def match(self, line: str):
        """
        라인을 받아 매칭되는 folder_name을 generator 형태로 yield
        """
        for folder_name, patterns in self.compiled_filters.items():
            if any(pat.search(line) for pat in patterns):
                yield folder_name

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
        self.sfm = ScreenshotFilterMatcher(self.config)
        
        # 기본 저장 경로 설정
        if folder_path is None:
            folder_path = self.config.get('local_path', './')
        self.folder_path = folder_path
        self.log_dir = os.path.join(self.folder_path, "logs")
        os.makedirs(self.log_dir, exist_ok=True)

        # 실시간 수집 상태 관리 변수
        self.file_count = 0
        self.overlap_lines = []
        self.current_log_path = ""
        self.current_filter_path = ""
        self.last_screenshot_time = 0.0
        self.latest_car_pos = None
        
        # 스레드 통신용 이벤트 및 락
        self.stop_event = None
        self.lock = threading.Lock()

        # 🚀 [개편] 실시간 로그를 메모리에 유지할 링 버퍼 (최근 10,000줄 저장)
        self.recent_logs = deque(maxlen=10000)
        
        # 🚀 [개편] 등록된 패턴 검색 작업 등록 리스트
        self.active_pattern_jobs = []

    def close_connection_by_error(self):
        """UI단 또는 외부에 의해 에러가 감지되었을 때 수집 스레드 및 커넥션을 완전히 강제 종료합니다."""
        logging.info(f"[{self.serial}] UI 단 요청으로 인한 커넥션 및 수집 종료 처리 시작")
        
        self.stop_live_logging()
        
        try:
            if hasattr(self.device_obj, 'connection') and self.device_obj.connection:
                self.device_obj.connection.close()
                logging.info(f"[{self.serial}] ppadb connection socket 강제 종료 완료")
        except Exception as e:
            logging.debug(f"[{self.serial}] Connection socket 종료 중 예외 (이미 닫힘): {e}")

        try:
            subprocess.run(["adb", "-s", self.serial, "logcat", "-c"], capture_output=True)
        except Exception:
            pass

    def _update_paths(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{self.file_count}" if self.file_count > 0 else ""
        self.current_log_path = os.path.join(self.log_dir, f"log_{timestamp}{suffix}.txt")
        self.current_filter_path = os.path.join(self.log_dir, f"log_{timestamp}{suffix}_filtered.txt")

    def _expand_log_buffer(self):
        try:
            self.device_obj.shell("logcat -G 100M")
        except Exception as e:
            logging.info(f"[{self.serial}] 로그 버퍼 설정 실패: {e}")

    def _update_overlap_context(self):
        try:
            with open(self.current_log_path, "r", encoding="utf-8", errors="replace") as rf:
                lines = rf.readlines()
                self.overlap_lines = lines[-10:] if len(lines) >= 10 else lines
        except Exception:
            self.overlap_lines = []

    def start_live_logging(self, debounce_time=3.0):
        self._expand_log_buffer()
        self._update_paths()

        self.stop_event = threading.Event()
        log_thread = threading.Thread(
            target=self._live_log_worker, 
            args=(debounce_time,), 
            daemon=True
        )
        log_thread.start()

        logging.info(f"[*] [{self.serial}] 단일 통합 소켓 로그 수집 시작")
        return self.stop_event

    def stop_live_logging(self):
        if self.stop_event is not None and not self.stop_event.is_set():
            logging.info(f"[{self.serial}] 로그 수집 중지 요청 중...")
            self.stop_event.set()
            return True
        return False

    def _live_log_worker(self, debounce_time):
        try:
            while not self.stop_event.is_set():
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



    # 🚀 [신규 메서드] 스크린샷 캡처를 별도 스레드에서 수행
    def _take_screenshot(self, save_dir):
        """스크린샷 작업을 별도 스레드에서 실행"""
        try:
            func_record_image.record_screenshot(
                device=self.device,
                log_manager=self,
                save_dir=save_dir
            )
        except Exception as e:
            logging.error(
                f"[{self.serial}] 스크린샷 캡처 중 오류 발생: {e}"
            )

    def _live_log_stream_handler(self, connection, debounce_time):
        is_snapshot_enabled = self.config.get('snapshop_log', False)
        filter_keywords = self.config.get('log_filter', [])
        is_filter_active = (
            is_snapshot_enabled
            and isinstance(filter_keywords, list)
            and len(filter_keywords) > 0
        )

        try:
            with ExitStack() as stack:
                f = stack.enter_context(
                    open(
                        self.current_log_path,
                        "w",
                        encoding="utf-8",
                        buffering=1024 * 1024
                    )
                )

                f_filter = None
                if is_filter_active:
                    f_filter = stack.enter_context(
                        open(self.current_filter_path, "w", encoding="utf-8")
                    )
                    f_filter.write(
                        f"=== Filter Active: {filter_keywords} ===\n\n"
                    )

                if self.overlap_lines:
                    f.write(
                        "\n" + "=" * 50 +
                        "\n=== Previous Context ===\n"
                    )
                    f.writelines(self.overlap_lines)
                    f.write("=" * 50 + "\n\n")

                while not self.stop_event.is_set():

                    chunk = connection.read(8192)

                    if not chunk:
                        return

                    text = chunk.decode("utf-8", errors="replace")
                    f.write(text)

                    lines = text.splitlines(keepends=True)

                    for line in lines:
                        clean_line = line.rstrip("\r\n")

                        # 1. 최근 로그
                        with self.lock:
                            self.recent_logs.append(
                                (time.time(), clean_line)
                            )

                        # 2. 최신 위치 로그는 즉시 갱신
                        if "win 0 SFN" in clean_line:
                            pc_time = datetime.now().strftime(
                                "%H:%M:%S.%f"
                            )[:-3]

                            self.latest_car_pos = (
                                pc_time,
                                clean_line
                            )

                        # 3. 패턴 작업
                        self._process_pattern_jobs(clean_line)

                        # 4. 필터 로그
                        if is_filter_active and f_filter:
                            if any(
                                word.upper() in clean_line.upper()
                                for word in filter_keywords
                            ):
                                f_filter.write(line)

                        # 5. 스크린샷 트리거
                        if self.sfm.is_active:
                            for folder_name in self.sfm.match(clean_line):

                                current_time = time.time()

                                if (
                                    current_time - self.last_screenshot_time
                                    >= debounce_time
                                ):
                                    self.last_screenshot_time = current_time

                                    logging.info(
                                        f"📸 [{self.serial}] "
                                        f"[{folder_name} 조건 충족 - 캡처 실행]: "
                                        f"{clean_line}"
                                    )

                                    target_save_dir = os.path.join(
                                        self.folder_path,
                                        folder_name
                                    )
                                    os.makedirs(
                                        target_save_dir,
                                        exist_ok=True
                                    )

                                    # ⭐ 핵심: 스크린샷을 별도 스레드에서 실행
                                    threading.Thread(
                                        target=self._take_screenshot,
                                        args=(target_save_dir,),
                                        daemon=True
                                    ).start()

                    if is_filter_active and f_filter:
                        f_filter.flush()

                    if os.path.getsize(
                        self.current_log_path
                    ) > 100 * 1024 * 1024:
                        f.flush()
                        break

            self._update_overlap_context()
            self.file_count += 1
            self._update_paths()

        except Exception as e:
            logging.error(
                f"[{self.serial}] 핸들러 실행 중 오류: {e}"
            )

        finally:
            connection.close()






    # 🚀 [신규 메서드] 패턴 모니터링 내부 처리기
    def _process_pattern_jobs(self, line):
        if not self.active_pattern_jobs:
            return

        with self.lock:
            for job in list(self.active_pattern_jobs):
                for key, pattern_re in list(job['compiled_patterns'].items()):
                    if key not in job['found_versions']:
                        match = pattern_re.search(line)
                        if match:
                            extracted_value = (match.group(1) if match.groups() else match.group()).strip()
                            job['found_versions'][key] = extracted_value
                            
                            try:
                                with open(job['file_path'], "a", encoding="utf-8") as f:
                                    f.write(f"{key}: {extracted_value}\n")
                                logging.info(f"[{self.serial}] 패턴 기록 완료! [{key}] -> {extracted_value}")
                                
                                if job['result_dict'] is not None:
                                    job['result_dict'][key] = extracted_value
                            except Exception as file_err:
                                logging.error(f"파일 기록 오류: {file_err}")

                # 모든 패턴 탐색 완료 시 작업 해제
                if len(job['found_versions']) >= len(job['search_patterns']):
                    logging.info(f"[{self.serial}] {' ALL PATTERNS FOUND ':=^50}")
                    job['stop_event'].set()
                    self.active_pattern_jobs.remove(job)

    # 🚀 [통합] 추가 ADB 세션 연결 없이 메모리 버퍼 및 실시간 모니터링 활용
    def get_snapshot_logs(self, folder_path=None, duration_sec=60):
        if folder_path is None:
            log_dir = os.path.join(self.config.get('local_path', './'), "logs", "snapshot")
        else:
            log_dir = folder_path
        os.makedirs(log_dir, exist_ok=True)

        log_filter = self.config.get('log_filter', [])
        is_filter_active = isinstance(log_filter, list) and len(log_filter) > 0

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(log_dir, f"Snapshot_{timestamp}.txt")
        filtered_file_path = os.path.join(log_dir, f"Snapshot_{timestamp}_filtered.txt")

        logging.info(f"[*] [{self.serial}] 스냅샷 로그 추출 시작 (과거 {duration_sec}초 ~ 미래 {duration_sec}초)")

        now = time.time()
        start_threshold = now - duration_sec

        # 1. 과거 로그 추출 (메모리 링 버퍼 조회)
        with self.lock:
            past_logs = [line for t, line in self.recent_logs if t >= start_threshold]

        try:
            with open(file_path, "w", encoding="utf-8") as f, \
                 open(filtered_file_path, "w", encoding="utf-8") if is_filter_active else ExitStack() as f_filter:
                
                if is_filter_active:
                    f_filter.write(f"=== Log Filter Active: {log_filter} ===\n\n")

                # 과거 로그 쓰기
                for line in past_logs:
                    f.write(line + "\n")
                    if is_filter_active:
                        if any(word.upper() in line.upper() for word in log_filter):
                            f_filter.write(line + "\n")

                separator = f"\n{'='*50}\n=== PAST LOG END / REAL-TIME START AT {datetime.now()} ===\n{'='*50}\n\n"
                f.write(separator)
                if is_filter_active:
                    f_filter.write(separator)

                # 2. 미래 duration_sec 동안 실시간 로깅 관찰
                end_time = time.time() + duration_sec
                last_idx = len(self.recent_logs)

                while time.time() < end_time:
                    time.sleep(0.1)
                    with self.lock:
                        current_len = len(self.recent_logs)
                        if current_len > last_idx:
                            new_items = list(self.recent_logs)[last_idx:current_len]
                            last_idx = current_len
                        else:
                            new_items = []

                    for _, line in new_items:
                        f.write(line + "\n")
                        if is_filter_active:
                            if any(word.upper() in line.upper() for word in log_filter):
                                f_filter.write(line + "\n")

            logging.info(f"[+] [{self.serial}] 스냅샷 저장 완료: {file_path}")
            return True
        except Exception as e:
            logging.error(f"[!] [{self.serial}] 스냅샷 수집 중 에러: {e}")
            return False

    # 🚀 [통합] 패턴 작업을 라이브 스레드에 작업으로 등록하여 수집
    def fetch_log_from_list(self, search_patterns, file_path, result_dict=None, timeout_seconds=300):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        stop_event = threading.Event()

        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                if all(f"{key}:" in content for key in search_patterns.keys()):
                    stop_event.set()
                    return stop_event

        job = {
            'search_patterns': search_patterns,
            'compiled_patterns': {key: re.compile(pattern) for key, pattern in search_patterns.items()},
            'file_path': file_path,
            'result_dict': result_dict,
            'found_versions': {},
            'stop_event': stop_event,
            'start_time': time.time()
        }

        with self.lock:
            self.active_pattern_jobs.append(job)

        # 타임아웃 감시 스레드
        def _timeout_checker():
            while not stop_event.is_set():
                if time.time() - job['start_time'] > timeout_seconds:
                    logging.warning(f"[{self.serial}] TIMEOUT: 패턴 모니터링 중단")
                    with self.lock:
                        if job in self.active_pattern_jobs:
                            self.active_pattern_jobs.remove(job)
                    stop_event.set()
                    break
                time.sleep(1)

        threading.Thread(target=_timeout_checker, daemon=True).start()
        return stop_event

