import os
import logging as std_logging
import asyncio
import time
import shutil
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path

# pymobiledevice3 관련 모듈
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.services.crash_reports import CrashReportsManager
from pymobiledevice3.services.house_arrest import HouseArrestService
from pymobiledevice3.services.installation_proxy import InstallationProxyService

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
            print(f"📥 파일 다운로드 중: {remote_path}")
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

        print(f"🚀 [{set_date_str}] 크래시 로그 추출을 시작합니다.")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            try:
                asyncio.run(self._pull_crash_logs_async(temp_path))
            except Exception as e:
                print(f"❌ 크래시 로그 추출 중 오류 발생: {e}")
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
                print("📥 장치에서 전체 로그 데이터를 수집하는 중...")

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

            if set_date_str:
                folder_suffix = set_date_str if set_date_str else "filtered"
                save_dir = self.base_dir / "IOS" / folder_suffix / "ios_pic"
                self._filter_local_media_by_metadata(
                    save_dir=save_dir,
                    set_date_str=set_date_str,
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


    @staticmethod
    def _parse_metadata_date(value):
        """메타데이터 날짜 값을 YYYY-MM-DD 문자열로 변환합니다."""
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")

        if value is None:
            return None

        value_text = str(value).strip()
        if not value_text:
            return None

        for date_format in (
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y:%m:%d",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value_text[:19], date_format).strftime(
                    "%Y-%m-%d"
                )
            except ValueError:
                continue

        try:
            return datetime.fromisoformat(
                value_text.replace("Z", "+00:00")
            ).strftime("%Y-%m-%d")
        except ValueError:
            return None


    def _get_local_media_metadata_date(self, local_path):
        """로컬 사진/영상에서 실제 촬영·생성 날짜를 읽습니다."""
        from hachoir.metadata import extractMetadata
        from hachoir.parser import createParser

        try:
            parser = createParser(str(local_path))
            if parser is None:
                return None

            with parser:
                metadata = extractMetadata(parser, quality=1.0)
        except Exception as e:
            logging.debug(f"메타데이터 분석 실패: {local_path} ({e})")
            return None

        if metadata is None:
            return None

        # 사진은 EXIF 원본 촬영일, 영상은 컨테이너 생성일을 우선합니다.
        for field_name in (
            "date_time_original",
            "creation_date",
            "date_time_digitized",
        ):
            try:
                metadata_value = metadata.get(field_name)
            except Exception:
                continue

            metadata_date = self._parse_metadata_date(metadata_value)
            if metadata_date:
                return metadata_date

        return None


    def _filter_local_media_by_metadata(self, save_dir, set_date_str):
        """다운로드된 로컬 미디어의 메타데이터를 검사해 다른 날짜 파일을 삭제합니다."""
        matched_count = 0
        deleted_count = 0
        unknown_count = 0

        # hachoir 내부 분석 로그는 표시하지 않습니다.
        hachoir_logger = std_logging.getLogger("hachoir")
        previous_level = hachoir_logger.level
        hachoir_logger.setLevel(std_logging.ERROR)

        try:
            for local_path in save_dir.rglob("*"):
                if (
                    not local_path.is_file()
                    or local_path.suffix.lower() not in IOS_MEDIA_EXTENSIONS
                ):
                    continue

                metadata_date = self._get_local_media_metadata_date(local_path)

                if metadata_date is None:
                    unknown_count += 1
                    logging.debug(
                        f"메타데이터 날짜를 확인하지 못해 유지합니다: {local_path}"
                    )
                    continue

                if metadata_date == set_date_str:
                    matched_count += 1
                    continue

                try:
                    local_path.unlink()
                    deleted_count += 1
                    print(
                        f"🗑️ 날짜 불일치 파일 삭제: {local_path.name} "
                        f"({metadata_date})"
                    )
                except OSError as e:
                    logging.warning(
                        f"날짜 불일치 파일 삭제 실패: {local_path} ({e})"
                    )
        finally:
            hachoir_logger.setLevel(previous_level)

        print(
            "✅ 로컬 메타데이터 2차 검사 완료 "
            f"(일치: {matched_count}개, "
            f"불일치 삭제: {deleted_count}개, "
            f"날짜 확인 불가 유지: {unknown_count}개)"
        )


    def get_ios_screenshot(self):
        """iOS 기기의 스크린샷을 저장합니다."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = self.base_dir / f"Screenshot_{timestamp}.png"

        cmd = [
            "pymobiledevice3",
            "developer",
            "dvt",
            "screenshot",
            str(save_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0 and save_path.exists():
            print(
                f"✅ 스크린샷 저장 완료: "
                f"{save_path} ({save_path.stat().st_size} bytes)"
            )
            return save_path

        print("❌ 스크린샷 촬영에 실패했습니다.")
        print(result.stderr or result.stdout)
        return None