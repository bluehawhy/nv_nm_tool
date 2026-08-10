import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

# [설정] 저장될 기본 위치 (바탕화면)
SAVE_ROOT = Path.home() / "Desktop" / "Android"

def run_adb(cmd):
    """ADB 명령 실행 공통 헬퍼"""
    try:
        result = subprocess.run(f"adb {cmd}", shell=True, capture_output=True, text=True, encoding='utf-8')
        return result.stdout.strip()
    except Exception as e:
        print(f"[ERROR] ADB 명령 실패: {e}")
        return ""

def check_connection():
    """기기 연결 확인"""
    res = run_adb("devices")
    lines = res.split('\n')[1:]
    connected = [line for line in lines if line.strip().endswith('device')]
    return len(connected) > 0



# ---------------------------------------------------------------------------
# 1. 미디어 파일 추출 (DCIM)
# ---------------------------------------------------------------------------
def pull_media(target_date):
    date_pattern = target_date.replace("-", "")
    
    # 수정 포인트: 문자열인 target_date를 Path 객체와 결합하여 경로로 만듭니다.
    # SAVE_ROOT가 Path 객체여야 합니다 (예: SAVE_ROOT = Path.home() / "Desktop" / "Android")
    media_dir = SAVE_ROOT / target_date / "Media"
    media_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"🚀 미디어 탐색 중 (패턴: {date_pattern})...")
    
    find_media = f"shell \"find /sdcard/DCIM/ -type f -name '*{date_pattern}*'\""
    media_files = run_adb(find_media).split('\n')
    
    count = 0
    for remote_path in media_files:
        remote_path = remote_path.strip()
        if not remote_path: continue
        
        fname = os.path.basename(remote_path)
        rel_path = remote_path.replace("/sdcard/DCIM/", "").lstrip("/")
        local_path = media_dir / rel_path
        
        # 상위 폴더(Camera 등) 생성
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"   📥 미디어 복사 중: {fname}", end="\r", flush=True)
        # 공백 대응을 위해 따옴표 추가
        run_adb(f"pull \"{remote_path}\" \"{local_path}\"")
        count += 1
    print(f"\n   ✅ 미디어 {count}개 추출 완료.")

# ---------------------------------------------------------------------------
# 2. 시스템 로그캣 추출 (Dump)
# ---------------------------------------------------------------------------
def pull_system_log(target_date):
    log_dir = target_date / "System_Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%H%M%S')
    log_file = log_dir / f"logcat_dump_{timestamp}.txt"
    
    print(f"🚀 시스템 로그캣 추출 중...")
    run_adb(f"logcat -d > \"{log_file}\"")
    print(f"   ✅ 시스템 로그 저장 완료.")


def pull_app_log(target_app, target_date):
    remote_root = f"/sdcard/Android/data/{target_app}/files/Download/logcat/"
    local_dir = SAVE_ROOT / target_date / "App_Logs"
    local_dir.mkdir(parents=True, exist_ok=True)

    print(f"🚀 내비 앱 로그 탐색 중: {remote_root}")

    # [수정] find 결과가 공백을 포함해도 한 줄씩 정확히 읽도록 처리
    # -maxdepth 1 뒤에 날짜 패턴을 넣어 해당 날짜 폴더들만 추출
    find_cmd = f"shell \"find {remote_root} -maxdepth 1 -type d -name '{target_date}*'\""
    folders_output = run_adb(find_cmd)
    
    if not folders_output:
        print(f"⚠️ [{target_date}] 에 해당하는 로그 폴더를 찾지 못했습니다.")
        return

    # 줄바꿈으로 분리하여 각 폴더 경로 처리
    folders = folders_output.split('\n')
    
    for r_folder in folders:
        r_folder = r_folder.strip()
        # 루트 경로와 같거나 비어있으면 스킵
        if not r_folder or r_folder == remote_root.rstrip('/'):
            continue
        
        folder_name = os.path.basename(r_folder)
        # 로컬에 저장될 최종 경로 (예: .../App_Logs/2026-06-01 10_54_50)
        current_local_folder = local_dir / folder_name
        current_local_folder.mkdir(parents=True, exist_ok=True)

        print(f"   📥 복사 중: {folder_name}")
        
        # 핵심: 경로 전체를 \"{r_folder}\"로 감싸서 공백이 있어도 하나로 인식하게 함
        # /. 을 붙여서 폴더 내부의 모든 파일과 하위 구조를 가져옴
        run_adb(f"pull \"{r_folder}/.\" \"{current_local_folder}\"")

    print(f"✅ 앱 로그 추출 작업 종료.")

def call_cmd():
    print("=" * 50)
    print("   Android Navi Data Collector v1.2")
    print("=" * 50)

    if check_connection():
        while True:
            print("\n1. 데이터 추출 (날짜 yyyy-mm-dd 입력)")
            print("0. 종료")
            cmd = input("👉 선택: ").strip()

            if cmd == '1':
                d_str = input("📅 날짜 입력: ").strip()
                try:
                    datetime.strptime(d_str, "%Y-%m-%d")
                    pull_media(target_date=d_str)
                    pull_app_log(target_app= 'navis.ncn.navi', target_date=d_str)
                except ValueError:
                    print("❌ 날짜 형식이 올바르지 않습니다.")
            elif cmd == '0':
                break
    else:
        print("❌ 안드로이드 기기가 연결되지 않았습니다.")
        sys.exit()


# ==========================================
if __name__ == "__main__":
    call_cmd()
