import sys
from pathlib import Path


ADB_RELATIVE_PATH = Path("resources") / "tools" / "platform-tools" / "adb.exe"


def get_bundled_adb_path() -> Path:
    """개발 및 PyInstaller 실행 환경에서 번들된 adb.exe 경로를 반환합니다."""
    candidates = []

    # PyInstaller onedir에서 resources를 실행 파일 옆에 둔 경우
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / ADB_RELATIVE_PATH)

        # PyInstaller 6.x의 _internal 또는 onefile 임시 번들 위치
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir).resolve() / ADB_RELATIVE_PATH)

    # 소스 실행: <project>/src/utils/adb_tools.py -> <project>
    candidates.append(Path(__file__).resolve().parents[2] / ADB_RELATIVE_PATH)

    checked = []
    for candidate in candidates:
        if candidate in checked:
            continue
        checked.append(candidate)

        if candidate.is_file():
            return candidate

    checked_paths = ", ".join(str(path) for path in checked)
    raise FileNotFoundError(
        "번들 ADB를 찾을 수 없습니다. "
        f"필요 파일: {ADB_RELATIVE_PATH} (확인 경로: {checked_paths})"
    )


def bundled_adb_command(*args) -> list[str]:
    """시스템 PATH를 사용하지 않는 번들 ADB 명령 배열을 만듭니다."""
    return [str(get_bundled_adb_path()), *(str(arg) for arg in args)]
