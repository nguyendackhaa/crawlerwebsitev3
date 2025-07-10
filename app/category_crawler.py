import os
import re
import zipfile
import traceback
import pandas as pd
import openpyxl
import shutil
import concurrent.futures
import threading
import queue
import time
import math
import json
from datetime import datetime
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
from openpyxl.utils import get_column_letter
from flask import current_app, session
from . import utils
from .utils import is_valid_url
from .crawler import is_category_url, is_product_url, extract_product_info

# Import Gemini API
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("⚠️ Google Generative AI không khả dụng. Sẽ sử dụng dictionary mapping.")

# Biến toàn cục để lưu trữ đối tượng socketio
_socketio = None
# Lock cho việc cập nhật tiến trình
_progress_lock = threading.Lock()
# Lock cho việc in thông báo
_print_lock = threading.Lock()

def update_socketio(socketio):
    """Cập nhật đối tượng socketio toàn cục"""
    global _socketio
    _socketio = socketio

def emit_progress(percent, message, log=None):
    """Gửi cập nhật tiến trình qua socketio"""
    global _socketio
    with _progress_lock:
        if _socketio:
            # Nếu có thông tin log, thêm vào payload
            payload = {
                'percent': percent,
                'message': message
            }
            if log:
                payload['log'] = log
                
            _socketio.emit('progress_update', payload)
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] [TIẾN TRÌNH {percent}%] {message}")
            if log:
                log_type = "CHI TIẾT"
                if "[THÀNH CÔNG]" in log or "[OK]" in log:
                    log_type = "THÀNH CÔNG"
                elif "[LỖI]" in log or "[CẢNH BÁO]" in log:
                    log_type = "LỖI"
                elif "[PHÂN TÍCH]" in log:
                    log_type = "PHÂN TÍCH"
                elif "[KẾT QUẢ]" in log:
                    log_type = "KẾT QUẢ"
                elif "[CRAWLER]" in log:
                    log_type = "CRAWLER"
                print(f"[{timestamp}] [{log_type}] {log}")

def safe_print(message):
    """Hàm in an toàn cho đa luồng"""
    with _print_lock:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")

def log_and_emit(message):
    """Ghi log và phát đi sự kiện với thông tin log"""
    with _print_lock:
        # Lấy phần trăm tiến trình hiện tại (không thay đổi)
        progress = getattr(log_and_emit, 'last_progress', 0)
        # Lấy thông báo tiến trình hiện tại (không thay đổi)
        status_message = getattr(log_and_emit, 'last_message', 'Đang xử lý...')
        
        # Phát đi sự kiện với thông tin log
        emit_progress(progress, status_message, message)
        
        # Đã in trong emit_progress, không cần in thêm

# Lưu trữ tiến trình hiện tại để log_and_emit có thể sử dụng
def update_progress(percent, message):
    """Cập nhật tiến trình hiện tại và lưu lại để sử dụng cho log"""
    log_and_emit.last_progress = percent
    log_and_emit.last_message = message
    emit_progress(percent, message)
    
    # In thêm vào terminal với thời gian - đã in trong emit_progress, không cần in nữa

