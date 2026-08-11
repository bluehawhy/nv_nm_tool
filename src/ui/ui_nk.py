import sys
import os
import re
import time

# PyQt6 Core, GUI, Widgets 통합 임포트
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer, QPoint
from PyQt6.QtGui import QTextCursor, QFont, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QDialog,
    QVBoxLayout, QHBoxLayout, QGroupBox, QSizePolicy, 
    QPushButton, QLabel, QLineEdit, QTextEdit, QComboBox, QStackedWidget,
    QInputDialog, QFileDialog
)

# 사용자 정의 로컬 모듈 임포트
from ..core import func_logging
from ..core import call_device

from ..core import func_device, func_record_image
from ..utils import configus, loggas
logging = loggas.logger


# 환경변수 로드
def check_local_path():
    config = configus.load_config("resources/configs/config.json")
    if os.path.isdir(config['local_path']) is False:
        logging.info('no dir in local')
        config['local_path'] = os.path.join(os.path.expanduser('~'), 'Desktop', 'NKM_Tool')
        config = configus.save_config(config, 'resources/configs/config.json')
    os.makedirs(config['local_path'], exist_ok=True)
    return config


# 스타일시트 로드
def load_stylesheet(widget, file_path="resources/configs/style.qss"):
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                style_data = f.read()
                widget.setStyleSheet(style_data)
        else:
            print(f"Style file not found: {file_path}")
    except Exception as e:
        print(f"Error loading style: {e}")


# --- 1. 외부 함수의 print()를 GUI로 가로채기 위한 클래스 ---
class StreamToLogger(QObject):
    log_written = pyqtSignal(str)

    def write(self, text):
        if text.strip():
            self.log_written.emit(text.strip())

    def flush(self):
        pass


# --- 2. 백그라운드 작업을 담당할 Worker 클래스 ---
class Worker(QThread):
    finished = pyqtSignal(str)

    def __init__(self, func, *args):
        super().__init__()
        self.func = func
        self.args = args

    def run(self):
        try:
            self.func(*self.args)
            time.sleep(1)  # 테스트용 딜레이
            self.finished.emit("Success")
        except Exception as e:
            self.finished.emit(f"Error: {str(e)}")


# ==========================================
# --- 3. 기기별 개별 컨트롤 위젯 정의 ---
# ==========================================

# (1) 안드로이드 컨트롤 위젯
class AndroidWidget(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # MAIN COMMANDS 섹션
        main_cmd_label = QLabel("MAIN COMMANDS")
        main_cmd_label.setProperty("class", "sectionLabel")
        layout.addWidget(main_cmd_label)
        
        self.main_window.add_btn(layout, "Take Screenshot", self.main_window.cmd_tk_screenshot)
        self.main_window.dynamic_btn = self.main_window.add_btn(layout, "Take video", self.main_window.cmd_rec_video)
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Activate ENG", self.main_window.cmd_activate_eng))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Engineer Mode", self.main_window.cmd_gotoeng))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Set MV Debug", self.main_window.cmd_set_mv_debug))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Set vehicle position", self.main_window.cmd_set_car_pos))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Demo Simulation overlay", self.main_window.cmd_set_demo_simulation_overlay))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "set Hybrid Navigation Info", self.main_window.cmd_set_hybrid_navigation_info))

        layout.addSpacing(5)

        # Text Search 섹션
        fts_label = QLabel("Text Search")
        fts_label.setProperty("class", "sectionLabel")
        layout.addWidget(fts_label)
        self.main_window.btn_send_txt = self.main_window.add_btn(layout, "Send Text(Only English/KOR)", self.main_window.cmd_send_txt)
        layout.addSpacing(5)

        # DEMO CONTROL 섹션
        demo_cmd_label = QLabel("DEMO CONTROL")
        demo_cmd_label.setProperty("class", "sectionLabel")
        layout.addWidget(demo_cmd_label)
        
        h_box_1 = QHBoxLayout()
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_1, "ON", self.main_window.cmd_demo_on))
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_1, "STOP", self.main_window.cmd_demo_stop))
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_1, "PAU", self.main_window.cmd_demo_pause))
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_1, "REP", self.main_window.cmd_demo_repeat))
        layout.addLayout(h_box_1)
        
        h_box_2 = QHBoxLayout()
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_2, "SPEED", self.main_window.cmd_demo_speed))
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_2, "GUID OFF", self.main_window.set_guidance_off))
        layout.addLayout(h_box_2)
        layout.addSpacing(5)


        # ETC 섹션
        etc_label = QLabel("ETC")
        etc_label.setProperty("class", "sectionLabel")
        layout.addWidget(etc_label)
        self.main_window.btn_upload = self.main_window.add_btn(layout, "File Upload", self.main_window.cmd_file_upload)

        layout.addSpacing(5)
        layout.addStretch(1)


