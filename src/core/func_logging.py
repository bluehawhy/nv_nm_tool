import os
import subprocess
import re
import threading
import time
from contextlib import ExitStack
from datetime import datetime
from ..utils import configus, loggas
from collections import deque



logging = loggas.logger

# logcat 소켓은 임의의 바이트 경계에서 잘릴 수 있으므로 완성된 로그 줄에서만
# 위치를 판별한다. win 번호/공백/대소문자는 기기 및 빌드별 차이를 허용한다.
CAR_POS_PATTERN = re.compile(
    r"\bwin\s+\d+\s+SFN\b.*?\bpos\s+-?\d+\s+-?\d+\b",
    re.IGNORECASE,
)

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

        self.record_manager = None
                
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
        self._lifecycle_lock = threading.Lock()
        self._log_thread = None
        self._active_connection = None

        # 실시간 로그를 메모리에 유지할 링 버퍼 (최근 10,000줄 저장)
        # deque 길이는 최대치에서 고정되므로 별도 증가 시퀀스로 신규 로그를 추적한다.
        self.recent_logs = deque(maxlen=10000)
        self._log_sequence = 0
        
        # 🚀 [개편] 등록된 패턴 검색 작업 등록 리스트
        self.active_pattern_jobs = []

    def set_record_manager(self, record_manager):
        self.record_manager = record_manager

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
            subprocess.run(
                ["adb", "-s", self.serial, "logcat", "-c"],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
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

    def start_live_logging(self, debounce_time=1.0, enable_filters=True):
        self.enable_filters = enable_filters

        with self._lifecycle_lock:
            if self._log_thread is not None and self._log_thread.is_alive():
                logging.info(f"[{self.serial}] 로그 수집 스레드가 이미 실행 중입니다.")
                return self.stop_event

            stop_event = threading.Event()
            log_thread = threading.Thread(
                target=self._live_log_worker,
                args=(debounce_time, stop_event),
                daemon=True,
                name=f"AndroidLogWorker-{self.serial}",
            )
            self.stop_event = stop_event
            self._log_thread = log_thread

        self._expand_log_buffer()
        self._update_paths()
        log_thread.start()

        logging.info(f"[*] [{self.serial}] 단일 통합 소켓 로그 수집 시작")
        return stop_event

    def stop_live_logging(self):
        with self._lifecycle_lock:
            stop_event = self.stop_event
            connection = self._active_connection
            log_thread = self._log_thread

        if stop_event is None:
            return False

        stop_requested = not stop_event.is_set()
        if stop_requested:
            logging.info(f"[{self.serial}] 로그 수집 중지 요청 중...")
            stop_event.set()

        # connection.read()가 대기 중이어도 즉시 빠져나올 수 있도록 활성 소켓을 닫는다.
        if connection is not None:
            try:
                connection.close()
            except Exception as e:
                logging.debug(f"[{self.serial}] 활성 로그 소켓 종료 중 예외: {e}")

        if (
            log_thread is not None
            and log_thread.is_alive()
            and log_thread is not threading.current_thread()
        ):
            log_thread.join(timeout=2)
            if log_thread.is_alive():
                logging.warning(f"[{self.serial}] 로그 수집 스레드 종료 대기 시간 초과")

        return stop_requested

    def _live_log_worker(self, debounce_time, stop_event):
        retry_delay = 0.5

        try:
            while not stop_event.is_set():
                stream_state = {"reason": "error"}

                def stream_handler(connection):
                    stream_state["reason"] = self._live_log_stream_handler(
                        connection,
                        debounce_time,
                        stop_event,
                    )

                try:
                    self.device_obj.shell(
                        "logcat -v threadtime",
                        handler=stream_handler,
                    )
                except Exception as e:
                    if not stop_event.is_set():
                        logging.error(f"[{self.serial}] 로그 수집 워커 에러: {e}")
                    stream_state["reason"] = "error"

                reason = stream_state["reason"]

                if stop_event.is_set() or reason == "stopped":
                    break

                if reason == "rotate":
                    retry_delay = 0.5
                    logging.info(f"[{self.serial}] 로그 파일 교체 후 수집을 계속합니다.")
                    continue

                logging.warning(
                    f"[{self.serial}] 로그 스트림 종료({reason}). "
                    f"{retry_delay:.1f}초 후 재연결합니다."
                )
                if stop_event.wait(retry_delay):
                    break
                retry_delay = min(retry_delay * 2, 5.0)
        finally:
            with self._lifecycle_lock:
                if self._log_thread is threading.current_thread():
                    self._log_thread = None
                self._active_connection = None

            logging.info(f"[{self.serial}] 로그 수집 쓰레드 최종 종료")

    # 🚀 [신규 메서드] 스크린샷 캡처를 별도 스레드에서 수행
    def _take_screenshot(self, save_dir):
        """스크린샷 작업을 별도 스레드에서 실행"""
        try:
            if self.record_manager is None:
                logging.warning(
                    f"[{self.serial}] "
                    "AndroidRecordManager가 연결되지 않았습니다."
                )
                return

            self.record_manager.record_screenshot(
                save_dir=save_dir
            )

        except Exception as e:
            logging.error(
                f"[{self.serial}] "
                f"스크린샷 캡처 중 오류 발생: {e}"
            )

    def _live_log_stream_handler(self, connection, debounce_time, stop_event):
        enable_filters = getattr(self, "enable_filters", True)

        is_snapshot_enabled = self.config.get('snapshop_log', False)
        filter_keywords = self.config.get('log_filter', [])
        is_filter_active = (
            enable_filters
            and is_snapshot_enabled
            and isinstance(filter_keywords, list)
            and len(filter_keywords) > 0
        )

        reason = "stopped"
        file_opened = False
        line_buffer = ""

        with self._lifecycle_lock:
            if stop_event.is_set() or stop_event is not self.stop_event:
                try:
                    connection.close()
                except Exception:
                    pass
                return "stopped"
            self._active_connection = connection

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
                file_opened = True

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

                while not stop_event.is_set():
                    chunk = connection.read(8192)

                    if not chunk:
                        reason = "eof"
                        break

                    text = chunk.decode("utf-8", errors="replace")
                    f.write(text)

                    # connection.read()는 로그 한 줄의 중간에서 끊길 수 있다.
                    # 이전 청크의 꼬리를 보관했다가 개행을 받은 뒤 완성된 줄만 처리한다.
                    line_buffer += text
                    complete_lines = line_buffer.split("\n")
                    line_buffer = complete_lines.pop()

                    for raw_line in complete_lines:
                        clean_line = raw_line.rstrip("\r")
                        line = raw_line + "\n"

                        # 1. 최근 로그
                        with self.lock:
                            self._log_sequence += 1
                            self.recent_logs.append(
                                (self._log_sequence, time.time(), clean_line)
                            )

                        # 2. 최신 위치 로그는 즉시 갱신
                        if CAR_POS_PATTERN.search(clean_line):
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
                        if enable_filters and self.sfm.is_active:
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
                        reason = "rotate"
                        break

                if stop_event.is_set():
                    reason = "stopped"

        except Exception as e:
            reason = "stopped" if stop_event.is_set() else "error"
            if reason == "error":
                logging.error(
                    f"[{self.serial}] 핸들러 실행 중 오류: {e}"
                )

        finally:
            try:
                connection.close()
            except Exception as e:
                logging.debug(f"[{self.serial}] 로그 소켓 종료 중 예외: {e}")

            with self._lifecycle_lock:
                if self._active_connection is connection:
                    self._active_connection = None

            # EOF/예외/정상 종료 모두 현재 파일을 먼저 마감한다.
            # 다음 연결은 반드시 새로운 파일 경로를 사용하므로 기존 로그가 보존된다.
            if file_opened:
                self._update_overlap_context()
                self.file_count += 1
                self._update_paths()

        return reason

    # 🚀 [신규 메서드] 패턴 모니터링 내부 처리기
    def _process_pattern_jobs(self, line):
        if not self.active_pattern_jobs:
            return

        with self.lock:
            for job in list(self.active_pattern_jobs):
                # 취소된 작업은 다음 로그 처리 시 정리한다.
                if job['stop_event'].is_set():
                    self.active_pattern_jobs.remove(job)
                    continue
                for key, pattern_re in list(job['compiled_patterns'].items()):
                    if key not in job['found_versions']:
                        match = pattern_re.search(line)
                        if match:
                            extracted_value = (match.group(1) if match.groups() else match.group()).strip()
                            job['found_versions'][key] = extracted_value

                            if job.get('pattern_only'):
                                if job.get('result_dict') is not None:
                                    job['result_dict'][key] = extracted_value
                                if job.get('on_result') is not None:
                                    job['on_result'](dict(job['result_dict']))
                                logging.info(
                                    f"[{self.serial}] 패턴 검색 완료! [{key}] -> {extracted_value}"
                                )
                            else:
                                try:
                                    with open(job['file_path'], "a", encoding="utf-8") as f:
                                        f.write(f"{key}: {extracted_value}\n")
                                    logging.info(
                                        f"[{self.serial}] 패턴 기록 완료! [{key}] -> {extracted_value}"
                                    )

                                    if job.get('result_dict') is not None:
                                        job['result_dict'][key] = extracted_value
                                    if job.get('on_result') is not None:
                                        job['on_result'](dict(job['result_dict']))
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
            log_dir = os.path.join(self.folder_path, "logs", "snapshot")
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
            past_logs = [
                (sequence, line)
                for sequence, timestamp, line in self.recent_logs
                if timestamp >= start_threshold
            ]
            last_sequence = self._log_sequence

        try:
            with open(file_path, "w", encoding="utf-8") as f, \
                 open(filtered_file_path, "w", encoding="utf-8") if is_filter_active else ExitStack() as f_filter:
                
                if is_filter_active:
                    f_filter.write(f"=== Log Filter Active: {log_filter} ===\n\n")

                # 과거 로그 쓰기
                for _, line in past_logs:
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

                while time.time() < end_time:
                    time.sleep(0.1)
                    with self.lock:
                        new_items = [
                            item
                            for item in self.recent_logs
                            if item[0] > last_sequence
                        ]
                        if new_items:
                            last_sequence = new_items[-1][0]

                    for _, _, line in new_items:
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
    def fetch_log_from_list(
        self,
        search_patterns,
        file_path=None,
        result_dict=None,
        timeout_seconds=300,
        on_result=None,
    ):
        stop_event = threading.Event()

        if file_path:
            file_dir = os.path.dirname(file_path)
            if file_dir:
                os.makedirs(file_dir, exist_ok=True)

            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if all(f"{key}:" in content for key in search_patterns.keys()):
                    if result_dict is not None:
                        for key in search_patterns.keys():
                            match = re.search(
                                rf"^{re.escape(key)}:\s*(.+)$", content, re.MULTILINE
                            )
                            if match:
                                result_dict[key] = match.group(1).strip()
                    stop_event.set()
                    return stop_event

        if result_dict is None:
            result_dict = {}

        job = {
            'search_patterns': search_patterns,
            'compiled_patterns': {key: re.compile(pattern) for key, pattern in search_patterns.items()},
            'file_path': file_path,
            'result_dict': result_dict,
            'pattern_only': file_path is None,
            'found_versions': {},
            'stop_event': stop_event,
            'start_time': time.time(),
            'on_result': on_result,
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
                time.sleep(0.5)

        threading.Thread(target=_timeout_checker, daemon=True).start()
        return stop_event

