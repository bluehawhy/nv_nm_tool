#call_device.py
import subprocess
import threading
import time
import asyncio

from ppadb.client import Client as AdbClient
from pymobiledevice3.exceptions import NoDeviceConnectedError
from pymobiledevice3.lockdown import create_using_usbmux
import serial
import serial.tools.list_ports
import wmi

import uiautomator2 as u2

from ..utils import loggas

# 로거 설정
logging = loggas.logger

# --- [ 장치 인식 및 연결 관리 ] ---
def check_device_from_device_mg(target_keywords={
        'Android': ['ADB Interface'],
        'Apple': ['Apple Mobile'],
        'twd_com': ['Silicon Labs'],
        'twd_adb': ['tcc']
    }):
    """장치관리자에서 연결되어 있는 기기 카테고리 선별"""
    c = wmi.WMI()
    logging.info("--- 장치 관리자 항목 스캔 시작 ---")
    
    found_devices = []
    for device in c.Win32_PnPEntity():
        name = str(device.Name) if device.Name else ""
        
        for category, keywords in target_keywords.items():
            if any(key.lower() in name.lower() for key in keywords):
                logging.info(f"✅ 발견 [{category}]: {name}")
                found_devices.append(category)
                break  # 한 장치가 여러 카테고리에 걸리지 않도록 탈출
                
    return list(set(found_devices))

# --- [ 시리얼 및 ADB 유틸리티 ] ---
def get_serial_ports():
    """Silicon Labs Quad CP2108 Interface 1 포트 탐색"""
    print('== search all port connected.....')
    ports = serial.tools.list_ports.comports()
    
    if not ports:
        logging.info('No port')
        print("== No serial port.")
        return None
    
    logging.info(f"{'Device':<10} | {'Description':<30} | {'HWID'}")
    logging.info("-" * 60)
    print('Silicon Labs Quad CP2108 USB to UART Bridge: Interface 1 찾는중....')
    
    for port in ports:
        logging.info(f"{port.device:<10} | {port.description:<30} | {port.hwid}")
        if 'Interface 1' in port.description:
            print(f"{port.device:<10} | {port.description:<30}")
            return port.device
    return None

def set_adb_mode(port):
    """시리얼 포트 명령어를 통해 디바이스를 ADB 모드로 전환"""
    try:
        ser = serial.Serial(port, 921600, timeout=1)
        if ser.is_open:
            print(f"Connected to {port} at 921600 bps.")
            
            # 명령어 순차 송신
            commands = [b'\n', b'su\n', b'setprop sys.usb.config adb\n']
            for cmd in commands:
                if cmd != b'\n':
                    print(f"Sending: {cmd.decode().strip()}")
                ser.write(cmd)
                time.sleep(0.5)

            ser.close()
            print("Done.")
            return 1
    except Exception as e:
        print(f"Error: {e}")
        return 0

