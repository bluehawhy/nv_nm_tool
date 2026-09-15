import os
import json
import logging as std_logging
import asyncio
import time
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

# pymobiledevice3 관련 모듈
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.services.crash_reports import CrashReportsManager
from pymobiledevice3.services.house_arrest import HouseArrestService
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.screenshot import ScreenshotService
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.screenshot import (
    Screenshot as DvtScreenshot,
)
from pymobiledevice3.remote.core_device.device_info import DeviceInfoService
from pymobiledevice3.remote.core_device.screen_capture_service import (
    ScreenCaptureService,
)
from pymobiledevice3.tunneld.api import (
    get_tunneld_device_by_udid,
    get_tunneld_devices,
)

try:
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    USERSPACE_TUNNEL_IMPORT_ERROR = None
except Exception as e:
    # PyInstaller 빌드에서 wintun.dll이 누락돼도 앱 전체가 종료되지 않게 합니다.
    UserspaceRsdTunnel = None
    USERSPACE_TUNNEL_IMPORT_ERROR = e

from src.utils import loggas, configus

logging = loggas.logger

IOS_MEDIA_EXTENSIONS = {
    # 사진
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".dng",
    ".gif",
    ".tif",
    ".tiff",
    ".webp",
    # 영상
    ".mov",
    ".mp4",
    ".m4v",
    ".avi",
    ".3gp",
}


