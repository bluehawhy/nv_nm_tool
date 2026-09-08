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
    # 공통 위치 정보 저장 (내장 함수)
    # =========================================================

    def _save_location_txt(
        self,
        car_pos_path,
        loca_log=True
    ):
        """
        스크린샷 및 비디오 녹화에서 공통으로 사용되는 위치 로그 저장 내장 함수.
        log_manager의 메모리 변수(latest_car_pos)를 들고와서 location_utils.save_loca 파서를 사용해 저장합니다.
        """

        if not loca_log:
            logging.info("Location logging is disabled (loca_log=False).")
            return

        if not self.log_manager:
            logging.warning("log_manager가 전달되지 않아 위치 로그를 기록하지 못했습니다.")
            with open(car_pos_path, "w", encoding="utf-8") as f:
                f.write("car_pos: N/A (LogManager is None)")
            return

        # log_manager의 메모리 변수 추출
        latest_data = getattr(self.log_manager, 'latest_car_pos', None)

        logging.info(
            f"[CAR_POS READ] "
            f"now={datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
            f"latest={latest_data}"
        )

        if not latest_data:
            logging.warning("No location info captured in memory yet.")
            with open(car_pos_path, "w", encoding="utf-8") as f:
                f.write("car_pos: N/A (Log not detected)")
            return

        # location_utils 파서를 호출하여 위치 정보 저장
        try:
            location_utils.save_loca(latest_data[1], car_pos_path)
            logging.info(f"Successfully saved location txt via location_utils to: {car_pos_path}")
        except Exception as e:
            logging.error(f"Failed to save location info via location_utils: {e}")

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
        - 위치 정보 저장 (_save_location_txt 내장함수 사용)

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

        car_pos_file = (
            f"Screenshot_{timestamp}_location.txt"
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

        car_pos_path = os.path.join(
            local_dir,
            car_pos_file
        )

        # -----------------------------------------------------
        # 위치 정보 저장 (공통 내장 함수 호출)
        # -----------------------------------------------------

        self._save_location_txt(
            car_pos_path,
            loca_log=loca_log
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
        save_dir=None,
        loca_log=True
    ):
        """
        기본 Android 화면과 Android Auto 화면을 동시에 녹화한다.
        """

        if duration is None:
            duration = self.config['video_recording_duration']

        device_obj_serial = self.device_obj.serial

        logging.info(
            f"[*] 비디오 녹화 시작 "
            f"(기기: {device_obj_serial}, 시간: {duration}초)"
        )

        try:
            # -------------------------------------------------
            # 기존 screenrecord 프로세스 종료
            # -------------------------------------------------

            logging.info(
                f"[{device_obj_serial}] "
                "기존 screenrecord 프로세스 정리 중..."
            )

            self.device_obj.shell("pkill -9 screenrecord")
            time.sleep(0.5)

            # 녹화 시작 전에 Android Auto Display ID 확보
            display_id = self.get_display_id()

            if not display_id:
                display_id = self.find_display_id()

            # -------------------------------------------------
            # 파일 경로 생성
            # -------------------------------------------------

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            video_file = (
                f"Screen_Recording_{timestamp}.mp4"
            )

            video_file_aa = (
                f"Screen_Recording_{timestamp}_android_auto.mp4"
            )

            car_pos_file = (
                f"Screen_Recording_{timestamp}_location.txt"
            )

            res = self.device.get('resolution', 'unknown')

            if res == '1920x720':
                remote_dir = "/sdcard"
            else:
                remote_dir = "/sdcard/DCIM/Screenshots"

            remote_path = (
                f"{remote_dir}/{video_file}"
            )

            remote_path_aa = (
                f"{remote_dir}/{video_file_aa}"
            )

            local_dir = (
                save_dir
                if save_dir
                else self.config['local_path']
            )

            os.makedirs(local_dir, exist_ok=True)

            local_path = os.path.join(
                local_dir,
                video_file
            )

            local_path_aa = os.path.join(
                local_dir,
                video_file_aa
            )

            car_pos_path = os.path.join(
                local_dir,
                car_pos_file
            )

            # -------------------------------------------------
            # 위치 정보 저장
            # -------------------------------------------------

            self._save_location_txt(
                car_pos_path,
                loca_log=loca_log
            )

            recording_errors = []

            # -------------------------------------------------
            # 기본 Android 화면 녹화 함수
            # -------------------------------------------------

            def record_main_display():
                try:
                    logging.info(
                        f"[{device_obj_serial}] "
                        "기본 화면 녹화 시작"
                    )

                    result = self.device_obj.shell(
                        f"screenrecord {remote_path}"
                    )

                    if result:
                        logging.debug(
                            f"[MAIN SCREENRECORD RESULT] {result}"
                        )

                except Exception as e:
                    recording_errors.append(
                        f"기본 화면 녹화 실패: {e}"
                    )

                    logging.error(
                        f"[{device_obj_serial}] "
                        f"기본 화면 녹화 실패: {e}"
                    )

            # -------------------------------------------------
            # Android Auto 화면 녹화 함수
            # -------------------------------------------------

            def record_android_auto():
                try:
                    logging.info(
                        "[ANDROID AUTO] "
                        f"화면 녹화 시작 "
                        f"(display={display_id})"
                    )

                    result = self.device_obj.shell(
                        f"screenrecord "
                        f"--display-id {display_id} "
                        f"{remote_path_aa}"
                    )

                    result_text = result or ""

                    if (
                        "not found" in result_text.lower()
                        or "unknown option" in result_text.lower()
                        or "invalid display" in result_text.lower()
                        or "failed" in result_text.lower()
                    ):
                        raise RuntimeError(
                            result_text.strip()
                        )

                    if result_text:
                        logging.debug(
                            "[ANDROID AUTO SCREENRECORD RESULT] "
                            f"{result_text}"
                        )

                except Exception as e:
                    recording_errors.append(
                        f"Android Auto 녹화 실패: {e}"
                    )

                    logging.error(
                        "[ANDROID AUTO] "
                        f"화면 녹화 실패: {e}"
                    )

            # -------------------------------------------------
            # 두 화면 동시 녹화 시작
            # -------------------------------------------------

            main_record_thread = threading.Thread(
                target=record_main_display,
                daemon=True,
                name="MainDisplayRecorder"
            )

            main_record_thread.start()

            aa_record_thread = None

            if display_id:
                aa_record_thread = threading.Thread(
                    target=record_android_auto,
                    daemon=True,
                    name="AndroidAutoDisplayRecorder"
                )

                aa_record_thread.start()

            else:
                logging.warning(
                    "[ANDROID AUTO] "
                    "Display ID를 찾지 못해 "
                    "Android Auto 녹화를 건너뜁니다."
                )

            # -------------------------------------------------
            # 녹화 시간 대기
            # -------------------------------------------------

            time.sleep(duration)

            # -------------------------------------------------
            # 두 녹화 프로세스 종료
            # SIGINT를 보내 MP4 파일을 정상 마무리
            # -------------------------------------------------

            logging.info(
                f"[{device_obj_serial}] "
                "모든 화면 녹화 종료 중..."
            )

            self.device_obj.shell(
                "pkill -2 screenrecord"
            )

            # screenrecord shell 명령이 종료될 때까지 잠시 대기
            main_record_thread.join(timeout=5)

            if aa_record_thread:
                aa_record_thread.join(timeout=5)

            # MP4 헤더 및 인덱스 저장 대기
            time.sleep(2)

            # -------------------------------------------------
            # 기본 화면 영상 Pull
            # -------------------------------------------------

            logging.info(
                f"[{device_obj_serial}] "
                f"기본 화면 영상 Pull: {local_path}"
            )

            try:
                self.device_obj.pull(
                    remote_path,
                    local_path
                )

                if os.path.exists(local_path):
                    logging.info(
                        f"[{device_obj_serial}] "
                        f"기본 화면 영상 저장 완료: {local_path}"
                    )
                else:
                    logging.error(
                        f"[{device_obj_serial}] "
                        "기본 화면 영상 Pull 실패"
                    )

            except Exception as pull_error:
                logging.error(
                    f"[{device_obj_serial}] "
                    f"기본 화면 영상 Pull 오류: {pull_error}"
                )

            # -------------------------------------------------
            # Android Auto 영상 Pull
            # -------------------------------------------------

            if display_id:
                logging.info(
                    "[ANDROID AUTO] "
                    f"영상 Pull: {local_path_aa}"
                )

                try:
                    self.device_obj.pull(
                        remote_path_aa,
                        local_path_aa
                    )

                    if os.path.exists(local_path_aa):
                        logging.info(
                            "[ANDROID AUTO] "
                            f"영상 저장 완료: {local_path_aa}"
                        )
                    else:
                        logging.error(
                            "[ANDROID AUTO] "
                            "영상 Pull 실패"
                        )

                except Exception as pull_error:
                    logging.error(
                        "[ANDROID AUTO] "
                        f"영상 Pull 오류: {pull_error}"
                    )

            for error in recording_errors:
                logging.error(error)

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

    def record_video_old(
        self,
        duration=None,
        save_dir=None,
        loca_log=True
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
                f"Screen_Recording_{timestamp}_location.txt"
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
            # 위치 로그 저장 (공통 내장 함수 호출)
            # -------------------------------------------------

            self._save_location_txt(
                car_pos_path,
                loca_log=loca_log
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