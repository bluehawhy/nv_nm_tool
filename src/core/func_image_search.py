'''
import time
import cv2
import numpy as np

from . import loggas

logging= loggas.logger

# ================================= search image ====================================
def preprocess_to_white_bg(image):

    """특정 색상 영역만 남기고 나머지는 모두 순수 흰색(255) 배경의 그레이스케일로 변환"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # 흰색~밝은 회색 글자 추출 마스크 (필요시 조절)
    lower_val = np.array([0, 0, 100])    
    upper_val = np.array([180, 60, 255])
    mask = cv2.inRange(hsv, lower_val, upper_val)
    
    # 배경은 흰색(255), 글자 영역(mask > 0)은 검정색(0)
    result = np.full(image.shape, 255, dtype=np.uint8)
    result[mask > 0] = 0 
    
    return cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)

def find_location_by_template(device, template_path, threshold=0.5, roi=None):

    """
    [Raw 바이너리 캡처 방식 적용]
    디스크에 스크린샷을 저장하지 않고, ppadb 메모리에서 직접 바이트를 넘겨받아 템플릿 매칭합니다.
    """
    time.sleep(0.2)  # 스크롤 대기
    device_obj = device['ppadb_device']['object']
    serial = device['ppadb_device']['serial']
    
    logging.info(f'[{serial}] search {template_path} via raw binary memory (ROI: {roi})')
    start_time = time.time()
    
    try:
        # 1. ppadb를 통해 PNG 바이너리 raw 데이터를 메모리로 직접 가져옴
        raw_screenshot = device_obj.screencap()
        if not raw_screenshot:
            logging.error(f"[{serial}] 스크린샷 바이너리를 획득하지 못했습니다.")
            return None
            
        # 2. 메모리 상의 바이트 배열을 OpenCV 이미지로 즉시 디코딩 (I/O 생략)
        img_np = np.frombuffer(raw_screenshot, dtype=np.uint8)
        scene = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
        
        template = cv2.imread(template_path)
        if scene is None or template is None:
            return None

        # 3. [핵심] ROI(특정 범위) 지정 시 scene 이미지 자르기
        offset_x, offset_y = 0, 0
        if roi:
            x_start, y_start, x_end, y_end = roi
            scene = scene[y_start:y_end, x_start:x_end]
            offset_x, offset_y = x_start, y_start

        # 4. 전처리 (각각 흰색 배경의 1채널 그레이스케일 변환)
        scene_p = preprocess_to_white_bg(scene)
        template_p = preprocess_to_white_bg(template)

        # 5. 템플릿의 글자(0) 영역 바운딩 박스 계산
        coords = cv2.findNonZero(cv2.bitwise_not(template_p))
        if coords is None:
            logging.error("템플릿에서 유효한 객체를 찾을 수 없습니다.")
            return None
        tx, ty, tw, th = cv2.boundingRect(coords)

        # 디버깅용 이미지 저장 (필요 없으면 주석 처리 가능)
        cv2.imwrite('resources/info/debug_scene.png', scene_p)
        cv2.imwrite('resources/info/debug_template.png', template_p)
        
        # 6. 템플릿 매칭 실행
        res = cv2.matchTemplate(scene_p, template_p, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        # 7. 임계값 검증 및 결과 좌표 계산 (ROI 오프셋 반영)
        if max_val >= threshold:
            logging.info(f"알고리즘: CCOEFF | 일치도: {max_val:.4f}")
            logging.info(f"좌표 보정: max_loc{max_loc} + 상대위치({tx},{ty}) + ROI오프셋({offset_x},{offset_y})")
            return {
                "x": max_loc[0] + tx + (tw // 2) + offset_x, 
                "y": max_loc[1] + ty + (th // 2) + offset_y
            }
            
        return None

    except Exception as e:
        logging.error(f"[{serial}] 템플릿 매칭 중 예외 발생: {e}")
        return None
    finally:
        logging.info(f"--- 실행 정보 ---\n소요 시간: {time.time() - start_time:.4f} 초")

def find_location_by_letters(device, letters, roi=None):
    import cv2
    import numpy as np
    import easyocr
    GLOBAL_READER = easyocr.Reader(['ko', 'en'], gpu=True)
    """
    [Raw 바이너리 캡처 방식 + OCR 리더 재사용 적용]
    메모리 내부 디코딩과 전역 리더 객체 재사용으로 획기적인 속도 향상을 이뤄낸 텍스트 검출 매칭 함수입니다.
    """
    time.sleep(0.2)  # 스크롤 및 화면 안정화 대기
    device_obj = device['ppadb_device']['object']
    serial = device['ppadb_device']['serial']
    
    logging.info(f"[{serial}] search texts {letters} via raw binary memory (ROI: {roi})")
    start_time = time.time()
    found_locations = {}
    
    try:
        # 1. ppadb를 통해 스크린샷 바이너리 직접 데이터 가져옴
        raw_screenshot = device_obj.screencap()
        if not raw_screenshot:
            logging.error(f"[{serial}] 스크린샷 바이너리를 획득하지 못했습니다.")
            return found_locations
            
        # 2. 파일 저장 없이 메모리 디코딩
        img_np = np.frombuffer(raw_screenshot, dtype=np.uint8)
        img = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
        if img is None:
            return found_locations
            
        # 3. ROI(특정 범위) 지정 시 이미지 자르기
        offset_x, offset_y = 0, 0
        if roi:
            x_start, y_start, x_end, y_end = roi
            img = img[y_start:y_end, x_start:x_end]
            offset_x, offset_y = x_start, y_start

        # 4. 글자 추출 (전역으로 기선언해둔 GLOBAL_READER 인스턴스 사용)
        results = GLOBAL_READER.readtext(img)
        logging.info(f'results - {results}')
        
        # 5. 추출된 텍스트 중 찾고자 하는 글자 리스트(letters)와 매칭
        for bbox, text, prob in results:
            text_cleaned = text.strip()
            
            for target_letter in letters:
                if target_letter in found_locations:
                    continue
                    
                # [수정] 'in' 대신 '=='를 사용하여 정확히 일치하는 단어만 매칭
                if target_letter.lower() == text_cleaned.lower():
                    xs = [pt[0] for pt in bbox]
                    ys = [pt[1] for pt in bbox]
                    
                    center_x = int((min(xs) + max(xs)) / 2) + offset_x
                    center_y = int((min(ys) + max(ys)) / 2) + offset_y
                    
                    found_locations[target_letter] = {"x": center_x, "y": center_y}
                    logging.info(f"글자 발견: '{target_letter}'(정확히 일치) -> 좌표: ({center_x}, {center_y})")

        # 1개의 엘리먼트만 넘겨받았을 때 딕셔너리가 아닌 내부 좌표 딕셔너리만 반환하는 구 구조를 유지하려면
        # 기존 호출 로직에 맞춰 반환형을 점검하세요. (현재는 전체 맵 객체 반환)
        return found_locations

    except Exception as e:
        logging.error(f"[{serial}] 문자 검출 중 예외 발생: {e}")
        return found_locations
    finally:
        logging.info(f"--- 실행 정보 ---\n소요 시간: {time.time() - start_time:.4f} 초")
'''