# (2) 애플 컨트롤 위젯 (임시 구현)
class AppleWidget(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        apple_label = QLabel("APPLE CONTROL")
        apple_label.setProperty("class", "sectionLabel")
        layout.addWidget(apple_label)

        # 임시 버튼 2개
        self.btn_apple_action1 = self.main_window.add_btn(layout, "Apple Action 1", self.cmd_apple_action1)
        self.btn_apple_action2 = self.main_window.add_btn(layout, "Apple Action 2", self.cmd_apple_action2)

        layout.addStretch(1)

    def cmd_apple_action1(self):
        self.main_window.log("Apple Action 1 triggered.")

    def cmd_apple_action2(self):
        self.main_window.log("Apple Action 2 triggered.")


# (3) TWD_ADB 컨트롤 위젯 (임시 구현)
class TwdAdbWidget(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # MAIN COMMANDS 섹션
        main_cmd_label = QLabel("MAIN COMMANDS")
        main_cmd_label.setProperty("class", "sectionLabel")
        layout.addWidget(main_cmd_label)
        
        self.main_window.add_btn(layout, "Take Screenshot", self.main_window.cmd_tk_screenshot)
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Activate ENG", self.main_window.cmd_activate_eng))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Engineer Mode", self.main_window.cmd_gotoeng))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Set MV Debug", self.main_window.cmd_set_mv_debug))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Set vehicle position", self.main_window.cmd_set_car_pos))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "Demo Simulation overlay", self.main_window.cmd_set_demo_simulation_overlay))
        self.main_window.lock_buttons.append(self.main_window.add_btn(layout, "set Hybrid Navigation Info", self.main_window.cmd_set_hybrid_navigation_info))

        layout.addSpacing(5)

        # Text Search 섹션
        fts_label = QLabel("Text Search")
        fts_label.setProperty("class", "sectionLabel")
        layout.addWidget(fts_label)
        self.main_window.btn_send_txt = self.main_window.add_btn(layout, "Send Text(Only English/KOR)", self.main_window.cmd_send_txt)
        layout.addSpacing(5)

        # DEMO CONTROL 섹션
        demo_cmd_label = QLabel("DEMO CONTROL")
        demo_cmd_label.setProperty("class", "sectionLabel")
        layout.addWidget(demo_cmd_label)
        
        h_box_1 = QHBoxLayout()
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_1, "ON", self.main_window.cmd_demo_on))
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_1, "STOP", self.main_window.cmd_demo_stop))
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_1, "PAU", self.main_window.cmd_demo_pause))
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_1, "REP", self.main_window.cmd_demo_repeat))
        layout.addLayout(h_box_1)
        
        h_box_2 = QHBoxLayout()
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_2, "SPEED", self.main_window.cmd_demo_speed))
        self.main_window.lock_buttons.append(self.main_window.add_btn(h_box_2, "GUID OFF", self.main_window.set_guidance_off))
        layout.addLayout(h_box_2)
        layout.addSpacing(5)


        # ETC 섹션
        etc_label = QLabel("ETC")
        etc_label.setProperty("class", "sectionLabel")
        layout.addWidget(etc_label)
        self.main_window.btn_upload = self.main_window.add_btn(layout, "File Upload", self.main_window.cmd_file_upload)

        layout.addSpacing(5)
        layout.addStretch(1)



