import re

import xml.etree.ElementTree as ET

from ..core import func_device
from ..utils import loggas

logging= loggas.logger

# ================================= search items by UI ====================================
def find_location_by_UI_class(device, letters, package_name=None, exact_match=True):
    """
    device: 딕셔너리(u2_device 포함)
    letters: 찾고자 하는 특정 단어들의 리스트
    package_name: 필터링할 특정 package명 (기본값 None이면 전체 탐색)
    exact_match: True일 경우 정확히 일치하는 텍스트만 탐색 (기본값 True)
    """
    d = device["u2_device"]

    # 1. UI 계층 구조 XML 가져오기 (compressed=True로 수집 데이터량 최소화)
    try:
        xml = d.dump_hierarchy(compressed=True)
        logging.info("Successfully dumped UI hierarchy (compressed).")
    except Exception as e:
        logging.error(f"UI 덤프 획득 실패: {e}")
        return {}

    # 2. XML 문자열 파싱
    try:
        root = ET.fromstring(xml.encode('utf-8') if isinstance(xml, str) and hasattr(ET, 'LXML_VERSION') else xml)
    except Exception as e:
        logging.error(f"XML 파싱 에러: {e}")
        return {}

    # 빠른 조회를 위해 찾을 대상 단어 목록을 lower_key -> original_key 맵으로 변환
    target_map = {letter.lower(): letter for letter in letters}
    result_locations = {}

    # bounds 문자열("[left,top][right,bottom]") 빠르게 파싱하는 헬퍼 함수 (정규식 대비 3배 이상 빠름)
    def parse_center_coords(bounds_str):
        try:
            # "[100,200][300,400]" -> ["100,200", "300,400"] -> [100, 200, 300, 400]
            parts = bounds_str[1:-1].replace("][", ",").split(",")
            if len(parts) == 4:
                sx, sy, ex, ey = map(int, parts)
                return (sx + ex) // 2, (sy + ey) // 2
        except Exception:
            pass
        return None, None

    # 3. XML 전체 노드를 단 '1회'만 순회하며 모든 letters 조건 체크
    for node in root.iter("node"):
        # 찾고자 하는 타겟을 모두 찾았다면 더 이상 순회하지 않고 조기 종료 (Early Exit)
        if not target_map:
            break

        node_package = node.get("package", "")
        if package_name is not None and package_name != node_package:
            continue

        bounds = node.get("bounds", "")
        if not bounds:
            continue

        node_res_id = node.get("resource-id", "").lower()
        content_desc = node.get("content-desc", "").lower()
        text_val = node.get("text", "").lower()

        # 남아있는 target 목록들과 한 번에 비교
        found_target_key = None

        for t_lower in list(target_map.keys()):
            # [1차] 완전 일치 조건
            is_exact_match = (
                (content_desc == t_lower)
                or (text_val == t_lower)
                or (node_res_id.endswith(f"/{t_lower}"))
            )

            if is_exact_match:
                found_target_key = t_lower
                break

            # [2차] partial match 허용 시 부분 일치 조건
            if not exact_match:
                is_partial_match = (
                    (t_lower in node_res_id)
                    or (t_lower in content_desc)
                    or (t_lower in text_val)
                )
                if is_partial_match:
                    found_target_key = t_lower
                    break

        # 매칭된 요소가 있는 경우 좌표 계산 후 타겟 목록에서 제거
        if found_target_key:
            mid_x, mid_y = parse_center_coords(bounds)
            if mid_x is not None:
                result_locations[found_target_key] = {"x": mid_x, "y": mid_y}
                logging.info(f"🎯 [{found_target_key}] 매칭 성공 -> 좌표: ({mid_x}, {mid_y})")
                
                # 찾은 항목은 다음 노드 검색 대상에서 제외하여 연산 속도 점진적 증가
                del target_map[found_target_key]

    # 미매칭 항목 로깅
    for remain_target in target_map.keys():
        logging.info(f"'{remain_target}'에 매칭되는 UI 요소를 찾지 못했습니다. (패키지 필터: {package_name})")

    logging.info(result_locations)
    return result_locations