def is_adb_server_running():
    """ADB 서버 응답 가능 여부 확인"""
    try:
        subprocess.run(
            ["adb", "host-features"],
            capture_output=True,
            timeout=2, 
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False

def start_adb_server():
    """ADB 서버 시작"""
    try:
        subprocess.run(
            ["adb", "start-server"], 
            check=True, 
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        logging.info("ADB Server started successfully.")
    except Exception as e:
        logging.info(f"Failed to start ADB server: {e}")

def kill_all_adb():
    """실행 중인 adb.exe 프로세스 강제 종료"""
    command = ["taskkill", "/F", "/IM", "adb.exe", "/T"]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='cp949',
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        output = (result.stdout + result.stderr).upper()
        if "SUCCESS" in output or "성공" in output:
            logging.info("Successfully killed adb.exe processes.")
        elif "NOT FOUND" in output or "찾을 수 없습니다" in output:
            logging.info("No adb.exe process was running.")
        else:
            logging.info(f"taskkill output: {output.strip()}")
        return 1
    except Exception as e:
        logging.error(f"Error during taskkill: {e}")
        return 0

# --- [ 개별 기기 상세정보 획득 함수 (안정성 강화) ] ---

def get_detailed_devices(dev_info):
    """
    규격화된 딕셔너리를 받아 디바이스 상세 스펙 정보를 채운 뒤 딕셔너리로 반환합니다.
    """
    detailed_data = {}
    detected_type = dev_info.get('detected_type')

    if detected_type in ['Android', 'twd_adb']:
        adb_obj = dev_info.get('ppadb_device')
        if adb_obj:
            try:
                product_name = adb_obj.shell("getprop ro.product.name").strip()
                model_name = adb_obj.shell("getprop ro.product.model").strip()
                wm_size = adb_obj.shell("wm size").strip().split(":")[-1].strip()
                
                detailed_data = {
                    'serial': adb_obj.serial,
                    'model': model_name,
                    'product': product_name,
                    'resolution': wm_size
                }
            except Exception as e:
                logging.error(f"Android/TWD 상세 정보 획득 중 에러: {e}")
                detailed_data = {
                    'serial': getattr(adb_obj, 'serial', 'Unknown'),
                    'model': 'Android Device (Shell Error)',
                    'product': 'Android',
                    'resolution': 'Unknown'
                }
                
    elif detected_type == 'Apple':
        lock_device = dev_info.get('lockdown_device')
        if lock_device:
            try:
                serial_val = getattr(lock_device, 'identifier', 'Unknown UDID')
                device_name = lock_device.all_values.get('DeviceName', 'Apple Device')
                product_type = lock_device.all_values.get('ProductType', 'iOS Device')
                product_version = lock_device.all_values.get('ProductVersion', 'Unknown OS')
                
                detailed_data = {
                    'serial': serial_val,
                    'model': f"{device_name} ({product_type})",
                    'product': f"iOS {product_version}",
                    'resolution': 'Unknown'
                }
            except Exception as e:
                logging.error(f"iOS 락다운 상세 정보 조회 실패: {e}")
                detailed_data = {
                    'serial': getattr(lock_device, 'identifier', 'Unknown'),
                    'model': 'Apple Device (Locked/Error)',
                    'product': 'Apple',
                    'resolution': 'Unknown'
                }
        else:
            detailed_data = {
                'serial': 'Unknown',
                'model': 'Apple Device',
                'product': 'Apple',
                'resolution': 'Unknown'
            }
            
    return detailed_data

# --- [ 주기적 연결 상태 점검 (Health Check) ] ---
def is_device_connected(device):
    """
    현재 연결되어 있는 특정 디바이스의 물리적/소프트웨어적 연결 유효성을 검사합니다.
    3초 주기 타이머 호출에 최적화되어 빠른 속도로 True/False를 반환합니다.
    
    :param target_device_info: discover_and_connect_device()에서 반환받은 dict 개체
    :return: bool (연결 유지 시 True, 해제 시 False)
    """
    if not device:
        return False

    dev_type = device.get('detected_type')
    target_serial = device.get('serial')

    try:
        # 1. Android 및 TWD (ADB 기반 장비)
        if dev_type in ['Android', 'twd_adb']:
            adb_obj = device.get('ppadb_device')
            if not adb_obj or not target_serial:
                return False
            
            # ADB 서버가 죽었거나 빠르게 응답하지 않는지 검사
            if not is_adb_server_running():
                return False

            # pure-python-adb client 이용 -> 현재 연결된 시리얼 목록에 존재하는지 확인 (가장 빠른 방식)
            controller = ADBController(host="127.0.0.1", port=5037)
            current_devices = controller.client.devices()
            connected_serials = [dev.serial for dev in current_devices]
            
            return target_serial in connected_serials

        # 2. Apple (iOS 장비)
        elif dev_type == 'Apple':
            # pymobiledevice3 usbmuxd 연결 상태 빠르게 재점검
            lockdown = asyncio.run(create_using_usbmux())
            if not lockdown:
                return False
            
            # UDID/Identifier 일치 여부 확인
            current_identifier = getattr(lockdown, 'identifier', None)
            if target_serial and target_serial != 'Unknown UDID':
                return current_identifier == target_serial
            
            return True

        return False

    except Exception as e:
        logging.warning(f"디바이스 상태 확인 중 예외 발생 (연결 끊김으로 간주): {e}")
        return False

# --- [ 연결 관리 전용 클래스 설계 ] ---
class AndroidConnector:
    """Android(순수 ADB 디바이스) 연결 및 초기화를 담당하는 클래스"""
    def __init__(self, controller):
        self.controller = controller

    def connect(self, categories):
        # Android 타겟 키워드가 장치관리자에 잡혀있을 때만 진행
        if 'Android' not in categories:
            return []

        devices = []
        try:
            adb_devices = self.controller.client.devices()
        except Exception as e:
            logging.error(f"Android ADB 연결 시도 중 에러: {e}")
            if 'WinError 10061' in str(e):
                start_adb_server()
                time.sleep(1)
                try:
                    adb_devices = self.controller.client.devices()
                except Exception as retry_e:
                    logging.error(f"ADB 재시작 후에도 연결 실패: {retry_e}")
                    return []
            else:
                return []

        for adb_obj in adb_devices:
            dev_info = {
                'detected_type': 'Android',
                'ppadb_device': adb_obj,
                'u2_device': None,
                'lockdown_device': None
            }
            # 상세 모델명 정보 추가
            dev_info.update(get_detailed_devices(dev_info))
            
            # uiautomator2 동적 연결
            try:
                dev_info['u2_device'] = u2.connect(adb_obj.serial)
                #print(f"connected to android device: {adb_obj.serial}")
            except Exception as u2_err:
                logging.error(f"Android [{adb_obj.serial}] u2 동적 연결 실패: {u2_err}")
                
            devices.append(dev_info)
        return devices


class TWDConnector:
    """TWD 장비의 시리얼 포트 모드 스위칭 및 ADB 연결을 담당하는 클래스"""
    def __init__(self, controller):
        self.controller = controller

    def connect(self, categories):
        if not any(cat in categories for cat in ['twd_com', 'twd_adb']):
            return []

        devices = []
        adb_devices = []
        
        # 1차 시도: 이미 ADB 모드로 활성화되어 있는 TWD 기기 수집
        try:
            adb_devices = self.controller.client.devices()
        except Exception:
            pass

        # 2차 시도: ADB가 비어있고 장치관리자에 twd 시리얼 포트가 감지된 경우
        if not adb_devices and 'twd_com' in categories:
            print('failed to twd via adb \n start to connect via port and activate adb mode')
            port = get_serial_ports()
            if port:
                print('port for twd in PC \n start adb mode')
                set_adb_mode(port)
                time.sleep(1.5)  # 모드 전환 시간 보장
                try:
                    adb_devices = self.controller.client.devices()
                except Exception as e:
                    logging.error(f"TWD 시리얼 전환 후 ADB 재시도 실패: {e}")

        # TWD 장치 맵핑
        for adb_obj in adb_devices:
            dev_info = {
                'detected_type': 'twd_adb',
                'ppadb_device': adb_obj,
                'u2_device': None,
                'lockdown_device': None
            }
            dev_info.update(get_detailed_devices(dev_info))
            
            try:
                dev_info['u2_device'] = u2.connect(adb_obj.serial)
                print(f"connected to twd device: {adb_obj.serial}")
            except Exception as u2_err:
                logging.error(f"TWD [{adb_obj.serial}] u2 동적 연결 실패: {u2_err}")
                
            devices.append(dev_info)
        return devices

    
class AppleConnector:
    """iOS(아이폰/아이패드) 기기 감지 및 락다운 연결을 담당하는 클래스"""
    def connect(self, categories):
        if 'Apple' not in categories:
            return []

        devices = []
        try:
            # USB를 통한 Usbmuxd 원격 락다운 확인
            lockdown = asyncio.run(create_using_usbmux())
            if lockdown:
                print('connected to iOS')
                apple_info = {
                    'detected_type': 'Apple',
                    'ppadb_device': None,
                    'u2_device': None,
                    'lockdown_device': lockdown
                }
                apple_info.update(get_detailed_devices(apple_info))
                devices.append(apple_info)
        except NoDeviceConnectedError:
            print("[WARN] 연결된 iOS 장치를 찾을 수 없습니다. USB 케이블을 확인하세요.")
        except Exception as e:
            logging.error(f"iOS 상세 연결 오류: {e}")
            
        return devices


# --- [ 최종 메인 오케스트레이션 함수 ] ---
def discover_and_connect_device():
    """
    장치 관리자를 확인한 후, 각 전용 Connector 클래스를 활용하여
    연결된 디바이스들을 규격화된 포맷으로 반환합니다.
    """
    target_keywords = {
        'Android': ['ADB Interface'],
        'Apple': ['Apple Mobile'],
        'twd_com': ['Silicon Labs'],
        'twd_adb': ['tcc']
    }

    # 1. 장치 관리자 확인
    categories = check_device_from_device_mg(target_keywords)
    if not categories:
        logging.info("장치 관리자에서 감지된 지원 기기가 없습니다.")
        return []

    logging.info(f"감지된 타입들 리스트를 기반으로 순차 연결 시작: {categories}")
    
    connected_devices = []
    
    # 2. 공통 ADB 클라이언트 매니저 초기화
    controller = None
    if any(cat in categories for cat in ['Android', 'twd_com', 'twd_adb']):
        controller = ADBController(host="127.0.0.1", port=5037)

    # 3. 객체지향적인 커넥터 인스턴스화
    android_connector = AndroidConnector(controller)
    twd_connector = TWDConnector(controller)
    apple_connector = AppleConnector()

    # 4. 각 디바이스 유형별 연결 수행 및 수집
    connected_devices.extend(android_connector.connect(categories))
    connected_devices.extend(twd_connector.connect(categories))
    connected_devices.extend(apple_connector.connect(categories))

    logging.info(connected_devices)

    return connected_devices


# --- [ 멀티스레딩 클래스 ] ---
class ADBController:
    """ADB 클라이언트를 관리하는 메인 관리자"""
    def __init__(self, host="localhost", port=5037):
        self.client = AdbClient(host=host, port=port)

    def run_worker(self, serial_num, command):
        worker = WorkerThread(self.client, serial_num, command)
        worker.start()


class WorkerThread(threading.Thread):
    """각 디바이스 소켓 통신을 담당하는 독립 스레드"""
    def __init__(self, adb_client, serial_num, command):
        super().__init__()
        self.adb_client = adb_client
        self.serial = serial_num
        self.command = command
        self.daemon = True

    def run(self):
        device = self.adb_client.device(self.serial)
        if not device:
            print(f"[{self.serial}] 기기를 찾을 수 없습니다.")
            return

        while True:
            res = device.shell(f"{self.command}")
            print(f"[{self.serial}] 작업 중: {res.strip()}")
            time.sleep(2)


