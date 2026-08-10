import os
import sys
import time
import shutil
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path

# pymobiledevice3 관련 모듈
from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.services.crash_reports import CrashReportsManager
from pymobiledevice3.services.house_arrest import HouseArrestService
from pymobiledevice3.services.installation_proxy import InstallationProxyService

# 설정 및 로깅 모듈 연동 (프로젝트 구조에 맞게 임포트 경로 확인 필요)
# import configus
# from . import loggas
# logging = loggas.logger


class IOSDeviceController:
    """
    미리 생성된 lockdown_device(PlistUsbmuxLockdownClient)를 주입받아
    iOS 기기의 앱 정보 검색, 크래시 로그 수집, 앱 샌드박스 파일 다운로드,
    사진 다운로드, 스크린샷 기능을 수행하는 클래스입니다.
    """
    def __init__(self, lockdown_device, folder_path=None):
        if not lockdown_device:
            raise ValueError("유효한 lockdown_device(iOS 연결 세션) 객체가 필요합니다.")
        self.lockdown = lockdown_device

        # [추가] 설정 파일 로드
        try:
            self.config = configus.load_config('static/config.json')
        except NameError:
            # configus 모듈이 임포트되지 않았을 때를 위한 임시 가드 (실제 환경에 맞게 조정 가능)
            self.config = {"local_path": str(Path.home() / "Desktop")}

        # [추가] 기본 저장 및 로그 경로 설정
        if folder_path is None:
            folder_path = self.config.get('local_path', './')
        
        self.base_dir = Path(folder_path)
        self.log_dir = self.base_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

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

    def download_all_photos(self):
        """기기 내 모든 사진(/DCIM)을 다운로드합니다."""
        save_dir = self.base_dir / "IOS" / "ios_pic_all"
        save_dir.mkdir(parents=True, exist_ok=True)
        
        print("🚀 모든 사진/동영상 다운로드를 시작합니다 (DCIM pull)...")

        try:
            with AfcService(self.lockdown) as afc:
                remote_base = "/DCIM"
                
                if not afc.exists(remote_base):
                    print("❌ DCIM 폴더를 찾을 수 없습니다. 장치 잠금을 해제해 주세요.")
                    return

                sub_dirs = [d for d in afc.listdir(remote_base) if d not in (".", "..")]
                
                for sub_dir in sub_dirs:
                    remote_sub_path = f"{remote_base}/{sub_dir}"
                    current_local_dir = save_dir / sub_dir
                    current_local_dir.mkdir(parents=True, exist_ok=True)
                    
                    print(f"📂 폴더 진입: {remote_sub_path}")
                    
                    try:
                        photos = [p for p in afc.listdir(remote_sub_path) if p not in (".", "..")]
                    except Exception:
                        continue

                    for photo_name in photos:
                        remote_path = f"{remote_sub_path}/{photo_name}"
                        local_path = current_local_dir / photo_name
                        
                        if local_path.exists() and local_path.stat().st_size > 0:
                            continue

                        try:
                            info = afc.stat(remote_path)
                            size_mb = int(info.get("st_size", 0)) / (1024 * 1024)
                            
                            print(f"📥 [{sub_dir}] {photo_name} ({size_mb:.1f} MB) 다운로드 중...", end="\r", flush=True)
                            afc.pull(remote_path, str(local_path))
                            print(f"📥 [{sub_dir}] {photo_name} 완료!                      ")
                        except Exception as e:
                            print(f"❌ {photo_name} 다운로드 실패: {e}")
                
                print("\n✅ 모든 사진 저장 완료!")

        except Exception as e:
            print(f"\n❌ 사진 다운로드 중 치명적 오류: {e}")

    def download_photos_by_date(self, set_date_str=None, target_ext=None):
        """특정 수정 일자 및 특정 확장자(.JPG, .PNG 등)를 기준으로 사진을 필터링하여 다운로드합니다."""
        folder_suffix = set_date_str if set_date_str else "filtered"
        save_dir = self.base_dir / "IOS" / folder_suffix / "ios_pic"
        save_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"🚀 사진 필터링 다운로드 시작 (날짜: {set_date_str}, 확장자: {target_ext})")

        try:
            with AfcService(self.lockdown) as afc:
                remote_base = "/DCIM"
                sub_dirs = [d for d in afc.listdir(remote_base) if d not in (".", "..")]
                
                for sub_dir in sub_dirs:
                    remote_sub_path = f"{remote_base}/{sub_dir}"
                    photos = [p for p in afc.listdir(remote_sub_path) if p not in (".", "..")]

                    for photo_name in photos:
                        if target_ext and not photo_name.upper().endswith(target_ext.upper()):
                            continue

                        remote_path = f"{remote_sub_path}/{photo_name}"

                        if set_date_str:
                            info = afc.stat(remote_path)
                            mtime = info.get("st_mtime") 
                            
                            try:
                                file_date = mtime.strftime('%Y-%m-%d')
                            except AttributeError:
                                file_date = datetime.fromtimestamp(mtime / 1000000000).strftime('%Y-%m-%d')
                            
                            if file_date != set_date_str:
                                continue
                        else:
                            file_date = sub_dir

                        local_path = save_dir / photo_name
                        if local_path.exists() and local_path.stat().st_size > 0:
                            continue

                        print(f"📥 [{file_date}] {photo_name} 다운로드 중...", end="\r", flush=True)
                        afc.pull(remote_path, str(local_path))
                
                print("\n✅ 필터링 기반 사진 다운로드 완료!")

        except Exception as e:
            print(f"\n❌ 사진 필터링 복사 중 오류 발생: {e}")

    def get_ios_screenshot_fixed(self):
        """터널 데몬 프로세스를 구동하여 안전하게 iOS 기기 스크린샷을 확보합니다."""
        current_dir = self.base_dir / "IOS" / "screenshot"
        current_dir.mkdir(parents=True, exist_ok=True)
        
        print("🚀 [1/3] 터널 프로세스 시작...")
        
        tunnel = subprocess.Popen(
            ["pymobiledevice3", "remote", "tunneld"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        tunnel_ready = False
        start_time = time.time()
        while time.time() - start_time < 15:
            line = tunnel.stdout.readline()
            if not line: 
                break
            print(f"   [Tunnel Log] {line.strip()}")
            if "Application startup complete" in line or "Uvicorn running" in line:
                print("✅ 터널 서버가 준비되었습니다.")
                tunnel_ready = True
                time.sleep(2)
                break
        
        if not tunnel_ready:
            print("❌ 터널 생성에 실패했습니다.")
            tunnel.terminate()
            return

        try:
            print("📸 [2/3] 스크린샷 촬영 시도...")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"Screenshot_{timestamp}.png"
            save_path = current_dir / filename
            
            cmd = ["pymobiledevice3", "developer", "dvt", "screenshot", str(save_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if save_path.exists():
                print(f"✅ [3/3] 물리 파일 생성 완료! ({save_path.stat().st_size} bytes)")
            else:
                print("❌ 스크린샷 파일 생성에 실패했습니다.")
                print(f"🔍 디바이스 개발자 도구 세션 에러 로그:\n{result.stderr}")
                
        finally:
            print("🛑 터널 프로세스 리소스를 해제합니다...")
            tunnel.terminate()
            try:
                tunnel.wait(timeout=3)
            except Exception:
                tunnel.kill()