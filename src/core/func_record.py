import time
from datetime import datetime
import os
import threading
import re
import shutil
import math
import struct
import av
import numpy as np
from fractions import Fraction

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
        self.refresh_interval = float(refresh_interval)
        if not math.isfinite(self.refresh_interval) or self.refresh_interval <= 0:
            raise ValueError("refresh_interval은 유한한 양수여야 합니다.")

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

        while not self._stop_event.wait(self.refresh_interval):
            self.find_display_id()

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

        if self._thread and not self._thread.is_alive():
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

    @staticmethod
    def _check_capture_result(result):
        text = (result or "").strip()
        if any(word in text.lower() for word in (
            "failed", "invalid", "not valid", "permission denied",
            "no such file", "no space left", "error",
        )):
            raise RuntimeError(text)

    def _capture_android_auto_once(
        self,
        display_id,
        screenshot_path
    ):
        """
        Android Auto Virtual Display 1회 캡처
        """

        remote_path = (
            f"/sdcard/auto_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        )

        start = time.perf_counter()

        result = self.device_obj.shell(
            f"screencap -d "
            f"{display_id} "
            f"{remote_path}"
        )

        self._check_capture_result(result)

        self.device_obj.pull(
            remote_path,
            screenshot_path
        )

        if not os.path.isfile(screenshot_path) or os.path.getsize(screenshot_path) == 0:

            raise RuntimeError(
                "Android Auto screenshot "
                "pull failed."
            )

        self.device_obj.shell(f"rm -f {remote_path}")

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

    def record_screenshot(self,loca_log=True,save_dir=None):
        """
        스크린샷 캡처

        항상:
        - 기본 Android 화면 캡처
        - 위치 정보 저장 (_save_location_txt 내장함수 사용)

        Android Auto Display ID가 존재하면:
        - Android Auto 화면도 추가 캡처
        """

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        logging.info(timestamp)

        screenshot_file = (f"Screenshot_{timestamp}.png")
        screenshot_file_aa = (f"Screenshot_{timestamp}_android_auto.png")
        car_pos_file = (f"Screenshot_{timestamp}_location.txt")
        local_dir = (save_dir if save_dir else self.config['local_path'])
        os.makedirs(local_dir, exist_ok=True)
        screenshot_path = os.path.join(local_dir,screenshot_file)
        screenshot_path_aa = os.path.join(local_dir,screenshot_file_aa)
        car_pos_path = os.path.join(local_dir,car_pos_file)

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

        logging.info(f"Starting screenshot: "f"{screenshot_file}")

        try:

            start = time.perf_counter()

            result = self.device_obj.screencap()

            with open(screenshot_path,"wb") as f:
                f.write(result)

            elapsed = (time.perf_counter() - start)

            logging.info("[SCREENCAP DONE] "f"elapsed={elapsed:.3f}s")

            logging.info(f"Screenshot saved successfully: "f"{screenshot_path}")

        except Exception as e:

            logging.error(f"Failed to take screenshot: {e}")

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
    def _read_android_auto_raw(self, file_path, validate_only=False):
        with open(file_path, "rb") as file:
            data = file.read()

        if len(data) < 12:
            raise RuntimeError(f"RAW 헤더가 불완전합니다: {file_path}")

        width, height, pixel_format = struct.unpack_from("<III", data)

        if width <= 0 or height <= 0:
            raise RuntimeError(
                f"잘못된 RAW 해상도: {width}x{height}"
            )

        # Android 픽셀 형식:
        # 1: RGBA_8888, 2: RGBX_8888
        # 3: RGB_888,   5: BGRA_8888
        channels = {
            1: 4,
            2: 4,
            3: 3,
            5: 4,
        }.get(pixel_format)

        if channels is None:
            raise RuntimeError(
                f"지원하지 않는 RAW 픽셀 형식: {pixel_format}"
            )

        pixel_bytes = width * height * channels
        header_size = len(data) - pixel_bytes

        if header_size not in (12, 16):
            raise RuntimeError(
                "RAW 파일 크기가 예상과 다릅니다. "
                f"size={len(data)}, "
                f"resolution={width}x{height}, "
                f"format={pixel_format}, "
                f"header={header_size}"
            )

        if validate_only:
            return

        pixels = np.frombuffer(
            data,
            dtype=np.uint8,
            count=pixel_bytes,
            offset=header_size,
        ).reshape(height, width, channels)

        if pixel_format == 5:
            # BGRA → RGB
            rgb = pixels[:, :, [2, 1, 0]]
        else:
            # RGBA / RGBX / RGB → RGB
            rgb = pixels[:, :, :3]

        return np.ascontiguousarray(rgb)


    def _capture_android_auto_video_frames(
        self,
        display_id,
        duration,
        target_fps,
        remote_frame_dir,
        result_data
    ):
        """
        duration 동안 Android Auto Virtual Display를 기기 내부 RAW로 저장한다.

        Pull은 촬영이 모두 끝난 뒤 record_video()에서 수행한다.
        """

        frame_interval = 1.0 / target_fps
        started_at = time.perf_counter()
        captured_frames = []

        try:
            self.device_obj.shell(
                f"mkdir -p {remote_frame_dir}"
            )

            while time.perf_counter() - started_at < duration:
                frame_started_at = time.perf_counter()
                frame_index = len(captured_frames)
                frame_name = f"frame_{frame_index:06d}.raw"
                remote_path = f"{remote_frame_dir}/{frame_name}"
                result = self.device_obj.shell(
                    f"screencap -d "
                    f"{display_id} "
                    f"{remote_path}"
                )
                self._check_capture_result(result)

                captured_at = time.perf_counter() - started_at
                captured_frames.append(
                    {
                        "name": frame_name,
                        "remote_path": remote_path,
                        "captured_at": captured_at,
                    }
                )

                logging.debug(
                    "[ANDROID AUTO VIDEO FRAME] "
                    f"count={len(captured_frames)}, "
                    f"elapsed={captured_at:.3f}s"
                )

                wait_time = frame_interval - (
                    time.perf_counter() - frame_started_at
                )
                if wait_time > 0:
                    time.sleep(wait_time)

            result_data["frames"] = captured_frames
            result_data["elapsed"] = time.perf_counter() - started_at

        except Exception as e:
            result_data["error"] = e
            result_data["frames"] = captured_frames
            result_data["elapsed"] = time.perf_counter() - started_at
            logging.error(
                "[ANDROID AUTO VIDEO] "
                f"프레임 캡처 실패: {e}"
            )

    def _pull_android_auto_video_frames(
        self,
        frames,
        local_frame_dir
    ):
        """촬영이 끝난 Android Auto 프레임을 PC로 복사한다."""

        os.makedirs(local_frame_dir, exist_ok=True)

        for frame_info in frames:
            local_path = os.path.join(
                local_frame_dir,
                frame_info["name"]
            )

            self.device_obj.pull(
                frame_info["remote_path"],
                local_path
            )

            if not os.path.exists(local_path):
                raise RuntimeError(
                    "Android Auto 프레임 Pull 실패: "
                    f"{frame_info['name']}"
                )

            # 기기 원본을 삭제하기 전에 RAW 구조와 데이터 크기를 검증한다.
            self._read_android_auto_raw(local_path, validate_only=True)

            frame_info["local_path"] = local_path


    def _create_android_auto_video(
        self,
        frames,
        output_path,
        duration,
        output_fps=10
    ):
        """PyAV를 사용해 Android Auto RAW 프레임을 MP4로 만든다."""


        if not frames:
            raise RuntimeError("영상으로 만들 Android Auto 프레임이 없습니다.")

        if not all(math.isfinite(v) and v > 0 for v in (duration, output_fps)):
            raise ValueError("duration과 output_fps는 유한한 양수여야 합니다.")

        rate = Fraction(str(output_fps)).limit_denominator(1000)
        output_frame_count = max(1, round(duration * output_fps))


        first_array = self._read_android_auto_raw(
            frames[0]["local_path"]
        )
        source_height, source_width = first_array.shape[:2]

        del first_array

        # H.264 yuv420p는 가로/세로 크기가 짝수여야 한다.
        video_width = source_width - (source_width % 2)
        video_height = source_height - (source_height % 2)

        container = av.open(output_path, mode="w")
        try:
            stream = container.add_stream("libx264", rate=rate)
            stream.width = video_width
            stream.height = video_height
            stream.pix_fmt = "yuv420p"
            stream.options = {
                "preset": "veryfast",
                "crf": "23",
            }

            source_index = 0
            previous_source_index = None
            previous_array = None

            for output_index in range(output_frame_count):
                frame_time = output_index / output_fps

                while (
                    source_index + 1 < len(frames)
                    and frames[source_index + 1]["captured_at"] <= frame_time
                ):
                    source_index += 1

                if source_index != previous_source_index:
                    rgb_array = self._read_android_auto_raw(
                        frames[source_index]["local_path"]
                    )

                    if rgb_array.shape[:2] != (source_height, source_width):
                        raise RuntimeError(
                            "촬영 도중 Android Auto 해상도가 변경됐습니다."
                        )

                    # 기존 H.264 짝수 해상도 처리 유지
                    previous_array = np.ascontiguousarray(
                        rgb_array[:video_height, :video_width, :]
                    )

                    previous_source_index = source_index

                video_frame = av.VideoFrame.from_ndarray(
                    previous_array,
                    format="rgb24"
                )
                video_frame.pts = output_index
                video_frame.time_base = Fraction(1, 1) / rate

                for packet in stream.encode(video_frame):
                    container.mux(packet)

            for packet in stream.encode():
                container.mux(packet)

        finally:
            container.close()

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("Android Auto 영상 생성에 실패했습니다.")

    def record_video(self,duration=None,save_dir=None,loca_log=True,android_auto_fps=10):
        """
        기본 Android 화면과 Android Auto 화면을 동시에 촬영한다.

        - 기본 화면: screenrecord
        - Android Auto: duration 동안 screencap -d 반복
        - 촬영 종료 후 Android Auto 프레임 Pull 및 PyAV MP4 생성
        """

        if duration is None:
            duration = self.config['video_recording_duration']

        duration = float(duration)
        android_auto_fps = float(android_auto_fps)
        if not all(math.isfinite(v) and v > 0 for v in (duration, android_auto_fps)):
            raise ValueError("duration과 android_auto_fps는 유한한 양수여야 합니다.")

        device_obj_serial = self.device_obj.serial
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")       # 최종 파일명
        temp_timestamp = now.strftime("%Y%m%d_%H%M%S_%f")  # 임시 폴더
        local_dir = save_dir if save_dir else self.config['local_path']
        os.makedirs(local_dir, exist_ok=True)

        video_file = f"Screen_Recording_{timestamp}.mp4"
        aa_video_file = f"Screen_Recording_{timestamp}_android_auto.mp4"
        car_pos_file = f"Screen_Recording_{timestamp}_location.txt"

        res = self.device.get('resolution', 'unknown')
        remote_video_dir = ("/sdcard"if res == '1920x720' else "/sdcard/DCIM/Screenshots")

        remote_video_path = f"{remote_video_dir}/{video_file}"
        remote_frame_dir = f"/sdcard/aa_video_frames_{temp_timestamp}"
        local_video_path = os.path.join(local_dir, video_file)
        local_aa_video_path = os.path.join(local_dir, aa_video_file)
        car_pos_path = os.path.join(local_dir, car_pos_file)

        # 저장폴더/_aa_temp/날짜시간/ 안에 RAW 저장
        aa_temp_dir = os.path.join(local_dir, "_aa_temp")

        local_temp_root = os.path.join(
            aa_temp_dir,
            temp_timestamp
        )
        local_frame_dir = local_temp_root

        display_id = self.get_display_id()
        if not display_id:
            display_id = self.find_display_id()

        aa_result = {
            "frames": [],
            "elapsed": 0.0,
            "error": None,
        }
        main_record_error = []
        aa_video_created = False
        remote_frames_pulled = False

        logging.info(
            f"[*] 비디오 녹화 시작 "
            f"(기기: {device_obj_serial}, 시간: {duration}초)"
        )

        try:
            self.device_obj.shell(f"mkdir -p {remote_video_dir}")
            self.device_obj.shell("pkill -2 screenrecord")
            time.sleep(0.5)

            self._save_location_txt(
                car_pos_path,
                loca_log=loca_log
            )

            def record_main_display():
                try:
                    self.device_obj.shell(
                        f"screenrecord {remote_video_path}"
                    )
                except Exception as e:
                    main_record_error.append(e)
                    logging.error(
                        f"[{device_obj_serial}] "
                        f"기본 화면 녹화 실패: {e}"
                    )

            main_thread = threading.Thread(
                target=record_main_display,
                daemon=True,
                name="MainDisplayRecorder"
            )
            main_thread.start()

            aa_thread = None
            if display_id:
                aa_thread = threading.Thread(
                    target=self._capture_android_auto_video_frames,
                    args=(
                        display_id,
                        duration,
                        android_auto_fps,
                        remote_frame_dir,
                        aa_result,
                    ),
                    daemon=True,
                    name="AndroidAutoFrameRecorder"
                )
                aa_thread.start()
            else:
                logging.warning(
                    "[ANDROID AUTO VIDEO] "
                    "Display ID가 없어 촬영을 건너뜁니다."
                )

            # 두 촬영은 각 스레드에서 동시에 진행된다.
            time.sleep(duration)

            logging.info(
                f"[{device_obj_serial}] 기본 화면 녹화 종료 중..."
            )
            self.device_obj.shell("pkill -2 screenrecord")
            main_thread.join(timeout=5)

            if aa_thread:
                # 진행 중인 마지막 screencap이 끝날 때까지 기다린다.
                aa_thread.join(timeout=10)
                if aa_thread.is_alive():
                    raise RuntimeError(
                        "Android Auto 프레임 촬영 스레드가 종료되지 않았습니다."
                    )

            time.sleep(2)

            # 기본 영상 오류가 Android Auto 후처리를 막지 않도록 분리한다.
            try:
                if main_thread.is_alive():
                    raise RuntimeError("기본 화면 녹화가 아직 종료되지 않았습니다.")
                if main_record_error:
                    raise RuntimeError(str(main_record_error[0]))
                self.device_obj.pull(remote_video_path, local_video_path)
                if not os.path.isfile(local_video_path) or os.path.getsize(local_video_path) == 0:
                    raise RuntimeError("기본 화면 영상 Pull에 실패했습니다.")
                logging.info(f"기본 화면 영상 저장 완료: {local_video_path}")
            except Exception as error:
                logging.error(f"기본 화면 영상 처리 실패: {error}")

            # Android Auto 프레임은 촬영이 모두 끝난 뒤 Pull한다.
            if display_id:
                if aa_result["error"]:
                    raise RuntimeError(
                        f"Android Auto 프레임 촬영 실패: {aa_result['error']}"
                    )

                frames = aa_result["frames"]
                if not frames:
                    raise RuntimeError("Android Auto 캡처 프레임이 없습니다.")

                self._pull_android_auto_video_frames(
                    frames,
                    local_frame_dir
                )
                remote_frames_pulled = True

                # PC 복사가 끝난 후 기기 내부 RAW를 삭제한다.
                cleanup_result = self.device_obj.shell(
                    f"rm -rf {remote_frame_dir}"
                )
                if cleanup_result and cleanup_result.strip():
                    logging.warning(f"기기 프레임 삭제 확인 필요: {cleanup_result}")
                logging.info(
                    "[ANDROID AUTO VIDEO] 기기 내부 프레임 삭제 완료"
                )

                self._create_android_auto_video(
                    frames,
                    local_aa_video_path,
                    duration,
                    output_fps=android_auto_fps
                )
                aa_video_created = True

                logging.info(
                    "[ANDROID AUTO VIDEO] "
                    f"영상 생성 완료: {local_aa_video_path} "
                    f"(captured={len(frames)}, "
                    f"elapsed={aa_result['elapsed']:.3f}s, "
                    f"output={android_auto_fps}fps)"
                )


        except Exception as e:
            logging.error(
                f"[{device_obj_serial}] 비디오 태스크 에러: {e}"
            )

        finally:
            # 영상 생성 성공 후에만 PC 임시 RAW를 삭제한다.
            if aa_video_created:
                # 영상 생성 성공 후 현재 작업 폴더만 삭제.
                # _aa_temp 폴더 자체는 유지한다.
                try:
                    shutil.rmtree(local_temp_root)

                    logging.info(
                        "[ANDROID AUTO VIDEO] "
                        f"현재 작업 임시 폴더 삭제 완료: {local_temp_root}"
                    )

                except OSError as error:
                    logging.warning(
                        "[ANDROID AUTO VIDEO] "
                        f"임시 폴더 삭제 실패: {error}"
                    )
            elif display_id:
                logging.warning(
                    "[ANDROID AUTO VIDEO] "
                    f"PC 임시 프레임 보존: {local_temp_root}"
                )
                if display_id and not remote_frames_pulled:
                    logging.warning(
                        "[ANDROID AUTO VIDEO] "
                        f"기기 프레임 보존: {remote_frame_dir}"
                    )

            logging.info(
                f"[{device_obj_serial}] 비디오 작업 완료"
            )
