import os
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


    def _pull_recursive(self, afc, remote_path, local_base_path):
        """폴더와 파일을 구분하여 재귀적으로 다운로드하는 내부 헬퍼 함수"""
        item_name = os.path.basename(remote_path)
        
        try:
            content = afc.get_file_contents(remote_path)
            local_file_path = local_base_path / item_name
            print(f"📥 파일 다운로드 중: {remote_path}")
            with open(local_file_path, "wb") as f:
                f.write(content)
                
        except Exception as e:
            if "isn't a file" in str(e) or "INVALID_ARG" in str(e):
                new_local_dir = local_base_path / item_name
                new_local_dir.mkdir(parents=True, exist_ok=True)
                
                try:
                    children = afc.listdir(remote_path)
                    for child in children:
                        if child in (".", ".."):
                            continue
                        self._pull_recursive(afc, f"{remote_path}/{child}", new_local_dir)
                except Exception as list_err:
                    print(f"⚠️ 폴더 목록 읽기 실패 ({remote_path}): {list_err}")
            else:
                print(f"❌ 처리 불가 경로 ({remote_path}): {e}")


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
        """특정 일자(YYYY-MM-DD)의 crash log(.ips)만 필터링하여 설정된 경로에 저장합니다."""
        # 💡 생성자에서 정의한 log_dir 또는 base_dir 하위로 경로 유연화
        final_dir = self.base_dir / "IOS" / set_date_str / "crash_logs"
        final_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"🚀 [{set_date_str}] 크래시 로그 추출을 시작합니다.")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            try:
                with CrashReportsManager(self.lockdown) as crash_manager:
                    print("📥 장치에서 전체 로그 데이터를 수집하는 중...")
                    try:
                        crash_manager.pull(str(temp_path))
                    except AttributeError:
                        crash_manager.copy(str(temp_path))

                count = 0
                for file_path in temp_path.rglob("*.ips"):
                    if set_date_str in file_path.name:
                        shutil.move(str(file_path), str(final_dir / file_path.name))
                        count += 1
                
                print(f"✅ 추출 완료! 총 {count}개의 '{set_date_str}' 로그를 저장했습니다.")
                print(f"📂 위치: {final_dir}")

            except Exception as e:
                print(f"❌ 크래시 로그 추출 중 오류 발생: {e}")


    def download_logs_final(self, bundle_id="hmi.navis.NMaps"):
        """전체 bundle_id의 로그 전체를 재귀적으로 다운로드합니다 (미필터링)"""
        remote_root = "Documents/log"
        local_root = self.base_dir / "ios_app_logs"
        local_root.mkdir(parents=True, exist_ok=True)

        try:
            with HouseArrestService(self.lockdown, bundle_id) as afc:
                print(f"📂 '{bundle_id}' 샌드박스 접근 성공")
                
                if not afc.exists(remote_root):
                    print(f"❌ 장치 내 경로 없음: {remote_root}")
                    return

                items = afc.listdir(remote_root)
                for item in items:
                    if item in (".", ".."):
                        continue
                    self._pull_recursive(afc, f"{remote_root}/{item}", local_root)
                        
            print(f"\n✅ 작업 완료! 저장 위치: {local_root.absolute()}")

        except Exception as e:
            print(f"❌ 샌드박스 로그 다운로드 중 치명적 오류: {e}")

    def download_filtered_logs(self, set_date_str, bundle_id="hmi.navis.NMaps"):
        """특정일(YYYY-MM-DD)의 bundle_id 로그 파일만 필터링하여 다운로드합니다."""
        remote_root = "Documents/log"
        local_root = self.base_dir / "IOS" / set_date_str / "ios_app_logs"
        local_root.mkdir(parents=True, exist_ok=True)

        print(f"📅 필터링 기준 앱 ID: {bundle_id}")
        print(f"📅 필터링 기준 날짜: {set_date_str}")

        try:
            with HouseArrestService(self.lockdown, bundle_id) as afc:
                print(f"📂 '{bundle_id}' 샌드박스 접근 성공")
                
                if not afc.exists(remote_root):
                    print(f"❌ 장치 내 경로 없음: {remote_root}")
                    return
                
                items = afc.listdir(remote_root)
                download_count = 0
                
                for item in items:
                    if item in (".", ".."): 
                        continue

                    if "log_" in item:
                        try:
                            item_date = item.split("log_")[1][:10]
                            if item_date != set_date_str:
                                continue
                        except (IndexError, ValueError):
                            pass

                    print(f"🔎 대상 확인: {item}")
                    self._pull_recursive(afc, f"{remote_root}/{item}", local_root)
                    download_count += 1
                
                if download_count == 0:
                    print(f"ℹ️ {set_date_str} 기준에 매칭되는 로그 폴더/파일이 존재하지 않습니다.")
                        
            print(f"\n✅ 작업 완료! 저장 위치: {local_root.absolute()}")

        except Exception as e:
            print(f"❌ 필터 로그 다운로드 중 오류 발생: {e}")

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
                    mtime = info.get("st_mtime")

                    if isinstance(mtime, datetime):
                        file_date = mtime.strftime("%Y-%m-%d")
                    elif isinstance(mtime, (int, float)):
                        # 구버전 AFC가 나노초 timestamp를 반환하는 경우도 처리합니다.
                        timestamp = mtime / 1_000_000_000 if mtime > 10_000_000_000 else mtime
                        file_date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                    else:
                        logging.warning(
                            f"미디어 수정 시간을 확인할 수 없어 건너뜁니다: {remote_path}"
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