def find_setting_icon_by_UI(device):
    """
    기기 유형 및 경로 탐색 상태(RG Status)에 따라 영역을 설정하여
    package, clickable, enabled 조건을 만족하는 최적 노드의 정중앙 좌표를 반환합니다.
    """
    d = device['u2_device']
    
    try:
        # 1. 기기의 실시간 해상도 및 타입 획득
        device_type, res_x, res_y = func_device.get_device_type_and_res(device)
        detected_type = device.get('detected_type', device_type)

        # 2. X, Y 범위 조건 분기 (min/max Y 지정)
        min_x = res_x - 300
        min_y = 0
        max_y = res_y  # 기본적으로 전체 높이까지 허용

        if detected_type == 'twd_adb':
            is_rg_active = check_rg_status(device=device)
            logging.info(f"[twd_adb] RG Status (경로탐색 여부): {is_rg_active}")
            
            if not is_rg_active:
                # False: 경로 없음 -> 상단 영역만 탐색 (예: Y값 300 미만만 허용)
                max_y = 300
            else:
                # True: 경로 안내 중 -> 하단 영역만 탐색 (예: Y값 300 이상만 허용)
                min_y = res_y - 300

        logging.info(f"device type - {detected_type}, 기기 해상도: {res_x}x{res_y} | 타겟 검색 범위: X >= {min_x}, {min_y} <= Y < {max_y}")

        # 3. UI 계층 구조 XML 가져오기
        try:
            xml_str = d.dump_hierarchy(compressed=True)
            logging.info("Successfully dumped UI hierarchy (compressed).")
        except Exception as e:
            logging.error(f"UI 덤프 획득 실패: {e}")
            return None

        # 4. XML 데이터 파싱 및 조건 탐색
        root = ET.fromstring(xml_str)
        target_bounds = None
        max_score = -1

        for node in root.iter('node'):
            attrib = node.attrib
            
            if (attrib.get('clickable') != 'true' or 
                attrib.get('enabled') != 'true' or 
                attrib.get('package') != 'navis.ncn.navi'):
                continue

            bounds_str = attrib.get('bounds')
            if not bounds_str:
                continue
            
            try:
                parts = bounds_str[1:-1].replace("][", ",").split(",")
                left, top, right, bottom = map(int, parts)
            except Exception:
                continue

            # 🎯 [핵심 수정] X 조건 + Y 최소/최대 범위 조건 모두 만족하는지 확인
            if left >= min_x and (min_y <= top < max_y):
                score = right + bottom
                if score > max_score:
                    max_score = score
                    target_bounds = (left, top, right, bottom)

        # 5. 좌표 반환
        if target_bounds:
            left, top, right, bottom = target_bounds
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2
            
            logging.info(f"[필터링 성공] 타겟 Bounds: left={left}, top={top}, right={right}, bottom={bottom}")
            logging.info(f"[중앙 좌표 계산] 리턴 좌표 -> x: {center_x}, y: {center_y}")
            return {'x': center_x, 'y': center_y}
            
        logging.warning("지정한 영역 내에 조건을 만족하는 버튼이 없습니다.")

    except Exception as e:
        logging.error(f"영역 버튼 추적 중 에러 발생: {e}", exc_info=True)

    return None



def find_eng_back_button_by_UI(device):
    """
    '개발 설정' 또는 'Engineering' 타이틀 주변의 클릭 가능한 뒤로가기 버튼의 중심 좌표를 반환합니다.
    """
    d = device["u2_device"]
    package_name = "navis.ncn.navi"

    # 1. UI 계층 구조 XML 가져오기 (compressed=True)
    try:
        xml = d.dump_hierarchy(compressed=True)
        logging.info("Successfully dumped UI hierarchy (compressed).")
    except Exception as e:
        logging.error(f"UI 덤프 획득 실패: {e}")
        return None

    # 2. XML 문자열 파싱
    try:
        root = ET.fromstring(xml)
    except Exception as e:
        logging.error(f"XML 파싱 에러: {e}")
        return None

    target_titles = ("개발 설정", "Engineering")
    title_bounds = None
    candidate_buttons = []

    # 3. 단 1회 순회로 타이틀 노드와 클릭 가능 버튼 후보를 동시에 수집
    for node in root.iter("node"):
        attrib = node.attrib
        if attrib.get("package") != package_name:
            continue

        bounds_str = attrib.get("bounds", "")
        if not bounds_str:
            continue

        # 문자열 파싱
        try:
            parts = bounds_str[1:-1].replace("][", ",").split(",")
            b_start_x, b_start_y, b_end_x, b_end_y = map(int, parts)
        except Exception:
            continue

        text_val = attrib.get("text", "")
        content_desc = attrib.get("content-desc", "")

        # 3-1. 타이틀 노드 발견 (최초 1회만 기록)
        if title_bounds is None:
            if any(title in text_val or title in content_desc for title in target_titles):
                title_bounds = (b_start_x, b_start_y, b_end_x, b_end_y)
                logging.info(f"기준 타이틀 발견: bounds=[{b_start_x},{b_start_y}][{b_end_x},{b_end_y}]")

        # 3-2. 클릭 가능한 버튼 후보 수집
        if attrib.get("clickable") == "true":
            candidate_buttons.append((b_start_x, b_start_y, b_end_x, b_end_y))

    # 타이틀을 찾지 못했으면 진행 불가
    if title_bounds is None:
        logging.info("⚠️ '개발 설정' 또는 'Engineering' 타이틀을 화면에서 찾지 못했습니다.")
        return None

    t_start_x, t_start_y, _, t_end_y = title_bounds
    valid_y_min = t_start_y - 50
    valid_y_max = t_end_y + 50

    # 4. 수집된 후보 버튼 중 타이틀 좌측 + Y축 오차 범위 내의 첫 번째 버튼 선택
    for b_start_x, b_start_y, b_end_x, b_end_y in candidate_buttons:
        if b_start_x < t_start_x and (valid_y_min <= b_start_y <= valid_y_max):
            mid_x = (b_start_x + b_end_x) // 2
            mid_y = (b_start_y + b_end_y) // 2
            logging.info(f"🎯 뒤로가기 버튼 매칭 성공: X={mid_x}, Y={mid_y}")
            return {"x": mid_x, "y": mid_y}

    logging.info("⚠️ 타이틀 좌측에서 클릭 가능한 버튼을 찾지 못했습니다.")
    return None

