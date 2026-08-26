import time
from datetime import datetime
import os
import threading
import re

from . import location_utils
from ..utils import configus, loggas


logging = loggas.logger


class AndroidRecordManager:
    """
    Android 화면 녹화 / 스크린샷 관리

    - 일반 Android 화면 스크린샷
    - Android Auto Virtual Display 스크린샷
    - 화면 녹화
    - 위치 로그 저장
    - Android Auto Virtual Display ID 주기적 갱신
    """

    def __init__(
        self,
        device,
        log_manager=None,
        refresh_interval=60
    ):
        self.device = device
        self.device_obj = device['ppadb_device']
        self.log_manager = log_manager

        self.config = configus.load_config(
            'resources/configs/config.json'
        )

        # Android Auto Virtual Display ID
        self.display_id = None

        # Display ID 갱신 주기
        self.refresh_interval = refresh_interval

        # Thread 제어
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    # =========================================================
    # Android Auto Display 관리
    # =========================================================

    def find_display_id(self):
        """
        SurfaceFlinger에서 Android Auto 메인 Virtual Display ID 검색

        target:
            name="com.google.android.projection.gearhead/jqp"
        """

        try:
            start = time.perf_counter()

            output = self.device_obj.shell(
                "dumpsys SurfaceFlinger --displays"
            )

            elapsed = time.perf_counter() - start

            # Virtual Display 단위로 분리
            displays = re.split(
                r'(?=Virtual Display\s+\d+)',
                output
            )

            new_display_id = None

            for display in displays:

                # Android Auto 실제 Projection Display 확인
                if (
                    'name="com.google.android.projection.gearhead/'
                    in display
                ):

                    match = re.search(
                        r'Virtual Display\s+(\d+)',
                        display
                    )

                    if match:

                        new_display_id = match.group(1)
                        break

            if not new_display_id:

                logging.debug(
                    "[ANDROID AUTO] "
                    f"Virtual Display not found "
                    f"(search={elapsed:.3f}s)"
                )

                with self._lock:
                    self.display_id = None

                return None

            with self._lock:

                old_display_id = self.display_id
                self.display_id = new_display_id

            if old_display_id != new_display_id:

                logging.info(
                    "[ANDROID AUTO] Display ID updated: "
                    f"{old_display_id} -> {new_display_id} "
                    f"(search={elapsed:.3f}s)"
                )

            return new_display_id

        except Exception as e:

            logging.error(
                f"[ANDROID AUTO] "
                f"Display search failed: {e}"
            )

            return None

    def get_display_id(self):
        """
        현재 캐싱된 Android Auto Display ID 반환
        """

        with self._lock:
            return self.display_id

    def _refresh_loop(self):
        """
        refresh_interval 주기로 Android Auto Display ID 검색
        """

        logging.info(
            "[ANDROID AUTO] "
            f"Display monitor started "
            f"(interval={self.refresh_interval}s)"
        )

        while not self._stop_event.is_set():

            self.find_display_id()

            self._stop_event.wait(
                self.refresh_interval
            )

        logging.info(
            "[ANDROID AUTO] "
            "Display monitor stopped"
        )

    def start(self):
        """
        Android Auto Display ID 감시 시작

        시작 즉시 한 번 검색하고,
        이후 refresh_interval 주기로 갱신
        """

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()

        # 시작 즉시 Display 검색
        self.find_display_id()

        self._thread = threading.Thread(
            target=self._refresh_loop,
            daemon=True,
            name="AndroidAutoDisplayMonitor"
        )

        self._thread.start()

    def stop(self):
        """
        Android Auto Display ID 감시 종료
        """

        self._stop_event.set()

        if self._thread and self._thread.is_alive():

            self._thread.join(
                timeout=2
            )

        self._thread = None

    # =========================================================
    # 위치 정보 저장
    # =========================================================

    def _save_screenshot_location(
        self,
        timestamp,
        local_dir,
        loca_log
    ):
        """
        현재 최신 차량 위치 로그 저장
        """

        if not loca_log:

            logging.info(
                "Location logging is disabled "
                "(loca_log=False)."
            )

            return

        car_pos_file = (
            f"Screenshot_{timestamp}_location.txt"
        )

        car_pos_path = os.path.join(
            local_dir,
            car_pos_file
        )

        if not self.log_manager:

            logging.warning(
                "log_manager가 전달되지 않아 "
                "위치 로그를 기록하지 못했습니다."
            )

            with open(
                car_pos_path,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    "car_pos: N/A "
                    "(LogManager is None)"
                )

            return

        latest_data = getattr(
            self.log_manager,
            'latest_car_pos',
            None
        )

        logging.info(
            f"[CAR_POS READ] "
            f"now="
            f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
            f"latest={latest_data}"
        )

        if not latest_data:

            logging.warning(
                "No location info captured yet."
            )

            with open(
                car_pos_path,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    "car_pos: N/A "
                    "(Log not detected)"
                )

            return

        # -----------------------------------------------------
        # tuple 구조
        # (pc_time, log_line)
        # -----------------------------------------------------

        if isinstance(latest_data, tuple):

            pc_time, log_line = latest_data

            logging.info(
                f"Location info captured "
                f"(Recv: {pc_time}): "
                f"{log_line.strip()}"
            )

            with open(
                car_pos_path,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    f"[PC Recv Time]: "
                    f"{pc_time}\n"
                )

                f.write(
                    f"[Log Raw Line]: "
                    f"{log_line}\n"
                )

        # -----------------------------------------------------
        # 기존 문자열 구조
        # -----------------------------------------------------

        else:

            logging.info(
                f"Location info captured: "
                f"{latest_data.strip()}"
            )

            with open(
                car_pos_path,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    latest_data + "\n"
                )

    # =========================================================
    # Android Auto Screenshot
    # =========================================================

    def _capture_android_auto(
        self,
        screenshot_path
    ):
        """
        Android Auto Virtual Display 캡처

        현재 저장된 display_id 사용.

        실패하면:
        1. Display ID 재검색
        2. 새 ID로 한 번 재시도
        """

        display_id = self.get_display_id()

        if not display_id:

            logging.debug(
                "[ANDROID AUTO] "
                "Display ID is None. "
                "Skipping Android Auto screenshot."
            )

            return False

        try:

            self._capture_android_auto_once(
                display_id,
                screenshot_path
            )

            return True

        except Exception as first_error:

            logging.warning(
                "[ANDROID AUTO] "
                f"Capture failed "
                f"(display={display_id}): "
                f"{first_error}"
            )

            # 실패 시 즉시 재검색
            new_display_id = self.find_display_id()

            if not new_display_id:

                logging.warning(
                    "[ANDROID AUTO] "
                    "Display ID re-search failed."
                )

                return False

            logging.info(
                "[ANDROID AUTO] "
                f"Retry capture "
                f"(display={new_display_id})"
            )

            try:

                self._capture_android_auto_once(
                    new_display_id,
                    screenshot_path
                )

                return True

            except Exception as retry_error:

                logging.error(
                    "[ANDROID AUTO] "
                    f"Retry capture failed: "
                    f"{retry_error}"
                )

                return False

    def _capture_android_auto_once(
        self,
        display_id,
        screenshot_path
    ):
        """
        Android Auto Virtual Display 1회 캡처
        """

        remote_path = (
            "/sdcard/auto_screenshot.png"
        )

        start = time.perf_counter()

        result = self.device_obj.shell(
            f"screencap -d "
            f"{display_id} "
            f"{remote_path}"
        )

        error_text = result or ""

        if (
            "Failed to take" in error_text
            or "not valid" in error_text
            or "Capturing failed" in error_text
        ):

            raise RuntimeError(
                error_text.strip()
            )

        self.device_obj.pull(
            remote_path,
            screenshot_path
        )

        if not os.path.exists(
            screenshot_path
        ):

            raise RuntimeError(
                "Android Auto screenshot "
                "pull failed."
            )

        elapsed = (
            time.perf_counter() - start
        )

        logging.info(
            "[ANDROID AUTO SCREENCAP DONE] "
            f"display={display_id}, "
            f"elapsed={elapsed:.3f}s"
        )

    # =========================================================
    # Screenshot
    # =========================================================
    def record_screenshot(
        self,
        loca_log=True,
        save_dir=None
    ):
        """
        스크린샷 캡처

        항상:
        - 기본 Android 화면 캡처
        - 위치 정보 저장

        Android Auto Display ID가 존재하면:
        - Android Auto 화면도 추가 캡처
        """

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        screenshot_file = (
            f"Screenshot_{timestamp}.png"
        )

        screenshot_file_aa = (
            f"Screenshot_{timestamp}_android_auto.png"
        )

        local_dir = (
            save_dir
            if save_dir
            else self.config['local_path']
        )

        screenshot_path = os.path.join(
            local_dir,
            screenshot_file
        )

        screenshot_path_aa = os.path.join(
            local_dir,
            screenshot_file_aa
        )

        # -----------------------------------------------------
        # 위치 정보 저장
        # -----------------------------------------------------

        self._save_screenshot_location(
            timestamp,
            local_dir,
            loca_log
        )

        # -----------------------------------------------------
        # 기본 Android 화면 캡처
        # -----------------------------------------------------

        logging.info(
            f"Starting screenshot: "
            f"{screenshot_file}"
        )

        try:

            start = time.perf_counter()

            result = self.device_obj.screencap()

            with open(
                screenshot_path,
                "wb"
            ) as f:

                f.write(result)

            elapsed = (
                time.perf_counter() - start
            )

            logging.info(
                "[SCREENCAP DONE] "
                f"elapsed={elapsed:.3f}s"
            )

            logging.info(
                f"Screenshot saved successfully: "
                f"{screenshot_path}"
            )

        except Exception as e:

            logging.error(
                f"Failed to take screenshot: {e}"
            )

        # -----------------------------------------------------
        # Android Auto 화면 캡처
        # -----------------------------------------------------

        display_id = self.get_display_id()

        if display_id:

            logging.info(
                "[ANDROID AUTO] "
                f"Starting screenshot "
                f"(display={display_id})"
            )

            self._capture_android_auto(
                screenshot_path_aa
            )

        else:

            logging.debug(
                "[ANDROID AUTO] "
                "Display ID is None. "
                "Skipping Android Auto screenshot."
            )

        return screenshot_path

    # =========================================================
    # Video Recording
    # =========================================================

    def record_video(
        self,
        duration=None,
        save_dir=None
    ):
        """
        비디오 녹화 및 위치 정보 캡처 수행
        """

        if duration is None:

            duration = self.config[
                'video_recording_duration'
            ]

        device_obj_serial = (
            self.device_obj.serial
        )

        logging.info(
            f"[*] 비디오 녹화 시작 "
            f"(기기: {device_obj_serial}, "
            f"시간: {duration}초)"
        )

        try:

            # -------------------------------------------------
            # 기존 screenrecord 종료
            # -------------------------------------------------

            logging.info(
                f"[{device_obj_serial}] "
                "기존 screenrecord 프로세스 정리 중..."
            )

            self.device_obj.shell(
                "pkill -9 screenrecord"
            )

            time.sleep(0.5)

            # -------------------------------------------------
            # 파일 이름
            # -------------------------------------------------

            timestamp = datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )

            video_file = (
                f"Screen_Recording_{timestamp}.mp4"
            )

            car_pos_file = (
                f"Screen_Recording_{timestamp}"
                f"_location.txt"
            )

            res = self.device.get(
                'resolution',
                'unknown'
            )

            remote_path = (
                f"/sdcard/{video_file}"
                if res == '1920x720'
                else (
                    f"/sdcard/DCIM/Screenshots/"
                    f"{video_file}"
                )
            )

            local_dir = (
                save_dir
                if save_dir
                else self.config['local_path']
            )

            local_path = os.path.join(
                local_dir,
                video_file
            )

            car_pos_path = os.path.join(
                local_dir,
                car_pos_file
            )

            # -------------------------------------------------
            # 위치 로그 수집
            # -------------------------------------------------

            if self.log_manager:

                tmp_log_file_path = (
                    "resources/info/"
                    "loca_info_video.txt"
                )

                if os.path.exists(
                    tmp_log_file_path
                ):

                    try:
                        os.remove(
                            tmp_log_file_path
                        )
                    except Exception:
                        pass

                results = {}

                loca_stop_signal = (
                    self.log_manager.fetch_log_from_list(
                        search_patterns={
                            'car_pos': ".*win 0 SFN.*"
                        },
                        file_path=tmp_log_file_path,
                        result_dict=results,
                        timeout_seconds=2
                    )
                )

                loca_stop_signal.wait(
                    timeout=2
                )

                if 'car_pos' in results:

                    logging.info(
                        f"[{device_obj_serial}] "
                        f"Location info found: "
                        f"{results['car_pos']}"
                    )

                    location_utils.save_loca(
                        results['car_pos'],
                        car_pos_path
                    )

                else:

                    logging.warning(
                        f"[{device_obj_serial}] "
                        "No location info captured "
                        "in 2 seconds."
                    )

                    with open(
                        car_pos_path,
                        "w",
                        encoding="utf-8"
                    ) as f:

                        f.write(
                            "car_pos: N/A "
                            "(Log not detected)"
                        )

                loca_stop_signal.set()

            else:

                logging.warning(
                    f"[{device_obj_serial}] "
                    "log_manager가 제공되지 않아 "
                    "위치 정보를 수집하지 않습니다."
                )

            # -------------------------------------------------
            # 녹화 시작
            # -------------------------------------------------

            logging.info(
                f"[{device_obj_serial}] "
                f"Recording Start: "
                f"{video_file}"
            )

            def start_recording():

                self.device_obj.shell(
                    f"screenrecord {remote_path}"
                )

            record_thread = threading.Thread(
                target=start_recording,
                daemon=True
            )

            record_thread.start()

            # -------------------------------------------------
            # 녹화 시간 대기
            # -------------------------------------------------

            time.sleep(duration)

            # -------------------------------------------------
            # 녹화 종료
            # -------------------------------------------------

            logging.info(
                f"[{device_obj_serial}] "
                "Stopping recording..."
            )

            self.device_obj.shell(
                "pkill -2 screenrecord"
            )

            time.sleep(3)

            # -------------------------------------------------
            # 파일 Pull
            # -------------------------------------------------

            logging.info(
                f"[{device_obj_serial}] "
                f"Pulling video file to: "
                f"{local_path}"
            )

            try:

                self.device_obj.pull(
                    remote_path,
                    local_path
                )

                if os.path.exists(
                    local_path
                ):

                    logging.info(
                        f"[{device_obj_serial}] "
                        "Video pull successful: "
                        f"{local_path}"
                    )

                    print(
                        f"[Done] copy video: "
                        f"{local_path}"
                    )

                else:

                    logging.error(
                        f"[{device_obj_serial}] "
                        "Pull failed: "
                        f"File not found at "
                        f"{local_path}"
                    )

            except Exception as pull_error:

                logging.error(
                    f"[{device_obj_serial}] "
                    f"Pull Error during transfer: "
                    f"{pull_error}"
                )

        except Exception as e:

            logging.error(
                f"[{device_obj_serial}] "
                f"비디오 태스크 에러: {e}"
            )

        finally:

            logging.info(
                f"[{device_obj_serial}] "
                "비디오 작업 완료"
            )