class IOSDeviceController:
    """
    미리 생성된 lockdown_device(PlistUsbmuxLockdownClient)를 주입받아
    iOS 기기의 앱 정보 검색, 크래시 로그 수집, 앱 샌드박스 파일 다운로드,
    사진 다운로드, 스크린샷 기능을 수행하는 클래스입니다.
    """
    def __init__(self, device, folder_path=None):
        if not isinstance(device, dict):
            raise ValueError("유효한 iOS 장치 딕셔너리가 필요합니다.")

        lockdown_device = device.get("lockdown_device")

        if lockdown_device is None:
            raise ValueError(
                "장치 정보에 lockdown_device가 없습니다."
            )

        self.device = device
        self.lockdown = lockdown_device

        if folder_path is None:
            try:
                self.config = configus.load_config("resources/configs/config.json")
            except Exception as e:
                logging.warning(f"iOS 설정 파일 로드 실패: {e}")

                self.config = {"local_path": str(Path.home() / "Desktop" / "NKM_Tool")}

            folder_path = self.config.get("local_path",str(Path.home() / "Desktop" / "NKM_Tool"))
        else:
            # 외부에서 저장 경로를 직접 전달한 경우
            self.config["local_path"] = str(folder_path)

        # 기본 저장 경로
        self.base_dir = Path(folder_path)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # 로그 저장 경로
        self.log_dir = self.base_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # iPhone 연결 시 CarPlay 디스플레이 ID를 한 번만 조회해 캐시합니다.
        # 연결 해제 시 IOSDeviceController 자체가 폐기되므로 별도 초기화는 필요 없습니다.
        self.carplay_unique_id = None
        if self._get_device_display_name() == "iPhone":
            try:
                asyncio.run(self._load_carplay_display_unique_id_async())
            except Exception as e:
                logging.debug(
                    f"CarPlay 디스플레이 초기 조회 실패: {e}",
                    exc_info=True,
                )


    async def _pull_recursive(self, afc, remote_path, local_base_path):
        """AFC 경로를 재귀적으로 다운로드하고 저장한 파일 수를 반환합니다."""
        item_name = remote_path.rstrip("/").rsplit("/", 1)[-1]

        try:
            if await afc.isdir(remote_path):
                new_local_dir = local_base_path / item_name
                new_local_dir.mkdir(parents=True, exist_ok=True)

                file_count = 0
                for child in await afc.listdir(remote_path):
                    if child in (".", ".."):
                        continue
                    file_count += await self._pull_recursive(
                        afc,
                        f"{remote_path}/{child}",
                        new_local_dir,
                    )
                return file_count

            local_file_path = local_base_path / item_name
            print(f"파일 다운로드 중: {remote_path}")
            await afc.pull(
                remote_path,
                str(local_file_path),
                progress_bar=False,
            )
            return 1

        except Exception as e:
            logging.warning(f"로그 경로 다운로드 실패: {remote_path} ({e})")
            return 0


    def get_apps(self, bundle_id=None):
        """app 미설치 시 전체 어플 리스트 반환 / bundle_id가 있으면 해당 app 정보만 반환"""
        service = InstallationProxyService(self.lockdown)
        apps = service.get_apps(application_type="Any")
        
        if bundle_id is None:
            return apps
        
        try:
            return apps[bundle_id]
        except KeyError:
            print(f"❌ 해당 번들 ID({bundle_id})를 찾을 수 없습니다.")
            return list(apps.keys())

    def get_crash_logs(self, set_date_str):
        """특정 일자(YYYY-MM-DD)의 crash log(.ips)만 저장합니다."""
        final_dir = self.base_dir / "IOS" / set_date_str / "crash_logs"
        final_dir.mkdir(parents=True, exist_ok=True)

        print(f"[{set_date_str}] 크래시 로그 추출을 시작합니다.")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            try:
                asyncio.run(self._pull_crash_logs_async(temp_path))
            except Exception as e:
                print(f"크래시 로그 추출 중 오류 발생: {e}")
                return

            count = 0
            for file_path in temp_path.rglob("*.ips"):
                if set_date_str not in file_path.name:
                    continue

                destination = final_dir / file_path.name
                if destination.exists():
                    destination.unlink()
                shutil.move(str(file_path), str(destination))
                count += 1

            print(
                f"✅ 추출 완료! 총 {count}개의 "
                f"'{set_date_str}' 로그를 저장했습니다."
            )
            print(f"📂 위치: {final_dir}")


    async def _pull_crash_logs_async(self, temp_path):
        """현재 이벤트 루프에서 iOS 세션을 열어 crash report를 수집합니다."""
        serial = self.device.get("serial") or getattr(
            self.lockdown,
            "identifier",
            None,
        )

        async with await create_using_usbmux(serial=serial) as lockdown:
            async with CrashReportsManager(lockdown) as crash_manager:
                print("장치에서 전체 로그 데이터를 수집하는 중...")

                # pull() 중 발생하는 파일별 INFO와 무시 가능한 AFC WARNING을 숨깁니다.
                crash_previous_level = crash_manager.logger.level
                afc_previous_level = crash_manager.afc.logger.level
                crash_manager.logger.setLevel(std_logging.WARNING)
                crash_manager.afc.logger.setLevel(std_logging.ERROR)
                try:
                    await crash_manager.pull(
                        str(temp_path),
                        progress_bar=False,
                    )
                finally:
                    crash_manager.logger.setLevel(crash_previous_level)
                    crash_manager.afc.logger.setLevel(afc_previous_level)


    def download_filtered_logs(
        self,
        set_date_str,
        bundle_id="hmi.navis.NMaps",
    ):
        """특정일(YYYY-MM-DD)의 bundle_id 로그만 다운로드합니다."""
        local_root = self.base_dir / "IOS" / set_date_str / "ios_app_logs"
        local_root.mkdir(parents=True, exist_ok=True)

        print(f"📅 필터링 기준 앱 ID: {bundle_id}")
        print(f"📅 필터링 기준 날짜: {set_date_str}")

        try:
            download_count = asyncio.run(
                self._download_filtered_logs_async(
                    set_date_str=set_date_str,
                    bundle_id=bundle_id,
                    local_root=local_root,
                )
            )
        except Exception as e:
            print(f"❌ 필터 로그 다운로드 중 오류 발생: {e}")
            return

        if download_count == 0:
            print(
                f"ℹ️ {set_date_str} 기준에 매칭되는 "
                f"로그 폴더/파일이 존재하지 않습니다."
            )

        print(f"\n✅ 작업 완료! 저장 위치: {local_root.absolute()}")


    async def _download_filtered_logs_async(
        self,
        set_date_str,
        bundle_id,
        local_root,
    ):
        """현재 이벤트 루프에서 앱 샌드박스 로그를 다운로드합니다."""
        remote_root = "/Documents/log"
        serial = self.device.get("serial") or getattr(
            self.lockdown,
            "identifier",
            None,
        )

        async with await create_using_usbmux(serial=serial) as lockdown:
            async with await HouseArrestService.create(
                lockdown,
                bundle_id,
            ) as afc:
                print(f"📂 '{bundle_id}' 샌드박스 접근 성공")

                if not await afc.exists(remote_root):
                    print(f"❌ 장치 내 경로 없음: {remote_root}")
                    return 0

                download_count = 0
                for item in await afc.listdir(remote_root):
                    if item in (".", ".."):
                        continue

                    if "log_" in item:
                        try:
                            item_date = item.split("log_", 1)[1][:10]
                            if item_date != set_date_str:
                                continue
                        except (IndexError, ValueError):
                            continue

                    print(f"🔎 대상 확인: {item}")
                    download_count += await self._pull_recursive(
                        afc,
                        f"{remote_root}/{item}",
                        local_root,
                    )

                return download_count


    def download_photos_by_date(self, set_date_str=None, target_ext=None):
        """특정 날짜의 사진/영상을 다운로드합니다. target_ext가 없으면 모든 미디어 확장자를 대상으로 합니다."""
        try:
            asyncio.run(
                self._download_photos_by_date_async(
                    set_date_str=set_date_str,
                    target_ext=target_ext,
                )
            )
        except Exception as e:
            print(f"\n❌ 사진/영상 필터링 복사 중 오류 발생: {e}")


    async def _download_photos_by_date_async(self, set_date_str=None, target_ext=None):
        """pymobiledevice3 비동기 AFC API를 사용하여 사진을 다운로드합니다."""
        folder_suffix = set_date_str if set_date_str else "filtered"
        save_dir = self.base_dir / "IOS" / folder_suffix / "ios_pic"
        save_dir.mkdir(parents=True, exist_ok=True)

        normalized_target_ext = None
        if target_ext:
            normalized_target_ext = str(target_ext).strip().lower()
            if not normalized_target_ext.startswith("."):
                normalized_target_ext = f".{normalized_target_ext}"

        extension_label = normalized_target_ext or "전체 사진/영상"
        print(
            f"🚀 사진/영상 필터링 다운로드 시작 "
            f"(날짜: {set_date_str}, 확장자: {extension_label})"
        )

        # 기존 lockdown은 장치 검색용 asyncio.run()에서 생성되었으므로,
        # 현재 작업의 이벤트 루프에서 같은 장치로 새 세션을 엽니다.
        serial = self.device.get("serial") or getattr(self.lockdown, "identifier", None)
        async with await create_using_usbmux(serial=serial) as lockdown:
            async with AfcService(lockdown) as afc:
                await self._download_photos_from_afc(
                    afc=afc,
                    save_dir=save_dir,
                    set_date_str=set_date_str,
                    target_ext=normalized_target_ext,
                )


    async def _download_photos_from_afc(
        self,
        afc,
        save_dir,
        set_date_str=None,
        target_ext=None,
    ):
        remote_base = "/DCIM"
        sub_dirs = []

        for item in await afc.listdir(remote_base):
            if item in (".", ".."):
                continue

            remote_item_path = f"{remote_base}/{item}"
            try:
                if await afc.isdir(remote_item_path):
                    sub_dirs.append(item)
                else:
                    logging.debug(
                        f"DCIM 최상위 파일은 폴더 순회 대상에서 제외합니다: "
                        f"{remote_item_path}"
                    )
            except Exception as e:
                logging.warning(
                    f"DCIM 항목 확인 실패로 건너뜁니다: "
                    f"{remote_item_path} ({e})"
                )

        download_count = 0

        for sub_dir in sub_dirs:
            remote_sub_path = f"{remote_base}/{sub_dir}"

            try:
                photos = [
                    item
                    for item in await afc.listdir(remote_sub_path)
                    if item not in (".", "..")
                ]
            except Exception as e:
                logging.warning(
                    f"사진/영상 폴더를 읽을 수 없어 건너뜁니다: "
                    f"{remote_sub_path} ({e})"
                )
                continue

            for photo_name in photos:
                photo_ext = Path(photo_name).suffix.lower()

                if target_ext:
                    if photo_ext != target_ext:
                        continue
                elif photo_ext not in IOS_MEDIA_EXTENSIONS:
                    continue

                remote_path = f"{remote_sub_path}/{photo_name}"

                if set_date_str:
                    info = await afc.stat(remote_path)

                    # 생성일만 기준으로 날짜를 판정합니다.
                    birth_time = info.get("st_birthtime")

                    if isinstance(birth_time, datetime):
                        file_date = birth_time.strftime("%Y-%m-%d")
                    elif isinstance(birth_time, (int, float)):
                        # 구버전 AFC가 나노초 timestamp를 반환하는 경우도 처리합니다.
                        timestamp = (
                            birth_time / 1_000_000_000
                            if birth_time > 10_000_000_000
                            else birth_time
                        )
                        file_date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                    else:
                        logging.warning(
                            f"미디어 생성 시간을 확인할 수 없어 건너뜁니다: {remote_path}"
                        )
                        continue

                    if file_date != set_date_str:
                        continue
                else:
                    file_date = sub_dir

                local_path = save_dir / photo_name
                if local_path.exists() and local_path.stat().st_size > 0:
                    continue

                print(
                    f"📥 [{file_date}] {photo_name} 다운로드 중...",
                    end="\r",
                    flush=True,
                )
                await afc.pull(remote_path, str(local_path))
                download_count += 1

        print(f"\n✅ 필터링 기반 사진/영상 다운로드 완료! ({download_count}개)")


    def get_ios_screenshot(self):
        """내부 API로 Apple 기기와 CarPlay 화면을 가능한 한 동시에 캡처합니다."""
        try:
            return asyncio.run(self._get_ios_screenshot_async())
        except Exception as e:
            print(
                "iOS 스크린샷 촬영 불가: "
                "기기 또는 터널 연결 상태를 확인해 주세요."
            )
            logging.debug(f"iOS 스크린샷 촬영 실패: {e}", exc_info=True)
            return None


    def _get_device_display_name(self):
        """lockdown 정보에서 사용자에게 표시할 Apple 장치 종류를 반환합니다."""
        all_values = getattr(self.lockdown, "all_values", {}) or {}
        device_class = str(all_values.get("DeviceClass", "")).lower()
        product_type = str(all_values.get("ProductType", "")).lower()
        model = str(self.device.get("model", "")).lower()

        if (
            device_class == "ipad"
            or product_type.startswith("ipad")
            or "ipad" in model
        ):
            return "iPad"
        if (
            device_class == "iphone"
            or product_type.startswith("iphone")
            or "iphone" in model
        ):
            return "iPhone"
        return "Apple 기기"


    def _get_os_major_version(self):
        """lockdown 정보에서 iOS/iPadOS 주 버전을 안전하게 반환합니다."""
        all_values = getattr(self.lockdown, "all_values", {}) or {}
        product_version = (
            all_values.get("ProductVersion")
            or getattr(self.lockdown, "product_version", "")
            or str(self.device.get("product", "")).replace("iOS", "").strip()
        )
        try:
            return int(str(product_version).split(".", 1)[0])
        except (TypeError, ValueError):
            return None


    async def _cache_carplay_display_unique_id(self, rsd):
        """현재 활성화된 CarPlay 디스플레이 ID를 컨트롤러에 캐시합니다."""
        async with DeviceInfoService(rsd) as device_info_service:
            display_info = await device_info_service.get_display_info()

        active_external_displays = []
        for display in display_info.get("displays", []):
            if not display.get("external"):
                continue

            unique_id = display.get("uniqueId") or display.get(
                "displayUniqueID"
            )
            size = display.get("currentMode", {}).get("size", [0, 0])

            if (
                unique_id
                and isinstance(size, (list, tuple))
                and len(size) >= 2
                and size[0] > 0
                and size[1] > 0
            ):
                active_external_displays.append(display)

        if not active_external_displays:
            print("ℹ활성 CarPlay 디스플레이가 없습니다.")
            return None

        active_external_displays.sort(
            key=lambda display: (
                not str(
                    display.get("deviceName", "")
                ).lower().startswith("wireless"),
                str(display.get("deviceName", "")),
            )
        )
        carplay_display = active_external_displays[0]
        self.carplay_unique_id = carplay_display.get(
            "uniqueId"
        ) or carplay_display.get("displayUniqueID")
        print(
            "CarPlay 디스플레이 확인 완료: "
            f"{self.carplay_unique_id}"
        )
        return self.carplay_unique_id


    async def _load_carplay_display_unique_id_async(self):
        """컨트롤러 생성 시 CarPlay 디스플레이 ID를 한 번만 조회합니다."""
        serial = self.device.get("serial") or getattr(
            self.lockdown,
            "identifier",
            None,
        )

        if UserspaceRsdTunnel is not None:
            try:
                async with UserspaceRsdTunnel(
                    serial=serial,
                    autopair=True,
                ) as rsd:
                    return await self._cache_carplay_display_unique_id(rsd)
            except Exception as e:
                logging.debug(
                    f"CarPlay 초기 조회용 내장 터널 연결 실패: {e}",
                    exc_info=True,
                )
        elif USERSPACE_TUNNEL_IMPORT_ERROR is not None:
            logging.debug(
                "CarPlay 초기 조회용 내장 터널 로드 실패: "
                f"{USERSPACE_TUNNEL_IMPORT_ERROR}"
            )

        rsd = None
        try:
            if serial:
                rsd = await get_tunneld_device_by_udid(serial)
            else:
                rsd_devices = await get_tunneld_devices()
                if rsd_devices:
                    rsd = rsd_devices[0]
                    for unused_rsd in rsd_devices[1:]:
                        await unused_rsd.close()

            if rsd is None:
                print(
                    "ℹCarPlay 디스플레이 초기 조회에 사용할 "
                    "RSD 연결을 찾을 수 없습니다."
                )
                return None

            return await self._cache_carplay_display_unique_id(rsd)
        finally:
            if rsd is not None:
                await rsd.close()


    async def _get_ios_screenshot_async(self):
        """내장 터널을 우선 사용하고 실행 중인 tunneld로 폴백합니다."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = self.base_dir / f"Screenshot_{timestamp}.png"
        carplay_path = self.base_dir / f"Screenshot_{timestamp}_carplay.png"
        serial = self.device.get("serial") or getattr(
            self.lockdown,
            "identifier",
            None,
        )
        device_display_name = self._get_device_display_name()
        os_major_version = self._get_os_major_version()

        async def capture_primary_with_lockdown():
            """RSD 캡처 실패 시 USB lockdown screenshotr 서비스로 재시도합니다."""
            # screenshotr는 최신 OS에서 제거된 구형 개발자 서비스입니다.
            # 호출하면 InvalidService와 미회수 Future 경고까지 발생하므로 생략합니다.
            if os_major_version is not None and os_major_version >= 17:
                print(
                    f"ℹ{device_display_name}OS {os_major_version}에서는 구형 USB "
                    "스크린샷 서비스를 지원하지 않아 해당 재시도를 생략합니다."
                )
                return None

            try:
                # 장치 검색 단계에서 만든 lockdown 객체는 이전 이벤트 루프에
                # 연결돼 있으므로 현재 루프에서 같은 UDID의 세션을 새로 엽니다.
                async with await create_using_usbmux(serial=serial) as lockdown:
                    async with ScreenshotService(lockdown) as screenshot_service:
                        image_data = await screenshot_service.take_screenshot()

                if not image_data:
                    raise ValueError("스크린샷 이미지 데이터가 없습니다.")

                save_path.write_bytes(image_data)
                print(
                    f"{device_display_name} 스크린샷 저장 완료: "
                    f"{save_path} ({save_path.stat().st_size} bytes)"
                )
                return save_path
            except Exception as e:
                print(
                    f"{device_display_name} USB 스크린샷 촬영 실패: "
                    f"{type(e).__name__}: {e}"
                )
                logging.error(
                    f"{device_display_name} lockdown 스크린샷 촬영 실패: {e}",
                    exc_info=True,
                )
                return None

        async def capture_primary_with_dvt(rsd):
            """CoreDevice 캡처가 없는 최신 iPadOS에서 DVT로 촬영합니다."""
            try:
                async with DvtProvider(rsd) as dvt_provider:
                    async with DvtScreenshot(dvt_provider) as screenshot:
                        image_data = await screenshot.get_screenshot()

                if not image_data:
                    raise ValueError("DVT 스크린샷 이미지 데이터가 없습니다.")

                save_path.write_bytes(image_data)
                print(
                    f"{device_display_name} DVT 스크린샷 저장 완료: "
                    f"{save_path} ({save_path.stat().st_size} bytes)"
                )
                return save_path
            except Exception as e:
                print(
                    f"{device_display_name} DVT 스크린샷 촬영 실패: "
                    f"{type(e).__name__}: {e}"
                )
                logging.error(
                    f"{device_display_name} DVT 스크린샷 촬영 실패: {e}",
                    exc_info=True,
                )
                return None

        async def capture_with_rsd(rsd):
            carplay_unique_id = self.carplay_unique_id
            rsd_services = (
                (getattr(rsd, "peer_info", None) or {}).get("Services", {})
            )
            has_core_screenshot = (
                ScreenCaptureService.SERVICE_NAME in rsd_services
            )
            has_dvt_screenshot = DvtProvider.RSD_SERVICE_NAME in rsd_services
            logging.info(
                "Apple 스크린샷 서비스 확인: "
                f"CoreDevice={has_core_screenshot}, DVT={has_dvt_screenshot}, "
                f"OS={getattr(rsd, 'product_version', 'Unknown')}"
            )

            async def capture_screen(display_name, output_path, unique_id):
                try:
                    async with ScreenCaptureService(rsd) as capture_service:
                        response = await capture_service.capture_screenshot(
                            display_unique_id=unique_id
                        )

                    image_data = response.get("image")
                    if not image_data:
                        raise ValueError("스크린샷 이미지 데이터가 없습니다.")

                    output_path.write_bytes(image_data)
                    print(
                        f"{display_name} 스크린샷 저장 완료: "
                        f"{output_path} ({output_path.stat().st_size} bytes)"
                    )
                    return output_path
                except Exception as e:
                    print(
                        f"{display_name} RSD 스크린샷 촬영 실패: "
                        f"{type(e).__name__}: {e}"
                    )
                    logging.error(
                        f"{display_name} 스크린샷 촬영 실패: {e}",
                        exc_info=True,
                    )
                    return None

            capture_tasks = []
            if has_core_screenshot:
                capture_tasks.append(
                    capture_screen(device_display_name, save_path, None)
                )
            else:
                print(
                    f"ℹ{device_display_name}OS {getattr(rsd, 'product_version', '')}에서 "
                    "CoreDevice 스크린샷 서비스를 제공하지 않아 DVT 방식으로 시도합니다."
                )

            if carplay_unique_id and has_core_screenshot:
                capture_tasks.append(
                    capture_screen(
                        "CarPlay",
                        carplay_path,
                        carplay_unique_id,
                    )
                )

            capture_results = (
                await asyncio.gather(*capture_tasks)
                if capture_tasks
                else []
            )

            if save_path in capture_results:
                return save_path
            if has_dvt_screenshot:
                dvt_path = await capture_primary_with_dvt(rsd)
                if dvt_path:
                    return dvt_path
            else:
                print(
                    f"{device_display_name}에서 DVT 스크린샷 서비스도 "
                    "제공되지 않습니다."
                )
            primary_fallback_path = await capture_primary_with_lockdown()
            if primary_fallback_path:
                return primary_fallback_path
            if carplay_path in capture_results:
                return carplay_path
            return None

        # wintun.dll이 정상 포함된 빌드는 별도 콘솔 없이 내장 터널을 사용합니다.
        if UserspaceRsdTunnel is not None:
            try:
                async with UserspaceRsdTunnel(
                    serial=serial,
                    autopair=True,
                ) as rsd:
                    return await capture_with_rsd(rsd)
            except Exception as e:
                logging.debug(
                    f"내장 userspace 터널 연결 실패: {e}",
                    exc_info=True,
                )
        elif USERSPACE_TUNNEL_IMPORT_ERROR is not None:
            logging.debug(
                "내장 userspace 터널 로드 실패: "
                f"{USERSPACE_TUNNEL_IMPORT_ERROR}"
            )

        # 내장 터널을 사용할 수 없으면 이미 실행 중인 tunneld를 재사용합니다.
        rsd = None
        try:
            if serial:
                rsd = await get_tunneld_device_by_udid(serial)
            else:
                rsd_devices = await get_tunneld_devices()
                if rsd_devices:
                    rsd = rsd_devices[0]
                    for unused_rsd in rsd_devices[1:]:
                        await unused_rsd.close()

            if rsd is None:
                print(
                    "ℹ️ 내장 터널과 실행 중인 tunneld 연결을 "
                    "모두 찾을 수 없어 USB 방식으로 재시도합니다."
                )
                return await capture_primary_with_lockdown()

            return await capture_with_rsd(rsd)
        finally:
            if rsd is not None:
                await rsd.close()