def find_setting_back_off_button_by_UI(device):
    """
    'Settings' 또는 '설정' 화면 상단에 위치한 
    왼쪽(뒤로가기) 버튼과 오른쪽(끄기) 버튼의 중심 좌표를 찾아 반환합니다.
    """
    d = device["u2_device"]
    package_name = "navis.ncn.navi"

    # 1. UI 계층 구조 XML 가져오기 (compressed=True)
    try:
        xml = d.dump_hierarchy(compressed=True)
    except Exception as e:
        logging.error(f"UI 덤프 획득 실패: {e}")
        return {}

    # 2. XML 문자열 파싱
    try:
        root = ET.fromstring(xml)
    except Exception as e:
        logging.error(f"XML 파싱 에러: {e}")
        return {}

    target_titles = ("Settings", "설정")
    title_y_range = None
    candidate_buttons = set()

    # 3. 단 1회 순회로 타이틀 Y축 범위 및 클릭 가능 버튼 수집
    for node in root.iter("node"):
        attrib = node.attrib
        if attrib.get("package") != package_name:
            continue

        bounds_str = attrib.get("bounds", "")
        if not bounds_str:
            continue

        # 음수 좌표 대응 Fast Parsing: "[left,top][right,bottom]" -> 정수 4개
        try:
            parts = bounds_str[1:-1].replace("][", ",").split(",")
            b_start_x, b_start_y, b_end_x, b_end_y = map(int, parts)
        except Exception:
            continue

        # 3-1. 타이틀 노드 발견 시 Y축 범위 기록 (최초 1회)
        if title_y_range is None:
            text_val = attrib.get("text", "")
            content_desc = attrib.get("content-desc", "")
            if any(title in text_val or title in content_desc for title in target_titles):
                title_y_range = (b_start_y - 40, b_end_y + 40)

        # 3-2. 클릭 가능 버튼 좌표 계산 후 수집
        if attrib.get("clickable") == "true":
            mid_x = (b_start_x + b_end_x) // 2
            mid_y = (b_start_y + b_end_y) // 2
            candidate_buttons.add((mid_x, mid_y))

    # 타이틀 영역을 찾지 못한 경우
    if title_y_range is None:
        logging.info("⚠️ 화면에서 'Settings' 또는 '설정' 영역을 찾지 못했습니다.")
        return {}

    valid_y_min, valid_y_max = title_y_range

    # 4. Y축 유효 범위 내의 버튼들만 필터링
    valid_buttons = [
        (x, y) for x, y in candidate_buttons 
        if valid_y_min <= y <= valid_y_max
    ]

    # 5. X축 기준으로 정렬하여 가장 왼쪽/오른쪽 버튼 추출
    if len(valid_buttons) >= 2:
        valid_buttons.sort(key=lambda pos: pos[0])
        
        back_pos = valid_buttons[0]   # 가장 왼쪽 (뒤로가기)
        off_pos = valid_buttons[-1]   # 가장 오른쪽 (끄기)

        result = {
            "ui_set_back": {"x": back_pos[0], "y": back_pos[1]},
            "ui_set_off": {"x": off_pos[0], "y": off_pos[1]}
        }
        logging.info(f"🎯 버튼 검출 완료: {result}")
        return result

    logging.info(f"⚠️ 매칭되는 버튼 개수가 부족합니다. (발견된 버튼 수: {len(valid_buttons)})")
    return {}