class CategoryCrawler:
    def __init__(self, socketio, upload_folder=None, selected_fields=None):
        """Khởi tạo CategoryCrawler với socketio instance và upload_folder"""
        self.socketio = socketio
        update_socketio(socketio)
        
        # Xử lý các trường được chọn để cào
        self.selected_fields = selected_fields or []
        
        # Mapping giữa field value và tên cột trong Excel
        self.field_mapping = {
            'product_name': 'Tên sản phẩm',
            'product_code': 'Mã sản phẩm', 
            'specifications': 'Tổng quan',
            'price': 'Giá',
            'product_image': 'Ảnh sản phẩm',
            'url': 'URL'
        }
        
        # Log thông tin về các trường được chọn
        if self.selected_fields:
            selected_names = [self.field_mapping.get(field, field) for field in self.selected_fields]
            log_and_emit(f"[INIT] ✅ Các trường sẽ được cào: {', '.join(selected_names)}")
        else:
            log_and_emit(f"[INIT] ⚠️ Không có trường nào được chỉ định, sẽ cào tất cả trường")
        
        # Tối ưu hóa đa luồng - tăng số lượng worker
        import os
        cpu_count = os.cpu_count() or 4
        self.max_workers = min(32, cpu_count * 4)  # Tối đa 32 workers, hoặc 4x CPU cores
        self.max_workers_download = min(16, cpu_count * 2)  # Riêng cho download
        self.max_workers_parse = min(24, cpu_count * 3)  # Riêng cho parse HTML
        
        # Cài đặt thời gian chờ tối ưu
        self.request_delay = 0.1  # Giảm delay từ 0.2 xuống 0.1
        self.batch_size = 50  # Tăng batch size từ 20 lên 50
        
        # Connection pooling và session management
        self.session_pool = {}  # Pool các session cho từng domain
        self.max_sessions_per_domain = 8
        
        # Tạo semaphore để kiểm soát số lượng request đồng thời đến cùng một domain
        self.domain_semaphores = {}
        self.max_concurrent_per_domain = 10  # Tăng từ mặc định lên 10
        
        # Lưu đường dẫn upload_folder
        self.upload_folder = upload_folder
        
        # Timeout tối ưu
        self.request_timeout = 15  # Giảm từ 20 xuống 15 giây
        self.max_retries = 2  # Giảm từ 3 xuống 2 lần thử
        self.retry_delay = 0.5  # Giảm từ 1 xuống 0.5 giây
        
        # Cache để tránh request trùng lặp
        self.url_cache = {}
        self.response_cache = {}
        
        # Khởi tạo Gemini API
        self._setup_gemini_api()
        
        # Dictionary fallback cho trường hợp Gemini không khả dụng
        self.fallback_translation_dict = {
            # Thuật ngữ tiếng Trung quan trọng nhất
            '類型感測器': 'Loại cảm biến',
            '輸出方法': 'Phương thức đầu ra',
            '控制方法': 'Phương thức điều khiển',
            '比例帶': 'Dải tỷ lệ',
            '週期時間': 'Thời gian chu kỳ',
            '手動重置': 'Đặt lại thủ công',
            '單位': 'Đơn vị',
            '設定方法': 'Phương thức cài đặt',
            '設定範圍': 'Phạm vi cài đặt',
            '工作電壓': 'Điện áp làm việc',
            '耗電流': 'Dòng điện tiêu thụ',
            '絕緣電阻': 'Điện trở cách điện',
            '耐壓強度': 'Cường độ chịu điện áp',
            '工作環境': 'Môi trường làm việc',
            '耐振動': 'Khả năng chịu rung động',
            '面板厚度': 'Độ dày bảng điều khiển',
            
            # Thuật ngữ tiếng Anh quan trọng nhất - TÊN SẢN PHẨM
            'Temperature Controller': 'Bộ điều khiển nhiệt độ',
            'Pressure Controller': 'Bộ điều khiển áp suất',
            'Flow Controller': 'Bộ điều khiển lưu lượng',
            'Level Controller': 'Bộ điều khiển mức',
            'Speed Controller': 'Bộ điều khiển tốc độ',
            'Sensor': 'Cảm biến',
            'Temperature Sensor': 'Cảm biến nhiệt độ',
            'Pressure Sensor': 'Cảm biến áp suất',
            'Proximity Sensor': 'Cảm biến tiệm cận',
            'Photo Sensor': 'Cảm biến quang',
            'Ultrasonic Sensor': 'Cảm biến siêu âm',
            'Inductive Sensor': 'Cảm biến cảm ứng',
            'Capacitive Sensor': 'Cảm biến điện dung',
            'Magnetic Sensor': 'Cảm biến từ',
            'Flow Sensor': 'Cảm biến lưu lượng',
            'Level Sensor': 'Cảm biến mức',
            'Vibration Sensor': 'Cảm biến rung động',
            'TC Series': 'Dòng TC',
            'Series': 'Dòng',
            'Controller': 'Bộ điều khiển',
            'Temperature': 'Nhiệt độ',
            'Sensor Type': 'Loại cảm biến',
            'Output Method': 'Phương thức đầu ra',
            'Control Method': 'Phương thức điều khiển',
            'Operating Voltage': 'Điện áp làm việc',
            'Working Environment': 'Môi trường làm việc',
            'Panel Thickness': 'Độ dày bảng điều khiển',
            'Digital': 'Số',
            'Analog': 'Tương tự',
            'Switch': 'Công tắc',
            'Relay': 'Relay',
            'Timer': 'Bộ định thời',
            'Counter': 'Bộ đếm',
            'Display': 'Màn hình',
            'Indicator': 'Đèn báo',
            'Alarm': 'Báo động',
            'Monitor': 'Giám sát',
            'Transmitter': 'Bộ truyền',
            'Converter': 'Bộ chuyển đổi',
            'Amplifier': 'Bộ khuếch đại',
            'Signal': 'Tín hiệu',
            'Input': 'Đầu vào',
            'Output': 'Đầu ra',
            'Module': 'Module',
            'Unit': 'Đơn vị',
            'Device': 'Thiết bị',
            'Instrument': 'Thiết bị đo',
            'Meter': 'Đồng hồ đo',
            'Gauge': 'Đồng hồ',
            'Detector': 'Bộ phát hiện',
            'Actuator': 'Bộ truyền động',
            'Valve': 'Van',
            'Motor': 'Động cơ',
            'Pump': 'Bơm',
            'Fan': 'Quạt',
            'Heater': 'Máy sưởi',
            'Cooler': 'Máy làm mát',
            'Conditioner': 'Bộ điều hòa',
            'Regulator': 'Bộ điều chỉnh',
            'Stabilizer': 'Bộ ổn định',
            'Protection': 'Bảo vệ',
            'Safety': 'An toàn',
            'Security': 'Bảo mật',
            'Automatic': 'Tự động',
            'Manual': 'Thủ công',
            'Electric': 'Điện',
            'Electronic': 'Điện tử',
            'Mechanical': 'Cơ khí',
            'Pneumatic': 'Khí nén',
            'Hydraulic': 'Thủy lực',
            'Industrial': 'Công nghiệp',
            'Commercial': 'Thương mại',
            'Professional': 'Chuyên nghiệp',
            'Standard': 'Chuẩn',
            'Premium': 'Cao cấp',
            'Advanced': 'Nâng cao',
            'Basic': 'Cơ bản',
            'Compact': 'Nhỏ gọn',
            'Portable': 'Di động',
            'Fixed': 'Cố định',
            'Wireless': 'Không dây',
            'Wired': 'Có dây',
            'Smart': 'Thông minh',
            'Intelligent': 'Thông minh',
        }
        
        log_and_emit(f"[INIT] ⚡ Đã tối ưu hóa đa luồng: {self.max_workers} workers, batch_size={self.batch_size}")

    def _get_session_for_domain(self, url):
        """Lấy session tối ưu cho domain, với connection pooling"""
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            
            if domain not in self.session_pool:
                self.session_pool[domain] = []
            
            # Tìm session available hoặc tạo mới nếu chưa đạt max
            for session in self.session_pool[domain]:
                if not getattr(session, '_in_use', False):
                    session._in_use = True
                    return session
            
            # Tạo session mới nếu chưa đạt max
            if len(self.session_pool[domain]) < self.max_sessions_per_domain:
                import requests
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'vi-VN,vi;q=0.8,en-US;q=0.5,en;q=0.3',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                })
                # Cấu hình connection pooling
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10,
                    pool_maxsize=20,
                    max_retries=0  # Chúng ta tự xử lý retry
                )
                session.mount('http://', adapter)
                session.mount('https://', adapter)
                
                session._in_use = True
                self.session_pool[domain].append(session)
                return session
            
            # Nếu đã đạt max, chờ session available (không blocking)
            return self.session_pool[domain][0]  # Fallback về session đầu tiên
            
        except Exception as e:
            log_and_emit(f"[SESSION] ❌ Lỗi tạo session: {str(e)}")
            # Fallback về requests thông thường
            import requests
            return requests

    def _release_session(self, session):
        """Giải phóng session sau khi sử dụng"""
        try:
            if hasattr(session, '_in_use'):
                session._in_use = False
        except:
            pass

    def _setup_gemini_api(self):
        """Khởi tạo Gemini API với API key từ environment variables"""
        self.gemini_model = None
        
        if not GEMINI_AVAILABLE:
            log_and_emit("[GEMINI] Google Generative AI không khả dụng")
            return
            
        try:
            # Lấy API key từ environment variables
            api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                # Thử lấy từ file cấu hình nếu có
                config_file = os.path.join(os.path.dirname(__file__), '..', 'config.json')
                if os.path.exists(config_file):
                    with open(config_file, 'r') as f:
                        config = json.load(f)
                        api_key = config.get('gemini_api_key')
            
            if api_key:
                genai.configure(api_key=api_key)
                self.gemini_model = genai.GenerativeModel('gemini-pro')
                log_and_emit("[GEMINI] ✅ Đã khởi tạo Gemini API thành công")
            else:
                log_and_emit("[GEMINI] ⚠️ Không tìm thấy GEMINI_API_KEY, sẽ sử dụng dictionary fallback")
                
        except Exception as e:
            log_and_emit(f"[GEMINI] ❌ Lỗi khởi tạo Gemini API: {str(e)}")
            self.gemini_model = None

    def _translate_tech_terms_with_gemini(self, specs_data):
        """
        Sử dụng Gemini API để dịch thông số kỹ thuật từ tiếng Anh/Trung sang tiếng Việt
        
        Args:
            specs_data (list): Danh sách tuple (param, value)
            
        Returns:
            list: Danh sách tuple đã được dịch sang tiếng Việt
        """
        if not specs_data or not self.gemini_model:
            return self._translate_specs_table_data_fallback(specs_data)
            
        try:
            # Tạo prompt cho Gemini
            specs_text = ""
            for i, (param, value) in enumerate(specs_data):
                specs_text += f"{i+1}. {param}: {value}\n"
            
            prompt = f"""
Bạn là chuyên gia dịch thuật ngữ kỹ thuật điện tử và tự động hóa. Hãy dịch các thông số kỹ thuật sau đây từ tiếng Anh/Trung sang tiếng Việt một cách chính xác và chuyên nghiệp.

YÊU CẦU:
- Dịch CHÍNH XÁC thuật ngữ kỹ thuật
- Giữ nguyên các giá trị số, ký hiệu, đơn vị (°C, VAC, Hz, A, Ω, v.v.)
- Giữ nguyên các ký hiệu đặc biệt và mã model
- Trả về theo định dạng JSON với cấu trúc: {{"translated_specs": [["tham_số_tiếng_việt", "giá_trị"], ...]}}

THÔNG SỐ CẦN DỊCH:
{specs_text}

VÍ DỤ:
Input: "類型感測器: K, J, E, T, R, B, S, N, C, L, U, PLII"
Output: "Loại cảm biến: K, J, E, T, R, B, S, N, C, L, U, PLII"

Input: "Operating Voltage: 110/220 VAC±20% 50/60Hz" 
Output: "Điện áp làm việc: 110/220 VAC±20% 50/60Hz"

Hãy dịch tất cả các thông số trên và trả về JSON:
"""

            # Gọi Gemini API
            response = self.gemini_model.generate_content(prompt)
            response_text = response.text.strip()
            
            # Parse JSON response
            try:
                # Tìm và extract JSON từ response
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    result = json.loads(json_str)
                    
                    if 'translated_specs' in result and isinstance(result['translated_specs'], list):
                        translated_specs = result['translated_specs']
                        log_and_emit(f"[GEMINI] ✅ Đã dịch {len(translated_specs)} thông số bằng AI")
                        return translated_specs
                    
            except json.JSONDecodeError as e:
                log_and_emit(f"[GEMINI] ❌ Lỗi parse JSON: {str(e)}")
                
            # Nếu không parse được JSON, thử parse text thông thường
            return self._parse_gemini_text_response(response_text, specs_data)
            
        except Exception as e:
            log_and_emit(f"[GEMINI] ❌ Lỗi gọi API: {str(e)}")
            return self._translate_specs_table_data_fallback(specs_data)

    def _parse_gemini_text_response(self, response_text, original_specs):
        """Parse response text từ Gemini khi không có JSON format"""
        try:
            lines = response_text.split('\n')
            translated_specs = []
            
            for line in lines:
                line = line.strip()
                if ':' in line and (line[0].isdigit() or '.' in line[:3]):
                    # Loại bỏ số thứ tự
                    content = re.sub(r'^\d+\.\s*', '', line)
                    if ':' in content:
                        param, value = content.split(':', 1)
                        translated_specs.append((param.strip(), value.strip()))
            
            if len(translated_specs) == len(original_specs):
                log_and_emit(f"[GEMINI] ✅ Parse text thành công: {len(translated_specs)} thông số")
                return translated_specs
            else:
                log_and_emit(f"[GEMINI] ⚠️ Số lượng không khớp, dùng fallback")
                return self._translate_specs_table_data_fallback(original_specs)
                
        except Exception as e:
            log_and_emit(f"[GEMINI] ❌ Lỗi parse text: {str(e)}")
            return self._translate_specs_table_data_fallback(original_specs)

    def _translate_specs_table_data_fallback(self, specs_data):
        """
        Fallback method sử dụng dictionary mapping khi Gemini không khả dụng
        
        Args:
            specs_data (list): Danh sách tuple (param, value)
            
        Returns:
            list: Danh sách tuple đã được dịch sang tiếng Việt
        """
        if not specs_data:
            return specs_data
            
        translated_specs = []
        for param, value in specs_data:
            # Dịch tham số bằng dictionary
            translated_param = param
            for original_term, vietnamese_term in self.fallback_translation_dict.items():
                if original_term.lower() in param.lower():
                    translated_param = param.replace(original_term, vietnamese_term)
                    break
            
            # Dịch giá trị (ít cần thiết hơn)
            translated_value = value
            for original_term, vietnamese_term in self.fallback_translation_dict.items():
                if original_term.lower() in value.lower():
                    translated_value = value.replace(original_term, vietnamese_term)
                    break
                    
            translated_specs.append((translated_param, translated_value))
            
        return translated_specs
        
    def process_category_urls(self, category_urls_text):
        """Xử lý danh sách URL danh mục và trả về kết quả"""
        try:
            # Tách thành danh sách URL, bỏ qua dòng trống
            raw_urls = category_urls_text.strip().split('\n')
            urls = [url.strip() for url in raw_urls if url.strip()]
            
            # Kiểm tra loại website để chọn phương thức xử lý phù hợp
            fotek_urls = [url for url in urls if 'fotek.com.tw' in url.lower()]
            codienhaiau_urls = [url for url in urls if 'codienhaiau.com' in url.lower()]
            baa_urls = [url for url in urls if 'baa.vn' in url.lower()]
            other_urls = [url for url in urls if not any(domain in url.lower() for domain in ['fotek.com.tw', 'codienhaiau.com', 'baa.vn'])]
            
            # Xử lý theo từng loại website
            results = []
            
            # Xử lý Fotek.com.tw
            if fotek_urls:
                log_and_emit(f"[FOTEK] Phát hiện {len(fotek_urls)} URL từ Fotek.com.tw")
                try:
                    success, message, zip_path = self.process_fotek_categories('\n'.join(fotek_urls))
                    results.append(f"Fotek: {message}")
                except Exception as e:
                    results.append(f"Fotek: Lỗi - {str(e)}")
            
            # Xử lý codienhaiau.com
            if codienhaiau_urls:
                log_and_emit(f"[CODIENHAIAU] Phát hiện {len(codienhaiau_urls)} URL từ codienhaiau.com")
                try:
                    success, message, zip_path = self.process_codienhaiau_categories('\n'.join(codienhaiau_urls))
                    results.append(f"Codienhaiau: {message}")
                except Exception as e:
                    results.append(f"Codienhaiau: Lỗi - {str(e)}")
            
            # Xử lý BAA.vn
            if baa_urls:
                log_and_emit(f"[BAA] Phát hiện {len(baa_urls)} URL từ BAA.vn")
                try:
                    success, message, zip_path = self.process_baa_categories('\n'.join(baa_urls))
                    results.append(f"BAA: {message}")
                except Exception as e:
                    results.append(f"BAA: Lỗi - {str(e)}")
            
            # Xử lý các website khác bằng phương pháp chung
            if other_urls:
                log_and_emit(f"[GENERIC] Phát hiện {len(other_urls)} URL từ các website khác")
                # Sử dụng logic xử lý cũ cho các URL khác
                success, message = self._process_generic_categories(other_urls)
                results.append(f"Generic: {message}")
            
            # Tổng hợp kết quả
            if results:
                final_message = " | ".join(results)
                return True, final_message
            else:
                return False, "Không có URL hợp lệ để xử lý"
                
        except Exception as e:
            error_message = str(e)
            traceback.print_exc()
            return False, f'Lỗi: {error_message}'

    def _process_generic_categories(self, urls):
        """Xử lý các URL danh mục bằng phương pháp chung (logic cũ)"""
        try:
            # Lọc các URL hợp lệ
            valid_urls = []
            invalid_urls = []
            
            # Gửi thông báo bắt đầu
            emit_progress(0, 'Đang kiểm tra URL danh mục...')
            
            # Kiểm tra các URL
            for url in urls:
                if is_valid_url(url) and is_category_url(url):
                    valid_urls.append(url)
                else:
                    invalid_urls.append(url)
            
            if not valid_urls:
                raise ValueError('Không có URL danh mục hợp lệ!')
                
            # Gửi thông báo cập nhật
            emit_progress(5, f'Đã tìm thấy {len(valid_urls)} URL danh mục hợp lệ')
            
            # Tạo thư mục chính để lưu kết quả
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            result_dir = os.path.join(self.upload_folder, f'category_info_{timestamp}') if self.upload_folder else f'category_info_{timestamp}'
            os.makedirs(result_dir, exist_ok=True)
            
            # Xử lý từng URL danh mục
            category_info = []
            
            for i, category_url in enumerate(valid_urls):
                try:
                    category_progress_base = 5 + int((i / len(valid_urls)) * 85)
                    emit_progress(category_progress_base, f'Đang xử lý danh mục {i+1}/{len(valid_urls)}: {category_url}')
                    
                    # Trích xuất tên danh mục từ URL
                    category_name = self._extract_category_name(category_url)
                    
                    # Tạo thư mục cho danh mục này
                    category_dir = os.path.join(result_dir, category_name)
                    os.makedirs(category_dir, exist_ok=True)
                    
                    # Thu thập liên kết sản phẩm từ danh mục này
                    emit_progress(category_progress_base + 5, f'Đang thu thập liên kết sản phẩm từ danh mục: {category_name}')
                    
                    category_products = self._extract_category_links([category_url])
                    
                    if category_products:
                        # Lưu các liên kết sản phẩm vào file txt
                        self._save_product_links(category_dir, category_name, category_products)
                        
                        # Thu thập thông tin sản phẩm
                        product_info_list = self._collect_product_info(category_dir, category_name, category_products, category_progress_base)
                        
                        # Thêm thông tin danh mục vào danh sách
                        category_info.append({
                            'Tên danh mục': category_name,
                            'URL danh mục': category_url,
                            'Số sản phẩm': len(category_products),
                            'Số sản phẩm có thông tin': len(product_info_list)
                        })
                    else:
                        print(f"Không tìm thấy sản phẩm nào trong danh mục: {category_url}")
                        
                except Exception as e:
                    print(f"Lỗi khi xử lý danh mục {category_url}: {str(e)}")
                    traceback.print_exc()
            
            # Tạo báo cáo và file nén
            self._create_reports(result_dir, category_info, valid_urls)
            
            # Tạo file ZIP
            zip_filename = f'category_info_{timestamp}.zip'
            zip_path = os.path.join(self.upload_folder, zip_filename) if self.upload_folder else zip_filename
            
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, self.upload_folder) if self.upload_folder else os.path.basename(file_path)
                        zipf.write(file_path, relative_path)
            
            # Lưu đường dẫn file ZIP vào session nếu đang chạy trong ứng dụng web
            try:
                if 'session' in globals() or 'session' in locals():
                    session['last_download'] = zip_filename
            except (ImportError, RuntimeError):
                # Không chạy trong ngữ cảnh Flask hoặc không có module Flask
                pass
            
            return True, f'Đã hoàn tất thu thập dữ liệu từ {len(valid_urls)} danh mục sản phẩm'
            
        except Exception as e:
            error_message = str(e)
            traceback.print_exc()
            return False, f'Lỗi: {error_message}'
    
    def _extract_category_name(self, url):
        """Trích xuất tên danh mục từ URL"""
        parsed_url = urlparse(url)
        url_path = parsed_url.path
        
        # Lấy phần cuối của URL làm tên danh mục
        category_name = url_path.strip('/').split('/')[-1]
        # Loại bỏ phần ID số từ tên danh mục nếu có
        category_name = re.sub(r'_\d+$', '', category_name)
        return category_name
    
    def _extract_category_links(self, urls, max_pages=None, only_first_page=False):
        """Trích xuất tất cả liên kết sản phẩm từ trang danh mục"""
        product_links = []
        visited_urls = set()
        all_pagination_urls = set()
        
        # Kiểm tra các URL đầu vào
        for url in urls:
            if not url.strip():
                continue
                
            url = url.strip()
            
            # Đảm bảo URL đầy đủ
            if not url.startswith('http'):
                url = 'http://' + url
            
            if not is_valid_url(url) or not is_category_url(url):
                continue
                
            all_pagination_urls.add(url)
        
        if not all_pagination_urls:
            return []
        
        processed_urls = 0
        total_urls = len(all_pagination_urls)
        
        while all_pagination_urls and (max_pages is None or processed_urls < max_pages):
            current_url = all_pagination_urls.pop()
            
            if current_url in visited_urls:
                continue
                
            visited_urls.add(current_url)
            processed_urls += 1
            
            try:
                # Cập nhật tiến trình
                progress = int((processed_urls / total_urls) * 100)
                emit_progress(progress, f'Đang xử lý URL {processed_urls}/{total_urls}: {current_url}')
                
                # Tải trang
                response = requests.get(current_url, headers={'User-Agent': 'Mozilla/5.0'})
                response.raise_for_status()
                
                # Parse HTML
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Xác định loại trang và xử lý tương ứng
                if "baa.vn" in current_url.lower():
                    urls_on_page, pagination_urls = self._extract_baa_links(soup, current_url)
                elif "autonics.com" in current_url.lower():
                    urls_on_page, pagination_urls = self._extract_autonics_links(soup, current_url)
                else:
                    urls_on_page, pagination_urls = self._extract_generic_links(soup, current_url)
                
                # Thêm URL sản phẩm vào danh sách kết quả
                for product_url in urls_on_page:
                    if product_url not in product_links:
                        product_links.append(product_url)
                
                # Thêm các URL phân trang vào danh sách để xử lý
                if not only_first_page:
                    for page_url in pagination_urls:
                        if page_url not in visited_urls:
                            all_pagination_urls.add(page_url)
                            total_urls += 1
                            
            except Exception as e:
                print(f"Lỗi khi xử lý URL {current_url}: {str(e)}")
                traceback.print_exc()
        
        emit_progress(90, f'Đã trích xuất xong {len(product_links)} liên kết sản phẩm')
        return product_links
    
    def _extract_baa_links(self, soup, current_url):
        """Trích xuất liên kết từ trang BAA.vn"""
        product_urls = []
        pagination_urls = []
        
        # Phân tích URL hiện tại
        current_parsed = urlparse(current_url)
        base_domain = f"{current_parsed.scheme}://{current_parsed.netloc}"
        
        # Tìm sản phẩm
        product_cards = soup.select('.product-item, .product_item, .product__card, .card.product__card')
        for card in product_cards:
            product_link = card.select_one('a')
            if product_link and product_link.has_attr('href'):
                href = product_link['href']
                full_url = self._make_full_url(href, current_url, base_domain)
                if full_url:
                    product_urls.append(full_url)
        
        # Tìm phân trang
        pagination_links = soup.select('.pagination li a[href]')
        for link in pagination_links:
            href = link.get('href')
            if href:
                full_url = self._make_full_url(href, current_url, base_domain)
                if full_url:
                    pagination_urls.append(full_url)
        
        return product_urls, pagination_urls
    
    def _extract_autonics_links(self, soup, current_url):
        """Trích xuất liên kết từ trang Autonics.com"""
        product_urls = []
        pagination_urls = []
        
        # Tìm sản phẩm
        product_links = soup.select('.product-item a[href], .product-list a[href], .product-container a[href]')
        for link in product_links:
            href = link.get('href')
            if href:
                full_url = self._make_full_url(href, current_url)
                if full_url and 'product' in full_url.lower() and not any(excluded in full_url.lower() for excluded in ['category', 'list', 'search']):
                    product_urls.append(full_url)
        
        # Tìm phân trang
        pagination_links = soup.select('.pagination a[href], .paging a[href]')
        for link in pagination_links:
            href = link.get('href')
            if href:
                full_url = self._make_full_url(href, current_url)
                if full_url:
                    pagination_urls.append(full_url)
        
        return product_urls, pagination_urls
    
    def _extract_generic_links(self, soup, current_url):
        """Trích xuất liên kết từ trang thông thường"""
        product_urls = []
        pagination_urls = []
        
        # Kiểm tra xem có phải trang Fotek.com.tw không
        if "fotek.com.tw" in current_url.lower():
            return self._extract_fotek_links(soup, current_url)
        
        # Các selector phổ biến cho sản phẩm
        product_selectors = [
            '.product-item a[href]', 
            '.product a[href]', 
            '.product-card a[href]',
            '.product_item a[href]',
            '.item.product a[href]',
            '.card.product a[href]',
            'a.product-item-link',
            'a.product-title',
            'a.product-name',
            'a[href*="product"]',
            'a[href*="san-pham"]',
            # Thêm selectors cho codienhaiau.com
            '.product-small a.woocommerce-LoopProduct-link'
        ]
        
        # Tìm sản phẩm
        combined_selector = ', '.join(product_selectors)
        product_links = soup.select(combined_selector)
        for link in product_links:
            href = link.get('href')
            if href:
                full_url = self._make_full_url(href, current_url)
                if full_url and ('product' in full_url.lower() or 'san-pham' in full_url.lower()) and not any(excluded in full_url.lower() for excluded in ['category', 'danh-muc', 'list', 'search']):
                    product_urls.append(full_url)
        
        # Tìm phân trang
        pagination_selectors = [
            '.pagination a[href]',
            '.paging a[href]',
            'a.page-link[href]',
            'a[href*="page="]',
            'a[href*="/page/"]',
            '.pages a[href]',
            # Thêm selectors cho codienhaiau.com
            'a.page-number',
            'a.next'
        ]
        
        combined_pagination_selector = ', '.join(pagination_selectors)
        pagination_elements = soup.select(combined_pagination_selector)
        for element in pagination_elements:
            href = element.get('href')
            if href:
                full_url = self._make_full_url(href, current_url)
                if full_url:
                    pagination_urls.append(full_url)
        
        return product_urls, pagination_urls
    
    def _extract_fotek_links(self, soup, current_url):
        """Trích xuất liên kết từ trang Fotek.com.tw"""
        product_urls = []
        pagination_urls = []
        
        # Phân tích URL hiện tại
        current_parsed = urlparse(current_url)
        base_domain = f"{current_parsed.scheme}://{current_parsed.netloc}"
        
        log_and_emit(f"[FOTEK] Đang phân tích trang Fotek: {current_url}")
        
        # Kiểm tra xem có phải trang danh mục chính không (chứa các danh mục con)
        category_boxes = soup.select('.box-img.r-4-3')
        if category_boxes:
            log_and_emit(f"[FOTEK] Tìm thấy {len(category_boxes)} danh mục con trong trang chính")
            
            # Đây là trang danh mục chính, trích xuất các danh mục con
            for box in category_boxes:
                overlay = box.select_one('.overlay')
                if overlay:
                    title_card = overlay.select_one('h4.title-card a.stretched-link')
                    if title_card and title_card.get('href'):
                        href = title_card['href']
                        full_url = self._make_full_url(href, current_url, base_domain)
                        if full_url:
                            # Đây là URL danh mục con, cần crawl để tìm sản phẩm
                            log_and_emit(f"[FOTEK] Tìm thấy danh mục con: {title_card.get_text(strip=True)} - {full_url}")
                            pagination_urls.append(full_url)
        else:
            # Đây có thể là trang danh mục con hoặc trang sản phẩm
            log_and_emit(f"[FOTEK] Không tìm thấy danh mục con, tìm kiếm sản phẩm...")
            
            # Tìm sản phẩm với các selector phù hợp cho Fotek
            product_selectors = [
                '.product-item a[href]',
                '.product-card a[href]', 
                '.item a[href]',
                'a[href*="/product/"]',
                'a[href*="product-"]',
                '.product-title a[href]',
                '.product-name a[href]'
            ]
            
            for selector in product_selectors:
                product_links = soup.select(selector)
                for link in product_links:
                    href = link.get('href')
                    if href:
                        full_url = self._make_full_url(href, current_url, base_domain)
                        if full_url and self._is_fotek_product_url(full_url):
                            product_urls.append(full_url)
            
            # Tìm phân trang cho Fotek
            pagination_links = soup.select('.pagination a[href], .paging a[href], a.page-link[href]')
            for link in pagination_links:
                href = link.get('href')
                if href:
                    full_url = self._make_full_url(href, current_url, base_domain)
                    if full_url:
                        pagination_urls.append(full_url)
        
        log_and_emit(f"[FOTEK] Kết quả: {len(product_urls)} sản phẩm, {len(pagination_urls)} trang/danh mục phụ")
        return product_urls, pagination_urls

    def _is_fotek_product_url(self, url):
        """Kiểm tra xem URL có phải là URL sản phẩm Fotek không"""
        try:
            if not url or not isinstance(url, str):
                return False
            
            # Kiểm tra URL có phải từ Fotek không
            if 'fotek.com.tw' not in url:
                return False
            
            # Kiểm tra URL có chứa dấu hiệu của URL sản phẩm không
            # Fotek thường có URL dạng /product/ hoặc chứa product ID
            product_indicators = ['/product/', 'product-', '/item/']
            
            for indicator in product_indicators:
                if indicator in url:
                    return True
            
            # Kiểm tra xem có phải URL danh mục không (loại trừ)
            if '/product-category/' in url or '/category/' in url:
                return False
                
            return False
        except Exception:
            return False

    def extract_fotek_product_info(self, url, index=1, output_dir=None):
        """
        Trích xuất thông tin sản phẩm từ trang Fotek.com.tw
        
        Args:
            url (str): URL của trang sản phẩm
            index (int): Số thứ tự của sản phẩm
            output_dir (str, optional): Thư mục để lưu ảnh sản phẩm
            
        Returns:
            dict: Thông tin sản phẩm đã trích xuất
        """
        # Khởi tạo kết quả với các trường cần thiết
        product_info = {
            'STT': index,
            'URL': url,
            'Mã sản phẩm': "",
            'Tên sản phẩm': "",
            'Giá': "",
            'Tổng quan': "",
            'Ảnh sản phẩm': ""
        }
        
        try:
            log_and_emit(f"[FOTEK] Đang trích xuất thông tin từ: {url}")
            
            # Tải nội dung trang với retry
            soup = None
            for attempt in range(self.max_retries):
                try:
                    soup = self._get_soup(url)
                    if soup:
                        break
                except Exception as e:
                    log_and_emit(f"[FOTEK] Lỗi khi tải trang (lần {attempt+1}/{self.max_retries}): {str(e)}")
                    if attempt < self.max_retries - 1:
                        time.sleep(1)
            
            if not soup:
                log_and_emit(f"[FOTEK] Không thể tải nội dung trang sau {self.max_retries} lần thử")
                return product_info
            
            # TRÍCH XUẤT TÊN SẢN PHẨM
            # Fotek thường có tên sản phẩm trong title hoặc h1
            name_selectors = [
                'h1.product-title',
                'h1.product-name', 
                '.product-title h1',
                '.product-name h1',
                'h1',
                '.main-title h1',
                '.page-title h1'
            ]
            
            for selector in name_selectors:
                name_elem = soup.select_one(selector)
                if name_elem:
                    product_name = name_elem.get_text(strip=True)
                    if product_name and len(product_name) > 3:  # Đảm bảo không phải title rỗng
                        # DỊCH TÊN SẢN PHẨM HOÀN TOÀN SANG TIẾNG VIỆT
                        original_name = product_name
                        translated_name = self._translate_complete_text_with_gemini(product_name)
                        product_info['Tên sản phẩm'] = translated_name
                        log_and_emit(f"[FOTEK-TRANSLATE] Tên gốc: '{original_name}' → Tên đã dịch: '{translated_name}'")
                        break
            
            # TRÍCH XUẤT MÃ SẢN PHẨM
            # Từ tên sản phẩm hoặc các phần tử khác
            if product_info['Tên sản phẩm']:
                # Thử trích xuất từ tên sản phẩm
                name_parts = product_info['Tên sản phẩm'].split()
                for part in name_parts:
                    # Tìm phần có dạng mã sản phẩm (chữ cái + số)
                    if re.match(r'^[A-Za-z]{1,4}[-]?[0-9A-Za-z]{1,10}$', part):
                        product_info['Mã sản phẩm'] = part.upper()
                        log_and_emit(f"[FOTEK] Tìm thấy mã sản phẩm từ tên: {product_info['Mã sản phẩm']}")
                        break
            
            # Thử tìm mã sản phẩm từ các selector khác
            if not product_info['Mã sản phẩm']:
                code_selectors = [
                    '.product-code',
                    '.model-number', 
                    '.part-number',
                    '.sku',
                    '[class*="code"]',
                    '[class*="model"]'
                ]
                
                for selector in code_selectors:
                    code_elem = soup.select_one(selector)
                    if code_elem:
                        code_text = code_elem.get_text(strip=True)
                        if code_text and len(code_text) > 2:
                            product_info['Mã sản phẩm'] = code_text.upper()
                            log_and_emit(f"[FOTEK] Tìm thấy mã sản phẩm: {product_info['Mã sản phẩm']}")
                            break
            
            # TRÍCH XUẤT THÔNG SỐ KỸ THUẬT
            specs_data = []
            
            # Tìm bảng thông số kỹ thuật
            specs_tables = soup.select('table')
            for table in specs_tables:
                # Kiểm tra xem bảng có chứa thông số kỹ thuật không
                table_text = table.get_text().lower()
                if any(keyword in table_text for keyword in ['specification', 'parameter', 'feature', 'characteristic', 'thông số', 'đặc tính']):
                    log_and_emit(f"[FOTEK] Tìm thấy bảng thông số kỹ thuật")
                    
                    rows = table.select('tr')
                    for row in rows:
                        cells = row.select('td, th')
                        if len(cells) >= 2:
                            param = cells[0].get_text(strip=True)
                            value = cells[1].get_text(strip=True)
                            
                            if param and value and param != value:
                                specs_data.append((param, value))
                    break
            
            # Nếu không tìm thấy bảng, tìm thông số trong nội dung khác
            if not specs_data:
                # Tìm trong các div có class specification hoặc feature
                spec_containers = soup.select('.specification, .specifications, .features, .parameters, .tech-specs')
                for container in spec_containers:
                    # Tìm các cặp label-value
                    labels = container.select('.label, .spec-label, .param-name')
                    values = container.select('.value, .spec-value, .param-value')
                    
                    if len(labels) == len(values):
                        for label, value in zip(labels, values):
                            param = label.get_text(strip=True)
                            val = value.get_text(strip=True)
                            if param and val:
                                specs_data.append((param, val))
                        break
            
            # Tạo bảng HTML thông số kỹ thuật
            if specs_data:
                # DỊCH HOÀN TOÀN THÔNG SỐ KỸ THUẬT SANG TIẾNG VIỆT
                # Dịch từng cặp param-value hoàn toàn bằng Gemini
                translated_specs_data = []
                
                log_and_emit(f"[FOTEK-TRANSLATE] 🔄 Đang dịch hoàn toàn {len(specs_data)} thông số kỹ thuật...")
                
                for i, (param, value) in enumerate(specs_data):
                    # Dịch tham số hoàn toàn
                    translated_param = self._translate_complete_text_with_gemini(param)
                    
                    # Dịch giá trị hoàn toàn
                    translated_value = self._translate_complete_text_with_gemini(value)
                    
                    translated_specs_data.append((translated_param, translated_value))
                    
                    # Log chi tiết quá trình dịch
                    log_and_emit(f"[FOTEK-TRANSLATE] {i+1}/{len(specs_data)} - '{param}' | '{value}' → '{translated_param}' | '{translated_value}'")
                
                log_and_emit(f"[FOTEK-TRANSLATE] ✅ Đã hoàn thành dịch {len(translated_specs_data)} thông số")
                
                specs_html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;"><thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
                
                for param, value in translated_specs_data:
                    specs_html += f'<tr><td style="font-weight: bold;">{param}</td><td>{value}</td></tr>'
                
                # Thêm dòng Copyright vào cuối bảng
                specs_html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
                specs_html += '</tbody></table>'
                
                log_and_emit(f"[FOTEK-TRANSLATE] 💾 Đã tạo bảng thông số kỹ thuật HTML với {len(translated_specs_data)} thông số đã dịch hoàn toàn")
            else:
                log_and_emit(f"[FOTEK] ⚠️ Không có dữ liệu thông số kỹ thuật để tạo bảng")
                specs_html = self._generate_basic_specs_table(product_info['Mã sản phẩm'], product_info['Tên sản phẩm'])
            
            # TRÍCH XUẤT ẢNH SẢN PHẨM
            if product_info['Mã sản phẩm']:
                # Tạo URL ảnh theo định dạng yêu cầu
                product_info['Ảnh sản phẩm'] = f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_info['Mã sản phẩm']}.webp"
                
                # Nếu có thư mục output, tải ảnh gốc và lưu
                if output_dir:
                    self._download_fotek_product_image(soup, url, output_dir, product_info['Mã sản phẩm'])
                    
                    # Tải ảnh Wiring Diagram và Dimensions
                    self._download_fotek_wiring_and_dimensions(soup, url, output_dir, product_info['Mã sản phẩm'])
            
            log_and_emit(f"[FOTEK] Hoàn thành trích xuất: {product_info['Tên sản phẩm']}, Mã: {product_info['Mã sản phẩm']}")
            product_info['Tổng quan'] = specs_html
            return product_info
            
        except Exception as e:
            log_and_emit(f"[FOTEK] Lỗi khi trích xuất thông tin từ {url}: {str(e)}")
            traceback.print_exc()
            return product_info

    def _download_fotek_product_image(self, soup, product_url, output_dir, product_code):
        """Tải ảnh sản phẩm từ trang Fotek.com.tw"""
        try:
            # Tạo thư mục images nếu chưa tồn tại
            images_dir = os.path.join(output_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)
            
            # Tìm URL ảnh từ trang web
            image_url = None
            
            # Các selector để tìm ảnh sản phẩm
            img_selectors = [
                '.product-image img',
                '.main-image img',
                '.product-photo img',
                '.product-gallery img',
                'img[src*="product"]',
                'img[src*="catalog"]'
            ]
            
            for selector in img_selectors:
                img_elem = soup.select_one(selector)
                if img_elem and img_elem.get('src'):
                    src = img_elem.get('src')
                    if src and not src.startswith('data:'):  # Bỏ qua base64 images
                        # Đảm bảo URL đầy đủ
                        if src.startswith('//'):
                            image_url = 'https:' + src
                        elif src.startswith('/'):
                            image_url = 'https://www.fotek.com.tw' + src
                        elif src.startswith('http'):
                            image_url = src
                        
                        if image_url:
                            log_and_emit(f"[FOTEK] Tìm thấy ảnh sản phẩm: {image_url}")
                            break
            
            # Tải ảnh nếu tìm thấy
            if image_url:
                try:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Referer': product_url
                    }
                    
                    response = requests.get(image_url, headers=headers, stream=True, timeout=30)
                    response.raise_for_status()
                    
                    # Lưu ảnh
                    image_filename = f"{product_code}.webp"
                    image_path = os.path.join(images_dir, image_filename)
                    
                    with open(image_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    log_and_emit(f"[FOTEK] Đã tải và lưu ảnh: {image_path}")
                    return True
                    
                except Exception as e:
                    log_and_emit(f"[FOTEK] Lỗi khi tải ảnh từ {image_url}: {str(e)}")
                    return False
            else:
                log_and_emit(f"[FOTEK] Không tìm thấy URL ảnh cho sản phẩm: {product_url}")
                return False
                
        except Exception as e:
            log_and_emit(f"[FOTEK] Lỗi khi xử lý ảnh sản phẩm: {str(e)}")
            return False

    def _download_fotek_wiring_and_dimensions(self, soup, product_url, output_dir, product_code):
        """Tải ảnh Wiring Diagram và Dimensions từ các tab của Fotek"""
        try:
            # Tạo thư mục images nếu chưa tồn tại
            images_dir = os.path.join(output_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)
            
            log_and_emit(f"[FOTEK] Đang tìm Wiring Diagram và Dimensions cho sản phẩm: {product_code}")
            
            # Tìm tab content với id="myTabContent"
            tab_content = soup.select_one('#myTabContent')
            if not tab_content:
                log_and_emit(f"[FOTEK] Không tìm thấy tab content với id='myTabContent' cho {product_code}")
                return
            
            # 1. TẢI WIRING DIAGRAM TỪ TAB2
            wiring_tab = tab_content.select_one('#tab2')
            if wiring_tab:
                log_and_emit(f"[FOTEK] Tìm thấy tab Wiring Diagram (#tab2) cho {product_code}")
                wiring_img = wiring_tab.select_one('img')
                if wiring_img and wiring_img.get('src'):
                    wiring_img_url = wiring_img.get('src')
                    
                    # Đảm bảo URL đầy đủ
                    if wiring_img_url.startswith('//'):
                        wiring_img_url = 'https:' + wiring_img_url
                    elif wiring_img_url.startswith('/'):
                        wiring_img_url = 'https://www.fotek.com.tw' + wiring_img_url
                    elif not wiring_img_url.startswith('http'):
                        wiring_img_url = 'https://www.fotek.com.tw/' + wiring_img_url.lstrip('/')
                    
                    # Tên file theo định dạng: [Mã sản phẩm]-WD.webp
                    wiring_filename = f"{product_code}-WD.webp"
                    wiring_path = os.path.join(images_dir, wiring_filename)
                    
                    log_and_emit(f"[FOTEK] Đang tải Wiring Diagram từ: {wiring_img_url}")
                    success = self._download_fotek_image_from_url(wiring_img_url, wiring_path, product_url)
                    if success:
                        log_and_emit(f"[FOTEK] ✅ Đã tải Wiring Diagram: {wiring_filename}")
                    else:
                        log_and_emit(f"[FOTEK] ❌ Lỗi khi tải Wiring Diagram: {wiring_filename}")
                else:
                    log_and_emit(f"[FOTEK] Không tìm thấy ảnh trong tab Wiring Diagram cho {product_code}")
            else:
                log_and_emit(f"[FOTEK] Không tìm thấy tab Wiring Diagram (#tab2) cho {product_code}")
            
            # 2. TẢI DIMENSIONS TỪ TAB3  
            dimensions_tab = tab_content.select_one('#tab3')
            if dimensions_tab:
                log_and_emit(f"[FOTEK] Tìm thấy tab Dimensions (#tab3) cho {product_code}")
                dimensions_img = dimensions_tab.select_one('img')
                if dimensions_img and dimensions_img.get('src'):
                    dimensions_img_url = dimensions_img.get('src')
                    
                    # Đảm bảo URL đầy đủ
                    if dimensions_img_url.startswith('//'):
                        dimensions_img_url = 'https:' + dimensions_img_url
                    elif dimensions_img_url.startswith('/'):
                        dimensions_img_url = 'https://www.fotek.com.tw' + dimensions_img_url
                    elif not dimensions_img_url.startswith('http'):
                        dimensions_img_url = 'https://www.fotek.com.tw/' + dimensions_img_url.lstrip('/')
                    
                    # Tên file theo định dạng: [Mã sản phẩm]-DMS.webp
                    dimensions_filename = f"{product_code}-DMS.webp"
                    dimensions_path = os.path.join(images_dir, dimensions_filename)
                    
                    log_and_emit(f"[FOTEK] Đang tải Dimensions từ: {dimensions_img_url}")
                    success = self._download_fotek_image_from_url(dimensions_img_url, dimensions_path, product_url)
                    if success:
                        log_and_emit(f"[FOTEK] ✅ Đã tải Dimensions: {dimensions_filename}")
                    else:
                        log_and_emit(f"[FOTEK] ❌ Lỗi khi tải Dimensions: {dimensions_filename}")
                else:
                    log_and_emit(f"[FOTEK] Không tìm thấy ảnh trong tab Dimensions cho {product_code}")
            else:
                log_and_emit(f"[FOTEK] Không tìm thấy tab Dimensions (#tab3) cho {product_code}")
                
        except Exception as e:
            log_and_emit(f"[FOTEK] Lỗi khi tải Wiring Diagram và Dimensions cho {product_code}: {str(e)}")
            traceback.print_exc()

    def _download_fotek_image_from_url(self, image_url, save_path, referer_url):
        """Tải ảnh từ URL và lưu vào đường dẫn chỉ định"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': referer_url
            }
            
            response = requests.get(image_url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            
            # Lưu ảnh trực tiếp
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            return True
            
        except Exception as e:
            log_and_emit(f"[FOTEK] Lỗi khi tải ảnh từ {image_url}: {str(e)}")
            return False

    def process_fotek_categories(self, category_urls_text):
        """Xử lý danh sách URL danh mục trên Fotek.com.tw"""
        try:
            # Tách thành danh sách URL, bỏ qua dòng trống
            raw_urls = category_urls_text.strip().split('\n')
            urls = [url.strip() for url in raw_urls if url.strip()]
            
            # Lọc các URL hợp lệ
            valid_urls = []
            invalid_urls = []
            
            # Gửi thông báo bắt đầu
            emit_progress(0, 'Đang kiểm tra URL danh mục Fotek...')
            
            # Kiểm tra các URL
            for url in urls:
                if is_valid_url(url) and 'fotek.com.tw' in url:
                    valid_urls.append(url)
                else:
                    invalid_urls.append(url)
        
            if not valid_urls:
                raise ValueError('Không có URL danh mục Fotek.com.tw hợp lệ!')
            
            # Gửi thông báo cập nhật
            emit_progress(5, f'Đã tìm thấy {len(valid_urls)} URL danh mục Fotek hợp lệ')
            
            # Tạo thư mục chính để lưu kết quả
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            result_dir = os.path.join(self.upload_folder, f'fotek_info_{timestamp}') if self.upload_folder else f'fotek_info_{timestamp}'
            os.makedirs(result_dir, exist_ok=True)
            
            # Xử lý từng URL danh mục
            category_info = []
            
            for i, category_url in enumerate(valid_urls):
                try:
                    category_progress_base = 5 + int((i / len(valid_urls)) * 85)
                    emit_progress(category_progress_base, f'Đang xử lý danh mục Fotek {i+1}/{len(valid_urls)}: {category_url}')
                    
                    # Trích xuất tên danh mục từ URL
                    category_name = self._extract_category_name(category_url)
                    
                    # Tạo thư mục cho danh mục này
                    category_dir = os.path.join(result_dir, category_name)
                    os.makedirs(category_dir, exist_ok=True)
                    
                    # Thu thập liên kết sản phẩm từ danh mục này
                    emit_progress(category_progress_base + 5, f'Đang thu thập liên kết từ danh mục Fotek: {category_name}')
                    
                    # Fotek có thể có nhiều cấp danh mục, cần xử lý đệ quy
                    category_products = self._extract_fotek_category_products(category_url)
                    
                    if category_products:
                        # Lưu các liên kết sản phẩm vào file txt
                        self._save_product_links(category_dir, category_name, category_products)
                        
                        # Thu thập thông tin sản phẩm
                        product_info_list = self._collect_fotek_product_info(category_dir, category_name, category_products, category_progress_base)
                        
                        # Thêm thông tin danh mục vào danh sách
                        category_info.append({
                            'Tên danh mục': category_name,
                            'URL danh mục': category_url,
                            'Số sản phẩm': len(category_products),
                            'Số sản phẩm có thông tin': len(product_info_list)
                        })
                        
                        log_and_emit(f"[FOTEK] Đã thu thập {len(category_products)} sản phẩm từ danh mục {category_name}")
                    else:
                        log_and_emit(f"[FOTEK] Không tìm thấy sản phẩm nào trong danh mục: {category_url}")
                        
                except Exception as e:
                    log_and_emit(f"[FOTEK] Lỗi khi xử lý danh mục {category_url}: {str(e)}")
                    traceback.print_exc()
            
            # Tạo báo cáo và file nén
            self._create_reports(result_dir, category_info, valid_urls)
            
            # Nén kết quả
            zip_filename = f'fotek_info_{timestamp}.zip'
            zip_path = os.path.join(self.upload_folder, zip_filename) if self.upload_folder else zip_filename
            
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, self.upload_folder) if self.upload_folder else os.path.basename(file_path)
                        zipf.write(file_path, relative_path)
            
            # Lưu đường dẫn file ZIP vào session
            try:
                if 'session' in globals() or 'session' in locals():
                    session['last_download'] = zip_filename
            except (ImportError, RuntimeError):
                pass
            
            return True, f'Đã hoàn tất thu thập dữ liệu từ {len(valid_urls)} danh mục Fotek.com.tw', zip_path
            
        except Exception as e:
            error_message = str(e)
            traceback.print_exc()
            return False, f'Lỗi: {error_message}', None

    def _extract_fotek_category_products(self, category_url):
        """Trích xuất tất cả sản phẩm từ danh mục Fotek (xử lý cấu trúc đa cấp)"""
        all_products = []
        visited_urls = set()
        urls_to_process = [category_url]
        
        while urls_to_process:
            current_url = urls_to_process.pop(0)
            
            if current_url in visited_urls:
                continue
                
            visited_urls.add(current_url)
            log_and_emit(f"[FOTEK] Đang xử lý URL: {current_url}")
            
            try:
                soup = self._get_soup(current_url)
                if not soup:
                    continue
                
                # Trích xuất liên kết từ trang hiện tại
                product_urls, sub_category_urls = self._extract_fotek_links(soup, current_url)
                
                # Thêm sản phẩm vào danh sách
                for product_url in product_urls:
                    if product_url not in all_products:
                        all_products.append(product_url)
                
                # Thêm danh mục con vào danh sách cần xử lý
                for sub_url in sub_category_urls:
                    if sub_url not in visited_urls and sub_url not in urls_to_process:
                        urls_to_process.append(sub_url)
                        log_and_emit(f"[FOTEK] Thêm danh mục con để xử lý: {sub_url}")
                
            except Exception as e:
                log_and_emit(f"[FOTEK] Lỗi khi xử lý {current_url}: {str(e)}")
        
        log_and_emit(f"[FOTEK] Tổng cộng tìm thấy {len(all_products)} sản phẩm từ {len(visited_urls)} trang")
        return all_products

    def _collect_fotek_product_info(self, category_dir, category_name, product_links, progress_base):
        """Thu thập thông tin sản phẩm Fotek với đa luồng tối ưu"""
        if not product_links:
            log_and_emit(f"[FOTEK] Không có liên kết sản phẩm để thu thập từ danh mục: {category_name}")
            return []
            
        # Cập nhật tiến trình
        progress_max = min(40, 85 - (progress_base - 5))
        update_progress(progress_base, f"⚡ Đang cào dữ liệu {len(product_links)} sản phẩm Fotek với {self.max_workers_download} luồng từ danh mục: {category_name}")
        log_and_emit(f"[FOTEK-MULTI] 🚀 Bắt đầu thu thập {len(product_links)} sản phẩm Fotek với đa luồng tối ưu")
        
        # Worker function tối ưu cho Fotek
        def fotek_optimized_worker(batch_data):
            """Worker function xử lý một batch sản phẩm Fotek"""
            batch_results = []
            batch_errors = []
            
            for item in batch_data:
                product_url, index = item
                try:
                    product_info = self.extract_fotek_product_info(product_url, index, category_dir)
                    if product_info:
                        product_info['index'] = index
                        batch_results.append(product_info)
                    else:
                        batch_errors.append((index, product_url, "Không thể trích xuất thông tin sản phẩm"))
                except Exception as e:
                    batch_errors.append((index, product_url, str(e)))
                    log_and_emit(f"[FOTEK] ❌ Lỗi khi xử lý sản phẩm {index}: {product_url} - {str(e)}")
                    
            return batch_results, batch_errors
        
        # Chia thành batches
        batch_size = self.batch_size
        batches = []
        for i in range(0, len(product_links), batch_size):
            batch = [(product_links[j], j + 1) for j in range(i, min(i + batch_size, len(product_links)))]
            batches.append(batch)
        
        log_and_emit(f"[FOTEK-BATCH] 📦 Chia thành {len(batches)} batch, mỗi batch {batch_size} sản phẩm Fotek")
        
        # Xử lý đa luồng
        product_info_list = []
        all_errors = []
        completed_count = 0
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers_download) as executor:
            # Submit tất cả batches
            future_to_batch = {executor.submit(fotek_optimized_worker, batch): i for i, batch in enumerate(batches)}
            
            # Xử lý kết quả khi hoàn thành
            for future in concurrent.futures.as_completed(future_to_batch):
                batch_idx = future_to_batch[future]
                try:
                    batch_results, batch_errors = future.result()
                    
                    # Thêm kết quả
                    product_info_list.extend(batch_results)
                    all_errors.extend(batch_errors)
                    
                    # Cập nhật tiến trình
                    completed_count += len(batches[batch_idx])
                    current_progress = progress_base + int((completed_count / len(product_links)) * progress_max)
                    
                    # Tính toán thống kê
                    success_rate = len(product_info_list) / completed_count * 100 if completed_count > 0 else 0
                    
                    update_progress(
                        current_progress, 
                        f"⚡ Đã xử lý {completed_count}/{len(product_links)} sản phẩm Fotek "
                        f"({success_rate:.1f}% thành công)"
                    )
                    
                    log_and_emit(f"[FOTEK-PROGRESS] ✅ Batch {batch_idx + 1}/{len(batches)} hoàn thành: "
                               f"+{len(batch_results)} sản phẩm, {len(batch_errors)} lỗi")
                    
                except Exception as e:
                    log_and_emit(f"[FOTEK-ERROR] ❌ Lỗi xử lý batch {batch_idx + 1}: {str(e)}")
                    
                # Giảm delay để tăng tốc độ
                time.sleep(0.02)
        
        # Báo cáo lỗi chi tiết
        if all_errors:
            error_summary = {}
            for idx, url, error in all_errors:
                error_type = type(error).__name__ if hasattr(error, '__class__') else 'Unknown'
                error_summary[error_type] = error_summary.get(error_type, 0) + 1
            
            error_details = ", ".join([f"{k}: {v}" for k, v in error_summary.items()])
            log_and_emit(f"[FOTEK-ERRORS] ⚠️ Có {len(all_errors)} lỗi: {error_details}")
        
        # Sắp xếp theo số thứ tự
        try:
            product_info_list.sort(key=lambda x: x.get('index', 0) if isinstance(x, dict) else 0)
        except Exception as e:
            log_and_emit(f"[FOTEK-WARNING] ⚠️ Lỗi khi sắp xếp danh sách sản phẩm: {str(e)}")
        
        # Tạo file excel
        excel_file = os.path.join(category_dir, f"{category_name}_products.xlsx")
        if product_info_list:
            try:
                important_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'URL', 'Tổng quan', 'Ảnh sản phẩm']
                df = pd.DataFrame([{field: product.get(field, '') for field in important_fields} for product in product_info_list])
                df.to_excel(excel_file, index=False, engine='openpyxl')
                log_and_emit(f"[FOTEK-SUCCESS] 💾 Đã lưu {len(product_info_list)} sản phẩm Fotek vào file {excel_file}")
            except Exception as e:
                log_and_emit(f"[FOTEK-ERROR] ❌ Lỗi khi tạo file Excel: {str(e)}")
        
        # Thống kê cuối cùng
        success_rate = len(product_info_list) / len(product_links) * 100 if product_links else 0
        log_and_emit(f"[FOTEK-FINAL] 🎯 Hoàn thành danh mục Fotek {category_name}: "
                   f"{len(product_info_list)}/{len(product_links)} sản phẩm ({success_rate:.1f}% thành công)")
        
        return product_info_list

    def _make_full_url(self, href, current_url, base_domain=None):
        """Chuyển đổi URL tương đối thành URL đầy đủ"""
        if not href:
            return None
            
        if href.startswith('http'):
            return href
            
        if base_domain:
            if href.startswith('/'):
                return f"{base_domain}{href}"
            return f"{current_url.rstrip('/')}/{href.lstrip('/')}"
            
        parsed_url = urlparse(current_url)
        if href.startswith('/'):
            return f"{parsed_url.scheme}://{parsed_url.netloc}{href}"
        return f"{current_url.rstrip('/')}/{href.lstrip('/')}"
    
    def _save_product_links(self, category_dir, category_name, product_links):
        """Lưu danh sách liên kết sản phẩm vào file"""
        category_file = os.path.join(category_dir, f'{category_name}_links.txt')
        with open(category_file, 'w', encoding='utf-8') as f:
            for link in product_links:
                f.write(link + '\n')
    
    def _download_product_info_worker(self, product_url, index, category_dir, is_codienhaiau, result_queue, error_queue):
        """Tải thông tin sản phẩm từ URL và đưa vào hàng đợi kết quả"""
        try:
            # Xác định loại website và gọi method phù hợp
            if 'codienhaiau.com' in product_url:
                # Trích xuất thông tin sản phẩm từ codienhaiau.com
                product_info = self.extract_codienhaiau_product_info(product_url, index, category_dir)
            elif 'fotek.com.tw' in product_url:
                # Trích xuất thông tin sản phẩm từ fotek.com.tw
                product_info = self.extract_fotek_product_info(product_url, index, category_dir)
            else:
                # Trích xuất thông tin sản phẩm từ các trang web khác
                from .crawler import extract_product_info
                product_info = extract_product_info(product_url, required_fields=None, index=index)
            
            # Thêm index vào product_info để sắp xếp lại sau này
            if product_info:
                product_info['index'] = index
                result_queue.put(product_info)
            else:
                error_queue.put((index, product_url, "Không thể trích xuất thông tin sản phẩm"))
            
        except Exception as e:
            error_message = str(e)
            error_queue.put((index, product_url, error_message))
            log_and_emit(f"Lỗi khi xử lý sản phẩm {index}: {product_url} - {error_message}")
            traceback.print_exc()

    def _collect_product_info(self, category_dir, category_name, product_links, progress_base):
        """Thu thập thông tin sản phẩm từ danh sách liên kết với đa luồng tối ưu"""
        if not product_links:
            log_and_emit(f"[INFO] Không có liên kết sản phẩm để thu thập từ danh mục: {category_name}")
            return []
            
        # Cập nhật tiến trình
        progress_max = min(40, 85 - (progress_base - 5))
        update_progress(progress_base, f"⚡ Đang cào dữ liệu {len(product_links)} sản phẩm với {self.max_workers_download} luồng từ danh mục: {category_name}")
        log_and_emit(f"[MULTI-THREAD] 🚀 Bắt đầu thu thập {len(product_links)} sản phẩm với đa luồng tối ưu")
        
        # Tạo queue thread-safe để nhận kết quả
        result_queue = queue.Queue()
        error_queue = queue.Queue()
        completed_count = 0
        
        # Xác định loại website
        is_codienhaiau = any('codienhaiau.com' in url for url in product_links if url)
        is_fotek = any('fotek.com.tw' in url for url in product_links if url)
        is_baa = any('baa.vn' in url for url in product_links if url)
        
        # Worker function tối ưu hóa
        def optimized_worker(batch_data):
            """Worker function xử lý một batch sản phẩm"""
            batch_results = []
            batch_errors = []
            
            for item in batch_data:
                product_url, index = item
                try:
                    # Xác định method extract phù hợp
                    if is_codienhaiau:
                        product_info = self.extract_codienhaiau_product_info(product_url, index, category_dir)
                    elif is_fotek:
                        product_info = self.extract_fotek_product_info(product_url, index, category_dir)
                    elif is_baa:
                        product_info = self.extract_baa_product_info(product_url, index, category_dir)
                    else:
                        from .crawler import extract_product_info
                        product_info = extract_product_info(product_url, required_fields=None, index=index)
                    
                    if product_info:
                        product_info['index'] = index
                        batch_results.append(product_info)
                    else:
                        batch_errors.append((index, product_url, "Không thể trích xuất thông tin"))
                        
                except Exception as e:
                    batch_errors.append((index, product_url, str(e)))
                    
            return batch_results, batch_errors
        
        # Chia thành batches để xử lý hiệu quả hơn
        batch_size = self.batch_size
        batches = []
        for i in range(0, len(product_links), batch_size):
            batch = [(product_links[j], j + 1) for j in range(i, min(i + batch_size, len(product_links)))]
            batches.append(batch)
        
        log_and_emit(f"[BATCH] 📦 Chia thành {len(batches)} batch, mỗi batch {batch_size} sản phẩm")
        
        # Xử lý đa luồng với ThreadPoolExecutor
        product_info_list = []
        all_errors = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers_download) as executor:
            # Submit tất cả batches
            future_to_batch = {executor.submit(optimized_worker, batch): i for i, batch in enumerate(batches)}
            
            # Xử lý kết quả khi hoàn thành
            for future in concurrent.futures.as_completed(future_to_batch):
                batch_idx = future_to_batch[future]
                try:
                    batch_results, batch_errors = future.result()
                    
                    # Thêm kết quả vào danh sách chính
                    product_info_list.extend(batch_results)
                    all_errors.extend(batch_errors)
                    
                    # Cập nhật tiến trình
                    completed_count += len(batches[batch_idx])
                    current_progress = progress_base + int((completed_count / len(product_links)) * progress_max)
                    
                    # Tính toán thống kê
                    success_rate = len(product_info_list) / completed_count * 100 if completed_count > 0 else 0
                    
                    update_progress(
                        current_progress, 
                        f"⚡ Đã xử lý {completed_count}/{len(product_links)} sản phẩm "
                        f"({success_rate:.1f}% thành công) từ danh mục: {category_name}"
                    )
                    
                    log_and_emit(f"[PROGRESS] ✅ Batch {batch_idx + 1}/{len(batches)} hoàn thành: "
                               f"+{len(batch_results)} sản phẩm, {len(batch_errors)} lỗi")
                    
                except Exception as e:
                    log_and_emit(f"[ERROR] ❌ Lỗi xử lý batch {batch_idx + 1}: {str(e)}")
                    
                # Giãn cách nhỏ để tránh overload
                time.sleep(0.05)
        
        # Báo cáo lỗi nếu có
        if all_errors:
            error_summary = {}
            for idx, url, error in all_errors:
                error_type = type(error).__name__ if hasattr(error, '__class__') else 'Unknown'
                error_summary[error_type] = error_summary.get(error_type, 0) + 1
            
            error_details = ", ".join([f"{k}: {v}" for k, v in error_summary.items()])
            log_and_emit(f"[ERRORS] ⚠️ Có {len(all_errors)} lỗi: {error_details}")
        
        # Sắp xếp theo index
        try:
            product_info_list.sort(key=lambda x: x.get('index', 0) if isinstance(x, dict) else 0)
        except Exception as e:
            log_and_emit(f"[WARNING] ⚠️ Lỗi khi sắp xếp danh sách: {str(e)}")
        
        # Tạo file Excel với error handling
        excel_file = os.path.join(category_dir, f"{category_name}_products.xlsx")
        if product_info_list:
            try:
                important_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'URL', 'Tổng quan', 'Ảnh sản phẩm']
                df = pd.DataFrame([{field: product.get(field, '') for field in important_fields} for product in product_info_list])
                df.to_excel(excel_file, index=False, engine='openpyxl')
                
                log_and_emit(f"[SUCCESS] 💾 Đã lưu {len(product_info_list)} sản phẩm vào file Excel: {excel_file}")
            except Exception as e:
                log_and_emit(f"[ERROR] ❌ Lỗi khi tạo file Excel: {str(e)}")
        
        # Thống kê cuối cùng
        success_rate = len(product_info_list) / len(product_links) * 100 if product_links else 0
        log_and_emit(f"[FINAL] 🎯 Hoàn thành danh mục {category_name}: "
                   f"{len(product_info_list)}/{len(product_links)} sản phẩm ({success_rate:.1f}% thành công)")
        
        return product_info_list
    
    def _create_reports(self, result_dir, category_info, valid_urls):
        """Tạo báo cáo tổng hợp cho quá trình thu thập dữ liệu"""
        try:
            # Tạo file báo cáo tổng hợp
            report_file = os.path.join(result_dir, 'tong_hop.xlsx')
            emit_progress(90, f'Đang tạo báo cáo tổng hợp, vui lòng đợi...')
            # Tạo DataFrame cho báo cáo tổng hợp
            df_summary = pd.DataFrame(category_info)
            # Tạo sheet báo cáo
            with pd.ExcelWriter(report_file, engine='openpyxl') as writer:
                # Sheet danh mục
                df_summary.to_excel(writer, sheet_name='Danh mục', index=False)
                # Tìm kiếm file Excel sản phẩm đã tạo trong mỗi thư mục
                all_products = []
                for cat_info in category_info:
                    try:
                        cat_name = cat_info['Tên danh mục']
                        # Sử dụng tên danh mục trực tiếp (đã được xử lý khi tạo thư mục)
                        cat_dir = os.path.join(result_dir, cat_name)
                        excel_file = os.path.join(cat_dir, f'{cat_name}_products.xlsx') # Sử dụng tên file đã lưu
                        
                        if os.path.exists(excel_file):
                            print(f"  > Đang đọc file Excel danh mục: {excel_file}")
                            df_cat = pd.read_excel(excel_file)
                            
                            # Thêm thông tin danh mục vào mỗi sản phẩm
                            df_cat['Danh mục'] = cat_name
                            
                            # Đảm bảo cột 'Tổng quan' tồn tại trước khi thêm vào all_products
                            if 'Tổng quan' not in df_cat.columns:
                                df_cat['Tổng quan'] = "" # Thêm cột rỗng nếu không có
                                
                            all_products.append(df_cat)
                            print(f"  > Đã đọc thành công {len(df_cat)} dòng từ file {excel_file}")
                        else:
                            print(f"  > Cảnh báo: File Excel danh mục không tồn tại: {excel_file}")
                            
                    except Exception as e:
                        print(f"Lỗi khi đọc file Excel cho danh mục {cat_name}: {str(e)}")
                        traceback.print_exc()

                # Kết hợp tất cả sản phẩm vào một sheet
                if all_products:
                    df_all_products = pd.concat(all_products, ignore_index=True)
                    
                    # Định nghĩa thứ tự các cột mong muốn
                    desired_order = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'Danh mục', 'URL', 'Tổng quan', 'Mô tả', 'Ảnh sản phẩm']
                    
                    # Lọc và sắp xếp lại các cột theo thứ tự mong muốn
                    # Giữ lại các cột có trong DataFrame và sắp xếp theo desired_order
                    existing_cols_in_order = [col for col in desired_order if col in df_all_products.columns]
                    # Thêm bất kỳ cột nào khác không có trong desired_order vào cuối
                    other_cols = [col for col in df_all_products.columns if col not in desired_order]
                    final_cols_order = existing_cols_in_order + other_cols
                    
                    df_all_products = df_all_products[final_cols_order]
                    
                    # Ghi ra sheet tổng hợp
                    df_all_products.to_excel(writer, sheet_name='Tổng hợp sản phẩm', index=False)
                    log_and_emit(f"Đã tạo sheet \'Tổng hợp sản phẩm\' với {len(df_all_products)} dòng")
                    
                    # Định dạng sheet
                    worksheet = writer.sheets['Tổng hợp sản phẩm']
                    
                    # Điều chỉnh độ rộng cột
                    for idx, col in enumerate(df_all_products.columns):
                        # Đặt độ rộng cột dựa trên tên cột và loại dữ liệu
                        if col == 'STT':
                            max_width = 5
                        elif col == 'Mã sản phẩm':
                            max_width = 15
                        elif col == 'Tên sản phẩm':
                            max_width = 40
                        elif col == 'Giá':
                            max_width = 15
                        elif col == 'Danh mục':
                             max_width = 20 # Thêm độ rộng cho cột Danh mục
                        elif col == 'Tổng quan':
                            max_width = 80 # Tăng độ rộng cho cột Tổng quan
                        elif col == 'Mô tả': # Mô tả gốc từ web, có thể vẫn cần
                             max_width = 60
                        elif col in ['Ảnh sản phẩm', 'Ảnh bổ sung', 'Tài liệu kỹ thuật', 'URL']:
                            max_width = 50
                        else:
                            # Tính toán độ rộng dựa trên nội dung (tối đa 50)
                            try:
                                # Chỉ lấy mẫu vài dòng để ước tính độ rộng
                                max_len = max([len(str(x)) for x in df_all_products[col].head(20).tolist()] + [len(col)])
                                max_width = min(50, max_len + 2) # Cộng thêm 2 cho padding
                            except Exception:
                                 max_width = 20 # Mặc định nếu tính toán lỗi
                                 
                        col_letter = get_column_letter(idx + 1)
                        worksheet.column_dimensions[col_letter].width = max_width

                    print(f"Đã định dạng sheet \'Tổng hợp sản phẩm\'")

                else:
                     print(f"  > Không có dữ liệu sản phẩm để tạo sheet \'Tổng hợp sản phẩm\'")

                print(f"Đã tạo báo cáo tổng hợp: {report_file}")
            emit_progress(100, f'Đã hoàn tất thu thập dữ liệu từ {len(valid_urls)} danh mục sản phẩm')
        except Exception as e:
            print(f"Lỗi khi tạo báo cáo: {str(e)}")
            traceback.print_exc()
            
    def extract_codienhaiau_product_info(self, url, index=1, output_dir=None):
        """
        Trích xuất thông tin sản phẩm từ trang codienhaiau.com
        
        Args:
            url (str): URL của trang sản phẩm
            index (int): Số thứ tự của sản phẩm
            output_dir (str, optional): Thư mục để lưu ảnh sản phẩm
            
        Returns:
            dict: Thông tin sản phẩm đã trích xuất
        """
        # Khởi tạo kết quả với trường STT luôn có và URL nếu được chọn
        product_info = {'STT': index}
        
        # Thêm URL nếu được chọn
        if 'url' in self.selected_fields or not self.selected_fields:
            product_info['URL'] = url
        
        # Khởi tạo các trường khác dựa trên selected_fields
        if not self.selected_fields:  # Nếu không có trường nào được chọn, cào tất cả
            product_info.update({
                'Mã sản phẩm': "",
                'Tên sản phẩm': "",
                'Giá': "",
                'Tổng quan': "",
                'Ảnh sản phẩm': ""
            })
        else:
            # Chỉ khởi tạo các trường được chọn
            if 'product_code' in self.selected_fields:
                product_info['Mã sản phẩm'] = ""
            if 'product_name' in self.selected_fields:
                product_info['Tên sản phẩm'] = ""
            if 'price' in self.selected_fields:
                product_info['Giá'] = ""
            if 'specifications' in self.selected_fields:
                product_info['Tổng quan'] = ""
            if 'product_image' in self.selected_fields:
                product_info['Ảnh sản phẩm'] = ""
        
        try:
            # Tải nội dung trang với retry
            soup = None
            for attempt in range(self.max_retries):
                try:
                    soup = self._get_soup(url)
                    if soup:
                        break
                except Exception as e:
                    print(f"  > Lỗi khi tải trang (lần {attempt+1}/{self.max_retries}): {str(e)}")
                    if attempt < self.max_retries - 1:
                        time.sleep(1)  # Chờ 1 giây trước khi thử lại
            
            if not soup:
                print(f"  > Không thể tải nội dung trang sau {self.max_retries} lần thử")
                return product_info

            # TRÍCH XUẤT TÊN SẢN PHẨM (chỉ khi được chọn)
            if 'product_name' in self.selected_fields or not self.selected_fields:
                product_name_elem = soup.select_one('h1.product_title')
                if product_name_elem:
                    product_name = product_name_elem.get_text(strip=True)
                    product_info['Tên sản phẩm'] = product_name
                    print(f"  > Tìm thấy tên sản phẩm: {product_name}")

            # TRÍCH XUẤT MÃ SẢN PHẨM (chỉ khi được chọn)
            product_code = ""
            if 'product_code' in self.selected_fields or not self.selected_fields:
                # Phương pháp 1: Từ SKU hoặc mã sản phẩm hiển thị riêng (ưu tiên cao nhất)
                sku_elem = soup.select_one('.sku_wrapper .sku, .product_meta .sku, [itemprop="sku"], .sku')
                if sku_elem:
                    product_code = sku_elem.get_text(strip=True)
                    print(f"  > Tìm thấy mã sản phẩm từ SKU (phương pháp 1): {product_code}")
                
                # Phương pháp 2: Từ tiêu đề sản phẩm
                if not product_code and product_info.get('Tên sản phẩm'):
                    # Tìm mã sản phẩm trong tên (thường ở cuối tên, sau dấu gạch ngang hoặc khoảng trắng)
                    name_parts = product_info['Tên sản phẩm'].split('-')
                    if len(name_parts) > 1:
                        potential_code = name_parts[-1].strip()
                        # Kiểm tra xem phần cuối có phải là mã sản phẩm không (chỉ chứa chữ cái, số, gạch ngang, gạch dưới)
                        if re.match(r'^[A-Za-z0-9\-_]{3,}$', potential_code):
                            product_code = potential_code
                            print(f"  > Tìm thấy mã sản phẩm từ tên (phương pháp 2): {product_code}")
                
                # Phương pháp 3: Tìm trong bảng thông số kỹ thuật
                if not product_code:
                    # Tìm trong bảng thông số kỹ thuật
                    specs_table = soup.select_one('table.woocommerce-product-attributes, table.shop_attributes')
                    if specs_table:
                        for row in specs_table.select('tr'):
                            header = row.select_one('th')
                            value = row.select_one('td')
                            if header and value:
                                header_text = header.get_text(strip=True).lower()
                                if any(keyword in header_text for keyword in ['mã', 'model', 'code', 'sku', 'part number']):
                                    product_code = value.get_text(strip=True)
                                    print(f"  > Tìm thấy mã sản phẩm từ bảng thông số (phương pháp 3): {product_code}")
                                    break
                
                # Phương pháp 4: Từ URL
                if not product_code:
                    # Trích xuất từ URL (thường là phần cuối URL)
                    url_parts = url.rstrip('/').split('/')
                    if url_parts:
                        last_part = url_parts[-1]
                        # Nếu có dấu gạch ngang, lấy phần cuối cùng
                        if '-' in last_part:
                            potential_code = last_part.split('-')[-1]
                            # Kiểm tra xem phần cuối có phải là mã sản phẩm không (ít nhất 3 ký tự)
                            if re.match(r'^[A-Za-z0-9\-_]{3,}$', potential_code):
                                product_code = potential_code
                                print(f"  > Tìm thấy mã sản phẩm từ URL (phương pháp 4): {product_code}")
                
                # Lưu mã sản phẩm đã tìm thấy (viết hoa)
                if product_code:
                    product_info['Mã sản phẩm'] = product_code.upper()
                    print(f"  > Mã sản phẩm cuối cùng: {product_info['Mã sản phẩm']}")

            # TRÍCH XUẤT GIÁ SẢN PHẨM (chỉ khi được chọn)
            if 'price' in self.selected_fields or not self.selected_fields:
                price_elem = soup.select_one('.price ins .amount, .price .amount, .product-page-price .amount, .woocommerce-Price-amount')
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    # Làm sạch giá (loại bỏ ký tự đặc biệt)
                    price_text = re.sub(r'[^\d,.]', '', price_text)
                    print(f"  > Tìm thấy giá sản phẩm: {price_text}")
                    product_info['Giá'] = price_text

            # TRÍCH XUẤT BẢNG THÔNG SỐ KỸ THUẬT (chỉ khi được chọn)
            if 'specifications' in self.selected_fields or not self.selected_fields:
                specs_found = False
                specs_html = ""
                specs_data = [] # Lưu dữ liệu trích xuất được
                
                # Tìm tab mô tả
                tab_description = soup.select_one('#tab-description, .woocommerce-Tabs-panel--description')
                if tab_description:
                    print(f"  > Tìm thấy tab mô tả sản phẩm")
                    
                    # Tạo đối tượng BeautifulSoup mới với parser lxml để xử lý HTML tốt hơn
                    tab_soup = BeautifulSoup(str(tab_description), 'lxml')
                    
                    # Tìm bảng thông số kỹ thuật cụ thể
                    specs_table = tab_soup.select_one('table.woocommerce-product-attributes, table.shop_attributes')
                    
                    if specs_table:
                        print(f"  > Tìm thấy bảng thông số kỹ thuật với class phù hợp")
                        specs_found = True
                        
                        # Xử lý từng hàng trong bảng
                        rows = specs_table.find_all('tr')
                        print(f"  > Số lượng hàng trong bảng: {len(rows)}")
                        
                        for row_index, row in enumerate(rows):
                            try:
                                # Lấy tất cả các ô trong hàng (td hoặc th)
                                cells = row.find_all(['td', 'th'])
                                if not cells:
                                    continue
                                
                                row_data = []
                                for cell in cells:
                                    # Lấy nội dung văn bản và thuộc tính colspan/tag
                                    text = cell.get_text(strip=True)
                                    colspan = int(cell.get('colspan', 1))
                                    cell_type = cell.name
                                    row_data.append({'text': text, 'colspan': colspan, 'tag': cell_type})
                                
                                # Bỏ qua hàng tiêu đề nếu hàng đầu tiên chứa <th> hoặc là hàng rỗng
                                if row_index == 0 and (any(cell['tag'] == 'th' for cell in row_data) or not row_data):
                                    continue
                                
                                # Bỏ qua hàng chỉ chứa <th>
                                if all(cell['tag'] == 'th' for cell in row_data):
                                    continue
                                
                                # Trích xuất thông số và giá trị
                                param = None
                                value = None

                                # Xử lý hàng có 2 ô (Thông số, Giá trị)
                                if len(row_data) == 2:
                                    param = row_data[0]['text'].strip()
                                    value = row_data[1]['text'].strip()
                                    
                                # Xử lý hàng có nhiều hơn 2 ô hoặc cấu trúc phức tạp hơn (có colspan)
                                elif len(row_data) > 2:
                                    # Lấy ô đầu tiên làm thông số (kết hợp với các ô giữa nếu có colspan)
                                    param_cells = row_data[:-1]
                                    param_parts = [cell['text'] for cell in param_cells if cell['text']]
                                    param = " - ".join(param_parts).strip()
                                    
                                    # Lấy ô cuối cùng làm giá trị
                                    value_cell = row_data[-1]
                                    value = value_cell['text'].strip()

                                # Trường hợp chỉ có một ô (có thể là tiêu đề phụ hoặc ghi chú)
                                elif len(row_data) == 1 and row_data[0]['text']:
                                    continue
                                    
                                # Thêm vào danh sách dữ liệu nếu trích xuất thành công cặp param/value
                                if param and value:
                                    specs_data.append((param, value))
                                    print(f"  > Đã thêm vào specs_data: {param} = {value}")
                                    
                            except Exception as e:
                                print(f"  > Lỗi khi xử lý hàng {row_index + 1}: {str(e)}")
                                continue
                        
                        print(f"\n  > Tổng số thông số đã trích xuất: {len(specs_data)}")
                        
                        # Nếu có dữ liệu thì tạo bảng HTML theo định dạng chuẩn
                        if specs_data:
                            specs_html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;"><thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
                            
                            for param, value in specs_data:
                                specs_html += f'<tr><td style="font-weight: bold;">{param}</td><td>{value}</td></tr>'
                            
                            # Thêm dòng Copyright vào cuối bảng
                            specs_html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
                            specs_html += '</tbody></table>'
                            print(f"  > Đã tạo bảng thông số kỹ thuật HTML với {len(specs_data)} thông số")
                        else:
                            print(f"  > Không có dữ liệu để tạo bảng")
                            specs_html = self._generate_basic_specs_table(product_code, product_info.get('Tên sản phẩm', ''))
                    else:
                        print(f"  > Không tìm thấy bảng thông số kỹ thuật trong tab mô tả")
                        specs_html = self._generate_basic_specs_table(product_code, product_info.get('Tên sản phẩm', ''))
                        specs_found = True
                else:
                    print(f"  > Không tìm thấy tab mô tả sản phẩm")
                    specs_html = self._generate_basic_specs_table(product_code, product_info.get('Tên sản phẩm', ''))
                    specs_found = True
                
                # Lưu bảng thông số kỹ thuật vào trường Tổng quan
                if specs_found and specs_html:
                    product_info['Tổng quan'] = specs_html

            # XỬ LÝ ẢNH SẢN PHẨM (chỉ khi được chọn)
            if 'product_image' in self.selected_fields or not self.selected_fields:
                # Tải và lưu ảnh sản phẩm nếu có thư mục đầu ra
                if output_dir and product_info.get('Mã sản phẩm'):
                    # Tải ảnh từ codienhaiau.com và lưu vào thư mục
                    image_url_result = self._download_codienhaiau_product_image(soup, url, output_dir, product_info.get('Mã sản phẩm', ''))
                    if image_url_result:
                         product_info['Ảnh sản phẩm'] = image_url_result
                         print(f"Đã xử lý ảnh sản phẩm: {product_info['Ảnh sản phẩm']}")
                    else:
                         # Nếu không tải được, sử dụng URL ảnh theo định dạng yêu cầu
                         product_info['Ảnh sản phẩm'] = f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_info.get('Mã sản phẩm', 'no_code')}.webp"
                         print(f"Không tải được ảnh, sử dụng URL mặc định: {product_info['Ảnh sản phẩm']}")
                else:
                    # Nếu không có thư mục đầu ra hoặc không có mã sản phẩm, chỉ lấy URL ảnh
                     if product_info.get('Mã sản phẩm'):
                         product_info['Ảnh sản phẩm'] = f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_info['Mã sản phẩm']}.webp"
                     else:
                          product_info['Ảnh sản phẩm'] = self._get_image_url(soup, url, product_info.get('Mã sản phẩm', ''))
                          print(f"Không có mã sản phẩm, lấy URL ảnh từ trang web: {product_info['Ảnh sản phẩm']}")
            
            # Log thông tin trích xuất được
            selected_info = []
            if 'product_name' in product_info:
                selected_info.append(f"Tên: {product_info.get('Tên sản phẩm', 'N/A')}")
            if 'Mã sản phẩm' in product_info:
                selected_info.append(f"Mã: {product_info.get('Mã sản phẩm', 'N/A')}")
            if 'Giá' in product_info:
                selected_info.append(f"Giá: {product_info.get('Giá', 'N/A')}")
            
            print(f"Đã trích xuất thông tin sản phẩm: {', '.join(selected_info)}")
            
            return product_info
            
        except Exception as e:
            print(f"Lỗi khi trích xuất thông tin từ {url}: {str(e)}")
            traceback.print_exc()
            # Trả về thông tin cơ bản đã thu thập được (nếu có) ngay cả khi có lỗi
            return product_info

    def extract_baa_product_info(self, url, index=1, output_dir=None):
        """
        Trích xuất thông tin sản phẩm từ trang BAA.vn với khả năng xử lý nội dung ẩn và chuẩn hóa dữ liệu
        
        Args:
            url (str): URL của trang sản phẩm
            index (int): Số thứ tự của sản phẩm
            output_dir (str, optional): Thư mục để lưu ảnh sản phẩm
            
        Returns:
            dict: Thông tin sản phẩm đã trích xuất
        """
        try:
            print(f"Đang trích xuất thông tin từ {url}")
            
            # Khởi tạo kết quả với các trường cần thiết
            product_info = {
                'STT': index,
                'URL': url,
                'Mã sản phẩm': "",
                'Tên sản phẩm': "",
                'Giá': "",
                'Tổng quan': "",
                'Ảnh sản phẩm': ""
            }
            
            # Tải nội dung trang với retry
            soup = None
            for attempt in range(self.max_retries):
                try:
                    soup = self._get_soup(url)
                    if soup:
                        break
                except Exception as e:
                    print(f"  > Lỗi khi tải trang (lần {attempt+1}/{self.max_retries}): {str(e)}")
                    if attempt < self.max_retries - 1:
                        time.sleep(1)  # Chờ 1 giây trước khi thử lại
            
            if not soup:
                print(f"  > Không thể tải nội dung trang sau {self.max_retries} lần thử")
                return product_info
            
            # Trích xuất tên sản phẩm
            product_name_elem = soup.select_one('h1.product__name')
            if product_name_elem:
                product_info['Tên sản phẩm'] = product_name_elem.text.strip()
            
            # Trích xuất mã sản phẩm
            product_code_elem = soup.select_one('.product__symbol__value')
            if product_code_elem:
                # Lấy mã sản phẩm và viết hoa
                product_code = product_code_elem.text.strip()
                product_info['Mã sản phẩm'] = product_code.upper()
                print(f"  > Tìm thấy mã sản phẩm: {product_info['Mã sản phẩm']}")
            
            # Trích xuất giá
            price_elem = soup.select_one('.product-detail__price-current')
            if price_elem:
                product_info['Giá'] = price_elem.text.strip()
                # Loại bỏ văn bản không cần thiết, chỉ giữ số
                product_info['Giá'] = re.sub(r'[^\d.,]', '', product_info['Giá'])
            
            # Trích xuất thông số kỹ thuật
            specs_table = soup.select_one('.product-info__contents table')
            if specs_table:
                # Chuẩn hóa bảng thông số
                product_info['Tổng quan'] = self._normalize_baa_specs(specs_table)
            else:
                desc_elem = soup.select_one('.product-info__contents, .product-detail__description')
                if desc_elem:
                    product_info['Tổng quan'] = str(desc_elem)
            
            # Xử lý ảnh sản phẩm
            if output_dir and product_info['Mã sản phẩm']:
                # Tạo thư mục Anh nếu chưa tồn tại
                images_dir = os.path.join(output_dir, "Anh")
                os.makedirs(images_dir, exist_ok=True)
                
                # Tìm ảnh sản phẩm
                img_urls = []
                main_img = soup.select_one('.product-detail__photo img, .product__gallery img')
                if main_img and main_img.get('src'):
                    img_urls.append(main_img['src'])
                
                # Tìm thêm ảnh từ gallery nếu có
                gallery_imgs = soup.select('.product__gallery img, .product-detail__gallery img')
                for img in gallery_imgs:
                    if img.get('src') and img['src'] not in img_urls:
                        img_urls.append(img['src'])
                
                # Tải ảnh và lưu vào thư mục
                if img_urls:
                    img_url = img_urls[0]  # Lấy ảnh đầu tiên
                    try:
                        # Đảm bảo URL ảnh đầy đủ
                        if not img_url.startswith(('http://', 'https://')):
                            # Nếu URL tương đối, tạo URL đầy đủ
                            base_url = urlparse(url).scheme + '://' + urlparse(url).netloc
                            img_url = urljoin(base_url, img_url)
                        
                        # Tải ảnh và lưu
                        img_path = os.path.join(images_dir, f"{product_info['Mã sản phẩm']}.webp")
                        response = requests.get(img_url, timeout=10)
                        if response.status_code == 200:
                            # Chuyển đổi sang WebP
                            from PIL import Image
                            from io import BytesIO
                            
                            image = Image.open(BytesIO(response.content))
                            if image.mode in ("RGBA", "P"):
                                image = image.convert("RGB")
                            
                            image.save(img_path, "WEBP", quality=90)
                            product_info['Ảnh sản phẩm'] = img_path
                            print(f"  > Đã lưu ảnh sản phẩm: {img_path}")
                    except Exception as e:
                        print(f"  > Lỗi khi tải ảnh: {str(e)}")
                        # Vẫn lưu URL ảnh nếu có lỗi khi tải
                        product_info['Ảnh sản phẩm'] = img_url
                else:
                    print(f"  > Không tìm thấy ảnh cho sản phẩm: {product_info['Mã sản phẩm']}")
            
            print(f"Đã trích xuất thông tin sản phẩm: {product_info['Tên sản phẩm']}, Mã: {product_info['Mã sản phẩm']}, Giá: {product_info['Giá']}")
            return product_info
            
        except Exception as e:
            print(f"Lỗi khi trích xuất thông tin từ {url}: {str(e)}")
            traceback.print_exc()
            return product_info
    
    def _normalize_baa_specs(self, specs_table):
        """Chuẩn hóa bảng thông số kỹ thuật từ BAA.vn, bao gồm cả nội dung ẩn và dịch sang tiếng Việt"""
        try:
            rows = []
            
            # Trích xuất tất cả các hàng từ bảng
            for tr in specs_table.select('tr'):
                tds = tr.find_all(['td', 'th'])
                if len(tds) >= 2:
                    param = tds[0].get_text(strip=True)
                    value_td = tds[1]
                    
                    # Xử lý nội dung ẩn (thường có class moreellipses và morecontent)
                    visible_text = value_td.get_text(" ", strip=True)
                    
                    # Tìm nội dung ẩn nếu có
                    hidden_content = value_td.select_one('.morecontent span')
                    if hidden_content:
                        hidden_text = hidden_content.get_text(" ", strip=True)
                        if hidden_text and hidden_text not in visible_text:
                            # Loại bỏ [...] và thay thế bằng nội dung đầy đủ
                            visible_text = visible_text.replace('[...]', '').strip() + ' ' + hidden_text
                    
                    rows.append((param, visible_text.strip()))
            
            # Dịch thông số kỹ thuật sang tiếng Việt bằng Gemini API
            if rows:
                translated_rows = self._translate_tech_terms_with_gemini(rows)
                print(f"  > [BAA] Đã dịch {len(translated_rows)} thông số sang tiếng Việt bằng AI")
            else:
                translated_rows = []
            
            # Thêm dòng Copyright vào cuối bảng
            translated_rows.append(("Copyright", "Haiphongtech.vn"))
            
            # Tạo HTML table chuẩn với dữ liệu đã dịch
            html = '<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial;"><thead><tr><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
            for param, value in translated_rows:
                html += f'<tr><td>{param}</td><td>{value}</td></tr>'
            html += '</tbody></table>'
            
            return html
        except Exception as e:
            print(f"Lỗi khi chuẩn hóa bảng thông số: {str(e)}")
            if specs_table:
                return str(specs_table)
            return ""
    
    def process_baa_categories(self, category_urls_text):
        """Xử lý danh sách các URL danh mục từ BAA.vn và trích xuất thông tin sản phẩm"""
        try:
            # Tách danh sách URL thành các dòng riêng biệt
            category_urls = [url.strip() for url in category_urls_text.splitlines() if url.strip()]
            
            # Kiểm tra tính hợp lệ của URL
            valid_urls = []
            for url in category_urls:
                if url.startswith(('http://', 'https://')) and ('baa.vn' in url):
                    valid_urls.append(url)
            
            if not valid_urls:
                return False, "Không có URL danh mục BAA.vn hợp lệ nào", None
            
            # Tạo thư mục đầu ra với timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            result_dir = os.path.join(self.upload_folder, f'baa_products_{timestamp}')
            os.makedirs(result_dir, exist_ok=True)
            
            # Tạo thư mục cho báo cáo tổng hợp
            report_dir = os.path.join(result_dir, "Bao_cao")
            os.makedirs(report_dir, exist_ok=True)
            
            # File Excel tổng hợp
            total_report_path = os.path.join(report_dir, "Tong_hop.xlsx")
            
            # Theo dõi tiến trình tổng thể
            update_progress(5, f"Bắt đầu xử lý {len(valid_urls)} danh mục từ BAA.vn")
            
            # Xử lý đa luồng cho các danh mục
            category_info = []
            max_workers = min(8, len(valid_urls))
            
            def process_category(category_url, index):
                try:
                    # Trích xuất tên danh mục từ URL hoặc nội dung trang
                    category_name = self._extract_category_name(category_url)
                    category_progress_base = 5 + (index * 90 / len(valid_urls))
                    
                    update_progress(category_progress_base, f"Đang xử lý danh mục {index+1}/{len(valid_urls)}: {category_name}")
                    
                    # Tạo thư mục cho danh mục
                    category_dir = os.path.join(result_dir, category_name)
                    os.makedirs(category_dir, exist_ok=True)
                    
                    # Tạo thư mục cho ảnh sản phẩm
                    images_dir = os.path.join(category_dir, "Anh")
                    os.makedirs(images_dir, exist_ok=True)
                    
                    # Phát hiện và xử lý trang phân trang
                    max_pages = self._get_max_pages_for_baa_category(category_url)
                    product_links = []
                    
                    update_progress(category_progress_base + 5, f"Đang thu thập liên kết sản phẩm từ {max_pages} trang của danh mục {category_name}")
                    
                    # Thu thập tất cả link sản phẩm từ tất cả trang
                    for page in range(1, max_pages + 1):
                        page_url = category_url
                        if page > 1:
                            # Xây dựng URL phân trang của BAA.vn
                            page_url = self._make_baa_pagination_url(category_url, page)
                        
                        # Lấy soup cho trang hiện tại
                        soup = self._get_soup(page_url)
                        if not soup:
                            continue
                        
                        # Trích xuất link sản phẩm từ trang hiện tại
                        page_links = self._extract_baa_links(soup, page_url)
                        if page_links:
                            product_links.extend(page_links)
                        
                        update_progress(category_progress_base + 5 + (page * 10 / max_pages),
                                      f"Đã thu thập {len(product_links)} liên kết từ trang {page}/{max_pages} của danh mục {category_name}")
                    
                    # Loại bỏ các link trùng lặp
                    product_links = list(dict.fromkeys(product_links))
                    
                    # Lưu danh sách URL sản phẩm
                    self._save_product_links(category_dir, category_name, product_links)
                    
                    update_progress(category_progress_base + 20, 
                                  f"Đã thu thập {len(product_links)} liên kết sản phẩm từ danh mục {category_name}. Bắt đầu trích xuất thông tin...")
                    
                    # Thu thập thông tin sản phẩm với xử lý đa luồng
                    product_info_list = []
                    results_queue = queue.Queue()
                    errors_queue = queue.Queue()
                    
                    # Chia thành các batch nhỏ hơn để cập nhật tiến độ thường xuyên
                    batch_size = max(1, min(20, len(product_links) // 5))  # Tối đa 20 sản phẩm mỗi batch
                    batches = [product_links[i:i+batch_size] for i in range(0, len(product_links), batch_size)]
                    
                    for batch_idx, batch in enumerate(batches):
                        batch_progress_base = category_progress_base + 20 + (batch_idx * 50 / len(batches))
                        update_progress(batch_progress_base, 
                                      f"Đang xử lý batch {batch_idx+1}/{len(batches)} ({len(batch)} sản phẩm) từ danh mục {category_name}")
                        
                        # Xử lý đa luồng cho mỗi batch
                        threads = []
                        for i, product_url in enumerate(batch):
                            thread = threading.Thread(
                                target=self._download_product_info_worker,
                                args=(product_url, i + batch_idx * batch_size + 1, category_dir, False, results_queue, errors_queue)
                            )
                            threads.append(thread)
                            thread.start()
                        
                        # Theo dõi tiến độ xử lý batch hiện tại
                        processed = 0
                        while processed < len(batch):
                            if not results_queue.empty():
                                result = results_queue.get()
                                product_info_list.append(result)
                                processed += 1
                                # Cập nhật tiến độ
                                update_progress(batch_progress_base + (processed * 50 / len(batch) / len(batches)),
                                              f"Đã xử lý {len(product_info_list)}/{len(product_links)} sản phẩm từ danh mục {category_name}")
                            
                            # Kiểm tra các lỗi
                            while not errors_queue.empty():
                                error = errors_queue.get()
                                print(f"Lỗi khi xử lý sản phẩm: {error}")
                            
                            time.sleep(0.1)  # Tránh CPU quá tải
                        
                        # Đảm bảo tất cả các thread trong batch đã hoàn thành
                        for thread in threads:
                            thread.join()
                    
                    # Tạo báo cáo Excel cho danh mục này
                    category_excel_path = os.path.join(category_dir, f"{category_name}.xlsx")
                    
                    if product_info_list:
                        df = pd.DataFrame(product_info_list)
                        df.to_excel(category_excel_path, index=False)
                    
                    # Thêm vào thông tin danh mục
                    return {
                        'tên': category_name,
                        'url': category_url,
                        'đường dẫn': category_dir,
                        'số sản phẩm': len(product_links),
                        'đã xử lý': len(product_info_list),
                        'báo cáo': category_excel_path,
                        'sản phẩm': product_info_list
                    }
                    
                except Exception as e:
                    print(f"Lỗi khi xử lý danh mục {category_url}: {str(e)}")
                    traceback.print_exc()
                    return {
                        'tên': self._extract_category_name(category_url),
                        'url': category_url,
                        'lỗi': str(e),
                        'số sản phẩm': 0,
                        'đã xử lý': 0,
                        'sản phẩm': []
                    }
            
            # Xử lý các danh mục
            all_results = []
            all_products = []
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_url = {executor.submit(process_category, url, i): url for i, url in enumerate(valid_urls)}
                
                for future in concurrent.futures.as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        result = future.result()
                        if result:
                            all_results.append(result)
                            if 'sản phẩm' in result and result['sản phẩm']:
                                all_products.extend(result['sản phẩm'])
                    except Exception as e:
                        print(f"Lỗi khi xử lý {url}: {str(e)}")
            
            update_progress(95, f"Đã xử lý {len(all_results)} danh mục. Đang tạo báo cáo tổng hợp...")
            
            # Tạo báo cáo tổng hợp
            if all_products:
                df_all = pd.DataFrame(all_products)
                df_all.to_excel(total_report_path, index=False)
            
            # Tạo báo cáo tổng quan
            summary_data = []
            for result in all_results:
                summary_data.append({
                    'Tên danh mục': result.get('tên', 'N/A'),
                    'URL danh mục': result.get('url', 'N/A'),
                    'Số sản phẩm': result.get('số sản phẩm', 0),
                    'Số sản phẩm đã xử lý': result.get('đã xử lý', 0),
                    'Tỷ lệ thành công': f"{result.get('đã xử lý', 0) * 100 / result.get('số sản phẩm', 1):.2f}%" if result.get('số sản phẩm', 0) > 0 else "0%"
                })
            
            if summary_data:
                summary_path = os.path.join(report_dir, "Tong_quan.xlsx")
                pd.DataFrame(summary_data).to_excel(summary_path, index=False)
            
            # Nén kết quả
            update_progress(98, "Đang nén kết quả thành file ZIP...")
            
            zip_filename = f'baa_products_{timestamp}.zip'
            zip_path = os.path.join(self.upload_folder, zip_filename)
            
            # Nén toàn bộ thư mục kết quả
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, self.upload_folder)
                        zipf.write(file_path, arcname)
            
            total_products = sum(result.get('số sản phẩm', 0) for result in all_results)
            processed_products = sum(result.get('đã xử lý', 0) for result in all_results)
            
            update_progress(100, f"Hoàn thành! Đã xử lý {processed_products}/{total_products} sản phẩm từ {len(valid_urls)} danh mục")
            
            return True, f"Đã cào thành công {processed_products}/{total_products} sản phẩm từ {len(valid_urls)} danh mục BAA.vn", zip_path
        
        except Exception as e:
            error_message = str(e)
            print(f"Lỗi khi xử lý danh mục BAA.vn: {error_message}")
            traceback.print_exc()
            return False, f"Lỗi: {error_message}", None
    
    def _get_max_pages_for_baa_category(self, category_url):
        """Phát hiện số trang tối đa cho danh mục BAA.vn"""
        try:
            # Lấy nội dung trang
            soup = self._get_soup(category_url)
            if not soup:
                return 1
            
            # Tìm các thành phần phân trang
            pagination = soup.select_one('.pagination, .page-list, nav[aria-label="Page navigation"]')
            if not pagination:
                return 1
            
            # Tìm số trang lớn nhất
            max_page = 1
            page_links = pagination.select('a.page-link')
            
            for link in page_links:
                text = link.get_text(strip=True)
                # Thử chuyển đổi văn bản thành số
                try:
                    page_num = int(text)
                    max_page = max(max_page, page_num)
                except ValueError:
                    pass
            
            return max(max_page, 1)
        except Exception as e:
            print(f"Lỗi khi xác định số trang tối đa: {str(e)}")
            return 1
    
    def _make_baa_pagination_url(self, base_url, page_number):
        """Tạo URL phân trang cho BAA.vn"""
        try:
            # Phân tích URL gốc
            parsed_url = urlparse(base_url)
            path = parsed_url.path
            query = parsed_url.query
            
            # Kiểm tra xem URL đã có tham số page chưa
            params = {}
            if query:
                for param in query.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        params[key] = value
            
            # Cập nhật tham số page
            params['page'] = str(page_number)
            
            # Tạo query string mới
            new_query = '&'.join([f"{key}={value}" for key, value in params.items()])
            
            # Tạo URL mới
            new_url = f"{parsed_url.scheme}://{parsed_url.netloc}{path}?{new_query}"
            return new_url
        except Exception as e:
            print(f"Lỗi khi tạo URL phân trang: {str(e)}")
            # Nếu có lỗi, thử thêm ?page=X vào cuối URL
            if '?' in base_url:
                return f"{base_url}&page={page_number}"
            else:
                return f"{base_url}?page={page_number}"
            
    def _extract_baa_links(self, soup, current_url):
        """Trích xuất các liên kết sản phẩm từ trang danh mục BAA.vn"""
        try:
            links = []
            
            # Tìm các thẻ sản phẩm
            product_cards = soup.select('.product-item, .product-card, .item-product')
            
            if not product_cards:
                # Tìm kiếm theo cách khác nếu không tìm thấy card sản phẩm
                product_links = soup.select('a.product-name, .product-title > a, .item-name > a')
                for link in product_links:
                    href = link.get('href')
                    if href:
                        full_url = self._make_full_url(href, current_url)
                        if full_url and self._is_baa_product_url(full_url):
                            links.append(full_url)
            else:
                # Xử lý từng card sản phẩm
                for card in product_cards:
                    # Tìm liên kết trong card
                    link = card.select_one('a.product-name, .product-title > a, h3 > a, .item-name > a')
                    if link and link.get('href'):
                        href = link.get('href')
                        full_url = self._make_full_url(href, current_url)
                        if full_url and self._is_baa_product_url(full_url):
                            links.append(full_url)
            
            # Loại bỏ các URL trùng lặp
            return list(dict.fromkeys(links))
        except Exception as e:
            print(f"Lỗi khi trích xuất liên kết từ {current_url}: {str(e)}")
            return []
    
    def _is_baa_product_url(self, url):
        """Kiểm tra xem URL có phải là URL sản phẩm BAA.vn không"""
        try:
            if not url or not isinstance(url, str):
                return False
            
            # Kiểm tra URL có phải từ BAA.vn không
            if 'baa.vn' not in url:
                return False
            
            # Kiểm tra URL có chứa các dấu hiệu của URL sản phẩm không
            product_indicators = ['/san-pham/', '/product/', '/p/']
            
            for indicator in product_indicators:
                if indicator in url:
                    return True
            
            return False
        except Exception:
            return False

    def _generate_product_spec_table(self, product_code, product_name):
        """Tạo bảng thông số kỹ thuật cho sản phẩm"""
        table_html = '<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial;"><thead><tr><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
        
        # Xác định loại sản phẩm dựa trên mã sản phẩm
        product_type = ""
        product_specs = {}
        
        # Xử lý cho bộ chuyển đổi tín hiệu CN-6xxx
        if product_code and product_code.startswith("CN-6"):
            product_type = "signal_converter"
            
            # Xác định loại tín hiệu đầu vào/đầu ra từ mã sản phẩm
            input_type = ""
            output_type = ""
            
            if "C1" in product_code:
                input_type = "RTD : JPt100Ω, DPt100Ω, DPt50Ω, Cu50Ω, Cu100Ω"
                output_type = "K, J, E, T, R, B, S, N, C, L, U, PLII"
            elif "C2" in product_code:
                input_type = "TC : K, J, E, T, R, B, S, N, C, L, U, PLII"
                output_type = "RTD : JPt100Ω, DPt100Ω, DPt50Ω, Cu50Ω, Cu100Ω"
            elif "R1" in product_code:
                input_type = "RTD : JPt100Ω, DPt100Ω, DPt50Ω, Cu50Ω, Cu100Ω"
                output_type = "4-20mA, 0-20mA"
            elif "R2" in product_code:
                input_type = "TC : K, J, E, T, R, B, S, N, C, L, U, PLII"
                output_type = "4-20mA, 0-20mA"
            elif "R4" in product_code:
                input_type = "Nhiệt điện trở NTC/PTC"
                output_type = "4-20mA, 0-20mA"
            elif "V1" in product_code:
                input_type = "RTD : JPt100Ω, DPt100Ω, DPt50Ω, Cu50Ω, Cu100Ω"
                output_type = "1-5V DC, 0-5V DC, 0-10V DC"
            elif "V2" in product_code:
                input_type = "TC : K, J, E, T, R, B, S, N, C, L, U, PLII"
                output_type = "1-5V DC, 0-5V DC, 0-10V DC"
            
            # Xác định nguồn cấp dựa trên mã sản phẩm
            power_supply = "100-240VAC"
            if "6401" in product_code:
                power_supply = "24VDC"
            
            # Tạo thông số sản phẩm
            product_specs = {
                "Nguồn cấp": power_supply,
                "Loại ngõ vào_RTD": input_type,
                "Loại ngõ vào_TC": output_type,
                "Nhiệt độ xung quanh": "-10 đến 50°C, bảo quản: -20 đến 60°C",
                "Độ ẩm xung quanh": "35 đến 85%RH, bảo quản: 35 đến 85%RH",
                "Tiêu chuẩn": "RoHS"
            }
        # Xử lý cho bộ chuyển tín hiệu SCM
        elif product_code and product_code.startswith("SCM-"):
            product_type = "scm_converter"
            
            # Tạo thông số sản phẩm cho SCM
            product_specs = {
                "Nguồn cấp": "24VDC",
                "Loại ngõ vào": "USB",
                "Loại ngõ ra": "RS485",
                "Tốc độ truyền": "9600, 19200, 38400, 57600, 115200 bps",
                "Nhiệt độ xung quanh": "-10 đến 50°C, bảo quản: -20 đến 60°C",
                "Độ ẩm xung quanh": "35 đến 85%RH, bảo quản: 35 đến 85%RH",
                "Tiêu chuẩn": "CE"
            }
        
        # Thêm các thông số vào bảng
        if product_specs:
            for key, value in product_specs.items():
                table_html += f'<tr><td>{key}</td><td>{value}</td></tr>'
        else:
            # Nếu không xác định được loại sản phẩm, thêm thông tin cơ bản
            table_html += f'<tr><td>Mã sản phẩm</td><td>{product_code}</td></tr>'
            table_html += f'<tr><td>Tên sản phẩm</td><td>{product_name}</td></tr>'
            table_html += f'<tr><td>Tiêu chuẩn</td><td>CE</td></tr>'
        
        # Thêm copyright
        table_html += '<tr><td>Copyright</td><td>Haiphongtech.vn</td></tr>'
        table_html += '</tbody></table>'
        
        return table_html

    def _extract_standard_from_img(self, img):
        """Trích xuất thông tin tiêu chuẩn từ thẻ img"""
        if not img:
            return "CE"
        
        alt_text = img.get('alt', '').strip()
        src = img.get('src', '')
        
        # Trích xuất tên tiêu chuẩn từ alt hoặc src của ảnh
        standard_name = ''
        if alt_text:
            # Trích xuất từ alt text
            parts = alt_text.split('-')
            if len(parts) > 1:
                standard_name = parts[-1].strip()
            else:
                standard_name = alt_text
        elif src:
            # Nếu không có alt, trích xuất từ đường dẫn ảnh
            filename = os.path.basename(src)
            parts = filename.split('.')
            if len(parts) > 1:
                name_parts = parts[0].split('-')
                if len(name_parts) > 1:
                    standard_name = name_parts[-1].strip()
        
        # Xác định tiêu chuẩn dựa trên tên
        if 'CE' in standard_name or 'ce' in standard_name.lower():
            return 'CE'
        elif 'UL' in standard_name or 'ul' in standard_name.upper():
            return 'UL'
        elif 'CSA' in standard_name or 'csa' in standard_name.upper():
            return 'CSA'
        elif 'ISO' in standard_name or 'iso' in standard_name.upper():
            return 'ISO'
        elif 'RoHS' in standard_name or 'rohs' in standard_name.upper():
            return 'RoHS'
        
        # Nếu không xác định được tên, đặt mặc định là RoHS
        return 'RoHS'

    def _get_image_url(self, soup, product_url, product_code):
        """Lấy URL ảnh sản phẩm (không tải về)"""
        # Nếu có mã sản phẩm, trả về URL theo định dạng yêu cầu
        if product_code:
            return f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_code}.webp"
        
        # Nếu không có mã sản phẩm, tìm URL ảnh từ trang web
        try:
            # Tìm ảnh trong div có id="cboxLoadedContent"
            cbox_content = soup.select_one('#cboxLoadedContent')
            if cbox_content:
                img = cbox_content.select_one('img.cboxPhoto')
                if img and img.get('src'):
                    return img.get('src')
        
            # Nếu không tìm thấy, thử các selector khác
            gallery_selectors = [
                '.woocommerce-product-gallery__image a',
                '.product-thumbnails a',
                '.thumbnails a',
                '.images img.wp-post-image',
                '.product-images img',
                'img.wp-post-image'
            ]
            
            for selector in gallery_selectors:
                elements = soup.select(selector)
                for element in elements:
                    if element.name == 'a' and element.get('href'):
                        return element.get('href')
                    elif element.name == 'img':
                        if element.get('data-large_image'):
                            return element.get('data-large_image')
                        elif element.get('data-src'):
                            return element.get('data-src')
                        elif element.get('src'):
                            return element.get('src')
        
            # Không tìm thấy URL ảnh
            print(f"Không tìm thấy URL ảnh cho sản phẩm: {product_url}")
            return ""
        except Exception as e:
            print(f"Lỗi khi tìm URL ảnh sản phẩm: {str(e)}")
            return ""

    def _download_codienhaiau_product_image(self, soup, product_url, output_dir, product_code):
        """Tải ảnh sản phẩm từ trang web và lưu vào thư mục"""
        try:
            # Tạo thư mục images nếu chưa tồn tại
            images_dir = os.path.join(output_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)
            
            # Định dạng tên file ảnh
            if not product_code:
                product_code = os.path.basename(product_url).replace('/', '_')
            
            image_filename = f"{product_code}.webp"
            image_path = os.path.join(images_dir, image_filename)
            
            # Tìm URL ảnh từ trang web
            image_url = None
            
            # Tìm ảnh trong div có id="cboxLoadedContent" - đây là ưu tiên số 1
            cbox_content = soup.select_one('#cboxLoadedContent')
            if cbox_content:
                img = cbox_content.select_one('img.cboxPhoto')
                if img and img.get('src'):
                    image_url = img.get('src')
                    print(f"Đã tìm thấy ảnh chất lượng cao trong cboxLoadedContent: {image_url}")
            
            # Nếu không tìm thấy, thử tìm trong gallery
            if not image_url:
                # Tìm kiếm ảnh qua các selector
                found_image = False
                gallery_selectors = [
                    '.woocommerce-product-gallery__image a',
                    '.product-thumbnails a',
                    '.thumbnails a',
                    'a.woocommerce-main-image',
                    'a.image-lightbox'
                ]
                
                for selector in gallery_selectors:
                    if found_image:
                        continue
                        
                    gallery_links = soup.select(selector)
                    for link in gallery_links:
                        href = link.get('href')
                        if href and (href.endswith('.jpg') or href.endswith('.png') or href.endswith('.jpeg') or href.endswith('.webp') or '/uploads/' in href):
                            # Loại bỏ các đường dẫn thumbnail có kích thước nhỏ
                            if not re.search(r'[-_]\d+x\d+\.(jpg|png|jpeg|webp)', href):
                                image_url = href
                                found_image = True
                                break
            
            # Nếu vẫn không tìm thấy, thử tìm trong thẻ img
            if not image_url:
                # Tìm kiếm ảnh qua các selector
                found_image = False
                img_selectors = [
                    '.woocommerce-product-gallery__image img',
                    '.images img.wp-post-image',
                    '.product-images img',
                    'img.wp-post-image',
                    '.product-main-image img',
                    '.product-gallery-slider img'
                ]
                
                for selector in img_selectors:
                    if found_image:
                        continue
                        
                    images = soup.select(selector)
                    for img in images:
                        # Ưu tiên tìm ảnh gốc từ các thuộc tính
                        if img.get('data-large_image'):
                            image_url = img.get('data-large_image')
                            found_image = True
                            break
                        elif img.get('data-src'):
                            image_url = img.get('data-src')
                            found_image = True
                            break
                        elif img.get('src'):
                            image_url = img.get('src')
                            found_image = True
                            break
            
            # Nếu không tìm thấy URL ảnh, trả về URL ảnh theo định dạng yêu cầu
            if not image_url:
                print(f"Không tìm thấy URL ảnh cho sản phẩm: {product_url}")
                return f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_code}.webp"
            
            # Tải ảnh về
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Referer': product_url
                }
                
                response = requests.get(image_url, headers=headers, stream=True, timeout=30)
                response.raise_for_status()
                
                # Lưu ảnh
                with open(image_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                print(f"Đã tải và lưu ảnh sản phẩm: {image_path}")
                
                # Trả về URL ảnh theo định dạng mới cho Haiphongtech
                return f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_code}.webp"
                
            except Exception as e:
                print(f"Lỗi khi tải ảnh từ {image_url}: {str(e)}")
                return f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_code}.webp"
        
        except Exception as e:
            print(f"Lỗi khi xử lý ảnh sản phẩm: {str(e)}")
            traceback.print_exc()
            return f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_code}.webp"

    def process_codienhaiau_categories(self, category_urls_text):
        """Xử lý danh sách URL danh mục trên codienhaiau.com"""
        try:
            # Tách thành danh sách URL, bỏ qua dòng trống
            raw_urls = category_urls_text.strip().split('\n')
            urls = [url.strip() for url in raw_urls if url.strip()]
            
            # Lọc các URL hợp lệ
            valid_urls = []
            invalid_urls = []
            
            # Gửi thông báo bắt đầu
            emit_progress(0, 'Đang kiểm tra URL danh mục...')
            
            # Kiểm tra các URL
            for url in urls:
                if is_valid_url(url) and 'codienhaiau.com' in url:
                    valid_urls.append(url)
                else:
                    invalid_urls.append(url)
        
            if not valid_urls:
                raise ValueError('Không có URL danh mục codienhaiau.com hợp lệ!')
            
            # Gửi thông báo cập nhật
            emit_progress(5, f'Đã tìm thấy {len(valid_urls)} URL danh mục hợp lệ')
            
            # Tạo thư mục chính để lưu kết quả
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            result_dir = os.path.join(self.upload_folder, f'codienhaiau_info_{timestamp}') if self.upload_folder else f'codienhaiau_info_{timestamp}'
            os.makedirs(result_dir, exist_ok=True)
            
            # Xử lý từng URL danh mục
            category_info = []
            
            for i, category_url in enumerate(valid_urls):
                try:
                    category_progress_base = 5 + int((i / len(valid_urls)) * 85)
                    emit_progress(category_progress_base, f'Đang xử lý danh mục {i+1}/{len(valid_urls)}: {category_url}')
                    
                    # Trích xuất tên danh mục từ URL
                    category_name = self._extract_category_name(category_url)
                    
                    # Tạo thư mục cho danh mục này
                    category_dir = os.path.join(result_dir, category_name)
                    os.makedirs(category_dir, exist_ok=True)
                    
                    # Thu thập liên kết sản phẩm từ danh mục này
                    emit_progress(category_progress_base + 5, f'Đang thu thập liên kết sản phẩm từ danh mục: {category_name}')
                    
                    # Lấy tổng số trang dựa trên danh mục cụ thể
                    max_pages = self._get_max_pages_for_category(category_url)
                    log_and_emit(f"[THÔNG TIN] Danh mục {category_name} có ước tính {max_pages} trang")
                    
                    # Thu thập liên kết từ trang đầu tiên để xác định cấu trúc phân trang
                    initial_soup = self._get_soup(category_url)
                    if not initial_soup:
                        log_and_emit(f"[LỖI] Không thể tải trang đầu tiên của danh mục {category_name}")
                        continue
                    
                    # Thu thập tất cả sản phẩm (bao gồm cả phân trang)
                    category_products = self._extract_codienhaiau_links(initial_soup, category_url)
                    
                    if category_products:
                        # Lưu các liên kết sản phẩm vào file txt
                        self._save_product_links(category_dir, category_name, category_products)
                        
                        # Thu thập thông tin sản phẩm
                        product_info_list = self._collect_product_info(category_dir, category_name, category_products, category_progress_base)
                        
                        # Thêm thông tin danh mục vào danh sách
                        category_info.append({
                            'Tên danh mục': category_name,
                            'URL danh mục': category_url,
                            'Số sản phẩm': len(category_products),
                            'Số sản phẩm có thông tin': len(product_info_list)
                        })
                        
                        log_and_emit(f"[THÀNH CÔNG] Đã thu thập {len(category_products)} sản phẩm từ danh mục {category_name}")
                    else:
                        log_and_emit(f"[CẢNH BÁO] Không tìm thấy sản phẩm nào trong danh mục: {category_url}")
                        print(f"Không tìm thấy sản phẩm nào trong danh mục: {category_url}")
                        
                except Exception as e:
                    log_and_emit(f"[LỖI] Khi xử lý danh mục {category_url}: {str(e)}")
                    print(f"Lỗi khi xử lý danh mục {category_url}: {str(e)}")
                    traceback.print_exc()
            
            # Tạo báo cáo và file nén
            self._create_reports(result_dir, category_info, valid_urls)
            
            # Nén kết quả
            zip_filename = f'codienhaiau_info_{timestamp}.zip'
            zip_path = os.path.join(self.upload_folder, zip_filename) if self.upload_folder else zip_filename
            
            # Đảm bảo file ZIP được tạo đúng cách
            if not utils.create_zip_from_folder(result_dir, zip_path):
                print(f"Không thể tạo file ZIP. Thử tạo lại...")
                # Thử tạo lại file ZIP nếu không thành công
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for root, dirs, files in os.walk(result_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, os.path.dirname(result_dir))
                            print(f"Thêm file vào ZIP: {arcname}")
                            zipf.write(file_path, arcname)
            
            # Kiểm tra file ZIP đã tạo
            if not os.path.exists(zip_path):
                print(f"CẢNH BÁO: File ZIP không tồn tại sau khi tạo: {zip_path}")
            else:
                zip_size = os.path.getsize(zip_path)
                print(f"File ZIP đã được tạo: {zip_path} (Kích thước: {zip_size} bytes)")
            
            # Lưu đường dẫn file ZIP vào session nếu đang chạy trong ứng dụng web
            try:
                if 'session' in globals() or 'session' in locals():
                    session['last_download'] = zip_filename
            except (ImportError, RuntimeError):
                # Không chạy trong ngữ cảnh Flask hoặc không có module Flask
                pass
            
            # Trả về thông báo thành công
            return True, f'Đã hoàn tất thu thập dữ liệu từ {len(valid_urls)} danh mục sản phẩm trên codienhaiau.com', zip_path
            
        except Exception as e:
            error_message = str(e)
            traceback.print_exc()
            return False, f'Lỗi: {error_message}', None
            
    def _get_soup(self, url):
        """Lấy nội dung trang web và trả về đối tượng BeautifulSoup với retry logic tối ưu"""
        
        # Kiểm tra cache trước
        if url in self.response_cache:
            cached_response = self.response_cache[url]
            if hasattr(cached_response, 'timestamp'):
                # Cache trong 5 phút
                import time
                if time.time() - cached_response.timestamp < 300:
                    return cached_response.soup
        
        # Lấy session tối ưu cho domain này
        session = self._get_session_for_domain(url)
        
        # Lấy semaphore cho domain
        domain = urlparse(url).netloc
        if domain not in self.domain_semaphores:
            self.domain_semaphores[domain] = threading.Semaphore(self.max_concurrent_per_domain)
        
        soup = None
        
        with self.domain_semaphores[domain]:
            for attempt in range(self.max_retries):
                try:
                    response = session.get(
                        url, 
                        timeout=self.request_timeout,
                        allow_redirects=True,
                        stream=False  # Không stream để tối ưu memory
                    )
                    response.raise_for_status()
                    
                    # Parse HTML
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Cache response
                    import time
                    class CachedResponse:
                        def __init__(self, soup):
                            self.soup = soup
                            self.timestamp = time.time()
                    
                    self.response_cache[url] = CachedResponse(soup)
                    
                    # Giới hạn cache size
                    if len(self.response_cache) > 1000:
                        # Xóa 20% cache cũ nhất
                        old_keys = list(self.response_cache.keys())[:200]
                        for key in old_keys:
                            del self.response_cache[key]
                    
                    break
                    
                except Exception as e:
                    if attempt < self.max_retries - 1:
                        log_and_emit(f"[NETWORK] 🔄 Lỗi khi tải {url}, thử lại lần {attempt + 1}: {str(e)}")
                        time.sleep(self.retry_delay)
                    else:
                        log_and_emit(f"[NETWORK] ❌ Lỗi khi tải {url} sau {self.max_retries} lần thử: {str(e)}")
                        
        # Giải phóng session
        self._release_session(session)
        
        return soup

    def _extract_codienhaiau_links(self, soup, current_url):
        """Trích xuất liên kết sản phẩm và phân trang từ trang codienhaiau.com với tối ưu hóa đa luồng"""
        product_urls = set()
        
        try:
            log_message = f"Đang trích xuất liên kết từ URL: {current_url}"
            log_and_emit(f"[CRAWLER] {log_message}")
            
            # Kiểm tra xem có phải là trang danh mục không
            if not soup or 'category' not in current_url:
                log_message = f"URL không phải là trang danh mục: {current_url}"
                log_and_emit(f"[LỖI] {log_message}")
                return []
            
            # Kiểm tra xem có sản phẩm nào không
            if soup.select('.woocommerce-info.woocommerce-no-products-found'):
                log_message = f"Không tìm thấy sản phẩm nào trong danh mục: {current_url}"
                log_and_emit(f"[CẢNH BÁO] {log_message}")
                return []
            
            # Tìm các liên kết sản phẩm với nhiều CSS selector khác nhau
            product_link_selectors = [
                '.products li.product a[href]',
                '.product-small .box a.woocommerce-LoopProduct-link',
                'li.product a[href]',
                'ul.products a[href]',
                '.products .product a[href]:not(.add_to_cart_button)',
                'h2.woocommerce-loop-product__title',
                'a.woocommerce-LoopProduct-link',
                '.products .product-title a',
                '.product-title a',
                '.product-small.box a.image-fade_in_back',
                '.box-image a[href]',
                '.box-text a[href]'
            ]
            
            # Tối ưu hóa trích xuất links
            log_and_emit(f"[PHÂN TÍCH] 🔍 Tìm liên kết sản phẩm với {len(product_link_selectors)} selector khác nhau")
            
            # Xử lý song song các selector
            def extract_links_by_selector(selector):
                """Extract links for a specific selector"""
                links = []
                try:
                    elements = soup.select(selector)
                    for element in elements:
                        # Đối với h2, tìm thẻ a cha gần nhất
                        if element.name == 'h2':
                            anchor = element.find_parent('a')
                            if anchor and anchor.get('href'):
                                href = anchor.get('href')
                            else:
                                # Hoặc tìm thẻ a con
                                anchor = element.find('a')
                                if anchor and anchor.get('href'):
                                    href = anchor.get('href')
                                else:
                                    continue
                        else:
                            href = element.get('href', '')
                        
                        # Kiểm tra xem liên kết này có phải là liên kết sản phẩm không
                        if href and '/product/' in href and not href.endswith('/product/'):
                            # Đảm bảo URL đầy đủ
                            full_url = urljoin(current_url, href)
                            links.append(full_url)
                            
                except Exception as e:
                    log_and_emit(f"[ERROR] Lỗi selector '{selector}': {str(e)}")
                    
                return selector, len(links), links
            
            # Sử dụng đa luồng để xử lý các selector
            all_links = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(product_link_selectors))) as executor:
                futures = [executor.submit(extract_links_by_selector, selector) for selector in product_link_selectors]
                
                for future in concurrent.futures.as_completed(futures):
                    try:
                        selector, count, links = future.result()
                        if count > 0:
                            all_links.extend(links)
                            log_and_emit(f"[SELECTOR] ✅ '{selector}': {count} liên kết")
                    except Exception as e:
                        log_and_emit(f"[ERROR] Lỗi xử lý selector: {str(e)}")
            
            # Loại bỏ trùng lặp
            product_urls.update(all_links)
            
            log_and_emit(f"[KẾT QUẢ] ✅ Tìm thấy {len(product_urls)} sản phẩm độc nhất từ trang hiện tại")
            
            # ---------------- XỬ LÝ PHÂN TRANG TỐI ƯU ----------------
            
            # Xác định mẫu URL cơ sở
            base_url = current_url
            page_number = 1
            
            # Trích xuất số trang hiện tại từ URL nếu có
            current_page_match = re.search(r'/page/(\d+)', current_url)
            if current_page_match:
                page_number = int(current_page_match.group(1))
                base_url = re.sub(r'/page/\d+/', '/', current_url)
                log_and_emit(f"[PHÂN TRANG] 📄 Đang ở trang {page_number}, URL cơ sở: {base_url}")
            
            # Đảm bảo base_url không có tham số truy vấn
            if '?' in base_url:
                base_url = base_url.split('?')[0]
            
            # Đảm bảo base_url kết thúc bằng '/'
            if not base_url.endswith('/'):
                base_url += '/'
            
            # Tạo mẫu URL cho phân trang
            page_url_template = f"{base_url}page/{{0}}/"
            
            # TÌM TỔNG SỐ TRANG - tối ưu hóa với đa luồng
            max_page = 1
            total_products = 0
            
            def find_pagination_info():
                """Tìm thông tin phân trang song song"""
                nonlocal max_page, total_products
                
                # Tìm từ widget bộ lọc
                widget_count = self._extract_product_count_from_widgets(soup, current_url)
                if widget_count > 0:
                    total_products = widget_count
                    calculated_pages = math.ceil(widget_count / 50) + 1
                    max_page = max(max_page, calculated_pages)
                    log_and_emit(f"[WIDGET] 📊 Tìm thấy {widget_count} sản phẩm, ước tính {calculated_pages} trang")
                
                # Tìm từ pagination links
                pagination_max = self._extract_max_page_from_pagination(soup)
                if pagination_max > max_page:
                    max_page = pagination_max
                    log_and_emit(f"[PAGINATION] 🔗 Tìm thấy tối đa {pagination_max} trang từ links")
                
                # Tìm từ result count
                if total_products == 0:
                    result_count = self._extract_product_count_from_results(soup)
                    if result_count > 0:
                        total_products = result_count
                        calculated_pages = math.ceil(result_count / 50) + 1
                        max_page = max(max_page, calculated_pages)
                        log_and_emit(f"[RESULTS] 📈 Tìm thấy {result_count} sản phẩm từ kết quả tìm kiếm")
            
            # Chạy tìm kiếm thông tin phân trang
            find_pagination_info()
            
            # Áp dụng giới hạn từ cài đặt
            max_pages_setting = self._get_max_pages_for_category(current_url)
            if max_page < max_pages_setting:
                log_and_emit(f"[SETTING] ⚙️ Áp dụng giới hạn cài đặt: {max_pages_setting} trang")
                max_page = max_pages_setting
            
            # Thu thập tất cả sản phẩm từ các trang phân trang
            if max_page > 1:
                log_and_emit(f"[PAGINATION] 🚀 Bắt đầu thu thập từ {max_page} trang với đa luồng")
                
                # Tạo URLs cho tất cả các trang
                page_urls = []
                for p in range(1, max_page + 1):
                    if p != page_number:  # Bỏ qua trang hiện tại
                        page_url = page_url_template.format(p)
                        page_urls.append((p, page_url))
                
                # Xử lý đa luồng cho phân trang
                def process_page(page_info):
                    """Xử lý một trang phân trang"""
                    page_num, page_url = page_info
                    page_products = set()
                    
                    try:
                        # Thêm delay nhỏ để tránh overload
                        time.sleep(0.05)
                        
                        page_soup = self._get_soup(page_url)
                        if not page_soup:
                            return page_num, []
                        
                        # Trích xuất sản phẩm từ trang này
                        for selector in product_link_selectors[:3]:  # Chỉ dùng 3 selector chính để tăng tốc
                            try:
                                links = page_soup.select(selector)
                                for link in links:
                                    href = link.get('href', '') if link.name != 'h2' else (
                                        link.find_parent('a').get('href', '') if link.find_parent('a') else 
                                        link.find('a').get('href', '') if link.find('a') else ''
                                    )
                                    
                                    if href and '/product/' in href and not href.endswith('/product/'):
                                        full_url = urljoin(current_url, href)
                                        page_products.add(full_url)
                                        
                            except Exception as e:
                                continue
                                
                        return page_num, list(page_products)
                        
                    except Exception as e:
                        log_and_emit(f"[ERROR] ❌ Lỗi trang {page_num}: {str(e)}")
                        return page_num, []
                
                # Sử dụng đa luồng để xử lý phân trang
                all_products = list(product_urls)
                processed_pages = 0
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers_parse) as executor:
                    # Submit tất cả các trang
                    page_futures = {executor.submit(process_page, page_info): page_info for page_info in page_urls}
                    
                    # Xử lý kết quả khi hoàn thành
                    for future in concurrent.futures.as_completed(page_futures):
                        try:
                            page_num, page_products = future.result()
                            processed_pages += 1
                            
                            # Thêm sản phẩm mới
                            new_products = [p for p in page_products if p not in all_products]
                            all_products.extend(new_products)
                            
                            # Cập nhật progress
                            progress = int((processed_pages / len(page_urls)) * 100)
                            log_and_emit(f"[PAGE] ✅ Trang {page_num}: +{len(new_products)} sản phẩm mới ({progress}%)")
                            
                        except Exception as e:
                            log_and_emit(f"[ERROR] ❌ Lỗi xử lý future: {str(e)}")
                
                # Loại bỏ trùng lặp cuối cùng
                all_products = list(set(all_products))
                log_and_emit(f"[FINAL] 🎯 Tổng cộng: {len(all_products)} sản phẩm từ {max_page} trang")
                
                return all_products
            else:
                log_and_emit(f"[SINGLE] 📄 Chỉ có một trang: {len(product_urls)} sản phẩm")
                return list(product_urls)
            
        except Exception as e:
            log_and_emit(f"[ERROR] ❌ Lỗi khi trích xuất liên kết từ {current_url}: {str(e)}")
            traceback.print_exc()
            return list(product_urls)

    def _extract_product_count_from_widgets(self, soup, current_url):
        """Trích xuất số lượng sản phẩm từ widget bộ lọc"""
        try:
            # Xác định thương hiệu từ URL
            category_name = None
            category_parts = current_url.split('/')
            for part in category_parts:
                if 'autonics' in part.lower():
                    category_name = 'autonics'
                    break
            
            if category_name:
                widget_selectors = [
                    '.woocommerce-widget-layered-nav-list .count',
                    '.product-categories .count',
                    '.widget_layered_nav .count',
                ]
                
                for selector in widget_selectors:
                    count_elements = soup.select(selector)
                    for count_element in count_elements:
                        parent_li = count_element.find_parent('li')
                        if parent_li:
                            link_element = parent_li.find('a')
                            if link_element and category_name in link_element.get_text().lower():
                                count_text = count_element.get_text().strip()
                                count_match = re.search(r'\((\d+)\)', count_text)
                                if count_match:
                                    return int(count_match.group(1))
            
            return 0
        except Exception:
            return 0

    def _extract_max_page_from_pagination(self, soup):
        """Trích xuất số trang tối đa từ pagination links"""
        try:
            max_page = 1
            pagination_selectors = [
                '.woocommerce-pagination a.page-numbers',
                'nav.woocommerce-pagination a',
                '.pagination a',
            ]
            
            for selector in pagination_selectors:
                pagination_links = soup.select(selector)
                for link in pagination_links:
                    href = link.get('href', '')
                    page_match = re.search(r'/page/(\d+)', href)
                    if page_match:
                        page_num = int(page_match.group(1))
                        max_page = max(max_page, page_num)
            
            return max_page
        except Exception:
            return 1

    def _extract_product_count_from_results(self, soup):
        """Trích xuất số lượng sản phẩm từ thẻ hiển thị kết quả"""
        try:
            selectors = [
                '.woocommerce-result-count',
                '.products-count',
                '.showing-count',
            ]
            
            for selector in selectors:
                elements = soup.select(selector)
                for element in elements:
                    text = element.get_text().strip()
                    # Tìm mẫu "trong số X" hoặc "of X"
                    total_match = re.search(r'trong số (\d+)|of (\d+)', text)
                    if total_match:
                        return int(total_match.group(1) or total_match.group(2))
            
            return 0
        except Exception:
            return 0

    def _get_max_pages_for_category(self, category_url):
        """Xác định số trang tối đa dựa trên URL danh mục"""
        category_lower = category_url.lower()
        
        # Giá trị mặc định cho số trang
        default_pages = 15
        
        # Xác định số trang dựa trên danh mục cụ thể và số lượng sản phẩm
        # Mỗi trang có 50 sản phẩm, cộng thêm thêm 2 trang dự phòng
        category_pages = {
            'bo-dieu-khien-nhiet-do-autonics': 30,  # 1412 sản phẩm, 50/trang = 29 trang
            'cam-bien-autonics': 52,                # 2576 sản phẩm, 50/trang = 52 trang
            'dong-ho-do-autonics': 12,              # 483 sản phẩm, 50/trang = 10 trang
            'bo-chuyen-doi-tin-hieu-autonics': 3,   # 33 sản phẩm
            'encoder-autonics': 12,                 # 500 sản phẩm, 50/trang = 10 trang
            'timer-autonics': 5,                    # 141 sản phẩm, 50/trang = 3 trang
            'bo-dem-autonics': 4,                   # 82 sản phẩm, 50/trang = 2 trang
            'bo-nguon-autonics': 3,                 # 33 sản phẩm
            'ro-le-ban-dan-autonics': 6,            # 200 sản phẩm, 50/trang = 4 trang
            'hmi-autonics': 2,                      # 30 sản phẩm
            'bo-dieu-khien-nguon-autonics': 16,     # 752 sản phẩm, 50/trang = 16 trang (cập nhật từ count widget)
            'cam-bien-muc-nuoc-autonics': 2,        # 2 sản phẩm
            'bo-hien-thi-so-autonics': 4,           # 73 sản phẩm, 50/trang = 2 trang
            'phu-kien-autonics': 2,                 # 3 sản phẩm
            'servo-autonics': 6,                    # 200 sản phẩm, 50/trang = 4 trang
            'bo-ghi-du-lieu-autonics': 4,           # 78 sản phẩm, 50/trang = 2 trang
            'cau-dau-day-dien-autonics': 2,         # 21 sản phẩm
        }
        
        # Thử tải trang và trích xuất số lượng sản phẩm từ widget bộ lọc
        try:
            # Tải trang danh mục
            soup = self._get_soup(category_url)
            if soup:
                # Tìm tất cả các phần tử span.count
                count_elements = soup.select('span.count')
                max_count = 0
                
                for count_element in count_elements:
                    # Trích xuất số từ trong dấu ngoặc, ví dụ "(752)" -> 752
                    count_text = count_element.get_text().strip()
                    count_match = re.search(r'\((\d+)\)', count_text)
                    if count_match:
                        count = int(count_match.group(1))
                        # Tìm phần tử cha có chứa thương hiệu
                        parent_li = count_element.find_parent('li')
                        if parent_li:
                            link_element = parent_li.find('a')
                            if link_element and 'autonics' in link_element.get_text().lower():
                                # Ưu tiên sử dụng số lượng từ bộ lọc thương hiệu autonics
                                log_and_emit(f"[THÔNG TIN] Tìm thấy số lượng sản phẩm từ widget thương hiệu: {count}")
                                if count > max_count:
                                    max_count = count
                
                # Nếu tìm thấy số lượng từ widget, tính số trang dựa trên đó
                if max_count > 0:
                    calculated_pages = math.ceil(max_count / 50) + 1  # Thêm 1 trang dự phòng
                    log_and_emit(f"[THÔNG TIN] Số trang tính từ widget bộ lọc ({max_count} sản phẩm): {calculated_pages}")
                    
                    # Kết hợp với giá trị từ cài đặt
                    # Tìm tên danh mục phù hợp
                    for category_name, default_page_count in category_pages.items():
                        if category_name in category_lower:
                            # Sử dụng giá trị lớn hơn giữa số trang tính toán và cài đặt
                            pages_to_use = max(calculated_pages, default_page_count)
                            log_and_emit(f"[THÔNG TIN] Áp dụng giới hạn {pages_to_use} trang cho danh mục {category_name}")
                            return pages_to_use
                    
                    # Nếu không tìm thấy danh mục trong cài đặt, sử dụng số trang tính toán
                    return calculated_pages
        except Exception as e:
            log_and_emit(f"[CẢNH BÁO] Lỗi khi phân tích số trang từ widget bộ lọc: {str(e)}")
        
        # Nếu không thể tính toán từ widget hoặc có lỗi, sử dụng giá trị từ cài đặt
        for category_name, pages in category_pages.items():
            if category_name in category_lower:
                log_and_emit(f"[THÔNG TIN] Áp dụng giới hạn {pages} trang cho danh mục {category_name}")
                return pages
        
        # Nếu không tìm thấy danh mục cụ thể, trả về giá trị mặc định
        return default_pages

    def _generate_basic_specs_table(self, product_code, product_name):
        """Tạo bảng thông số kỹ thuật cơ bản với mã và tên sản phẩm"""
        specs_html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;"><thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
        if product_code:
            specs_html += f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{product_code}</td></tr>'
        if product_name:
            specs_html += f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{product_name}</td></tr>'
        specs_html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
        specs_html += '</tbody></table>'
        return specs_html

    def _translate_complete_text_with_gemini(self, text):
        """
        Dịch hoàn toàn một đoạn văn bản từ tiếng Anh/Trung sang tiếng Việt
        Chuyên dụng cho Fotek.com.tw và các website khác
        
        Args:
            text (str): Văn bản cần dịch
            
        Returns:
            str: Văn bản đã được dịch sang tiếng Việt
        """
        if not text or not isinstance(text, str) or not self.gemini_model:
            return self._translate_complete_text_fallback(text)
            
        try:
            # Xác định loại văn bản để tối ưu prompt
            is_product_name = any(keyword in text.lower() for keyword in [
                'controller', 'sensor', 'switch', 'relay', 'timer', 'counter', 
                'display', 'indicator', 'series', 'tc', 'module', 'device'
            ])
            
            if is_product_name:
                prompt = f"""
Bạn là chuyên gia dịch tên sản phẩm điện tử công nghiệp từ tiếng Anh/Trung sang tiếng Việt.

YÊU CẦU DỊCH TÊN SẢN PHẨM:
- Dịch HOÀN TOÀN toàn bộ tên sản phẩm sang tiếng Việt tự nhiên
- Giữ nguyên MÃ SẢN PHẨM (các ký tự, số, dấu gạch ngang như TC4896-DD-□-□)
- Dịch chính xác các thuật ngữ kỹ thuật:
  * Temperature Controller → Bộ điều khiển nhiệt độ
  * Pressure Sensor → Cảm biến áp suất  
  * TC Series → Dòng TC
  * Controller → Bộ điều khiển
  * Sensor → Cảm biến
- Kết quả phải tự nhiên, dễ hiểu cho người Việt
- Chỉ trả về tên sản phẩm đã dịch, không giải thích

TÊN SẢN PHẨM GỐC:
"{text}"

TÊN SẢN PHẨM ĐÃ DỊCH:
"""
            else:
                prompt = f"""
Bạn là chuyên gia dịch thuật ngữ kỹ thuật điện tử, tự động hóa và điều khiển nhiệt độ. Hãy dịch văn bản sau đây từ tiếng Anh/Trung sang tiếng Việt một cách chính xác, tự nhiên và chuyên nghiệp.

YÊU CẦU:
- Dịch HOÀN TOÀN toàn bộ văn bản sang tiếng Việt
- Giữ nguyên các giá trị số, ký hiệu kỹ thuật (°C, VAC, Hz, A, Ω, mA, etc.)
- Giữ nguyên các mã model và ký hiệu đặc biệt trong dấu []
- Dịch tự nhiên, dễ hiểu cho người Việt
- Chỉ trả về văn bản đã dịch, không giải thích thêm

VĂN BẢN CẦN DỊCH:
"{text}"

HÃY DỊCH:
"""

            # Gọi Gemini API
            response = self.gemini_model.generate_content(prompt)
            translated_text = response.text.strip()
            
            # Loại bỏ dấu nháy kép nếu có
            if translated_text.startswith('"') and translated_text.endswith('"'):
                translated_text = translated_text[1:-1]
            elif translated_text.startswith("'") and translated_text.endswith("'"):
                translated_text = translated_text[1:-1]
                
            # Xử lý các phản hồi không mong muốn
            if translated_text.lower().startswith(('tên sản phẩm', 'kết quả', 'dịch:', 'dịch')):
                # Tìm phần dịch thực tế
                lines = translated_text.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not line.lower().startswith(('tên sản phẩm', 'kết quả', 'dịch:', 'dịch')):
                        translated_text = line
                        break
                
            log_and_emit(f"[GEMINI-TRANSLATE] ✅ Đã dịch: '{text}' → '{translated_text}'")
            return translated_text
            
        except Exception as e:
            log_and_emit(f"[GEMINI-TRANSLATE] ❌ Lỗi dịch văn bản: {str(e)}")
            return self._translate_complete_text_fallback(text)

    def _translate_complete_text_fallback(self, text):
        """
        Fallback method dịch văn bản bằng dictionary mapping
        
        Args:
            text (str): Văn bản cần dịch
            
        Returns:
            str: Văn bản đã được dịch (hoặc gốc nếu không dịch được)
        """
        if not text or not isinstance(text, str):
            return text
            
        translated_text = text
        
        # Danh sách thuật ngữ bổ sung cho Fotek và các website khác
        extended_translation_dict = {
            **self.fallback_translation_dict,
            
            # Thuật ngữ tiếng Anh trong tên sản phẩm - MỞ RỘNG
            'Temperature Controller': 'Bộ điều khiển nhiệt độ',
            'Pressure Controller': 'Bộ điều khiển áp suất',
            'Flow Controller': 'Bộ điều khiển lưu lượng',
            'Level Controller': 'Bộ điều khiển mức',
            'Speed Controller': 'Bộ điều khiển tốc độ',
            'Process Controller': 'Bộ điều khiển quy trình',
            'PID Controller': 'Bộ điều khiển PID',
            'Digital Controller': 'Bộ điều khiển số',
            'Analog Controller': 'Bộ điều khiển tương tự',
            'Smart Controller': 'Bộ điều khiển thông minh',
            
            # Các loại cảm biến
            'Temperature Sensor': 'Cảm biến nhiệt độ',
            'Pressure Sensor': 'Cảm biến áp suất',
            'Proximity Sensor': 'Cảm biến tiệm cận',
            'Photo Sensor': 'Cảm biến quang',
            'Photoelectric Sensor': 'Cảm biến quang điện',
            'Ultrasonic Sensor': 'Cảm biến siêu âm',
            'Inductive Sensor': 'Cảm biến cảm ứng',
            'Capacitive Sensor': 'Cảm biến điện dung',
            'Magnetic Sensor': 'Cảm biến từ',
            'Flow Sensor': 'Cảm biến lưu lượng',
            'Level Sensor': 'Cảm biến mức',
            'Vibration Sensor': 'Cảm biến rung động',
            'Position Sensor': 'Cảm biến vị trí',
            'Distance Sensor': 'Cảm biến khoảng cách',
            'Motion Sensor': 'Cảm biến chuyển động',
            'Gas Sensor': 'Cảm biến khí',
            'Humidity Sensor': 'Cảm biến độ ẩm',
            'Light Sensor': 'Cảm biến ánh sáng',
            'Sound Sensor': 'Cảm biến âm thanh',
            'Torque Sensor': 'Cảm biến mô-men xoắn',
            'Force Sensor': 'Cảm biến lực',
            'Acceleration Sensor': 'Cảm biến gia tốc',
            'Rotary Sensor': 'Cảm biến quay',
            'Linear Sensor': 'Cảm biến tuyến tính',
            
            # Series và dòng sản phẩm
            'TC Series': 'Dòng TC',
            'MT Series': 'Dòng MT',
            'NT Series': 'Dòng NT',
            'PT Series': 'Dòng PT',
            'RT Series': 'Dòng RT',
            'ST Series': 'Dòng ST',
            'XT Series': 'Dòng XT',
            'Pro Series': 'Dòng Pro',
            'Standard Series': 'Dòng chuẩn',
            'Premium Series': 'Dòng cao cấp',
            'Industrial Series': 'Dòng công nghiệp',
            'Commercial Series': 'Dòng thương mại',
            
            # Thuật ngữ kỹ thuật chung
            'Series': 'Dòng',
            'Controller': 'Bộ điều khiển',
            'Sensor': 'Cảm biến',
            'Temperature': 'Nhiệt độ',
            'Pressure': 'Áp suất',
            'Flow': 'Lưu lượng',
            'Level': 'Mức',
            'Speed': 'Tốc độ',
            'Process': 'Quy trình',
            'Digital': 'Số',
            'Analog': 'Tương tự',
            'Smart': 'Thông minh',
            'Intelligent': 'Thông minh',
            'Automatic': 'Tự động',
            'Manual': 'Thủ công',
            'Switch': 'Công tắc',
            'Relay': 'Relay',
            'Timer': 'Bộ định thời',
            'Counter': 'Bộ đếm',
            'Display': 'Màn hình',
            'Indicator': 'Đèn báo',
            'Monitor': 'Màn hình giám sát',
            'Alarm': 'Báo động',
            'Signal': 'Tín hiệu',
            'Input': 'Đầu vào',
            'Output': 'Đầu ra',
            'Module': 'Module',
            'Unit': 'Đơn vị',
            'Device': 'Thiết bị',
            'Instrument': 'Thiết bị đo',
            'Meter': 'Đồng hồ đo',
            'Gauge': 'Đồng hồ',
            'Detector': 'Bộ phát hiện',
            'Transmitter': 'Bộ truyền',
            'Converter': 'Bộ chuyển đổi',
            'Amplifier': 'Bộ khuếch đại',
            'Actuator': 'Bộ truyền động',
            'Valve': 'Van',
            'Motor': 'Động cơ',
            'Pump': 'Bơm',
            'Fan': 'Quạt',
            'Heater': 'Máy sưởi',
            'Cooler': 'Máy làm mát',
            'Conditioner': 'Bộ điều hòa',
            'Regulator': 'Bộ điều chỉnh',
            'Stabilizer': 'Bộ ổn định',
            'Protection': 'Bảo vệ',
            'Safety': 'An toàn',
            'Security': 'Bảo mật',
            'Electric': 'Điện',
            'Electronic': 'Điện tử',
            'Mechanical': 'Cơ khí',
            'Pneumatic': 'Khí nén',
            'Hydraulic': 'Thủy lực',
            'Industrial': 'Công nghiệp',
            'Commercial': 'Thương mại',
            'Professional': 'Chuyên nghiệp',
            'Standard': 'Chuẩn',
            'Premium': 'Cao cấp',
            'Advanced': 'Nâng cao',
            'Basic': 'Cơ bản',
            'Compact': 'Nhỏ gọn',
            'Portable': 'Di động',
            'Fixed': 'Cố định',
            'Wireless': 'Không dây',
            'Wired': 'Có dây',
            
            # Thuật ngữ tiếng Trung phổ biến trong thông số
            '類型感測器': 'Loại cảm biến',
            '類型感溫線': 'Loại dây cảm biến nhiệt', 
            '輸出方法': 'Phương thức đầu ra',
            '輸出方式': 'Phương thức đầu ra',
            '控制方法': 'Phương thức điều khiển',
            '控制方式': 'Phương thức điều khiển',
            '比例帶': 'Dải tỷ lệ',
            '週期時間': 'Thời gian chu kỳ',
            '動作週期': 'Chu kỳ hoạt động',
            '手動重置': 'Đặt lại thủ công',
            '手動復位': 'Đặt lại thủ công',
            '偏移修正': 'Hiệu chỉnh độ lệch',
            '單位': 'Đơn vị',
            '設定方法': 'Phương thức cài đặt',
            '設定方式': 'Phương thức cài đặt',
            '設定範圍': 'Phạm vi cài đặt',
            '工作電壓': 'Điện áp làm việc',
            '耗電流': 'Dòng điện tiêu thụ',
            '絕緣電阻': 'Điện trở cách điện',
            '絕緣阻抗': 'Trở kháng cách điện',
            '耐壓強度': 'Cường độ chịu điện áp',
            '工作環境': 'Môi trường làm việc',
            '工作溫度': 'Nhiệt độ làm việc',
            '工作溫度/濕度': 'Nhiệt độ/Độ ẩm làm việc',
            '耐振動': 'Khả năng chịu rung động',
            '面板厚度': 'Độ dày bảng điều khiển',
            
            # Giá trị kỹ thuật tiếng Anh
            'Relay': 'Relay',
            'Voltage': 'Điện áp',
            'Linear': 'Tuyến tính',
            'Proportion': 'Tỷ lệ',
            'Trimmer': 'Biến trở điều chỉnh',
            'Range': 'Phạm vi',
            'Setting Value': 'Giá trị cài đặt',
            'Between Power And Another Terminal': 'giữa nguồn và đầu cuối khác',
            'Direction': 'hướng',
            'Appro': 'Xấp xỉ',
            'Over': 'Trên',
            'max': 'tối đa',
            'min': 'tối thiểu',
            'F.S.': 'toàn thang đo',
            'Sec': 'giây',
            'Hrs': 'giờ',
            ' or ': ' hoặc ',
            'ON/OFF': 'BẬT/TẮT',
        }
        
        # Áp dụng từ điển dịch thuật mở rộng với thuật toán thông minh
        # Dịch theo thứ tự từ dài đến ngắn để tránh dịch sai
        sorted_terms = sorted(extended_translation_dict.items(), key=lambda x: len(x[0]), reverse=True)
        
        for original_term, vietnamese_term in sorted_terms:
            if original_term in translated_text:
                translated_text = translated_text.replace(original_term, vietnamese_term)
        
        return translated_text