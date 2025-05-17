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
            print(f"Tiến trình: {percent}% - {message}")
            if log:
                print(log)

def safe_print(message):
    """Hàm in an toàn cho đa luồng"""
    with _print_lock:
        print(message)

def log_and_emit(message):
    """Ghi log và phát đi sự kiện với thông tin log"""
    with _print_lock:
        # Lấy phần trăm tiến trình hiện tại (không thay đổi)
        progress = getattr(log_and_emit, 'last_progress', 0)
        # Lấy thông báo tiến trình hiện tại (không thay đổi)
        status_message = getattr(log_and_emit, 'last_message', 'Đang xử lý...')
        
        # Phát đi sự kiện với thông tin log
        emit_progress(progress, status_message, message)

# Lưu trữ tiến trình hiện tại để log_and_emit có thể sử dụng
def update_progress(percent, message):
    """Cập nhật tiến trình hiện tại và lưu lại để sử dụng cho log"""
    log_and_emit.last_progress = percent
    log_and_emit.last_message = message
    emit_progress(percent, message)

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
                from flask import session
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
            important_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'URL']
            
            # Thêm các trường khác nếu có
            for product in product_info_list:
                for key in product.keys():
                    if key not in important_fields and key != 'index' and key != 'Tổng quan' and not key.startswith('_'):
                        important_fields.append(key)
            
            # Tạo DataFrame từ danh sách sản phẩm với các trường quan trọng
            df = pd.DataFrame([{field: product.get(field, '') for field in important_fields} for product in product_info_list])
            
            # Lưu DataFrame vào file Excel
            df.to_excel(excel_file, index=False, engine='openpyxl')
            log_and_emit(f"Đã lưu thông tin {len(product_info_list)} sản phẩm vào file {excel_file}")
        
        return product_info_list
    
    def _create_reports(self, result_dir, category_info, valid_urls):
        """Tạo báo cáo tổng thể"""
        try:
            # Cập nhật tiến trình
            emit_progress(95, f'Đang tạo báo cáo tổng hợp...')
            
            # Tạo báo cáo tổng thể các danh mục
            report_file = os.path.join(result_dir, 'category_report.xlsx')
            
            # Tạo DataFrame từ danh sách thông tin danh mục
            df_categories = pd.DataFrame(category_info)
            
            # Tạo một ExcelWriter
            with pd.ExcelWriter(report_file, engine='openpyxl') as writer:
                # Lưu thông tin danh mục vào sheet "Danh mục"
                df_categories.to_excel(writer, sheet_name='Danh mục', index=False)
                
                # Tạo sheet "Thông tin" để lưu các URL đầu vào
                df_info = pd.DataFrame({'URL': valid_urls})
                df_info.to_excel(writer, sheet_name='URL đầu vào', index=False)
                
                # Cập nhật tiến trình
                emit_progress(97, f'Đang tổng hợp dữ liệu sản phẩm từ tất cả danh mục...')
                
                # Tạo sheet "Tổng hợp sản phẩm" để lưu tất cả sản phẩm từ tất cả danh mục
                all_products = []
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        if file.endswith('_products.xlsx'):
                            file_path = os.path.join(root, file)
                            try:
                                # Đọc file Excel sản phẩm
                                df_products = pd.read_excel(file_path)
                                
                                # Thêm thông tin danh mục
                                category_name = os.path.basename(root)
                                df_products['Danh mục'] = category_name
                                
                                # Thêm vào danh sách tổng hợp
                                all_products.append(df_products)
                            except Exception as e:
                                print(f"Lỗi khi đọc file {file_path}: {str(e)}")
                
                # Nếu có dữ liệu sản phẩm, tạo DataFrame tổng hợp
                if all_products:
                    emit_progress(98, f'Đang định dạng báo cáo tổng hợp...')
                    
                    df_all_products = pd.concat(all_products, ignore_index=True)
                    
                    # Chỉ giữ lại các cột cần thiết với thứ tự xác định
                    columns_order = [
                        'STT', 'Danh mục', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 
                        'Mô tả', 'Tổng quan', 'Ảnh sản phẩm', 'Ảnh bổ sung', 'Tài liệu kỹ thuật', 'URL'
                    ]
                    
                    # Lọc các cột hiện có
                    existing_columns = [col for col in columns_order if col in df_all_products.columns]
                    
                    # Thêm các cột khác không có trong danh sách cố định
                    other_columns = [col for col in df_all_products.columns if col not in columns_order]
                    final_columns = existing_columns + other_columns
                    
                    # Sắp xếp DataFrame theo các cột
                    df_all_products = df_all_products[final_columns]
                    
                    # Sắp xếp lại STT
                    df_all_products['STT'] = range(1, len(df_all_products) + 1)
                    
                    # Lưu vào sheet "Tổng hợp sản phẩm"
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
    
    def _download_codienhaiau_product_image(self, soup, product_url, output_dir, product_code):
        """Tải ảnh sản phẩm từ trang codienhaiau.com với chất lượng cao nhất"""
        try:
            # Tạo thư mục images để lưu ảnh
            images_dir = os.path.join(output_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)
            
            # Tạo thư mục riêng cho ảnh gốc
            original_images_dir = os.path.join(output_dir, 'images_original')
            os.makedirs(original_images_dir, exist_ok=True)
            
            # Tìm tất cả các ảnh sản phẩm
            all_images_urls = []
            main_image_url = None
            
            # Ưu tiên tìm ảnh độ phân giải cao trong popup lightbox
            cbox_content = soup.select_one('#cboxLoadedContent')
            if cbox_content:
                img = cbox_content.select_one('img.cboxPhoto')
                if img and img.get('src'):
                    main_image_url = img.get('src')
                    all_images_urls.append(main_image_url)
                    print(f"Đã tìm thấy ảnh độ phân giải cao trong cboxLoadedContent: {main_image_url}")
            
            # Tìm ảnh trong gallery sản phẩm - ưu tiên ảnh gốc không bị resize
            gallery_selectors = [
                '.woocommerce-product-gallery__image a',
                '.product-thumbnails a',
                '.thumbnails a',
                'a.woocommerce-main-image',
                'a.image-lightbox'
            ]
            
            for selector in gallery_selectors:
                gallery_links = soup.select(selector)
                for link in gallery_links:
                    href = link.get('href')
                    if href and (href.endswith('.jpg') or href.endswith('.png') or href.endswith('.jpeg') or '/uploads/' in href):
                        # Loại bỏ các đường dẫn thumbnail có kích thước nhỏ (100x100, etc.)
                        if not re.search(r'[-_]\d+x\d+\.(jpg|png|jpeg)', href):
                            if href not in all_images_urls:
                                all_images_urls.append(href)
                                if not main_image_url:
                                    main_image_url = href
            
            # Tìm ảnh trong thẻ img - ưu tiên data-large_image và data-src
            img_selectors = [
                '.woocommerce-product-gallery__image img',
                '.images img.wp-post-image',
                '.product-images img',
                'img.wp-post-image',
                '.product-main-image img',
                '.product-gallery-slider img'
            ]
            
            for selector in img_selectors:
                images = soup.select(selector)
                for img in images:
                    # Ưu tiên tìm ảnh gốc từ các thuộc tính 
                    if img.get('data-large_image'):
                        img_url = img.get('data-large_image')
                        if img_url not in all_images_urls:
                            all_images_urls.append(img_url)
                            if not main_image_url:
                                main_image_url = img_url
                    elif img.get('data-src'):
                        img_url = img.get('data-src')
                        if img_url not in all_images_urls:
                            all_images_urls.append(img_url)
                            if not main_image_url:
                                main_image_url = img_url
                    elif img.get('src'):
                        # Thử tìm phiên bản không có kích thước trong src
                        src = img.get('src')
                        # Kiểm tra nếu src chứa kích thước như 300x300, thử lấy phiên bản gốc
                        src_parts = src.split('-')
                        if len(src_parts) > 1 and re.search(r'\d+x\d+', src_parts[-1]):
                            # Xóa phần kích thước để lấy URL ảnh gốc
                            base_name = os.path.basename(src)
                            file_name, ext = os.path.splitext(base_name)
                            # Tạo URL gốc bằng cách loại bỏ pattern kích thước -NNNxNNN
                            original_file = re.sub(r'-\d+x\d+(\.[^.]+)$', r'\1', base_name)
                            original_src = src.replace(base_name, original_file)
                            if original_src not in all_images_urls:
                                all_images_urls.append(original_src)
                                if not main_image_url:
                                    main_image_url = original_src
                        elif src not in all_images_urls:
                            all_images_urls.append(src)
                            if not main_image_url:
                                main_image_url = src
            
            # Tìm các ảnh trong thẻ source (nếu có lazy loading)
            source_selectors = ['source[data-srcset]', 'source[srcset]']
            for selector in source_selectors:
                for source in soup.select(selector):
                    srcset = source.get('data-srcset') or source.get('srcset', '')
                    if srcset:
                        # Lấy URL của ảnh có độ phân giải cao nhất từ srcset
                        # Format của srcset: "url1 1x, url2 2x, url3 800w, ..."
                        largest_url = None
                        largest_width = 0
                        for part in srcset.split(','):
                            part = part.strip()
                            if not part:
                                continue
                            url_parts = part.split(' ')
                            if len(url_parts) >= 2:
                                url = url_parts[0]
                                descriptor = url_parts[1]
                                if descriptor.endswith('w'):
                                    try:
                                        width = int(descriptor[:-1])
                                        if width > largest_width:
                                            largest_width = width
                                            largest_url = url
                                    except ValueError:
                                        pass
                                elif descriptor.endswith('x') and descriptor[:-1] > '1':
                                    largest_url = url
                        
                        if largest_url and largest_url not in all_images_urls:
                            all_images_urls.append(largest_url)
                            if not main_image_url:
                                main_image_url = largest_url
            
            # Tìm tài liệu kỹ thuật
            pdf_urls = []
            pdf_selectors = [
                'a[href$=".pdf"]',
                'a[href*="/pdf/"]',
                'a[href*="manual"]',
                'a[href*="datasheet"]',
                'a[href*="catalog"]'
            ]
            
            for selector in pdf_selectors:
                pdf_links = soup.select(selector)
                for link in pdf_links:
                    href = link.get('href')
                    if href and ('.pdf' in href.lower() or '/pdf/' in href.lower() or 'manual' in href.lower() or 'datasheet' in href.lower() or 'catalog' in href.lower()):
                        # Chỉ lưu tài liệu từ cùng domain để tránh tải quá nhiều
                        parsed_url = urlparse(product_url)
                        parsed_href = urlparse(href)
                        
                        # Nếu href là URL tương đối, tạo URL đầy đủ
                        if not parsed_href.netloc:
                            href = urljoin(product_url, href)
                            parsed_href = urlparse(href)
                        
                        # Chỉ tải tài liệu từ cùng domain hoặc các domain đáng tin cậy
                        if (parsed_href.netloc == parsed_url.netloc or 
                            parsed_href.netloc.endswith('codienhaiau.com') or 
                            'drive.google.com' in parsed_href.netloc or
                            'cloudfront.net' in parsed_href.netloc):
                            
                            if href not in pdf_urls:
                                pdf_urls.append(href)
            
            # Tạo thư mục documents để lưu tài liệu
            if pdf_urls:
                docs_dir = os.path.join(output_dir, 'documents')
                os.makedirs(docs_dir, exist_ok=True)
            
            # Tải các tài liệu kỹ thuật
            downloaded_docs = []
            for i, pdf_url in enumerate(pdf_urls):
                try:
                    # Tạo tên file từ URL
                    pdf_filename = f"{product_code}_doc_{i+1}.pdf" if product_code else f"{os.path.basename(product_url).replace('/', '_')}_doc_{i+1}.pdf"
                    pdf_path = os.path.join(docs_dir, pdf_filename)
                    
                    # Tải PDF
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                        'Referer': product_url
                    }
                    response = requests.get(pdf_url, stream=True, headers=headers, timeout=30)
                    response.raise_for_status()
                    
                    # Lưu file
                    with open(pdf_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    print(f"Đã tải tài liệu: {pdf_path}")
                    downloaded_docs.append(pdf_path)
                    
                except Exception as e:
                    print(f"Lỗi khi tải tài liệu từ {pdf_url}: {str(e)}")
            
            # Nếu không tìm thấy ảnh nào, trả về None
            if not all_images_urls:
                print(f"Không tìm thấy ảnh sản phẩm trên {product_url}")
                
                # Trả về danh sách tài liệu nếu có
                if downloaded_docs:
                    return {'documents': downloaded_docs}
                return None
            
            # Tải các ảnh sản phẩm
            downloaded_images = []
            for i, image_url in enumerate(all_images_urls):
                try:
                    # Xác định định dạng tên file
                    if i == 0:  # Ảnh chính
                        image_filename = f"{product_code}" if product_code else f"{os.path.basename(product_url).replace('/', '_')}"
                    else:  # Ảnh phụ
                        image_filename = f"{product_code}_{i}" if product_code else f"{os.path.basename(product_url).replace('/', '_')}_{i}"
                    
                    # Định dạng file webp và file gốc
                    webp_path = os.path.join(images_dir, f"{image_filename}.webp")
                    
                    # Xác định định dạng file gốc (.jpg, .png, etc.)
                    original_ext = os.path.splitext(image_url)[1].lower()
                    if not original_ext or original_ext not in ['.jpg', '.jpeg', '.png', '.gif']:
                        original_ext = '.jpg'  # Mặc định là jpg
                    
                    original_path = os.path.join(original_images_dir, f"{image_filename}{original_ext}")
                    
                    # Tải ảnh
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                        'Referer': product_url
                    }
                    response = requests.get(image_url, stream=True, headers=headers, timeout=20)
                    response.raise_for_status()
                    
                    # Lưu file gốc trước
                    with open(original_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    print(f"Đã tải ảnh gốc: {original_path}")
                    
                    # Kiểm tra nếu có thư viện PIL để chuyển đổi sang webp
                    try:
                        from PIL import Image
                        
                        # Chuyển đổi ảnh gốc sang webp
                        image = Image.open(original_path)
                        
                        # Chuyển sang RGB nếu cần (để xử lý các ảnh RGBA, P, etc.)
                        if image.mode in ('RGBA', 'LA'):
                            # Giữ kênh alpha nếu có
                            image = image.convert('RGBA')
                        elif image.mode != 'RGB':
                            image = image.convert('RGB')
                        
                        # Lưu với chất lượng cao
                        image.save(webp_path, format="WEBP", quality=90)
                        print(f"Đã tải và chuyển đổi ảnh sản phẩm sang webp: {webp_path}")
                        downloaded_images.append(webp_path)
                        
                    except (ImportError, Exception) as e:
                        # Nếu không có PIL hoặc có lỗi, chỉ giữ ảnh gốc
                        print(f"Không thể chuyển đổi sang webp (lỗi: {str(e)}), chỉ giữ ảnh gốc")
                        downloaded_images.append(original_path)
                        
                except Exception as e:
                    print(f"Lỗi khi tải ảnh từ {image_url}: {str(e)}")
            
            # Trả về đường dẫn ảnh đã tải và tài liệu
            result = {
                'images': downloaded_images,
                'original_images': [os.path.join(original_images_dir, f) for f in os.listdir(original_images_dir) if os.path.isfile(os.path.join(original_images_dir, f))]
            }
            
            if downloaded_docs:
                result['documents'] = downloaded_docs
                
            return result
            
        except Exception as e:
            print(f"Lỗi khi tải ảnh sản phẩm: {str(e)}")
            traceback.print_exc()
            return None

    def extract_codienhaiau_product_info(self, url, index=1, output_dir=None):
        """Trích xuất thông tin sản phẩm từ trang codienhaiau.com"""
        try:
            print(f"Đang trích xuất thông tin từ {url}")
            
            # Khởi tạo kết quả
            product_info = {
                'STT': index,
                'URL': url,
                'Mã sản phẩm': "",
                'Tên sản phẩm': "",
                'Giá': "",
                'Mô tả': "",
                'Tổng quan': "",
                'Ảnh sản phẩm': "",
                'Ảnh bổ sung': "",
                'Tài liệu kỹ thuật': ""
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
            
            # Debug: Lưu nội dung HTML trang sản phẩm nếu có upload_folder
            if self.upload_folder:
                product_id = url.rstrip('/').split('/')[-1]
                debug_dir = os.path.join(self.upload_folder, 'debug_products')
                os.makedirs(debug_dir, exist_ok=True)
                with open(os.path.join(debug_dir, f"{product_id}.html"), 'w', encoding='utf-8') as f:
                    f.write(str(soup))
                print(f"  > Đã lưu HTML debug vào {product_id}.html")
            
            # Trích xuất tên sản phẩm
            print(f"  > Đang trích xuất tên sản phẩm")
            name_selectors = [
                'h1.product_title.entry-title',
                '.product_title',
                'h1.entry-title'
            ]
            
            for selector in name_selectors:
                name_element = soup.select_one(selector)
                if name_element:
                    product_info['Tên sản phẩm'] = name_element.text.strip()
                    break
            
            # Trích xuất mã sản phẩm
            description_selectors = [
                '.woocommerce-product-details__short-description',
                '.short-description',
                '.product-short-description'
            ]
            
            for selector in description_selectors:
                description = soup.select_one(selector)
                if description:
                    # Tìm kiếm mẫu "SKU: xxx" trong nội dung
                    sku_match = re.search(r'SKU:\s*([A-Za-z0-9\-]+)', description.text)
                    if sku_match:
                        product_info['Mã sản phẩm'] = sku_match.group(1).strip()
                        break
                    
                    # Trích xuất mô tả ngắn luôn
                    product_info['Mô tả'] = description.text.strip()
            
            # Nếu không tìm thấy mã trong mô tả, thử tìm trong các phần tử khác
            if not product_info['Mã sản phẩm']:
                sku_selectors = [
                    '.sku',
                    '.product_meta .sku',
                    '.product-sku',
                    'span[itemprop="sku"]'
                ]
                
                for selector in sku_selectors:
                    sku_element = soup.select_one(selector)
                    if sku_element:
                        product_info['Mã sản phẩm'] = sku_element.text.strip()
                        break
            
            # Trích xuất giá sản phẩm
            price_selectors = [
                'span.woocommerce-Price-amount',
                '.price .woocommerce-Price-amount',
                'p.price',
                '.product-price'
            ]
            
            for selector in price_selectors:
                price_element = soup.select_one(selector)
                if price_element:
                    product_info['Giá'] = price_element.text.strip()
                    break
            
            # Nếu chưa có mô tả chi tiết, thử tìm trong tab Mô tả sản phẩm
            if not product_info['Mô tả']:
                description_tab_selectors = [
                    '#tab-description',
                    '.woocommerce-Tabs-panel--description',
                    '.tab-pane.active .content-product-description'
                ]
                
                for selector in description_tab_selectors:
                    desc_element = soup.select_one(selector)
                    if desc_element:
                        # Loại bỏ các script, style không cần thiết
                        for s in desc_element.select('script, style'):
                            s.extract()
                        product_info['Mô tả'] = desc_element.text.strip()
                        break
            
            # Trích xuất thông số kỹ thuật
            tech_table_selectors = [
                'table.woocommerce-product-attributes',
                '.woocommerce-product-attributes',
                '.shop_attributes',
                '.product-attributes'
            ]
            
            for selector in tech_table_selectors:
                tech_table = soup.select_one(selector)
                if tech_table:
                    specs_table_html = '<table class="specs-table" border="1" cellpadding="5" style="border-collapse: collapse;"><tbody>'
                    rows = tech_table.select('tr')
                    
                    for row in rows:
                        # Xử lý các loại hàng khác nhau
                        th_elements = row.select('th')
                        td_elements = row.select('td')
                        
                        if th_elements and td_elements:
                            # Kiểm tra nếu có chứa "Tiêu chuẩn" và có ảnh
                            header_text = th_elements[0].text.strip()
                            if 'tiêu chuẩn' in header_text.lower() and td_elements[0].find('img'):
                                # Tìm alt text của ảnh để xác định loại tiêu chuẩn
                                img = td_elements[0].find('img')
                                alt_text = img.get('alt', '').strip() if img else ''
                                
                                # Trích xuất tên tiêu chuẩn từ alt hoặc src của ảnh
                                standard_name = ''
                                if alt_text:
                                    # Trích xuất từ alt text
                                    parts = alt_text.split('-')
                                    if len(parts) > 1:
                                        standard_name = parts[-1].strip()
                                    else:
                                        standard_name = alt_text
                                else:
                                    # Nếu không có alt, trích xuất từ đường dẫn ảnh
                                    src = img.get('src', '')
                                    if src:
                                        filename = os.path.basename(src)
                                        parts = filename.split('.')
                                        if len(parts) > 1:
                                            name_parts = parts[0].split('-')
                                            if len(name_parts) > 1:
                                                standard_name = name_parts[-1].strip()
                                
                                # Xác định tiêu chuẩn dựa trên tên
                                if 'CE' in standard_name or 'ce' in standard_name.lower():
                                    standard_name = 'CE'
                                elif 'UL' in standard_name or 'ul' in standard_name.upper():
                                    standard_name = 'UL'
                                elif 'CSA' in standard_name or 'csa' in standard_name.upper():
                                    standard_name = 'CSA'
                                elif 'ISO' in standard_name or 'iso' in standard_name.upper():
                                    standard_name = 'ISO'
                                elif not standard_name:
                                    # Nếu không xác định được tên, đặt mặc định là CE
                                    standard_name = 'CE'
                                
                                # Tạo nội dung mới
                                specs_table_html += f'<tr><td>{header_text}</td><td>tiêu chuẩn | {standard_name}</td></tr>'
                            else:
                                # Trường hợp bình thường
                                specs_table_html += f'<tr><td>{header_text}</td><td>{td_elements[0].text.strip()}</td></tr>'
                        elif len(td_elements) >= 2:
                            # Kiểm tra nếu có chứa "Tiêu chuẩn" và có ảnh
                            first_col_text = td_elements[0].text.strip()
                            if 'tiêu chuẩn' in first_col_text.lower() and td_elements[1].find('img'):
                                # Tìm alt text của ảnh để xác định loại tiêu chuẩn
                                img = td_elements[1].find('img')
                                alt_text = img.get('alt', '').strip() if img else ''
                                
                                # Trích xuất tên tiêu chuẩn từ alt hoặc src của ảnh
                                standard_name = ''
                                if alt_text:
                                    # Trích xuất từ alt text
                                    parts = alt_text.split('-')
                                    if len(parts) > 1:
                                        standard_name = parts[-1].strip()
                                    else:
                                        standard_name = alt_text
                                else:
                                    # Nếu không có alt, trích xuất từ đường dẫn ảnh
                                    src = img.get('src', '')
                                    if src:
                                        filename = os.path.basename(src)
                                        parts = filename.split('.')
                                        if len(parts) > 1:
                                            name_parts = parts[0].split('-')
                                            if len(name_parts) > 1:
                                                standard_name = name_parts[-1].strip()
                                
                                # Xác định tiêu chuẩn dựa trên tên
                                if 'CE' in standard_name or 'ce' in standard_name.lower():
                                    standard_name = 'CE'
                                elif 'UL' in standard_name or 'ul' in standard_name.upper():
                                    standard_name = 'UL'
                                elif 'CSA' in standard_name or 'csa' in standard_name.upper():
                                    standard_name = 'CSA'
                                elif 'ISO' in standard_name or 'iso' in standard_name.upper():
                                    standard_name = 'ISO'
                                elif not standard_name:
                                    # Nếu không xác định được tên, đặt mặc định là CE
                                    standard_name = 'CE'
                                
                                # Tạo nội dung mới
                                specs_table_html += f'<tr><td>{first_col_text}</td><td>tiêu chuẩn | {standard_name}</td></tr>'
                            else:
                                # Trường hợp có colspan thông thường
                                if td_elements[0].has_attr('colspan'):
                                    colspan = int(td_elements[0].get('colspan', 1))
                                    if colspan > 1 and len(td_elements) > 1:
                                        specs_table_html += f'<tr><td>{td_elements[0].text.strip()}</td><td>{td_elements[1].text.strip()}</td></tr>'
                                    else:
                                        specs_table_html += f'<tr><td colspan="2">{td_elements[0].text.strip()}</td></tr>'
                                else:
                                    specs_table_html += f'<tr><td>{td_elements[0].text.strip()}</td><td>{td_elements[1].text.strip()}</td></tr>'
                    
                    specs_table_html += '</tbody></table>'
                    product_info['Tổng quan'] = specs_table_html
                    break
            
            # Tải ảnh sản phẩm nếu có thư mục đầu ra
            if output_dir:
                media_data = self._download_codienhaiau_product_image(soup, url, output_dir, product_info['Mã sản phẩm'])
                if media_data:
                    # Xử lý trường hợp trả về dict mới
                    if isinstance(media_data, dict):
                        # Xử lý ảnh chính
                        if 'main_image' in media_data and media_data['main_image']:
                            product_info['Ảnh sản phẩm'] = media_data['main_image']
                        
                        # Xử lý danh sách ảnh bổ sung
                        if 'all_images' in media_data and len(media_data['all_images']) > 1:
                            # Bỏ qua ảnh chính và chỉ lấy các ảnh phụ
                            additional_images = media_data['all_images'][1:] if len(media_data['all_images']) > 1 else []
                            if additional_images:
                                product_info['Ảnh bổ sung'] = ', '.join(additional_images)
                        
                        # Xử lý tài liệu kỹ thuật
                        if 'documents' in media_data and media_data['documents']:
                            product_info['Tài liệu kỹ thuật'] = ', '.join(media_data['documents'])
                    else:
                        # Trường hợp cũ (chỉ trả về đường dẫn ảnh chính)
                        product_info['Ảnh sản phẩm'] = media_data
            
            print(f"Đã trích xuất thông tin sản phẩm: {product_info['Tên sản phẩm']}, Mã: {product_info['Mã sản phẩm']}, Giá: {product_info['Giá']}")
            return product_info
            
        except Exception as e:
            print(f"Lỗi khi trích xuất thông tin từ {url}: {str(e)}")
            traceback.print_exc()
            return product_info
    
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
                    
                    category_products = self._extract_codienhaiau_links(self._get_soup(category_url), category_url)
                    
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
                from flask import session
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
            print(log_message)
            log_and_emit(log_message)
            
            # Kiểm tra xem có phải là trang danh mục không
            if not soup or 'category' not in current_url:
                log_message = f"URL không phải là trang danh mục: {current_url}"
                print(log_message)
                log_and_emit(log_message)
                return []
            
            # Kiểm tra xem có sản phẩm nào không
            if soup.select('.woocommerce-info.woocommerce-no-products-found'):
                log_message = f"Không tìm thấy sản phẩm nào trong danh mục: {current_url}"
                print(log_message)
                log_and_emit(log_message)
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
            for selector in product_link_selectors:
                product_elements = soup.select(selector)
                
                log_message = f"Selector '{selector}' tìm thấy {len(product_elements)} liên kết"
                print(log_message)
                log_and_emit(log_message)
                
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
                        href = element.get('href')
                    
                    # Kiểm tra xem liên kết này có phải là liên kết sản phẩm không
                    if href and '/product/' in href and not href.endswith('/product/'):
                        # Đảm bảo URL đầy đủ
                        full_url = urljoin(current_url, href)
                        product_urls.add(full_url)
                
            # Xác định mẫu URL cơ sở cho phân trang
            base_url = current_url
            page_number = 1
            
            # Trích xuất số trang hiện tại từ URL nếu có
            current_page_match = re.search(r'/page/(\d+)', current_url)
            if current_page_match:
                page_number = int(current_page_match.group(1))
                # Loại bỏ phần /page/X/ từ URL để có URL cơ sở
                base_url = re.sub(r'/page/\d+/', '/', current_url)
            
            # Tạo mẫu URL cho phân trang
            if base_url.endswith('/'):
                page_url_template = f"{base_url}page/{{0}}/"
            else:
                page_url_template = f"{base_url}/page/{{0}}/"
                
            # Tìm phân trang
            pagination_selectors = [
                '.woocommerce-pagination a.page-numbers', 
                '.woocommerce-pagination a.next',
                'nav.woocommerce-pagination a', 
                '.nav-pagination a',
                'ul.page-numbers a',
                '.pagination a',
                '.load-more-button'
            ]
            
            max_page = 1
            
            # Tìm thẻ a chứa số trang lớn nhất
            for selector in pagination_selectors:
                pagination_links = soup.select(selector)
                for link in pagination_links:
                    href = link.get('href', '')
                    # Kiểm tra URL phân trang dạng /page/X/
                    page_match = re.search(r'/page/(\d+)', href)
                    if page_match:
                        page_num = int(page_match.group(1))
                        if page_num > max_page:
                            max_page = page_num
            
            # Nếu không tìm thấy liên kết phân trang, kiểm tra số lượng sản phẩm
            if max_page == 1:
                # Tìm thông tin về tổng số sản phẩm
                total_products = 0
                count_text = None
                
                # Tìm các phần tử có thể chứa thông tin số lượng
                for selector in ['.woocommerce-result-count', '.count-container', '.showing-count']:
                    element = soup.select_one(selector)
                    if element:
                        count_text = element.text
                        break
                
                # Phân tích văn bản để tìm tổng số sản phẩm
                if count_text:
                    matches = re.search(r'(\d+)\s*(?:sản phẩm|kết quả|products|results)', count_text, re.IGNORECASE)
                    if matches:
                        total_products = int(matches.group(1))
                        
                # Nếu biết tổng số sản phẩm, tính số trang (giả định 50 sản phẩm/trang)
                if total_products > 0:
                    products_per_page = 50  # Codienhaiau.com thường hiển thị 50 sản phẩm mỗi trang
                    max_page = (total_products + products_per_page - 1) // products_per_page
            
            # Tạo URL cho tất cả các trang
            if max_page > 1:
                print(f"Phát hiện {max_page} trang phân trang. Đang tạo URLs...")
                
                # Thêm URLs cho tất cả các trang (trừ trang hiện tại)
                for p in range(1, max_page + 1):
                    if p != page_number:  # Bỏ qua trang hiện tại
                        page_url = page_url_template.format(p)
                        pagination_urls.add(page_url)
            
            # Kết quả
            print(f"Tổng cộng tìm thấy {len(product_urls)} sản phẩm độc nhất")
            print(f"Tìm thấy {len(pagination_urls)} trang phân trang")
            
            # Tải tất cả các trang phân trang và thu thập sản phẩm
            if pagination_urls:
                print("Đang thu thập sản phẩm từ các trang phân trang...")
                
                # Làm mới danh sách sản phẩm đã có
                all_products = list(product_urls)
                
                for page_url in pagination_urls:
                    try:
                        print(f"Đang tải trang phân trang: {page_url}")
                        page_soup = self._get_soup(page_url)
                        page_products_found = 0
                        
                        for selector in product_link_selectors:
                            links = page_soup.select(selector)
                            
                            # Xử lý tương tự như trên
                            if selector == 'h2.woocommerce-loop-product__title':
                                for title in links:
                                    parent_link = title.find_parent('a')
                                    if parent_link and 'href' in parent_link.attrs:
                                        href = parent_link['href']
                                        if href and '/product/' in href:
                                            full_url = urljoin(current_url, href)
                                            if full_url not in product_urls:  # Kiểm tra trùng lặp
                                                all_products.append(full_url)
                                                page_products_found += 1
                            else:
                                for link in links:
                                    href = link.get('href', '')
                                    if href and '/product/' in href:
                                        full_url = urljoin(current_url, href)
                                        if full_url not in product_urls:  # Kiểm tra trùng lặp
                                            all_products.append(full_url)
                                            page_products_found += 1
                        
                        print(f"Tìm thấy {page_products_found} sản phẩm từ trang {page_url}")
                    except Exception as e:
                        print(f"Lỗi khi xử lý trang phân trang {page_url}: {str(e)}")
                        traceback.print_exc()
                
                # Loại bỏ trùng lặp và trả về danh sách các URL sản phẩm
                all_products = list(set(all_products))
            else:
                all_products = list(product_urls)
            
            print(f"Tổng cộng thu thập được {len(all_products)} URL sản phẩm từ tất cả các trang")
            return all_products
            
        except Exception as e:
            print(f"Lỗi khi trích xuất liên kết từ {current_url}: {str(e)}")
            traceback.print_exc()
            return [] 