def find_eng_swith_text_by_UI(device, letters, package_name="navis.ncn.navi"): 
    """
    지정한 letters 리스트의 각 항목을 기준으로 하부(동일 수평선상)의 위젯 좌표를 찾습니다.
    - 반환되는 딕셔너리의 Key는 모두 소문자로 변환되어 저장됩니다.
    - Switch와 EditText가 모두 있으면 Switch를 우선 리턴
    - 둘 중 하나만 있으면 해당 위젯 리턴
    - 없으면 기준 글자(letter) 자체의 좌표를 리턴
    """
    d = device["u2_device"]
    
    # 1. UI 계층 구조 XML 가져오기 (compressed=True)
    try:
        xml = d.dump_hierarchy(compressed=True)
        root = ET.fromstring(xml)
        logging.info("Successfully dumped and parsed UI hierarchy (compressed).")
    except Exception as e:
        logging.error(f"UI 덤프 획득 또는 파싱 실패: {e}")
        return {}

    # Fast Bounds Parsing 함수 (음수 대응)
    def parse_bounds(bounds_str):
        try:
            parts = bounds_str[1:-1].replace("][", ",").split(",")
            x1, y1, x2, y2 = map(int, parts)
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            return x1, y1, x2, y2, cx, cy
        except Exception:
            return None

    all_nodes_cache = []
    target_widgets = []

    # 2. XML 단 1회 순회: 전체 노드 캐싱 및 타겟 위젯(Switch/EditText) 선별
    for node in root.iter("node"):
        attrib = node.attrib
        if package_name and attrib.get("package") != package_name:
            continue

        bounds_str = attrib.get("bounds", "")
        if not bounds_str:
            continue

        parsed = parse_bounds(bounds_str)
        if not parsed:
            continue

        cls = attrib.get("class", "")
        text_val = attrib.get("text", "").lower()
        desc_val = attrib.get("content-desc", "").lower()

        node_info = {
            "text": text_val,
            "desc": desc_val,
            "parsed_bounds": parsed, # (x1, y1, x2, y2, cx, cy)
        }
        all_nodes_cache.append(node_info)

        # Switch 또는 EditText 위젯을 별도로 수집
        if cls in ("android.widget.Switch", "android.widget.EditText"):
            target_widgets.append((cls, parsed[4], parsed[5])) # (class, cx, cy)

    result = {}

    # 3. letters 리스트 처리 (캐싱된 데이터를 기반으로 빠르게 탐색)
    for letter in letters:
        lower_letter = letter.lower()
        base_node_info = None

        # 캐싱된 노드에서 기준 글자 찾기
        for node_info in all_nodes_cache:
            if lower_letter in node_info["text"] or lower_letter in node_info["desc"]:
                base_node_info = node_info
                break

        if base_node_info is None:
            logging.warning(f"❌ '{letter}' 노드를 화면에서 찾을 수 없습니다.")
            continue

        _, b_y1, _, b_y2, base_cx, base_cy = base_node_info["parsed_bounds"]
        valid_y_min = b_y1 - 50
        valid_y_max = b_y2 + 50

        found_switch = None
        found_edittext = None

        # 수집해둔 Switch / EditText 위젯 목록만 스캔
        for cls, w_cx, w_cy in target_widgets:
            if valid_y_min <= w_cy <= valid_y_max:
                if cls == "android.widget.Switch":
                    found_switch = {"x": w_cx, "y": w_cy}
                elif cls == "android.widget.EditText":
                    found_edittext = {"x": w_cx, "y": w_cy}

        # 우선순위 적용 (Switch > EditText > 기준 노드 좌표)
        if found_switch:
            result[lower_letter] = found_switch
            logging.info(f" [{lower_letter}] -> Switch 매칭 완료: {found_switch}")
        elif found_edittext:
            result[lower_letter] = found_edittext
            logging.info(f" [{lower_letter}] -> EditText 매칭 완료: {found_edittext}")
        else:
            result[lower_letter] = {"x": base_cx, "y": base_cy}
            logging.info(f"ℹ [{lower_letter}] -> 하부 위젯 없음, 원래 좌표 사용: {result[lower_letter]}")

    return result

