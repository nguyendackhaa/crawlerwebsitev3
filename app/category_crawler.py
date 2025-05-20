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
from datetime import datetime
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
from openpyxl.utils import get_column_letter
from flask import current_app, session
from . import utils
from .utils import is_valid_url
from .crawler import is_category_url, is_product_url, extract_product_info

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
    def __init__(self, socketio, upload_folder=None):
        """Khởi tạo CategoryCrawler với socketio instance và upload_folder"""
        self.socketio = socketio
        update_socketio(socketio)
        # Cài đặt số lượng luồng tối đa
        self.max_workers = 8
        # Cài đặt thời gian chờ giữa các request để tránh bị chặn
        self.request_delay = 0.2
        # Tạo semaphore để kiểm soát số lượng request đồng thời đến cùng một domain
        self.domain_semaphores = {}
        # Lưu đường dẫn upload_folder
        self.upload_folder = upload_folder
        # Timeout cho các request
        self.request_timeout = 20
        # Số lần thử lại tối đa cho mỗi request
        self.max_retries = 3
        # Thời gian chờ giữa các lần thử lại (giây)
        self.retry_delay = 1
        
    def process_category_urls(self, category_urls_text):
        """Xử lý danh sách URL danh mục và trả về kết quả"""
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
            if is_codienhaiau:
                # Trích xuất thông tin sản phẩm từ codienhaiau.com
                product_info = self.extract_codienhaiau_product_info(product_url, index, category_dir)
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
        """Thu thập thông tin sản phẩm từ danh sách liên kết"""
        if not product_links:
            log_and_emit(f"Không có liên kết sản phẩm để thu thập từ danh mục: {category_name}")
            return []
            
        # Cập nhật tiến trình
        progress_max = min(40, 85 - (progress_base - 5))  # Tối đa 40% cho việc cào sản phẩm
        update_progress(progress_base, f"Đang cào dữ liệu {len(product_links)} sản phẩm từ danh mục: {category_name}")
        log_and_emit(f"Tổng cộng có {len(product_links)} sản phẩm cần cào từ danh mục {category_name}")
        
        # Tạo hàng đợi để nhận kết quả và báo lỗi
        result_queue = queue.Queue()
        error_queue = queue.Queue()
        
        # Xác định xem có phải URL từ codienhaiau.com không
        is_codienhaiau = any('codienhaiau.com' in url for url in product_links if url)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            
            # Tạo các tác vụ tải thông tin sản phẩm
            for i, product_url in enumerate(product_links):
                future = executor.submit(
                    self._download_product_info_worker,
                    product_url, i, category_dir, is_codienhaiau, result_queue, error_queue
                )
                futures.append(future)
                
                # Thông báo đang bắt đầu tải sản phẩm
                current_progress = progress_base + int((i / len(product_links)) * progress_max)
                update_progress(current_progress, f"Đang cào sản phẩm {i}/{len(product_links)} ({int(i/len(product_links)*100)}%) từ danh mục: {category_name}")
                
                # Giãn cách giữa các request để tránh bị chặn
                time.sleep(self.request_delay)
                
            # Chờ tất cả các tác vụ hoàn thành
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                try:
                    # Lấy kết quả từ future nếu cần
                    _ = future.result()
                    completed += 1
                    
                    # Cập nhật tiến trình
                    current_progress = progress_base + int((completed / len(product_links)) * progress_max)
                    update_progress(current_progress, f"Đang cào sản phẩm {completed}/{len(product_links)} ({int(completed/len(product_links)*100)}%) từ danh mục: {category_name}")
                except Exception as e:
                    # Xử lý ngoại lệ nếu có
                    log_and_emit(f"Lỗi khi xử lý sản phẩm: {str(e)}")
                    
        # Thu thập tất cả kết quả
                product_info_list = []
        while not result_queue.empty():
            product_info = result_queue.get()
            if isinstance(product_info, dict):  # Đảm bảo chỉ lấy các đối tượng dict
                product_info_list.append(product_info)
        
        # Thu thập lỗi nếu có
        errors = []
        while not error_queue.empty():
            error_item = error_queue.get()
            if isinstance(error_item, tuple) and len(error_item) == 3:
                idx, url, error = error_item
                log_and_emit(f"Lỗi sản phẩm {idx}: {url} - {error}")
                errors.append(error_item)
        
        if errors:
            log_and_emit(f"Có {len(errors)} lỗi khi thu thập thông tin sản phẩm")
        
        # Sắp xếp theo số thứ tự ban đầu
        try:
            product_info_list.sort(key=lambda x: x.get('index', 0) if isinstance(x, dict) else 0)
        except Exception as e:
            log_and_emit(f"Lỗi khi sắp xếp danh sách sản phẩm: {str(e)}")
        
        # Tạo file excel chứa thông tin sản phẩm
        excel_file = os.path.join(category_dir, f"{category_name}_products.xlsx")
        if product_info_list:
            # Chỉ lấy các trường quan trọng để lưu vào Excel
            important_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'URL', 'Mô tả', 'Ảnh sản phẩm']
            
            # Tạo DataFrame từ danh sách sản phẩm với các trường quan trọng
            df = pd.DataFrame([{field: product.get(field, '') for field in important_fields} for product in product_info_list])
            
            # Lưu DataFrame vào file Excel
            df.to_excel(excel_file, index=False, engine='openpyxl')
            log_and_emit(f"Đã lưu thông tin {len(product_info_list)} sản phẩm vào file {excel_file}")
        
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
                        cat_dir = os.path.join(result_dir, secure_filename(cat_name))
                        excel_file = os.path.join(cat_dir, f'{secure_filename(cat_name)}.xlsx')
                        if os.path.exists(excel_file):
                            df_cat = pd.read_excel(excel_file)
                            # Thêm thông tin danh mục vào mỗi sản phẩm
                            df_cat['Danh mục'] = cat_name
                            all_products.append(df_cat)
                    except Exception as e:
                        print(f"Lỗi khi đọc file Excel cho danh mục {cat_name}: {str(e)}")
                # Kết hợp tất cả sản phẩm vào một sheet
                if all_products:
                    df_all_products = pd.concat(all_products, ignore_index=True)
                    # Đưa cột 'Danh mục' lên đầu nếu tồn tại
                    cols = list(df_all_products.columns)
                    if 'Danh mục' in cols:
                        cols.remove('Danh mục')
                        df_all_products = df_all_products[['Danh mục'] + cols]
                    # Ghi ra sheet tổng hợp
                    df_all_products.to_excel(writer, sheet_name='Tổng hợp sản phẩm', index=False)
                    # Định dạng sheet
                    worksheet = writer.sheets['Tổng hợp sản phẩm']
                    # Điều chỉnh độ rộng cột
                    for idx, col in enumerate(df_all_products.columns):
                        # Đặt độ rộng cột dựa trên loại dữ liệu
                        if col == 'STT':
                            max_width = 5
                        elif col == 'Mã sản phẩm':
                            max_width = 15
                        elif col == 'Tên sản phẩm':
                            max_width = 40
                        elif col == 'Giá':
                            max_width = 15
                        elif col == 'Mô tả':
                            max_width = 60
                        elif col == 'Tổng quan':
                            max_width = 60
                        elif col in ['Ảnh sản phẩm', 'Ảnh bổ sung', 'Tài liệu kỹ thuật', 'URL']:
                            max_width = 50
                        else:
                            # Tính toán độ rộng dựa trên nội dung (tối đa 50)
                            max_width = min(50, max([
                                len(str(df_all_products[col].iloc[i])) 
                                for i in range(min(10, len(df_all_products)))
                            ] + [len(col)]))
                        col_letter = get_column_letter(idx + 1)
                        worksheet.column_dimensions[col_letter].width = max_width
            print(f"Đã tạo báo cáo tổng hợp: {report_file}")
            emit_progress(100, f'Đã hoàn tất thu thập dữ liệu từ {len(valid_urls)} danh mục sản phẩm')
        except Exception as e:
            print(f"Lỗi khi tạo báo cáo: {str(e)}")
            traceback.print_exc()
            
    def extract_codienhaiau_product_info(self, url, index=1, output_dir=None):
        """Trích xuất thông tin sản phẩm từ trang codienhaiau.com"""
        try:
            print(f"Đang trích xuất thông tin từ {url}")
            # Khởi tạo kết quả chỉ với các trường cần thiết
            product_info = {
                'STT': index,
                'URL': url,
                'Mã sản phẩm': "",
                'Tên sản phẩm': "",
                'Giá': "",
                'Mô tả': "",
                'Ảnh sản phẩm': ""
            }
            # Tải nội dung trang với retry
            print(f"  > Đang tải nội dung trang {url}")
            response = None
            for attempt in range(self.max_retries):
                try:
                    response = requests.get(
                        url, 
                        headers={'User-Agent': 'Mozilla/5.0'}, 
                        timeout=self.request_timeout
                    )
                    response.raise_for_status()
                    print(f"  > Đã tải trang thành công sau {attempt+1} lần thử")
                    break
                except (requests.RequestException, Exception) as e:
                    if attempt < self.max_retries - 1:
                        print(f"  > Lỗi khi tải {url}, thử lại lần {attempt + 1}: {str(e)}")
                        time.sleep(self.retry_delay)
                    else:
                        print(f"  > Lỗi khi tải {url} sau {self.max_retries} lần thử: {str(e)}")
                        raise
            if not response:
                raise Exception(f"Không thể tải nội dung từ {url}")
            # Parse HTML
            print(f"  > Đang phân tích HTML từ {url}")
            soup = BeautifulSoup(response.text, 'html.parser')
            print(f"  > Đã phân tích HTML thành công")
            # Trích xuất tên sản phẩm
            product_name = ""
            name_element = soup.select_one('h1.mb-0, h1.product-title')
            if name_element:
                product_name = name_element.text.strip()
                print(f"  > Tìm thấy tên sản phẩm: {product_name}")
                product_info['Tên sản phẩm'] = product_name
            # Trích xuất mã sản phẩm
            product_code = ""
            code_element = soup.select_one('div.text-muted strong')
            if code_element:
                product_code = code_element.text.strip()
                print(f"  > Tìm thấy mã sản phẩm: {product_code}")
                product_info['Mã sản phẩm'] = product_code
            # Nếu không tìm được mã sản phẩm từ div.text-muted, thử trích xuất từ source HTML
            if not product_code:
                # Tìm mã sản phẩm từ JS hoặc cấu trúc HTML
                pattern = r'"sku":\s*"([^"]+)"'
                match = re.search(pattern, str(soup))
                if match:
                    product_code = match.group(1)
                    print(f"  > Tìm thấy mã sản phẩm từ JS: {product_code}")
                    product_info['Mã sản phẩm'] = product_code
            # Trích xuất giá sản phẩm
            price_text = ""
            price_element = soup.select_one('span.price-new')
            if price_element:
                price_text = price_element.text.strip()
                print(f"  > Tìm thấy giá sản phẩm: {price_text}")
                product_info['Giá'] = price_text
            # Trích xuất mô tả
            description = ""
            description_element = soup.select_one('div.tab-content div.editor-content')
            if description_element:
                print(f"  > Tìm thấy phần mô tả sản phẩm")
                # Loại bỏ các phần tử không cần thiết
                for img in description_element.find_all('img'):
                    img.decompose()
                
                # Lấy HTML của phần mô tả (giữ nguyên định dạng)
                description = str(description_element)
                product_info['Mô tả'] = description
            
            # Tạo tổng quan từ bảng thông số kỹ thuật
            if product_code and product_name:
                print(f"  > Tạo bảng thông số kỹ thuật")
                specs_table = self._generate_product_spec_table(product_code, product_name)
                product_info['Tổng quan'] = specs_table
            
            # Tải và lưu ảnh sản phẩm nếu có thư mục đầu ra
            if output_dir and product_info['Mã sản phẩm']:
                # Tạo URL ảnh theo định dạng yêu cầu
                product_info['Ảnh sản phẩm'] = f"https://haiphongtech.vn/wp-content/uploads/2025/05/{product_info['Mã sản phẩm']}.webp"
                
                # Tải ảnh từ codienhaiau.com và lưu vào thư mục
                image_url = self._download_codienhaiau_product_image(soup, url, output_dir, product_info['Mã sản phẩm'])
                if image_url:
                    # Nếu tìm được URL ảnh từ trang web, vẫn giữ URL ảnh từ haiphongtech.vn
                    print(f"Đã tải ảnh sản phẩm: {image_url}")
            else:
                # Nếu không có thư mục đầu ra hoặc không có mã sản phẩm, chỉ lấy URL ảnh
                product_info['Ảnh sản phẩm'] = self._get_image_url(soup, url, product_info['Mã sản phẩm'])
            
            print(f"Đã trích xuất thông tin sản phẩm: {product_info['Tên sản phẩm']}, Mã: {product_info['Mã sản phẩm']}, Giá: {product_info['Giá']}")
            return product_info
            
        except Exception as e:
            print(f"Lỗi khi trích xuất thông tin từ {url}: {str(e)}")
            traceback.print_exc()
            return product_info

    def extract_baa_product_info(self, url, index=1, output_dir=None):
        """Trích xuất thông tin sản phẩm từ trang BAA.vn với khả năng xử lý nội dung ẩn và chuẩn hóa dữ liệu"""
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
                product_info['Mã sản phẩm'] = product_code_elem.text.strip()
            
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
        """Chuẩn hóa bảng thông số kỹ thuật từ BAA.vn, bao gồm cả nội dung ẩn"""
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
            
            # Thêm dòng Copyright vào cuối bảng
            rows.append(("Copyright", "Haiphongtech.vn"))
            
            # Tạo HTML table chuẩn
            html = '<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial;"><thead><tr><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
            for param, value in rows:
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
        """Lấy nội dung trang web và trả về đối tượng BeautifulSoup với retry logic"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        for attempt in range(self.max_retries):
            try:
                response = requests.get(url, headers=headers, timeout=self.request_timeout)
                response.raise_for_status()
                return BeautifulSoup(response.text, 'html.parser')
            except Exception as e:
                if attempt < self.max_retries - 1:
                    print(f"Lỗi khi tải {url}, thử lại lần {attempt + 1}: {str(e)}")
                    time.sleep(self.retry_delay)
                else:
                    print(f"Lỗi khi tải {url} sau {self.max_retries} lần thử: {str(e)}")
                    raise

    def _extract_codienhaiau_links(self, soup, current_url):
        """Trích xuất liên kết sản phẩm và phân trang từ trang codienhaiau.com"""
        product_urls = set()
        pagination_urls = set()
        
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
            
            # Tìm kiếm các liên kết sản phẩm với các selector khác nhau
            log_and_emit(f"[PHÂN TÍCH] Tìm liên kết sản phẩm với nhiều selector khác nhau")
            found_links_by_selector = {}
            for selector in product_link_selectors:
                product_elements = soup.select(selector)
                found_links_by_selector[selector] = len(product_elements)
                
                for element in product_elements:
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
                        product_urls.add(full_url)
            
            # Hiển thị tổng kết các selector và số sản phẩm tìm được
            log_and_emit(f"[KẾT QUẢ] Tìm thấy {len(product_urls)} sản phẩm trong trang hiện tại")
            selector_summary = ""
            for selector, count in found_links_by_selector.items():
                if count > 0:
                    selector_summary += f"  - {selector}: {count} liên kết\n"
            
            if selector_summary:
                log_and_emit(f"[THÔNG TIN] Phân tích selector sản phẩm:\n{selector_summary.rstrip()}")
            
            # ---------------- XỬ LÝ PHÂN TRANG ----------------
            
            # Xác định mẫu URL cơ sở
            base_url = current_url
            page_number = 1
            
            # Trích xuất số trang hiện tại từ URL nếu có
            current_page_match = re.search(r'/page/(\d+)', current_url)
            if current_page_match:
                page_number = int(current_page_match.group(1))
                # Loại bỏ phần /page/X/ từ URL để có URL cơ sở
                base_url = re.sub(r'/page/\d+/', '/', current_url)
                log_and_emit(f"[PHÂN TRANG] Đang ở trang {page_number}, URL cơ sở: {base_url}")
            
            # Đảm bảo base_url không có tham số truy vấn
            if '?' in base_url:
                base_url = base_url.split('?')[0]
            
            # Đảm bảo base_url kết thúc bằng '/'
            if not base_url.endswith('/'):
                base_url += '/'
            
            # Tạo mẫu URL cho phân trang
            page_url_template = f"{base_url}page/{{0}}/"
            
            log_and_emit(f"[PHÂN TRANG] Mẫu URL phân trang: {page_url_template}")
            
            # TÌM TỔNG SỐ TRANG - Phương pháp 1: Từ các liên kết phân trang
            max_page = 1
            
            # Tìm phân trang với các selector khác nhau
            pagination_selectors = [
                '.woocommerce-pagination a.page-numbers', 
                '.woocommerce-pagination a.next',
                'nav.woocommerce-pagination a', 
                '.nav-pagination a',
                'ul.page-numbers a',
                '.pagination a',
                '.load-more-button',
                'a.next.page-numbers',
                'a.prev.page-numbers',
                'a.page-number'
            ]
            
            log_and_emit(f"[PHÂN TRANG] Đang tìm liên kết phân trang...")
            
            pagination_links_found = False
            for selector in pagination_selectors:
                pagination_links = soup.select(selector)
                if pagination_links:
                    pagination_links_found = True
                    log_and_emit(f"[THÀNH CÔNG] Tìm thấy {len(pagination_links)} liên kết phân trang với selector '{selector}'")
                
                    for link in pagination_links:
                        href = link.get('href', '')
                        # Kiểm tra URL phân trang dạng /page/X/
                        page_match = re.search(r'/page/(\d+)', href)
                        if page_match:
                            page_num = int(page_match.group(1))
                            if page_num > max_page:
                                max_page = page_num
            
            # Nếu không tìm thấy liên kết phân trang, thử tìm các phần tử phân trang khác
            if not pagination_links_found:
                log_and_emit(f"[CẢNH BÁO] Không tìm thấy liên kết phân trang với các selector thông thường")
                
                # Thử tìm các thẻ span có class page-numbers
                page_number_spans = soup.select('span.page-numbers')
                if page_number_spans:
                    log_and_emit(f"[THÔNG TIN] Tìm thấy {len(page_number_spans)} thẻ span page-numbers")
                    
                    for span in page_number_spans:
                        if span.get_text().isdigit():
                            page_num = int(span.get_text())
                            if page_num > max_page:
                                max_page = page_num
            
            # Thông báo về số trang tìm được
            if max_page > 1:
                log_and_emit(f"[PHÂN TRANG] Tìm thấy tối đa {max_page} trang từ phân trang")
            else:
                log_and_emit(f"[PHÂN TRANG] Không tìm thấy thông tin phân trang, đang tìm phương pháp khác...")
            
            # TÌM TỔNG SỐ SẢN PHẨM - PHƯƠNG PHÁP MỚI: Từ các widget bộ lọc (span.count)
            count_from_widgets = 0
            # Tìm tất cả các widget bộ lọc có thể chứa thông tin số lượng sản phẩm
            widget_selectors = [
                '.woocommerce-widget-layered-nav-list .count',  # Widget bộ lọc thương hiệu/thuộc tính
                '.product-categories .count',                  # Widget danh mục
                '.widget_layered_nav .count',                  # Widget bộ lọc tổng quát
                '.widget_layered_nav_filters .count',          # Widget bộ lọc đã chọn
                '.wc-layered-nav-term .count'                  # Mục bộ lọc riêng lẻ
            ]
            
            log_and_emit(f"[PHÂN TÍCH] Tìm thông tin số lượng sản phẩm từ widget bộ lọc...")
            
            # Tìm các widget bộ lọc có chứa thương hiệu của danh mục hiện tại
            # Ví dụ: nếu URL chứa "bo-dieu-khien-nguon-autonics", tìm widget có "Autonics"
            category_parts = current_url.split('/')
            category_name = None
            for part in category_parts:
                if 'autonics' in part.lower():
                    category_name = 'autonics'
                    break
            
            if category_name:
                log_and_emit(f"[THÔNG TIN] Đang tìm widget bộ lọc cho thương hiệu: {category_name}")
                
                # Tìm tất cả các widget bộ lọc
                for selector in widget_selectors:
                    count_elements = soup.select(selector)
                    for count_element in count_elements:
                        # Kiểm tra xem widget này có liên quan đến danh mục hiện tại không
                        parent_li = count_element.find_parent('li')
                        if parent_li:
                            link_element = parent_li.find('a')
                            if link_element and category_name in link_element.get_text().lower():
                                # Trích xuất số trong dấu ngoặc, ví dụ: "(752)" -> 752
                                count_text = count_element.get_text().strip()
                                count_match = re.search(r'\((\d+)\)', count_text)
                                if count_match:
                                    widget_count = int(count_match.group(1))
                                    log_and_emit(f"[THÀNH CÔNG] Tìm thấy số lượng sản phẩm từ widget bộ lọc: {widget_count} (từ {count_text})")
                                    if widget_count > count_from_widgets:
                                        count_from_widgets = widget_count
            
            # Nếu không tìm thấy theo thương hiệu cụ thể, tìm tổng số sản phẩm từ bất kỳ widget nào
            if count_from_widgets == 0:
                # Tìm tất cả các phần tử span.count
                all_count_elements = soup.select('span.count')
                if all_count_elements:
                    log_and_emit(f"[THÔNG TIN] Tìm thấy {len(all_count_elements)} phần tử có class 'count'")
                    
                    for count_element in all_count_elements:
                        count_text = count_element.get_text().strip()
                        count_match = re.search(r'\((\d+)\)', count_text)
                        if count_match:
                            widget_count = int(count_match.group(1))
                            log_and_emit(f"[THÔNG TIN] Phát hiện số lượng từ widget: {widget_count} (từ {count_text})")
                            if widget_count > count_from_widgets:
                                count_from_widgets = widget_count
                                
            # TÌM TỔNG SỐ SẢN PHẨM - Phương pháp 2: Từ thẻ có chứa tổng số sản phẩm
            total_products = 0
            
            # Nếu tìm được từ widget, sử dụng giá trị đó
            if count_from_widgets > 0:
                total_products = count_from_widgets
                log_and_emit(f"[THÀNH CÔNG] Sử dụng số lượng sản phẩm từ widget bộ lọc: {total_products}")
            else:
                # Các selector thường chứa thông tin về tổng số sản phẩm
                product_count_selectors = [
                    '.woocommerce-result-count',
                    '.products-count',
                    '.showing-count',
                    '.product-count',
                    '.result-count'
                ]
                
                log_and_emit(f"[PHÂN TÍCH] Tìm thông tin tổng số sản phẩm từ thẻ hiển thị số lượng...")
                
                for selector in product_count_selectors:
                    count_elements = soup.select(selector)
                    if count_elements:
                        for element in count_elements:
                            text = element.get_text().strip()
                            log_and_emit(f"[THÔNG TIN] Tìm thấy phần tử '{selector}': {text}")
                            
                            # Tìm số từ văn bản (có thể có nhiều dạng khác nhau)
                            # VD: "Hiển thị 1–50 trong số 141 kết quả"
                            # VD: "Showing 1-50 of 141 results"
                            # VD: "141 products"
                            
                            # Thử tìm mẫu "X–Y trong số Z"
                            total_match = re.search(r'trong số (\d+)', text)
                            if total_match:
                                total_products = int(total_match.group(1))
                                log_and_emit(f"[THÀNH CÔNG] Tìm thấy tổng số sản phẩm (vi): {total_products}")
                                break
                                
                            # Thử tìm mẫu "X-Y of Z"
                            total_match = re.search(r'of (\d+)', text)
                            if total_match:
                                total_products = int(total_match.group(1))
                                log_and_emit(f"[THÀNH CÔNG] Tìm thấy tổng số sản phẩm (en): {total_products}")
                                break
                                
                            # Thử tìm mẫu chỉ có một số
                            total_match = re.search(r'^(\d+)', text)
                            if total_match:
                                total_products = int(total_match.group(1))
                                log_and_emit(f"[THÀNH CÔNG] Tìm thấy tổng số sản phẩm (simple): {total_products}")
                                break
                    if total_products > 0:
                        break
            
            # TÌM TỔNG SỐ SẢN PHẨM - Phương pháp 3: Tìm từ các phần tử HTML khác
            if total_products == 0:
                # Tìm trong các phần tử có thể chứa thông tin số lượng
                product_count_elements = soup.select('.count, .product-count, .product-total, .total-count')
                for element in product_count_elements:
                    try:
                        text = element.get_text().strip()
                        # Tìm số trong văn bản
                        num_match = re.search(r'\d+', text)
                        if num_match:
                            num_str = num_match.group(0)
                            total_products = int(num_str)
                            log_and_emit(f"[THÀNH CÔNG] Phát hiện tổng số sản phẩm từ phần tử HTML: {total_products}")
                            break
                    except Exception as e:
                        continue
            
            # Nếu vẫn không tìm thấy tổng số sản phẩm, sử dụng max_page để ước tính
            if total_products == 0 and max_page > 1:
                # Ước tính dựa trên số sản phẩm trên trang đầu tiên và tổng số trang
                products_per_page = len(product_urls)
                if products_per_page > 0:
                    estimated_total = products_per_page * max_page
                    log_and_emit(f"[THÔNG TIN] Ước tính tổng số sản phẩm: {estimated_total} (dựa trên {products_per_page} sản phẩm/trang x {max_page} trang)")
                    total_products = estimated_total
            
            # LẤY SỐ TRANG TỐI ĐA từ cài đặt cho danh mục
            max_pages_setting = self._get_max_pages_for_category(current_url)
            
            # Nếu tổng số sản phẩm > 0, tính số trang dựa trên số sản phẩm (50 sản phẩm/trang)
            if total_products > 0:
                calculated_pages = math.ceil(total_products / 50)
                log_and_emit(f"[PHÂN TRANG] Số trang tính từ tổng số sản phẩm ({total_products}): {calculated_pages}")
                # Sử dụng giá trị lớn nhất giữa số trang tính toán và số trang đã tìm
                if calculated_pages > max_page:
                    max_page = calculated_pages
                    log_and_emit(f"[PHÂN TRANG] Cập nhật số trang tối đa thành {max_page}")
            
            # Nếu số trang tìm được nhỏ hơn cài đặt, sử dụng giá trị lớn hơn
            if max_page < max_pages_setting:
                log_and_emit(f"[THÔNG TIN] Số trang tìm được ({max_page}) nhỏ hơn cài đặt ({max_pages_setting}), sử dụng cài đặt")
                max_page = max_pages_setting
            
            # Tạo URL cho tất cả các trang
            if max_page > 1:
                log_and_emit(f"[PHÂN TRANG] Tạo URL cho {max_page} trang...")
                
                # Thêm URLs cho tất cả các trang (trừ trang hiện tại)
                for p in range(1, max_page + 1):
                    if p != page_number:  # Bỏ qua trang hiện tại
                        page_url = page_url_template.format(p)
                        pagination_urls.add(page_url)
                        if p <= 3 or p >= max_page - 2:  # Chỉ in ra một số trang để không làm rối log
                            log_and_emit(f"[PHÂN TRANG] Thêm trang {p}: {page_url}")
                        elif p == 4:
                            log_and_emit(f"[PHÂN TRANG] ... (bỏ qua {max_page - 5} trang ở giữa) ...")
            else:
                log_and_emit(f"[PHÂN TRANG] Chỉ có một trang, không cần phân trang")
            
            # Kết quả
            log_and_emit(f"[KẾT QUẢ] Tìm thấy {len(product_urls)} sản phẩm từ trang hiện tại")
            log_and_emit(f"[KẾT QUẢ] Tìm thấy {len(pagination_urls)} trang phân trang cần truy cập")
            
            # Tải tất cả các trang phân trang và thu thập sản phẩm
            if pagination_urls:
                log_and_emit(f"[TIẾN TRÌNH] Bắt đầu thu thập sản phẩm từ {len(pagination_urls)} trang phân trang...")
                
                # Làm mới danh sách sản phẩm đã có
                all_products = list(product_urls)
                processed_pagination = 0
                total_pagination = len(pagination_urls)
                
                for page_url in pagination_urls:
                    try:
                        processed_pagination += 1
                        log_and_emit(f"[TIẾN TRÌNH] Đang tải trang {processed_pagination}/{total_pagination}: {page_url}")
                        
                        # Thêm trì hoãn nhỏ để tránh tải quá nhanh
                        time.sleep(self.request_delay)
                        
                        # Tải trang với số lần thử lại
                        page_soup = None
                        for attempt in range(self.max_retries):
                            try:
                                page_soup = self._get_soup(page_url)
                                if page_soup:
                                    break
                            except Exception as e:
                                log_and_emit(f"[LỖI] Lần {attempt+1}/{self.max_retries} - Không thể tải trang {page_url}: {str(e)}")
                                if attempt < self.max_retries - 1:
                                    time.sleep(self.retry_delay)
                        
                        if not page_soup:
                            log_and_emit(f"[LỖI] Không thể tải trang {page_url} sau {self.max_retries} lần thử")
                            continue
                        
                        # Trích xuất sản phẩm từ trang này
                        page_products_found = 0
                        page_product_urls = set()
                        
                        for selector in product_link_selectors:
                            links = page_soup.select(selector)
                            if links:
                                log_and_emit(f"[THÔNG TIN] Selector '{selector}': tìm thấy {len(links)} phần tử")
                            
                            # Xử lý từng liên kết tìm được
                            for link in links:
                                href = None
                                # Xử lý trường hợp h2.woocommerce-loop-product__title
                                if selector == 'h2.woocommerce-loop-product__title':
                                    parent_link = link.find_parent('a')
                                    if parent_link and parent_link.get('href'):
                                        href = parent_link.get('href')
                                else:
                                    href = link.get('href', '')
                                
                                # Kiểm tra và xử lý URL sản phẩm
                                if href and '/product/' in href and not href.endswith('/product/'):
                                    full_url = urljoin(current_url, href)
                                    if full_url not in product_urls and full_url not in page_product_urls:
                                        all_products.append(full_url)
                                        page_product_urls.add(full_url)
                                        page_products_found += 1
                        
                        # Cập nhật tiến trình
                        progress_percent = int((processed_pagination / total_pagination) * 100)
                        log_and_emit(f"[KẾT QUẢ] Trang {processed_pagination}: Tìm thấy {page_products_found} sản phẩm mới ({progress_percent}%)")
                        
                        # In mẫu các URL sản phẩm tìm được
                        if page_product_urls:
                            sample_size = min(3, len(page_product_urls))
                            sample_urls = list(page_product_urls)[:sample_size]
                            sample_text = "\n".join([f"  - {url}" for url in sample_urls])
                            if len(page_product_urls) > sample_size:
                                sample_text += f"\n  - ... và {len(page_product_urls) - sample_size} sản phẩm khác"
                            log_and_emit(f"[MẪU SẢN PHẨM]\n{sample_text}")
                        
                    except Exception as e:
                        log_and_emit(f"[LỖI] Lỗi khi xử lý trang phân trang {page_url}: {str(e)}")
                        traceback.print_exc()
                
                # Loại bỏ trùng lặp và trả về danh sách các URL sản phẩm
                all_products = list(set(all_products))
                log_and_emit(f"[HOÀN THÀNH] Đã thu thập xong {len(all_products)} sản phẩm từ {total_pagination+1} trang")
            else:
                all_products = list(product_urls)
                log_and_emit(f"[HOÀN THÀNH] Thu thập {len(all_products)} sản phẩm từ 1 trang")
            
            return all_products
            
        except Exception as e:
            log_and_emit(f"[LỖI] Lỗi khi trích xuất liên kết từ {current_url}: {str(e)}")
            traceback.print_exc()
            return list(product_urls)

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