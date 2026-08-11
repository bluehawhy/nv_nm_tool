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

        try:
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
                        return  # 스트림 끊김 시 리턴
                    
                    text = chunk.decode('utf-8', errors='replace')
                    f.write(text)
                    
                    # 줄 단위 분할 및 필터링 검사
                    if (is_filter_active and f_filter) or self.sfm.is_active:
                        for line in text.splitlines():
                            
                            # 일반 로그 텍스트 필터 저장
                            if is_filter_active and f_filter:
                                if any(word.upper() in line.upper() for word in filter_keywords):
                                    f_filter.write(line + "\n")
                            
                            # 🔥 sfm.match(line) 호출로 call_logs와 동일하게 변경된 스크린샷 필터링
                            if self.sfm.is_active:
                                for folder_name in self.sfm.match(line):
                                    current_time = time.time()
                                    
                                    if current_time - self.last_screenshot_time >= debounce_time:
                                        self.last_screenshot_time = current_time
                                        logging.info(f"📸 [{self.serial}] [{folder_name} 조건 충족 - 캡처 실행]: {line.strip()}")
                                        
                                        try:
                                            target_save_dir = os.path.join(self.folder_path, folder_name)
                                            os.makedirs(target_save_dir, exist_ok=True)
                                            
                                            func_record_image.record_screenshot(
                                                device=self.device, 
                                                save_dir=target_save_dir
                                            )
                                        except Exception as screenshot_err:
                                            logging.error(f"스크린샷 캡처 중 오류 발생: {screenshot_err}")
                                    else:
                                        remaining = debounce_time - (current_time - self.last_screenshot_time)
                                        logging.debug(f"⏳ [디바운스 중] 스크린샷 무시됨 (남은 시간: {remaining:.1f}초): {line.strip()}")

                    if is_filter_active and f_filter:
                        f_filter.flush()

                    # 100MB 용량 체크 및 파일 로테이션
                    if os.path.getsize(self.current_log_path) > 100 * 1024 * 1024:
                        f.flush()
                        break 

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