# 정규식 패턴을 모듈/함수 외부에서 미리 컴파일하여 실행 시 오버헤드 제거
DISTANCE_PATTERN = re.compile(r'\d+(\.\d+)?\s*[\n\s]*(m|km)')
def check_rg_status(device):
    """
    3가지 조건이 동시에 모두 만족(AND)할 때만 경로 안내 중(True)으로 판단하는 함수
    (XML 계층 구조 메모리 검색 방식 적용으로 속도 최적화)
    
    :param device: uiautomator2 연결 객체 (d 또는 device['u2_device'])
    :return: 3가지 조건 모두 만족 시 True, 하나라도 미달 시 False
    """
    d = device['u2_device'] if isinstance(device, dict) and 'u2_device' in device else device
    
    try:
        cond_tbt_structure = False
        cond_text_pattern = False
        cond_arrival_text = False

        # 1. UI 계층 구조 XML 가져오기 (compressed=True)
        try:
            xml_str = d.dump_hierarchy(compressed=True)
            logging.info("Successfully dumped UI hierarchy (compressed).")
            logging.info(f"{xml_str}")
            
        except Exception as e:
            logging.error(f"UI 덤프 획득 실패: {e}")
            return False

        root = ET.fromstring(xml_str)

        # 2. XML 트리 단 1회 순회
        for node in root.iter('node'):
            attrib = node.attrib
            
            if attrib.get('package') != 'navis.ncn.navi':
                continue

            node_class = attrib.get('class')
            desc = attrib.get('content-desc', '')
            text = attrib.get('text', '')

            # ---------------------------------------------------------------------
            # [신규] 경로 활성화 상태 우선 검사 ("상세경로" 또는 "Route Details" 감지)
            # ---------------------------------------------------------------------
            if any(keyword in text or keyword in desc for keyword in ['상세경로', 'Route Details']):
                logging.info("✨ [경로 활성화 확인] '상세경로' 또는 'Route Details' 감지됨. 경로 활성화 상태로 판정합니다.")
                return True  # 또는 필요한 플래그 변수 설정

            # ---------------------------------------------------------------------
            # 조건 1: TBT 안내 레이아웃 구조 (clickable View -> 하위 ImageView) 검사
            # ---------------------------------------------------------------------
            if not cond_tbt_structure and node_class == 'android.view.View' and attrib.get('clickable') == 'true':
                # direct/sub-children 검사 (node.iter 이용하되 내부는 빠르게 break)
                for child in node.iter('node'):
                    if child is not node and child.attrib.get('class') == 'android.widget.ImageView':
                        cond_tbt_structure = True
                        logging.info("✨ [조건 1  충족] 상단 TBT 안내 레이아웃 구조 확인.")
                        break

            # ---------------------------------------------------------------------
            # 조건 2: m/km 안내 케이스 정규식 매칭 검사
            # ---------------------------------------------------------------------
            if not cond_text_pattern and node_class == 'android.view.View' and desc:
                if ('\n' in desc) and DISTANCE_PATTERN.search(desc):
                    cond_text_pattern = True
                    logging.info(f"✨ [조건 2 충족] 경로 안내 텍스트 확인: {desc.replace('\n', ' ')}")

            # ---------------------------------------------------------------------
            # 조건 3: 하단 요약 트레이 '도착' 키워드 감지 검사
            # ---------------------------------------------------------------------
            if not cond_arrival_text and (desc in ['도착', 'Arrival'] or text in ['도착', 'Arrival']):
                cond_arrival_text = True
                logging.info("✨ [조건 3 충족] '도착' 또는 'Arrival' 문구 확인.")

            # 💡 Early Exit: 순회 중간에 3개 조건이 모두 채워지면 즉시 종료
            if cond_tbt_structure and cond_text_pattern and cond_arrival_text:
                logging.info("🎯 [상태 확인] 3가지 조건 모두 AND 만족 -> 현재 확실한 [경로 안내 중] 상태입니다.")
                return True

        # 3. 미달 원인 로깅
        if not cond_tbt_structure:
            logging.info("❌ [조건 1 미달] 상단 TBT 안내 레이아웃 구조가 발견되지 않았습니다.")
        if not cond_text_pattern:
            logging.info("❌ [조건 2 미달] 경로 안내 정규식 패턴(m/km/방면)과 일치하는 텍스트가 없습니다.")
        if not cond_arrival_text:
            logging.info("❌ [조건 3 미달] 하단 트레이에서 '도착' 텍스트를 찾을 수 없습니다.")

    except Exception as e:
        logging.error(f"경로 상태 체크 중 예외 발생: {e}", exc_info=True)
        return False

    return False