# --- 4. 메인 GUI 클래스 ---
class MainWindow(QMainWindow):
    def __init__(self, version, revision):
        super().__init__()
        t0 = time.time()
        
        # [프레임리스 설정] 테두리를 없애고 배경 투명을 허용함
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # 변수 할당
        self.version = version
        self.revision = revision
        self.devices = []           # 검색된 기기 리스트 바구니
        self.device = None          # 현재 활성화(연결)된 단일 기기 딕셔너리
        self.and_log_manager = None # [수정] AndroidLogManager 객체 초기화
        self.device_type = None
        self.current_config = None
        self.is_scanning = False    # 기기 스캔 중 복수 실행 방지 플래그
        
        self.workers = []     # 스레드 바구니
        self.lock_buttons = [] # 상호 충돌 방지를 위해 묶어서 비활성화할 버튼 목록
        self.log_stop_signal = None
        self.version_stop_signal = None

        # 드래그 이동을 위한 좌표 저장 변수
        self.drag_pos = None

        # 프로그램 시작 시 기존 버전 정보 파일 초기화
        version_file = "resources/info/version_info.txt"
        if os.path.exists(version_file):
            os.remove(version_file)
            logging.info("start program - remove version_info.txt")

        # 상태값 초기화
        self.sw_version = "Checking..."
        self.map_version = "Checking..."
        self.version_found_flag = False
        self.last_update_time = time.time()
        self.timeout_limit = 300 

        logging.info(f"[Check Point 1] 기본 변수 설정: {time.time() - t0:.3f}s")
        t1 = time.time()
        

        # UI 및 스타일 로드
        self.init_ui()
        load_stylesheet(self)
        logging.info(f"[Check Point 2] 스타일 로드 및 UI 로드: {time.time() - t1:.3f}s")
        t2 = time.time()
        self.current_config = check_local_path()
        
        # 기타 설정
        self.stdout_receiver = StreamToLogger()
        self.stdout_receiver.log_written.connect(self.log)
        self._old_stdout = sys.stdout
        sys.stdout = self.stdout_receiver
        logging.info(f"[Check Point 3] 스트림 로그 설정: {time.time() - t2:.3f}s")
        t3 = time.time()

        # 타이머 설정 (초기에는 꺼두고 장치 연결 이후 활성화)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_version_info)

        # =========================================================
        # 🟢 [추가] 3초 주기 디바이스 연결 상시 점검 타이머 설정
        # =========================================================
        self.timer_device_check = QTimer(self)
        self.timer_device_check.setInterval(3000)  # 3초(3000ms)
        self.timer_device_check.timeout.connect(self.check_device_connection_status)
        self.is_checking_device = False  # 중복 검사 방지 플래그


        self.log(f"--- GUI Started ---")
        self.refresh_display()       # "Checking..." 상태로 화면 표시

        logging.info(f"[Check Point 4] UI 초기화: {time.time() - t3:.3f}s")
        t3 = time.time()
        # UI가 나타난 직후(0.1초 뒤) 디바이스 목록 스캔 시작
        QTimer.singleShot(3000, self.scan_devices)

    def init_ui(self):
        self.setMinimumSize(500, 600)
        
        # 1. 메인 베이스 위젯 생성
        central_widget = QWidget()
        central_widget.setObjectName("mainWidget")
        
        self.setMouseTracking(True)
        central_widget.setMouseTracking(True) 
   
        self.setCentralWidget(central_widget)
        
        # 메인 레이아웃 (수직: 타이틀바 + 컨텐츠)
        master_layout = QVBoxLayout(central_widget)
        master_layout.setContentsMargins(0, 0, 0, 0)
        master_layout.setSpacing(0)

        # 2. 커스텀 타이틀바 영역
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(35)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(15, 0, 0, 0)
        title_layout.setSpacing(0)

        self.title_label = QLabel(f"{self.version} - No device selected")
        self.title_label.setObjectName("titleLabel")
        self.title_label.setProperty("class", "sectionLabel")
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()

        # 최소화/최대화/닫기 버튼 및 R 버튼 생성
        self.r_btn = QPushButton("R")
        self.r_btn.setProperty("class", "titleBtn")
        self.r_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.r_btn.clicked.connect(self.open_revision_dialog)

        self.min_btn = QPushButton("ㅡ")
        self.min_btn.setProperty("class", "titleBtn")
        self.min_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.min_btn.clicked.connect(self.showMinimized)

        self.max_btn = QPushButton("□")
        self.max_btn.setProperty("class", "titleBtn")
        self.max_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.max_btn.clicked.connect(self.toggle_maximize)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setProperty("class", "titleBtn")
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.clicked.connect(self.close)

        title_layout.addWidget(self.r_btn)
        title_layout.addWidget(self.min_btn)
        title_layout.addWidget(self.max_btn)
        title_layout.addWidget(self.close_btn)
        
        master_layout.addWidget(title_bar)
        
        content_area = QWidget()
        content_layout = QHBoxLayout(content_area)
        content_layout.setContentsMargins(15, 5, 15, 5)
        content_layout.setSpacing(5)

        # --- [좌측: 컨트롤 패널 고정 영역] ---
        self.left_widget = QWidget()
        self.left_widget.setFixedWidth(200)
        
        left_panel = QVBoxLayout(self.left_widget)
        left_panel.setContentsMargins(0, 0, 0, 0)
        left_panel.setSpacing(8)

        # (고정 영역) SYSTEM STATUS
        sys_status_label = QLabel("SYSTEM STATUS")
        sys_status_label.setProperty("class", "sectionLabel")
        left_panel.addWidget(sys_status_label)
        
        self.info_label = QTextEdit()
        self.info_label.setObjectName("info_label")
        self.info_label.setProperty("class", "infoLabel")
        self.info_label.setReadOnly(True)
        self.info_label.setFixedHeight(60)
        self.info_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.info_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        left_panel.addWidget(self.info_label)

        left_panel.addSpacing(15)

        # (동적 교체 영역) QStackedWidget으로 위젯 관리
        self.control_stack = QStackedWidget()
        left_panel.addWidget(self.control_stack)

        # 각 디바이스 대응 위젯 등록
        self.empty_widget = QWidget()  # 기기가 연결되지 않았을 때의 빈 화면
        self.android_widget = AndroidWidget(self)
        self.apple_widget = AppleWidget(self)
        self.twd_widget = TwdAdbWidget(self)

        self.control_stack.addWidget(self.empty_widget)     # Index 0
        self.control_stack.addWidget(self.android_widget)   # Index 1
        self.control_stack.addWidget(self.apple_widget)     # Index 2
        self.control_stack.addWidget(self.twd_widget)       # Index 3

        # 초기 디바이스 설정은 빈화면(0)으로 고정
        self.control_stack.setCurrentIndex(0)

        # --- [우측: 로그 터미널 유동 영역] ---
        right_panel_widget = QWidget()
        right_panel = QVBoxLayout(right_panel_widget)
        right_panel.setContentsMargins(0, 0, 0, 0)
        right_panel.setSpacing(5)

        # 디바이스 전체 제어 영역
        device_vlay = QVBoxLayout()
        device_vlay.setContentsMargins(0, 0, 0, 0)
        device_vlay.setSpacing(6)

        h_box_dev1 = QHBoxLayout()
        h_box_dev1.setContentsMargins(0, 0, 0, 0)
        
        self.device_label = QLabel("Device")
        self.device_label.setProperty("class", "sectionLabel")
        
        # 새로고침 버튼 연결
        self.btn_device_refresh = QPushButton("Refresh")
        self.btn_device_refresh.setFixedWidth(55)
        self.btn_device_refresh.setFixedHeight(28)
        self.btn_device_refresh.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.btn_device_refresh.clicked.connect(self.scan_devices)
        
        h_box_dev1.addWidget(self.device_label)
        h_box_dev1.addStretch()
        h_box_dev1.addWidget(self.btn_device_refresh)

        # 두 번째 줄: 디바이스 리스트 콤보박스 + 커넥트 버튼
        h_box_dev2 = QHBoxLayout()
        h_box_dev2.setContentsMargins(0, 0, 0, 0)
        h_box_dev2.setSpacing(8)
        
        self.combo_device = QComboBox()
        self.combo_device.setFixedHeight(28)
        self.combo_device.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        # 커넥트 버튼 연결
        self.btn_device_connect = QPushButton("Connect")
        self.btn_device_connect.setFixedWidth(55)
        self.btn_device_connect.setFixedHeight(28)
        self.btn_device_connect.clicked.connect(self.handle_connection_action)
        
        h_box_dev2.addWidget(self.combo_device)
        h_box_dev2.addWidget(self.btn_device_connect)

        device_vlay.addLayout(h_box_dev1)
        device_vlay.addLayout(h_box_dev2)

        right_panel.addLayout(device_vlay)
        right_panel.addSpacing(5)

        log_header = QLabel("LOG TERMINAL")
        log_header.setProperty("class", "sectionLabel")
        right_panel.addWidget(log_header)

        self.log_display = QTextEdit()
        self.log_display.setProperty("class", "logLabel")
        self.log_display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.log_display.setReadOnly(True)
        right_panel.addWidget(self.log_display)

        # --- [메인 레이아웃 조립] ---
        content_layout.addWidget(self.left_widget, 0)
        content_layout.addWidget(right_panel_widget, 1)
        
        master_layout.addWidget(content_area)
        
        # 시작 시 전체 조작 버튼 비활성화 (장치 연결 전까지)
        self.set_interaction_buttons_enabled(False)

    def set_interaction_buttons_enabled(self, enabled):
        """커스텀 타이틀바 버튼을 제외한 메인 조작 버튼들을 제어하는 헬퍼 함수"""
        for btn in self.findChildren(QPushButton):
            if btn.property("class") != "titleBtn" and btn not in [self.btn_device_refresh, self.btn_device_connect]:
                btn.setEnabled(enabled)

    # --- 장치 검색 및 갱신 로직 ---
    def scan_devices(self):
        """백그라운드에서 연결 가능한 장치를 탐색하여 콤보박스에 로드"""
        if self.is_scanning:
            return
        self.is_scanning = True
        self.btn_device_refresh.setEnabled(False)
        self.btn_device_refresh.setText("Scan...")
        self.combo_device.clear()
        self.combo_device.addItem("Scanning...")
        self.log("Scanning for connected devices...")

        def run_scan():
            found = call_device.discover_and_connect_device()
            self.devices = found if found else []

        worker = Worker(run_scan)
        worker.daemon = True
        self.workers.append(worker)
        worker.finished.connect(self.on_scan_finished)
        worker.start()

    def on_scan_finished(self, status):
        """장치 스캔 완료 후 콤보박스 업데이트"""
        self.is_scanning = False
        self.btn_device_refresh.setText("Refresh")
        self.combo_device.clear()

        if not self.devices:
            self.combo_device.addItem("No Device Found")
            self.log("No devices found. Please reconnect USB and click Refresh.")
            self.set_interaction_buttons_enabled(False)
            self.btn_device_refresh.setEnabled(True)
            self.combo_device.setEnabled(True)
            return

        for idx, dev in enumerate(self.devices):
            display_name = dev.get('model', 'Unknown Device')
            self.combo_device.addItem(display_name, idx)

        if self.device is not None:
            self.combo_device.setEnabled(False)
            self.btn_device_refresh.setEnabled(False)
        else:
            self.combo_device.setEnabled(True)
            self.btn_device_refresh.setEnabled(True)

        self.log(f"Scan complete. {len(self.devices)} device(s) found.")
        
    def handle_connection_action(self):
        """버튼 하나로 연결/해제를 토글하는 핸들러"""
        if self.device is not None:
            self.disconnect_device()
        else:
            self.connect_selected_device()

    def disconnect_device(self):
        """모든 연결 및 로깅 작업을 안전하게 종료하고 위젯을 빈 화면으로 되돌림"""
        self.log("Disconnecting device...")

        # 1. 헬스 체크 타이머 정지 (안전한 hasattr 검사 사용)
        if hasattr(self, 'timer_device_check') and self.timer_device_check is not None:
            self.timer_device_check.stop()
        self.is_checking_device = False

        # 2. 5초 버전 체크 타이머 정지
        if hasattr(self, 'timer') and self.timer is not None:
            self.timer.stop()

        # 3. 백그라운드 스레드 정지 시그널 전달
        if self.log_stop_signal:
            self.log_stop_signal.set()
            self.log_stop_signal = None
        
        if self.version_stop_signal:
            self.version_stop_signal.set()
            self.version_stop_signal = None

        # 4. 장치 정보 및 내부 변수 초기화 (중요)
        self.device = None
        self.and_log_manager = None  # [수정] None으로 초기화
        self.version_found_flag = False
        self.sw_version = "Checking..."
        self.map_version = "Checking..."

        # 5. UI 요소 리셋
        self.combo_device.setEnabled(True)
        self.btn_device_refresh.setEnabled(True)
        self.btn_device_connect.setText("Connect")
        self.title_label.setText(f"{self.version} - No device selected")
        self.set_interaction_buttons_enabled(False)
        self.refresh_display()

        # 6. 연결 해제 시 스택 위젯을 빈 화면(Blank)으로 전환
        self.control_stack.setCurrentIndex(0)
        self.log("Device disconnected successfully.")

    def connect_selected_device(self):
        """기기 연결 및 기기 타입별 뷰 포트 스위칭 로직"""
        current_index = self.combo_device.currentData()
        if current_index is None or not self.devices:
            self.log("[Warning] No device selected to connect.")
            return

        self.device = self.devices[current_index]
        dev_type_str = self.device.get('detected_type', 'Device')

        # [수정] AndroidLogManager 객체 선언
        self.and_log_manager = func_logging.AndroidLogManager(device=self.device)

        #기존 UI 요소 비활성화 및 상태 업데이트
        self.combo_device.setEnabled(False)
        self.btn_device_refresh.setEnabled(False)
        self.btn_device_connect.setText("Disconnect")
        
        self.title_label.setText(f"{self.version} - Connected: {dev_type_str}")
        self.log(f"Connecting to device index: {current_index} ({dev_type_str})")

        # 연결된 디바이스 타입에 따라 QStackedWidget 화면 갱신
        if dev_type_str == 'Android':
            self.control_stack.setCurrentIndex(1)
            self.set_interaction_buttons_enabled(True)
        elif dev_type_str == 'Apple':
            self.control_stack.setCurrentIndex(2)
            self.set_interaction_buttons_enabled(True)
        elif dev_type_str == 'twd_adb':
            self.control_stack.setCurrentIndex(3)
            self.set_interaction_buttons_enabled(True)
        else:
            self.control_stack.setCurrentIndex(0)
            self.set_interaction_buttons_enabled(False)

        self.restart_background_tasks()
        self.timer.start(5000)

        # 🟢 [수정] 기기 연결 성공 시 3초 점검 타이머 시작
        self.timer_device_check.start()

    # =========================================================
    # 🟢 [수정] 백그라운드 3초 주기 연결 상태 검사 로직
    # =========================================================
    def check_device_connection_status(self):
        """3초마다 비동기 스레드로 call_device.is_device_connected 상태를 체크"""
        if self.device is None or self.is_checking_device:
            return

        self.is_checking_device = True
        target_dev = self.device

        def task():
            try:
                res = call_device.is_device_connected(target_dev)
                logging.info(f'Health Check - {res}')
                return res
            except Exception as e:
                logging.info(f"[Check Task Exception] {e}")
                return False

        def on_check_finished(result):
            self.is_checking_device = False

            if worker in self.workers:
                self.workers.remove(worker)
            
            # 장치가 등록되어 있는데 헬스체크 결과가 False(또는 None)인 경우
            if self.device is not None and not result:
                logging.warning("⚠️ Device connection lost! Stopping timers and disconnecting...")
                
                # 🟢 [핵심 1] 5초 주기 버전 수집 타이머를 즉시 중지 (무한 재시작 방지)
                if hasattr(self, 'timer') and self.timer.isActive():
                    self.timer.stop()

                # 🟢 [핵심 2] disconnect_device() 호출
                self.disconnect_device()

        worker = Worker(task)
        worker.daemon = True
        self.workers.append(worker)
        worker.finished.connect(on_check_finished)
        worker.start()

    def restart_background_tasks(self):
        if not self.device: 
            return

        dev_type_str = self.device.get('detected_type', 'Device')
        if dev_type_str == 'Apple':
            return 
                
        if self.sw_version == "Checking...":
            self.start_version_collector()

        if self.current_config.get('activate_log'):
            # 기존 수집 스레드가 멈춰있거나 없는 경우
            if self.log_stop_signal is None or self.log_stop_signal.is_set():
                # [수정] and_log_manager가 존재하는 경우에만 라이브 로깅 시작
                if self.and_log_manager is not None:
                    self.log_stop_signal = self.and_log_manager.start_live_logging()
                    logging.info("Restarted log collector.")

    def start_version_collector(self):
        if not self.device:
            return
            
        version_file = "resources/info/version_info.txt"
        if self.version_found_flag:
            return
        if self.version_stop_signal and not self.version_stop_signal.is_set():
            return
        
        search_dict = self.current_config.get('version_filter', {
            'sw_version': r"[VT]\d{3}\.\d{2}_\d{6}", 
            'map_version': r"(?i)(?:versionid|ndsversion)[:\(\s]*(\d{5})"
        })
        
        # [수정] and_log_manager가 없으면 동작하지 않음
        if self.and_log_manager is None:
            return

        self.version_stop_signal = self.and_log_manager.get_log_from_list(
            search_patterns=search_dict, 
            file_path=version_file, 
            result_dict={}
        )
        
        logging.info("Version info collector started.")

    def update_version_info(self):
        # 1. 장치 변수가 없으면 즉시 리턴
        if self.device is None:
            return

        # 2. 버전을 아직 다 못 찾았고, 백그라운드 스레드가 종료(stop_signal set)된 경우
        if self.device and not self.version_found_flag:
            if self.version_stop_signal and self.version_stop_signal.is_set():
                
                # 🟢 [핵심] 재시작하기 전에 디바이스가 진짜 연결되어 있는지 실제로 확인!
                if call_device.is_device_connected(self.device):
                    logging.warning("Background thread died before finding all info. Restarting...")
                    self.restart_background_tasks()
                else:
                    # 장치가 실제로 끊어진 상태라면 재시작하지 않고 disconnect 수행
                    logging.warning("Device disconnected unexpectedly. Triggering disconnect_device()...")
                    self.disconnect_device()
                    return  # 연결이 해제되었으므로 아래 파일 읽기 동작 중단

        # 3. 버전 정보 파일 읽기 로직
        version_info_file_path = "resources/info/version_info.txt"
        if os.path.exists(version_info_file_path):
            try:
                with open(version_info_file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                    map_match = re.search(r"map_version:\s*(\d+)", content)
                    if map_match: 
                        self.map_version = map_match.group(1)
                    
                    sw_match = re.search(r"sw_version:\s*([VT]\d{3}\.\d{2}_\d{6})", content)
                    if sw_match: 
                        self.sw_version = sw_match.group(1)
                    
                    if self.sw_version != "Checking..." and self.map_version != "Checking...":
                        if not self.version_found_flag:
                            self.version_found_flag = True
                            logging.info("모든 버전 정보 수집 완료. 더 이상 스레드를 재시작하지 않습니다.")
                            self.timer.stop()
                    
                    logging.info(f"업데이트 완료: SW:{self.sw_version}, Map:{self.map_version}")
                    self.refresh_display()
                    
            except Exception as e:
                logging.error(f"Error parsing version info: {e}")

    def refresh_display(self):
        info_html = f"""
        <div style="font-family: 'Malgun Gothic', sans-serif;">
            <b>SW:</b> {self.sw_version}<br>
            <b>Map:</b> {self.map_version}
        </div>
        """
        self.info_label.setHtml(info_html)
        self.info_label.viewport().update()

    def open_revision_dialog(self):
        """R 버튼 클릭 시 revision 정보를 보여주는 새 창 생성"""
        dialog = RevisionDialog(self, self.revision)
        dialog.exec()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.max_btn.setText("□")
            self.centralWidget().setStyleSheet("#mainWidget { border-radius: 15px; }")
        else:
            self.showMaximized()
            self.max_btn.setText("❐")
            self.centralWidget().setStyleSheet("#mainWidget { border-radius: 0px; }")

    MARGIN = 10
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint()
            self.resize_edge = self._get_edge(event.position().toPoint())
    
    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        edge = self._get_edge(pos)
        if edge == "top_left" or edge == "bottom_right":
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edge == "top_right" or edge == "bottom_left":
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edge in ["left", "right"]:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edge in ["top", "bottom"]:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

        if self.drag_pos is not None:
            global_pos = event.globalPosition().toPoint()
            delta = global_pos - self.drag_pos
            rect = self.geometry()

            if self.resize_edge == "none":
                if pos.y() < 35:
                    self.move(self.x() + delta.x(), self.y() + delta.y())
            else:
                if "left" in self.resize_edge:
                    rect.setLeft(rect.left() + delta.x())
                if "right" in self.resize_edge:
                    rect.setRight(rect.right() + delta.x())
                if "top" in self.resize_edge:
                    rect.setTop(rect.top() + delta.y())
                if "bottom" in self.resize_edge:
                    rect.setBottom(rect.bottom() + delta.y())
                
                if rect.width() >= self.minimumWidth() and rect.height() >= self.minimumHeight():
                    self.setGeometry(rect)

            self.drag_pos = global_pos
    
    def _get_edge(self, pos):
        w, h = self.width(), self.height()
        m = self.MARGIN
        edge = ""
        if pos.y() < m: edge += "top"
        elif pos.y() > h - m: edge += "bottom"
        if pos.x() < m: edge += "_left" if edge else "left"
        elif pos.x() > w - m: edge += "_right" if edge else "right"
        return edge if edge else "none"
    
    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        self.resize_edge = "none"
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def resizeEvent(self, event):
        dynamic_font_size = max(9, min(14, self.width() // 80))
        new_font = QFont()
        new_font.setPointSize(dynamic_font_size)

        for btn in self.findChildren(QPushButton):
            btn.setFont(new_font)
            if btn.property("class") != "titleBtn":
                btn.setFixedHeight(int(dynamic_font_size * 2.5)) 

        self.info_label.setFont(new_font)
        self.log_display.setFont(new_font)
        
        for label in self.findChildren(QLabel):
            if label.property("class") == "sectionLabel":
                label.setFont(new_font)

    def add_btn(self, layout, text, func):
        btn = QPushButton(text)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        btn.setMinimumHeight(25)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.clicked.connect(func)
        layout.addWidget(btn)
        return btn

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Tab):
            self.focusNextChild()
        elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Left):
            self.focusPreviousChild()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused_widget = self.focusWidget()
            if isinstance(focused_widget, QPushButton):
                focused_widget.click()
        else:
            super().keyPressEvent(event)

    def log(self, text):
        self.log_display.append(text)
        self.log_display.moveCursor(QTextCursor.MoveOperation.End)

    def run_task(self, func, *args):
        sender_button = self.sender()
        if isinstance(sender_button, QPushButton):
            if sender_button in self.lock_buttons:
                for btn in self.lock_buttons:
                    btn.setEnabled(False)
            else:
                sender_button.setEnabled(False)
        
        worker = Worker(func, *args)
        worker.daemon = True
        self.workers.append(worker)
        worker.finished.connect(lambda: self.cleanup_worker(worker, sender_button)) 
        worker.start()

    def cleanup_worker(self, worker, button=None):
        if worker in self.workers:
            self.workers.remove(worker)
        if button and isinstance(button, QPushButton):
            if button in self.lock_buttons:
                for btn in self.lock_buttons:
                    btn.setEnabled(True)
            else:
                button.setEnabled(True)

    # 각 버튼 액션들
    def cmd_gotoeng(self): self.run_task(func_device.go_to_eng_mode, self.device)
    def cmd_activate_eng(self): self.run_task(func_device.activate_eng, self.device)
    def cmd_react_adb(self): self.run_task(call_device.react_adb)
    def cmd_rec_video(self): self.run_task(func_record_image.record_video, self.device)
    def cmd_tk_screenshot(self): self.run_task(func_record_image.record_screenshot, self.device)
    def cmd_demo_on(self): self.run_task(func_device.set_demo_mode, self.device, "START")
    def cmd_demo_stop(self): self.run_task(func_device.set_demo_mode, self.device, "STOP")
    def cmd_demo_pause(self): self.run_task(func_device.set_demo_mode, self.device, "PAUSE")
    def cmd_demo_repeat(self): self.run_task(func_device.set_demo_mode, self.device, "REPEAT")
    def cmd_set_car_pos(self): self.run_task(func_device.select_latter_eng, self.device, "set car position")
    def cmd_set_demo_simulation_overlay(self): self.run_task(func_device.select_latter_eng, self.device, "demo simulation overlay")
    def cmd_set_hybrid_navigation_info(self): self.run_task(func_device.select_latter_box_eng, self.device, 'hybrid navigation info', '-')
    def set_guidance_off(self): self.run_task(func_device.set_guidance_off, self.device)
    
    def cmd_set_mv_debug(self): 
        val, ok = CustomInputDialog.get_int(self, "MV Debug", "Value:", 149)
        if ok: self.run_task(func_device.select_latter_box_eng, self.device, 'mv debug menu', val)

    def cmd_demo_speed(self):
        val, ok = CustomInputDialog.get_int(self, "Demo Speed", "Value:", 6)
        if ok: self.run_task(func_device.select_latter_box_eng, self.device, 'simulation speed', val)

    def cmd_send_txt(self):
        text, ok = CustomInputDialog.get_text(self, "Send Text", "Please write the text you want to input:")
        if ok: self.run_task(func_device.search_fts, self.device, text)

    def cmd_file_upload(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select File to Push", "", 
            "All Files (*);;Log Files (*.log);;Text Files (*.txt)"
        )
        if not file_path:
            return
        if not self.device:
            self.log("[Error] No device connected.")
            return

        self.btn_upload.setEnabled(False)
        self.btn_upload.setText("Uploading...") 
        file_name = os.path.basename(file_path)
        remote_path = f"/sdcard/Download/{file_name}"

        try:
            if hasattr(self, 'last_push_status'):
                del self.last_push_status
            func_device.push_file_background(self.device, file_path, remote_path)
            self.log(f"[Push Started] {file_name}")
            if not hasattr(self, 'push_check_timer'):
                self.push_check_timer = QTimer(self)
                self.push_check_timer.timeout.connect(self.check_push_progress)
            self.push_check_timer.start(1000)
        except Exception as e:
            self.log(f"[Error] Push failed: {str(e)}")
            self.btn_upload.setEnabled(True)
            self.btn_upload.setText("File Upload")

    def check_push_progress(self):
        progress_file = "resources/info/push_progress.txt"
        if not os.path.exists(progress_file):
            return
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if not lines: return
                last_line = lines[-1].strip()
            if hasattr(self, 'last_push_status') and self.last_push_status == last_line:
                return
            self.last_push_status = last_line
            self.log(f"[Pushing] {last_line}")
            if "Completed" in last_line or "Error" in last_line:
                self.push_check_timer.stop()
                self.btn_upload.setEnabled(True)
                self.btn_upload.setText("File Upload")
                self.log(f"[Finished] Status: {last_line}")
        except Exception:
            pass



class CustomInputDialog(QDialog):
    def __init__(self, parent=None, title="Title", label="Value:", value="", is_int=False):
        super().__init__(parent)
        load_stylesheet(self)
        
        # 1. 프레임 제거 및 배경 투명화
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(300)

        # 2. 메인 컨테이너 (#mainWidget)
        self.main_widget = QWidget(self)
        self.main_widget.setObjectName("mainWidget")
        
        self_layout = QVBoxLayout(self)
        self_layout.setContentsMargins(0, 0, 0, 0)
        self_layout.addWidget(self.main_widget)

        layout = QVBoxLayout(self.main_widget)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(0)

        # --- [상단] 타이틀바 ---
        self.title_bar = QWidget()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(35)
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(15, 0, 0, 0)
        
        self.title_label = QLabel(title)
        self.title_label.setObjectName("titleLabel")
        
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedSize(40, 35)
        self.close_btn.clicked.connect(self.reject)
        
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        title_layout.addWidget(self.close_btn)
        layout.addWidget(self.title_bar)


        # --- [중단] 입력 섹션 ---
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(20, 15, 20, 15)
        content_layout.setSpacing(8)

        self.sec_label = QLabel(label)
        self.sec_label.setObjectName("sectionLabel")
        
        # 입력 필드 (이름을 input_field로 통일)
        self.input_field = QLineEdit()
        self.input_field.setText(str(value))
        
        if is_int:
            from PyQt6.QtGui import QIntValidator
            self.input_field.setValidator(QIntValidator())
            
        self.input_field.returnPressed.connect(self.accept)

        content_layout.addWidget(self.sec_label)
        content_layout.addWidget(self.input_field)
        layout.addLayout(content_layout)

        # --- [하단] 버튼 섹션 ---
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 15, 5)
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self.accept)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.ok_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)
        self.input_field.setFocus()

    # 마우스 드래그 이동 기능
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._old_pos = event.globalPosition().toPoint()
    def mouseMoveEvent(self, event):
        if hasattr(self, '_old_pos') and event.buttons() == Qt.MouseButton.LeftButton:
            delta = QPoint(event.globalPosition().toPoint() - self._old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self._old_pos = event.globalPosition().toPoint()

    @staticmethod
    def get_int(parent, title, label, value=0):
        dialog = CustomInputDialog(parent, title, label, value, is_int=True)
        result = dialog.exec()
        try:
            val = dialog.input_field.text()
            return int(val) if val else 0, result == QDialog.DialogCode.Accepted
        except ValueError:
            return 0, False

    @staticmethod
    def get_text(parent, title, label, default_text=""):
        dialog = CustomInputDialog(parent, title, label, default_text, is_int=False)
        result = dialog.exec()
        # line_edit -> input_field로 수정하여 AttributeError 방지
        return dialog.input_field.text(), result == QDialog.DialogCode.Accepted

class RevisionDialog(QDialog):
    def __init__(self, parent=None, revision=None):
        super().__init__(parent)
        load_stylesheet(self)
        
        # 프레임 제거 및 배경 투명화
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(350, 250)

        # 메인 컨테이너
        self.main_widget = QWidget(self)
        self.main_widget.setObjectName("mainWidget")
        
        self_layout = QVBoxLayout(self)
        self_layout.setContentsMargins(0, 0, 0, 0)
        self_layout.addWidget(self.main_widget)

        layout = QVBoxLayout(self.main_widget)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(0)

        # --- [상단] 타이틀바 ---
        self.title_bar = QWidget()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(35)
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(15, 0, 0, 0)
        
        self.title_label = QLabel("Revision Info")
        self.title_label.setObjectName("titleLabel")
        
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        layout.addWidget(self.title_bar)

        # --- [중단] 텍스트 표시 영역 ---
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(20, 15, 20, 15)
        
        self.text_display = QTextEdit()
        self.text_display.setObjectName("logLabel")
        self.text_display.setProperty("class", "logLabel") # 메인 로그창과 동일한 배경/스타일 적용
        self.text_display.setReadOnly(True)
        
        # revision 리스트 내용을 텍스트창에 추가
        if revision:
            for re_info in revision:
                self.text_display.append(f"{re_info}")
                
        content_layout.addWidget(self.text_display)
        layout.addLayout(content_layout)

        # --- [하단] 버튼 섹션 (닫힘, 확인) ---
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 20, 5)
        btn_layout.setSpacing(10)
        btn_layout.addStretch()
        
        self.ok_btn = QPushButton("확인/OK")
        self.ok_btn.clicked.connect(self.accept)
        
        btn_layout.addWidget(self.ok_btn)
        layout.addLayout(btn_layout)