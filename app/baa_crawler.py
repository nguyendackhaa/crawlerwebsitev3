import os
import shutil
import time
import zipfile
from datetime import datetime
from app.crawler import (
    is_category_url, is_product_url, extract_product_urls, extract_product_info,
    download_baa_product_images_fixed, get_html_content
)
from app import socketio
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import re
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
import traceback
from queue import Queue

def get_category_vn_name(url):
    html = get_html_content(url)
    if html:
        soup = BeautifulSoup(html, 'html.parser')
        # Ưu tiên lấy từ breadcrumb
        breadcrumb = soup.select_one('.breadcrumb li.active, .breadcrumb-item.active, .breadcrumb li:last-child, .breadcrumb-item:last-child')
        if breadcrumb and breadcrumb.text.strip():
            return re.sub(r'[\\/:*?"<>|]', '_', breadcrumb.text.strip())
        # Hoặc lấy từ thẻ h1
        h1 = soup.select_one('h1, .category-title, .page-title')
        if h1 and h1.text.strip():
            return re.sub(r'[\\/:*?"<>|]', '_', h1.text.strip())
    # Nếu không lấy được thì fallback sang tên từ URL
    parsed = urlparse(url)
    path = parsed.path.strip('/').split('/')
    if path:
        last = path[-1]
        name = re.sub(r'_\d+$', '', last)
        return name
    return 'danh_muc'

def detect_pagination(url):
    """Phát hiện số trang tối đa cho danh mục"""
    try:
        html = get_html_content(url)
        if not html:
            return 1
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Tìm thành phần phân trang
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
        print(f"Lỗi khi phát hiện số trang: {str(e)}")
        return 1

def make_pagination_url(base_url, page_number):
    """Tạo URL phân trang cho BAA.vn"""
    if page_number <= 1:
        return base_url
        
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

def extract_product_series(url):
    """
    Trích xuất thông tin series từ trang sản phẩm BAA.vn
    Hỗ trợ cả trang sản phẩm đơn lẻ và trang danh mục series
    
    Args:
        url (str): URL của trang sản phẩm hoặc danh mục series
        
    Returns:
        str: Tên series hoặc 'Khac' nếu không tìm thấy
    """
    try:
        print(f"[DEBUG_SERIES] Bắt đầu trích xuất series từ: {url}")
        html = get_html_content(url)
        if not html:
            print(f"[DEBUG_SERIES] Không thể tải HTML từ {url}")
            return 'Khac'
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Method 1: Tìm thông tin series từ HTML structure sản phẩm đơn lẻ  
        symbol_headers = soup.select('.product__symbol-header')
        print(f"[DEBUG_SERIES] Tìm thấy {len(symbol_headers)} symbol headers")
        
        for header in symbol_headers:
            label_span = header.select_one('.product__symbol-label')
            if label_span and 'series' in label_span.text.lower():
                value_span = header.select_one('.product__symbol__value')
                if value_span:
                    # Thử các cách trích xuất khác nhau
                    series_candidates = []
                    
                    series_link = value_span.select_one('a .color-change-text')
                    if series_link:
                        series_candidates.append(series_link.text.strip())
                    
                    color_change = value_span.select_one('.color-change-text')
                    if color_change:
                        series_candidates.append(color_change.text.strip())
                    
                    series_a = value_span.select_one('a')
                    if series_a:
                        series_candidates.append(series_a.get_text(strip=True))
                    
                    full_text = value_span.get_text(strip=True)
                    if full_text:
                        series_candidates.append(full_text)
                    
                    for candidate in series_candidates:
                        if candidate and len(candidate.strip()) > 0:
                            clean_candidate = candidate.strip()
                            clean_candidate = re.sub(r'\s*&nbsp;\s*$', '', clean_candidate)
                            
                            if clean_candidate:
                                series_name = re.sub(r'[\\/:*?"<>|]', '_', clean_candidate)
                                series_name = re.sub(r'\s+', '_', series_name)
                                
                                if series_name and series_name != '_':
                                    print(f"[DEBUG_SERIES] Series từ symbol header: '{series_name}'")
                                    return series_name
        
        # Method 2: Trích xuất từ tiêu đề h1 (cho trang danh mục series)
        print(f"[DEBUG_SERIES] Thử trích xuất từ tiêu đề h1")
        h1_elements = soup.select('h1.product__list--title, h1.product__name, h1')
        for h1 in h1_elements:
            h1_text = h1.text.strip()
            print(f"[DEBUG_SERIES] H1 text: '{h1_text}'")
            
            # Pattern: Chỉ lấy từ cuối cùng trước "series"
            # Ví dụ: "HANYOUNG T series" -> "T", "QLIGHT S125TL series" -> "S125TL"
            series_pattern = r'\b([A-Z0-9]+)\s+series\b'
            matches = re.findall(series_pattern, h1_text, re.IGNORECASE)
            if matches:
                for match in matches:
                    series_name = match.strip()
                    if series_name:
                        clean_series = re.sub(r'[\\/:*?"<>|]', '_', series_name)
                        clean_series = re.sub(r'\s+', '_', clean_series)
                        print(f"[DEBUG_SERIES] Series từ h1: '{clean_series}_series'")
                        return clean_series + '_series'
        
        # Method 3: Trích xuất từ URL
        print(f"[DEBUG_SERIES] Thử trích xuất từ URL")
        url_pattern = r'/([^/]*series[^/]*?)(?:_\d+)?/?$'
        url_match = re.search(url_pattern, url, re.IGNORECASE)
        if url_match:
            url_series = url_match.group(1)
            clean_url_series = re.sub(r'[\\/:*?"<>|]', '_', url_series)
            clean_url_series = re.sub(r'[-_]+', '_', clean_url_series)
            print(f"[DEBUG_SERIES] Series từ URL: '{clean_url_series}'")
            return clean_url_series
        
        print(f"[DEBUG_SERIES] Không tìm thấy series, sử dụng 'Khac'")
        return 'Khac'
        
    except Exception as e:
        print(f"[ERROR] Lỗi khi trích xuất series từ {url}: {str(e)}")
        import traceback
        traceback.print_exc()
        return 'Khac'

def sanitize_folder_name(name):
    """
    Làm sạch tên thư mục để tránh ký tự không hợp lệ
    
    Args:
        name (str): Tên cần làm sạch
        
    Returns:
        str: Tên đã được làm sạch
    """
    if not name:
        return 'Khac'
    
    # Loại bỏ các ký tự không hợp lệ cho tên thư mục
    clean_name = re.sub(r'[\\/:*?"<>|]', '_', name)
    clean_name = re.sub(r'\s+', '_', clean_name)
    clean_name = clean_name.strip('_.')
    
    return clean_name if clean_name else 'Khac'

class BaaProductCrawler:
    def __init__(self, output_root=None, max_workers=8, max_retries=3):
        self.output_root = output_root or os.path.join(os.getcwd(), "output_baa")
        self.max_workers = max_workers
        self.max_retries = max_retries
        os.makedirs(self.output_root, exist_ok=True)

    def crawl_products(self, input_urls):
        """
        Nhận vào danh sách URL (danh mục hoặc sản phẩm), trả về tuple (list dict sản phẩm, đường dẫn folder kết quả)
        Cải tiến với mô hình đa luồng theo chức năng:
        - Luồng 1: Phân tích và phân loại URL
        - Luồng 2: Thu thập URL sản phẩm
        - Luồng 3: Xử lý thông tin sản phẩm
        - Luồng 4: Tải ảnh sản phẩm
        """
        # Đo thời gian thực hiện
        start_time = time.time()
        
        # Thống kê hiệu suất
        stats = {
            "urls_processed": 0,
            "products_found": 0,
            "products_processed": 0,
            "products_skipped": 0,  # Sản phẩm bỏ qua vì không có giá
            "images_downloaded": 0,
            "categories": 0,
            "single_products": 0,
            "failed_products": 0,
            "failed_images": 0
        }
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"Baa_ngay{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        all_products = []
        category_folders = []
        
        # Thu thập dữ liệu báo cáo ảnh từ tất cả danh mục
        all_image_report_data = []
        
        # Thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 0, 
            'message': f'Bắt đầu xử lý {len(input_urls)} URL từ BAA.vn',
            'detail': 'Đang phân tích và phân loại các URL...'
        })
        
        # Sử dụng Queue để truyền dữ liệu giữa các luồng xử lý
        category_queue = Queue()  # Hàng đợi cho các danh mục đã phân loại
        product_url_queue = Queue()  # Hàng đợi cho các URL sản phẩm từ danh mục
        
        # 1. Phân loại URL và tạo cấu trúc thư mục
        def classify_urls_worker():
            """Phân loại URL thành danh mục và sản phẩm đơn lẻ"""
            category_map = {}  # Lưu ánh xạ tên danh mục -> danh sách URL
            single_products = []  # Lưu danh sách URL sản phẩm đơn lẻ
            
            for i, url in enumerate(input_urls):
                try:
                    stats["urls_processed"] += 1
                    if is_category_url(url):
                        cat_name = get_category_vn_name(url)
                        if cat_name not in category_map:
                            category_map[cat_name] = []
                            stats["categories"] += 1
                        category_map[cat_name].append(url)
                        socketio.emit('progress_update', {
                            'percent': 2 * i // len(input_urls), 
                            'message': f'Đang phân tích URL {i+1}/{len(input_urls)}',
                            'detail': f'Đã xác định danh mục: {cat_name}'
                        })
                    elif is_product_url(url):
                        single_products.append(url)
                        stats["single_products"] += 1
                        socketio.emit('progress_update', {
                            'percent': 2 * i // len(input_urls), 
                            'message': f'Đang phân tích URL {i+1}/{len(input_urls)}',
                            'detail': f'Đã xác định sản phẩm đơn lẻ'
                        })
                except Exception as e:
                    print(f"Lỗi khi phân tích URL {url}: {str(e)}")
            
            # Đặt các danh mục vào hàng đợi
            for cat_name, cat_urls in category_map.items():
                category_queue.put((cat_name, cat_urls))
            
            # Đặt sản phẩm đơn lẻ vào hàng đợi nếu có
            if single_products:
                category_queue.put(('san_pham_le', single_products))
            
            return category_map, single_products
        
        # 2. Tạo các thư mục báo cáo
        report_dir = os.path.join(result_dir, "Bao_cao")
        os.makedirs(report_dir, exist_ok=True)
        
        # Thống kê tổng số lượng và thiết lập
        category_map, single_products = classify_urls_worker()
        total_categories = len(category_map)
        total_steps = total_categories + (1 if single_products else 0)
        
        # Tạo các thư mục danh mục trước
        for cat_name in category_map.keys():
            cat_dir = os.path.join(result_dir, cat_name)
            anh_dir = os.path.join(cat_dir, "Anh")
            os.makedirs(anh_dir, exist_ok=True)
            category_folders.append(cat_dir)
        
        # Nếu có sản phẩm đơn lẻ, tạo thư mục riêng
        if single_products:
            le_dir = os.path.join(result_dir, 'san_pham_le')
            anh_dir = os.path.join(le_dir, "Anh")
            os.makedirs(anh_dir, exist_ok=True)
            category_folders.append(le_dir)
        
        # 3. Xử lý từng danh mục với mô hình luồng theo chức năng
        for cat_idx, (cat_name, cat_urls) in enumerate(category_map.items()):
            current_step = cat_idx + 1
            step_progress_base = 5 + (current_step * 90 // max(1, total_steps))
            
            cat_dir = os.path.join(result_dir, cat_name)
            anh_dir = os.path.join(cat_dir, "Anh")
            
            socketio.emit('progress_update', {
                'percent': step_progress_base, 
                'message': f'Đang xử lý danh mục [{current_step}/{total_steps}]: {cat_name}',
                'detail': f'Tạo thư mục và chuẩn bị cào dữ liệu ({len(cat_urls)} URL nguồn)'
            })
            
            # Thu thập URL sản phẩm từ các danh mục, bao gồm xử lý phân trang
            # Sử dụng thread riêng để không chặn luồng chính
            product_urls_queue = Queue()  # Hàng đợi lưu URL sản phẩm của danh mục hiện tại
            
            def collect_product_urls_thread():
                """Thread thu thập URL sản phẩm từ danh mục và đặt vào hàng đợi"""
                product_urls = self._collect_product_urls_with_pagination(cat_urls)
                product_urls = list(dict.fromkeys(product_urls))  # Loại bỏ trùng lặp
                stats["products_found"] += len(product_urls)
                
                # Đặt mỗi URL vào hàng đợi để xử lý
                for url in product_urls:
                    product_urls_queue.put(url)
                
                # Đặt None để báo hiệu đã hết URL
                product_urls_queue.put(None)
                
                # Ghi log chi tiết
                print(f"[{cat_name}] Đã thu thập {len(product_urls)} URL sản phẩm từ {len(cat_urls)} URL danh mục")
                return product_urls
            
            # Khởi chạy thread thu thập URL
            from threading import Thread
            url_collector_thread = Thread(target=collect_product_urls_thread)
            url_collector_thread.start()
            url_collector_thread.join()  # Đợi thread hoàn thành
            
            # Lấy danh sách URL để lưu vào file và tiếp tục xử lý
            # Sao chép tất cả URL từ Queue (đã có None ở cuối)
            product_urls = []
            while True:
                url = product_urls_queue.get()
                if url is None:
                    break
                product_urls.append(url)
                product_urls_queue.put(url)  # Đặt lại URL để xử lý sản phẩm
            product_urls_queue.put(None)  # Đặt lại None vào cuối
            
            socketio.emit('progress_update', {
                'percent': step_progress_base + 2, 
                'message': f'[{cat_name}] Đã thu thập {len(product_urls)} liên kết sản phẩm',
                'detail': f'Chuẩn bị trích xuất thông tin và tải ảnh sản phẩm'
            })
            
            # Lưu danh sách URL sản phẩm
            urls_file = os.path.join(cat_dir, f"{cat_name}_urls.txt")
            with open(urls_file, 'w', encoding='utf-8') as f:
                for url in product_urls:
                    f.write(f"{url}\n")
            
            # Tạo file Du_lieu.xlsx
            data_excel = os.path.join(cat_dir, "Du_lieu.xlsx")
            
            # Hàng đợi lưu thông tin sản phẩm đã xử lý và code-url để tải ảnh
            product_info_queue = Queue()
            image_task_queue = Queue()
            
            # Thread xử lý thông tin sản phẩm
            def process_product_info_thread():
                """Thread xử lý thông tin chi tiết sản phẩm từ URL"""
                products = []
                code_url_map = {}
                series_products_map = {}  # Nhóm sản phẩm theo series
                required_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'Tổng quan', 'Series', 'URL']
                batch_size = min(20, max(1, len(product_urls) // 5))
                
                # Theo dõi tiến độ trong thread này
                items_processed = 0
                batch_start_time = time.time()
                batch_success = 0
                batch_failure = 0
                batch_skipped = 0  # Số sản phẩm bỏ qua vì không có giá
                
                # Phần trăm tiến độ cho batch này
                batch_percent_start = step_progress_base + 5
                batch_percent_range = 30 // max(1, total_steps)
                
                # Xử lý đa luồng trích xuất thông tin sản phẩm
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {}
                    
                    # Lấy URL từ hàng đợi và xử lý
                    url_index = 0
                    while True:
                        url = product_urls_queue.get()
                        if url is None:
                            break
                        
                        future = executor.submit(extract_product_info, url, required_fields, url_index + 1)
                        futures[future] = url
                        url_index += 1
                    
                    # Xử lý kết quả khi hoàn thành
                    for future in as_completed(futures):
                        url = futures[future]
                        try:
                            info = future.result()
                            if info:
                                # Trích xuất thông tin series từ URL sản phẩm
                                try:
                                    product_series = extract_product_series(url)
                                    info['Series'] = product_series
                                    print(f"[{cat_name}] Sản phẩm {info.get('Mã sản phẩm', 'N/A')} thuộc series: {product_series}")
                                except Exception as e:
                                    print(f"[{cat_name}] Lỗi khi trích xuất series cho {url}: {str(e)}")
                                    info['Series'] = 'Khac'
                                
                                # Kiểm tra xem sản phẩm có giá không để thống kê
                                product_price = info.get('Giá', '').strip()
                                
                                # Chuẩn hóa thông số kỹ thuật
                                info['Tổng quan'] = self._normalize_spec(info.get('Tổng quan', ''))
                                products.append(info)
                                
                                # Nhóm sản phẩm theo series
                                series_name = info['Series']
                                if series_name not in series_products_map:
                                    series_products_map[series_name] = []
                                series_products_map[series_name].append(info)
                                
                                # Lưu mã sản phẩm và URL để tải ảnh (nhóm theo series)
                                if info.get('Mã sản phẩm') and info.get('URL'):
                                    code_url_map[info['Mã sản phẩm']] = {
                                        'url': info['URL'],
                                        'series': series_name
                                    }
                                
                                if not product_price or product_price == '':
                                    # Thống kê sản phẩm không có giá nhưng vẫn xử lý
                                    batch_skipped += 1
                                    stats["products_skipped"] += 1
                                    print(f"[{cat_name}] Sản phẩm không có giá (vẫn lưu thông tin): {info.get('Tên sản phẩm', 'N/A')}")
                                else:
                                    batch_success += 1
                                
                                stats["products_processed"] += 1
                                
                                # Thêm vào hàng đợi thông tin sản phẩm
                                product_info_queue.put(info)
                            else:
                                batch_failure += 1
                                stats["failed_products"] += 1
                                print(f"[{cat_name}] Không thể trích xuất thông tin từ {url}")
                        except Exception as e:
                            batch_failure += 1
                            stats["failed_products"] += 1
                            print(f"[{cat_name}] Lỗi khi trích xuất: {str(e)}")
                        
                        # Cập nhật tiến độ
                        items_processed += 1
                        
                        # Tính tiến độ chi tiết hơn
                        batch_progress = batch_percent_start + (items_processed * batch_percent_range // len(product_urls))
                        
                        # Hiển thị thông tin tiến độ
                        if items_processed % 5 == 0 or items_processed == len(product_urls):
                            # Tính tốc độ xử lý
                            elapsed = time.time() - batch_start_time
                            speed = items_processed / elapsed if elapsed > 0 else 0
                            remaining = (len(product_urls) - items_processed) / speed if speed > 0 else 0
                            
                            # Format thời gian còn lại
                            remaining_info = ""
                            if remaining > 0:
                                if remaining < 60:
                                    remaining_info = f", còn lại: {remaining:.1f}s"
                                else:
                                    remaining_info = f", còn lại: {remaining/60:.1f}m"
                            
                            socketio.emit('progress_update', {
                                'percent': batch_progress, 
                                'message': f'[{cat_name}] Đã xử lý {items_processed}/{len(product_urls)} sản phẩm ({batch_success} có giá, {batch_skipped} không có giá, {batch_failure} lỗi)',
                                'detail': f'Tốc độ: {speed:.1f} sp/s{remaining_info}, đã phát hiện {len(series_products_map)} series'
                            })
                
                # Đặt None vào cuối hàng đợi để báo hiệu đã hoàn thành
                product_info_queue.put(None)
                
                # Đặt code_url_map và series_products_map vào image_task_queue để tải ảnh
                image_task_queue.put((code_url_map, series_products_map, anh_dir, cat_name, cat_idx, total_categories, step_progress_base + 40))
                
                print(f"[{cat_name}] Kết quả xử lý: {batch_success} sản phẩm có giá, {batch_skipped} sản phẩm không có giá, {batch_failure} lỗi")
                print(f"[{cat_name}] Đã phát hiện {len(series_products_map)} series: {list(series_products_map.keys())}")
                
                return products, code_url_map, series_products_map
            
            # Thread lưu thông tin sản phẩm vào file Excel
            def save_product_info_thread():
                """Thread lưu thông tin sản phẩm vào file Excel theo series"""
                products = []
                series_products_map = {}  # Thu thập sản phẩm theo series
                
                # Lấy thông tin sản phẩm từ hàng đợi
                while True:
                    info = product_info_queue.get()
                    if info is None:
                        break
                    products.append(info)
                    
                    # Nhóm sản phẩm theo series
                    series_name = info.get('Series', 'Khac')
                    if series_name not in series_products_map:
                        series_products_map[series_name] = []
                    series_products_map[series_name].append(info)
                
                # Tạo thư mục cho từng series và lưu dữ liệu
                for series_name, series_products in series_products_map.items():
                    # Tạo thư mục series
                    series_folder_name = sanitize_folder_name(series_name)
                    series_dir = os.path.join(cat_dir, series_folder_name)
                    series_anh_dir = os.path.join(series_dir, "Anh")
                    os.makedirs(series_anh_dir, exist_ok=True)
                    
                    # Lưu dữ liệu series vào file Excel riêng
                    series_excel_file = os.path.join(series_dir, f"{series_folder_name}_Du_lieu.xlsx")
                    if series_products:
                        df = pd.DataFrame(series_products)
                        df.to_excel(series_excel_file, index=False)
                        print(f"[{cat_name}] Đã lưu dữ liệu series '{series_name}': {len(series_products)} sản phẩm vào {series_excel_file}")
                
                # Lưu file tổng hợp tất cả sản phẩm (giữ nguyên chức năng cũ)
                if products:
                    df = pd.DataFrame(products)
                    df.to_excel(data_excel, index=False)
                    print(f"[{cat_name}] Đã lưu dữ liệu tổng hợp: {len(products)} sản phẩm")
                
                # Thêm các sản phẩm vào danh sách tổng hợp
                all_products.extend(products)
                
                print(f"[{cat_name}] Đã phân loại {len(products)} sản phẩm vào {len(series_products_map)} series")
                return products, series_products_map
            
            # Thread tải ảnh sản phẩm
            def download_images_thread():
                """Thread tải ảnh sản phẩm"""
                # Lấy thông tin tải ảnh từ hàng đợi
                code_url_map, series_products_map, anh_dir, cat_name, cat_idx, total_categories, percent_base = image_task_queue.get()
                
                # Tải ảnh sản phẩm
                img_map, image_report_data = self._download_product_images(code_url_map, series_products_map, anh_dir, cat_name, cat_idx, total_categories, percent_base)
                
                # Cập nhật thống kê
                stats["images_downloaded"] += len(img_map)
                stats["failed_images"] += len(code_url_map) - len(img_map)
                
                # Thu thập dữ liệu báo cáo ảnh để hợp nhất sau này
                nonlocal all_image_report_data
                all_image_report_data.extend(image_report_data)
                
                return img_map
            
            # Khởi chạy các thread xử lý
            product_processor_thread = Thread(target=process_product_info_thread)
            product_saver_thread = Thread(target=save_product_info_thread)
            image_downloader_thread = Thread(target=download_images_thread)
            
            # Bắt đầu thread xử lý sản phẩm
            product_processor_thread.start()
            
            # Bắt đầu thread lưu thông tin
            product_saver_thread.start()
            
            # Bắt đầu thread tải ảnh
            image_downloader_thread.start()
            
            # Đợi tất cả các thread hoàn thành
            product_processor_thread.join()
            product_saver_thread.join()
            image_downloader_thread.join()
            
            # Thông báo hoàn thành danh mục
            socketio.emit('progress_update', {
                'percent': step_progress_base + 65, 
                'message': f'[{cat_name}] Đã hoàn thành xử lý danh mục',
                'detail': f'Đã xử lý {len(product_urls)} sản phẩm'
            })
        
        # Tạo file tổng hợp ngoài cùng với nhiều sheet
        report_path = os.path.join(result_dir, 'Bao_cao_tong_hop.xlsx')
        
        # Thu thập thống kê series từ tất cả sản phẩm
        series_stats = {}
        for product in all_products:
            series_name = product.get('Series', 'Khac')
            if series_name not in series_stats:
                series_stats[series_name] = {
                    'So_luong': 0,
                    'Co_gia': 0,
                    'Khong_gia': 0,
                    'Danh_muc': set()
                }
            
            series_stats[series_name]['So_luong'] += 1
            
            # Thống kê theo giá
            product_price = product.get('Giá', '').strip()
            if product_price:
                series_stats[series_name]['Co_gia'] += 1
            else:
                series_stats[series_name]['Khong_gia'] += 1
            
            # Thu thập danh mục chứa series này
            # Có thể lấy từ URL hoặc thông tin khác
            product_url = product.get('URL', '')
            if product_url:
                # Trích xuất tên danh mục từ URL hoặc context
                for cat_name in category_map.keys():
                    series_stats[series_name]['Danh_muc'].add(cat_name)
                    break  # Chỉ cần 1 danh mục đại diện
        
        # Chuyển đổi set thành string cho việc lưu trữ
        series_summary_data = []
        for series_name, stats in series_stats.items():
            series_summary_data.append({
                'Series': series_name,
                'Tổng số sản phẩm': stats['So_luong'],
                'Sản phẩm có giá': stats['Co_gia'],
                'Sản phẩm không có giá': stats['Khong_gia'],
                'Tỷ lệ có giá (%)': f"{(stats['Co_gia'] * 100 / stats['So_luong']):.1f}" if stats['So_luong'] > 0 else "0.0",
                'Các danh mục': ', '.join(stats['Danh_muc']) if stats['Danh_muc'] else 'N/A'
            })
        
        # Tạo một ExcelWriter để ghi nhiều sheet vào cùng một file Excel
        with pd.ExcelWriter(report_path, engine='openpyxl') as writer:
            # Sheet dữ liệu sản phẩm
            if all_products:
                df = pd.DataFrame(all_products)
                df.to_excel(writer, sheet_name='Du_lieu_san_pham', index=False)
            
            # Sheet thống kê series
            if series_summary_data:
                series_df = pd.DataFrame(series_summary_data)
                # Sắp xếp theo số lượng sản phẩm giảm dần
                series_df = series_df.sort_values('Tổng số sản phẩm', ascending=False)
                series_df.to_excel(writer, sheet_name='Thong_ke_series', index=False)
            
            # Sheet thống kê tổng quan
            stats_data = [
                {
                    'Chỉ số': 'Tổng số URL xử lý',
                    'Giá trị': stats["urls_processed"]
                },
                {
                    'Chỉ số': 'Số danh mục',
                    'Giá trị': stats["categories"]
                },
                {
                    'Chỉ số': 'Số series phát hiện',
                    'Giá trị': len(series_stats)
                },
                {
                    'Chỉ số': 'Số sản phẩm đơn lẻ',
                    'Giá trị': stats["single_products"]
                },
                {
                    'Chỉ số': 'Tổng số sản phẩm tìm thấy',
                    'Giá trị': stats["products_found"]
                },
                {
                    'Chỉ số': 'Số sản phẩm xử lý thành công',
                    'Giá trị': stats["products_processed"]
                },
                {
                    'Chỉ số': 'Số sản phẩm không có giá',
                    'Giá trị': stats["products_skipped"]
                },
                {
                    'Chỉ số': 'Số sản phẩm lỗi',
                    'Giá trị': stats["failed_products"]
                },
                {
                    'Chỉ số': 'Số ảnh tải thành công',
                    'Giá trị': stats["images_downloaded"]
                },
                {
                    'Chỉ số': 'Số ảnh tải thất bại',
                    'Giá trị': stats["failed_images"]
                },
                {
                    'Chỉ số': 'Tỷ lệ thành công sản phẩm',
                    'Giá trị': f"{stats['products_processed'] * 100 / max(1, stats['products_found']):.2f}%"
                },
                {
                    'Chỉ số': 'Tỷ lệ thành công ảnh',
                    'Giá trị': f"{stats['images_downloaded'] * 100 / max(1, stats['products_processed']):.2f}%"
                },
                {
                    'Chỉ số': 'Thời gian xử lý',
                    'Giá trị': f"{(time.time() - start_time) / 60:.2f} phút"
                }
            ]
            stats_df = pd.DataFrame(stats_data)
            stats_df.to_excel(writer, sheet_name='Thong_ke_tong_quan', index=False)
            
            # Sheet báo cáo tải ảnh
            if all_image_report_data:
                image_df = pd.DataFrame(all_image_report_data)
                # Sắp xếp theo series và mã sản phẩm
                image_df = image_df.sort_values(['Series', 'Mã sản phẩm'], ascending=[True, True])
                image_df.to_excel(writer, sheet_name='Bao_cao_anh', index=False)
        
        # Nén thư mục kết quả thành file ZIP
        socketio.emit('progress_update', {
            'percent': 95, 
            'message': f'Đang nén kết quả thành file ZIP...',
            'detail': f'Đã xử lý {len(all_products)} sản phẩm, {stats["images_downloaded"]} ảnh'
        })
        
        zip_path = result_dir + '.zip'
        zip_filename = os.path.basename(zip_path)
        
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for folder in category_folders:
                    for root, dirs, files in os.walk(folder):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, result_dir)
                            zipf.write(file_path, arcname)
                
                # Thêm file báo cáo duy nhất vào ZIP
                zipf.write(report_path, os.path.basename(report_path))
        except Exception as e:
            print(f"Lỗi khi nén thư mục: {str(e)}")
            socketio.emit('progress_update', {
                'percent': 100, 
                'message': f'Hoàn thành lấy dữ liệu {len(all_products)} sản phẩm (không nén được)',
                'detail': f'Đã xảy ra lỗi khi nén: {str(e)}',
                'completed': True,
                'download_ready': False
            })
        else:
            # Tính toán tổng thời gian và hiệu suất
            total_time = time.time() - start_time
            products_per_second = stats["products_processed"] / total_time if total_time > 0 else 0
            
            # Tạo thông tin chi tiết cho download
            download_info = {
                'zip_path': zip_path,
                'zip_filename': zip_filename,
                'download_url': f'/download-baa-result/{zip_filename}',
                'total_products': len(all_products),
                'total_series': len(series_stats),
                'total_categories': stats["categories"],
                'processing_time': f"{total_time/60:.2f} phút",
                'success_rate': f"{stats['products_processed'] * 100 / max(1, stats['products_found']):.1f}%",
                'image_success_rate': f"{stats['images_downloaded'] * 100 / max(1, stats['products_processed']):.1f}%",
                'file_size': f"{os.path.getsize(zip_path) / (1024 * 1024):.2f} MB" if os.path.exists(zip_path) else "N/A"
            }
            
            socketio.emit('progress_update', {
                'percent': 100, 
                'message': f'🎉 Hoàn thành! Đã cào được {len(all_products)} sản phẩm từ {len(series_stats)} series',
                'detail': f'Thời gian: {total_time/60:.2f} phút • Tốc độ: {products_per_second:.2f} sp/s • File: {zip_filename}',
                'completed': True,
                'download_ready': True,
                'download_info': download_info,
                'series_stats': [
                    {
                        'series_name': series_name,
                        'product_count': stats_info['So_luong'],
                        'success_rate': f"{(stats_info['Co_gia'] * 100 / stats_info['So_luong']):.1f}%" if stats_info['So_luong'] > 0 else "0%"
                    }
                    for series_name, stats_info in sorted(series_stats.items(), key=lambda x: x[1]['So_luong'], reverse=True)[:10]  # Top 10 series
                ]
            })
        
        # Ghi log tổng kết
        print(f"=== Thống kê cào dữ liệu BAA.vn (Có hỗ trợ Series) ===")
        print(f"Tổng URL xử lý: {stats['urls_processed']}")
        print(f"Số danh mục: {stats['categories']}")
        print(f"Số series phát hiện: {len(series_stats)}")
        print(f"Số sản phẩm đơn lẻ: {stats['single_products']}")
        print(f"Tổng sản phẩm tìm thấy: {stats['products_found']}")
        print(f"Sản phẩm xử lý thành công: {stats['products_processed']}")
        print(f"Sản phẩm không có giá: {stats['products_skipped']}")
        print(f"Sản phẩm lỗi: {stats['failed_products']}")
        print(f"Ảnh tải thành công: {stats['images_downloaded']}")
        print(f"Ảnh tải thất bại: {stats['failed_images']}")
        print(f"Thời gian xử lý: {total_time:.2f}s ({total_time/60:.2f} phút)")
        print(f"Tốc độ trung bình: {products_per_second:.2f} sản phẩm/giây")
        
        # Log chi tiết về các series
        if series_stats:
            print(f"\n=== Chi tiết Series phát hiện ===")
            sorted_series = sorted(series_stats.items(), key=lambda x: x[1]['So_luong'], reverse=True)
            for series_name, stats_info in sorted_series:
                success_rate = (stats_info['Co_gia'] * 100 / stats_info['So_luong']) if stats_info['So_luong'] > 0 else 0
                print(f"- {series_name}: {stats_info['So_luong']} sản phẩm " +
                      f"({stats_info['Co_gia']} có giá, {stats_info['Khong_gia']} không có giá, " +
                      f"tỷ lệ có giá: {success_rate:.1f}%)")
        
        print(f"=======================================")
        
        return all_products, result_dir

    def _collect_product_urls_with_pagination(self, category_urls):
        """Thu thập URL sản phẩm từ danh mục, hỗ trợ phân trang với xử lý đa luồng"""
        all_product_urls = []
        
        # Thông báo bắt đầu và theo dõi tiến độ
        total_categories = len(category_urls)
        start_time = time.time()
        
        # Đếm số trang dự kiến để theo dõi tiến độ
        total_pages_estimate = 0
        
        # Kiểm tra số trang cho từng danh mục
        socketio.emit('progress_update', {
            'percent': 2,
            'message': f'Đang phát hiện số trang cho {len(category_urls)} danh mục',
            'detail': 'Phân tích cấu trúc phân trang...'
        })
        
        # Lưu thông tin số trang cho mỗi danh mục
        category_pages = {}
        
        # Phát hiện số trang cho từng danh mục trước khi xử lý
        for idx, category_url in enumerate(category_urls):
            try:
                max_pages = detect_pagination(category_url)
                category_pages[category_url] = max_pages
                total_pages_estimate += max_pages
                
                # Cập nhật tiến độ
                socketio.emit('progress_update', {
                    'percent': 2 + (idx * 3 // total_categories),
                    'message': f'Phát hiện phân trang ({idx+1}/{total_categories})',
                    'detail': f'Danh mục: {category_url} - {max_pages} trang'
                })
                
                print(f"Danh mục {idx+1}/{total_categories}: {category_url} - Phát hiện {max_pages} trang")
            except Exception as e:
                print(f"Lỗi khi phát hiện số trang cho {category_url}: {str(e)}")
                category_pages[category_url] = 1
        
        if total_pages_estimate == 0:
            total_pages_estimate = len(category_urls)  # Tối thiểu là 1 trang cho mỗi danh mục
        
        # Theo dõi tiến độ
        pages_processed = 0
        products_found = 0
        category_processed = 0
        
        # Hiển thị thông tin tổng quan
        print(f"Tổng số danh mục: {total_categories}, ước tính {total_pages_estimate} trang")
        socketio.emit('progress_update', {
            'percent': 5,
            'message': f'Chuẩn bị thu thập dữ liệu từ {total_pages_estimate} trang',
            'detail': f'Số danh mục: {total_categories}'
        })
        
        # Hàm worker để xử lý từng trang
        def process_page(url, is_category=True):
            """Xử lý một trang danh mục hoặc trang phân trang cụ thể"""
            try:
                product_urls = extract_product_urls(url)
                return url, product_urls, None
            except Exception as e:
                error_msg = str(e)
                print(f"Lỗi khi thu thập URL từ {url}: {error_msg}")
                return url, [], error_msg
        
        # Tạo danh sách các task phân trang
        pagination_tasks = []
        
        for category_url in category_urls:
            max_pages = category_pages[category_url]
            
            # Thêm trang đầu tiên cho mỗi danh mục
            pagination_tasks.append((category_url, True))
            
            # Thêm các trang phân trang khác nếu có
            for page in range(2, max_pages + 1):
                page_url = make_pagination_url(category_url, page)
                pagination_tasks.append((page_url, False))
        
        # Theo dõi tiến độ và xử lý đa luồng
        batch_size = 10  # Số lượng trang xử lý trong mỗi batch
        batches = [pagination_tasks[i:i+batch_size] for i in range(0, len(pagination_tasks), batch_size)]
        
        for batch_idx, batch in enumerate(batches):
            # Tính toán phần trăm tiến độ
            batch_start_percent = 5 + (batch_idx * 10 // len(batches))
            
            # Thông báo bắt đầu batch
            batch_start = batch_idx * batch_size
            batch_end = min(batch_start + batch_size, len(pagination_tasks))
            
            socketio.emit('progress_update', {
                'percent': batch_start_percent,
                'message': f'Đang thu thập batch {batch_idx+1}/{len(batches)}',
                'detail': f'Xử lý trang {batch_start+1}-{batch_end}/{len(pagination_tasks)}'
            })
            
            # Xử lý batch hiện tại với đa luồng
            batch_start_time = time.time()
            batch_results = []
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [executor.submit(process_page, url, is_category) for url, is_category in batch]
                
                # Thu thập kết quả khi hoàn thành
                for future in as_completed(futures):
                    try:
                        url, product_urls, error = future.result()
                        batch_results.append((url, product_urls, error))
                        
                        # Cập nhật thống kê
                        pages_processed += 1
                        if product_urls:
                            products_found += len(product_urls)
                            
                        # Cập nhật tiến độ chi tiết
                        progress_percent = batch_start_percent + (pages_processed * 10 // len(pagination_tasks))
                        
                        # Tính tốc độ xử lý
                        elapsed = time.time() - batch_start_time
                        pages_per_second = pages_processed / elapsed if elapsed > 0 else 0
                        est_remaining = (len(pagination_tasks) - pages_processed) / pages_per_second if pages_per_second > 0 else 0
                        
                        # Format thông báo thời gian còn lại
                        remaining_info = ""
                        if est_remaining > 0:
                            if est_remaining < 60:
                                remaining_info = f", còn lại: {est_remaining:.1f}s"
                            else:
                                remaining_info = f", còn lại: {est_remaining/60:.1f}m"
                        
                        # Cập nhật thông báo tiến độ
                        socketio.emit('progress_update', {
                            'percent': progress_percent,
                            'message': f'Đã xử lý {pages_processed}/{len(pagination_tasks)} trang',
                            'detail': f'Đã tìm thấy {products_found} URL sản phẩm{remaining_info}'
                        })
                    except Exception as e:
                        print(f"Lỗi khi xử lý future: {str(e)}")
            
            # Kiểm tra kết quả batch và thêm vào danh sách sản phẩm
            batch_products = 0
            for url, product_urls, error in batch_results:
                if product_urls:
                    all_product_urls.extend(product_urls)
                    batch_products += len(product_urls)
            
            # Log hiệu suất của batch
            batch_elapsed = time.time() - batch_start_time
            batch_pages_per_second = len(batch) / batch_elapsed if batch_elapsed > 0 else 0
            print(f"Batch {batch_idx+1}/{len(batches)} hoàn thành trong {batch_elapsed:.2f}s, " +
                  f"tốc độ: {batch_pages_per_second:.2f} trang/s, tìm thấy {batch_products} sản phẩm")
        
        # Loại bỏ URL trùng lặp
        unique_product_urls = list(dict.fromkeys(all_product_urls))
        
        # Log thống kê
        total_time = time.time() - start_time
        print(f"Đã thu thập xong {len(unique_product_urls)} URL sản phẩm (từ {len(all_product_urls)} URLs gốc)")
        print(f"Thời gian xử lý: {total_time:.2f}s, tốc độ: {pages_processed/total_time:.2f} trang/s")
        
        # Thông báo hoàn thành
        socketio.emit('progress_update', {
            'percent': 15,
            'message': f'Đã thu thập xong {len(unique_product_urls)} URL sản phẩm',
            'detail': f'Đã xử lý {pages_processed} trang từ {len(category_urls)} danh mục'
        })
        
        return unique_product_urls

    def _download_product_images(self, code_url_map, series_products_map, anh_dir, category_name, category_idx=0, total_categories=1, percent_base=50):
        """Tải ảnh sản phẩm với xử lý lỗi và retry thông minh, tạo một báo cáo duy nhất, nhóm theo series"""
        img_map = {}
        
        if not code_url_map:
            return img_map, []
        
        # Tạo thư mục hình ảnh cho danh mục chính nếu chưa tồn tại
        os.makedirs(anh_dir, exist_ok=True)
        
        # Tạo thư mục ảnh cho từng series
        series_img_dirs = {}
        for series_name in series_products_map.keys():
            series_folder_name = sanitize_folder_name(series_name)
            series_dir = os.path.join(os.path.dirname(anh_dir), series_folder_name)
            series_img_dir = os.path.join(series_dir, "Anh")
            os.makedirs(series_img_dir, exist_ok=True)
            series_img_dirs[series_name] = series_img_dir
        
        # Log thông tin bắt đầu
        print(f"[{category_name}] Bắt đầu tải {len(code_url_map)} ảnh sản phẩm vào {len(series_img_dirs)} series")
        
        # Thời gian bắt đầu
        start_time = time.time()
        
        # Chuẩn bị dữ liệu cho báo cáo tải ảnh (được hợp nhất với báo cáo chính)
        image_report_data = []
        
        def download_img_worker(item):
            """Worker function để tải ảnh với retry thông minh và lưu theo series"""
            code, url_info = item
            
            # Xử lý url_info (có thể là string hoặc dict)
            if isinstance(url_info, dict):
                url = url_info.get('url', '')
                series_name = url_info.get('series', 'Khac')
            else:
                url = url_info
                series_name = 'Khac'
            
            if not url:
                return code, '', 'URL trống', None
            
            # Xác định thư mục ảnh theo series
            target_img_dir = series_img_dirs.get(series_name, anh_dir)
            
            # Ghi nhận trạng thái không cần file log riêng
            success_msg = None
            error_msg = None
            download_time = 0
            
            try:
                # Kiểm tra xem ảnh đã tồn tại chưa (tránh tải lại)
                existing_image = os.path.join(target_img_dir, f"{code}.webp")
                if os.path.exists(existing_image) and os.path.getsize(existing_image) > 0:
                    return code, existing_image, 'Đã tồn tại', {
                        'Mã sản phẩm': code,
                        'URL': url,
                        'Series': series_name,
                        'Trạng thái': 'Đã tồn tại',
                        'Đường dẫn ảnh': existing_image,
                        'Kích thước (bytes)': os.path.getsize(existing_image),
                        'Thời gian tải (s)': 0
                    }
                
                # Retry với backoff
                retry_delays = [0.5, 1, 2, 3, 5]  # Độ trễ tăng dần giữa các lần thử
                download_start = time.time()
                
                for retry in range(self.max_retries):
                    try:
                        # Thử tải ảnh vào thư mục series
                        result = download_baa_product_images_fixed([url], target_img_dir, create_report=False)
                        
                        if result and result.get('report_data'):
                            for r in result['report_data']:
                                if r.get('Mã sản phẩm') == code:
                                    image_path = r.get('Đường dẫn ảnh', '')
                                    
                                    # Kiểm tra xem file ảnh có tồn tại và có kích thước > 0
                                    if image_path and os.path.exists(image_path) and os.path.getsize(image_path) > 0:
                                        # Tính thời gian tải
                                        download_time = time.time() - download_start
                                        
                                        # Ghi nhận thông tin thành công
                                        success_msg = f"Tải thành công vào series {series_name}, kích thước: {os.path.getsize(image_path)} bytes"
                                        
                                        # Trả về thông tin đầy đủ
                                        return code, image_path, success_msg, {
                                            'Mã sản phẩm': code,
                                            'URL': url,
                                            'Series': series_name,
                                            'Trạng thái': 'Thành công',
                                            'Đường dẫn ảnh': image_path,
                                            'Kích thước (bytes)': os.path.getsize(image_path),
                                            'Thời gian tải (s)': round(download_time, 2),
                                            'Số lần thử': retry + 1
                                        }
                                    else:
                                        error_msg = "Có đường dẫn ảnh nhưng file không tồn tại hoặc rỗng"
                        else:
                            error_msg = "Không nhận được phản hồi hợp lệ"
                        
                        # Nếu không thành công, chờ và thử lại
                        if retry < self.max_retries - 1:
                            # Chờ theo thời gian retry với backoff
                            time.sleep(retry_delays[min(retry, len(retry_delays)-1)])
                    except Exception as e:
                        error_msg = str(e)
                        
                        if retry < self.max_retries - 1:
                            time.sleep(retry_delays[min(retry, len(retry_delays)-1)])
                
                # Tính thời gian tải tổng cộng
                download_time = time.time() - download_start
                
                return code, '', f"Lỗi sau {self.max_retries} lần thử: {error_msg}", {
                    'Mã sản phẩm': code,
                    'URL': url,
                    'Series': series_name,
                    'Trạng thái': 'Thất bại',
                    'Đường dẫn ảnh': '',
                    'Kích thước (bytes)': 0,
                    'Thời gian tải (s)': round(download_time, 2),
                    'Lỗi': error_msg,
                    'Số lần thử': self.max_retries
                }
                
            except Exception as e:
                error_msg = str(e)
                download_time = time.time() - download_start
                
                return code, '', f"Lỗi ngoài: {error_msg}", {
                    'Mã sản phẩm': code,
                    'URL': url,
                    'Series': series_name,
                    'Trạng thái': 'Lỗi',
                    'Đường dẫn ảnh': '',
                    'Kích thước (bytes)': 0,
                    'Thời gian tải (s)': round(download_time, 2),
                    'Lỗi': error_msg,
                    'Số lần thử': 1
                }
        
        # Tính toán phần trăm cho phần tải ảnh
        percent_range = 30 // max(1, total_categories)
        percent_start = percent_base + (category_idx * 90 // max(1, total_categories))
        
        # Theo dõi tiến độ và hiệu suất
        total_images = len(code_url_map)
        success_count = 0
        fail_count = 0
        start_batch_time = time.time()
        batch_size = 20  # Kích thước batch cho việc báo cáo hiệu suất
        
        # Dữ liệu cho báo cáo tải ảnh
        image_report_data = []
        
        # Thống kê theo series
        series_stats = {}
        for series_name in series_products_map.keys():
            series_stats[series_name] = {'success': 0, 'fail': 0}
        
        # Xử lý đa luồng tải ảnh với theo dõi tiến độ
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Tạo các future cho việc tải ảnh
            img_futures = {executor.submit(download_img_worker, item): item[0] 
                          for item in code_url_map.items()}
            
            # Xử lý từng future khi hoàn thành
            for idx, future in enumerate(as_completed(img_futures)):
                try:
                    code, img_path, status, report_entry = future.result()
                    
                    if report_entry:
                        image_report_data.append(report_entry)
                        series_name = report_entry.get('Series', 'Khac')
                    
                    if code:
                        if img_path:
                            img_map[code] = img_path
                            success_count += 1
                            if series_name in series_stats:
                                series_stats[series_name]['success'] += 1
                        else:
                            fail_count += 1
                            if series_name in series_stats:
                                series_stats[series_name]['fail'] += 1
                            print(f"[{category_name}] Không thể tải ảnh cho {code}: {status}")
                except Exception as e:
                    fail_count += 1
                    print(f"[{category_name}] Lỗi xử lý future: {str(e)}")
                
                # Cập nhật tiến độ
                items_done = idx + 1
                
                # Tính tốc độ và ước tính thời gian còn lại
                elapsed = time.time() - start_time
                current_speed = items_done / elapsed if elapsed > 0 else 0
                est_remaining = (total_images - items_done) / current_speed if current_speed > 0 else 0
                
                # Báo cáo hiệu suất theo batch
                if items_done % batch_size == 0 or items_done == total_images:
                    batch_elapsed = time.time() - start_batch_time
                    batch_speed = batch_size / batch_elapsed if batch_elapsed > 0 else 0
                    print(f"[{category_name}] Tiến độ tải ảnh: {items_done}/{total_images}, " +
                          f"batch speed: {batch_speed:.2f} img/s, total speed: {current_speed:.2f} img/s")
                    start_batch_time = time.time()
                
                # Format thông báo tiến độ
                remaining_info = ""
                if est_remaining > 0:
                    if est_remaining < 60:
                        remaining_info = f", còn lại: {est_remaining:.1f}s"
                    else:
                        remaining_info = f", còn lại: {est_remaining/60:.1f}m"
                
                # Tính phần trăm tiến độ
                percent = percent_start + (items_done * percent_range // total_images)
                
                # Cập nhật tiến độ lên giao diện
                socketio.emit('progress_update', {
                    'percent': percent, 
                    'message': f'[{category_name}] Đã tải ảnh {items_done}/{total_images} ' +
                              f'(thành công: {success_count}, thất bại: {fail_count})',
                    'detail': f'Tốc độ: {current_speed:.1f} ảnh/s{remaining_info}, {len(series_img_dirs)} series'
                })
        
        # Tạo báo cáo Excel cho việc tải ảnh (lưu vào danh sách dữ liệu, sẽ được hợp nhất vào báo cáo chính)
        if image_report_data:
            # Sắp xếp dữ liệu báo cáo theo series và mã sản phẩm để dễ tra cứu
            try:
                sorted_data = sorted(image_report_data, key=lambda x: (x.get('Series', ''), x.get('Mã sản phẩm', '')))
                print(f"[{category_name}] Đã thu thập {len(sorted_data)} bản ghi báo cáo tải ảnh")
                
                # Log thống kê theo series
                print(f"[{category_name}] Thống kê tải ảnh theo series:")
                for series_name, stats in series_stats.items():
                    total_series = stats['success'] + stats['fail']
                    if total_series > 0:
                        success_rate = (stats['success'] * 100) / total_series
                        print(f"  - {series_name}: {stats['success']}/{total_series} thành công ({success_rate:.1f}%)")
                
                return img_map, sorted_data
            except Exception as e:
                print(f"[{category_name}] Lỗi khi sắp xếp báo cáo tải ảnh: {str(e)}")
        
        # Báo cáo kết quả cuối cùng
        total_time = time.time() - start_time
        avg_time_per_image = total_time / total_images if total_images > 0 else 0
        images_per_second = total_images / total_time if total_time > 0 else 0
        
        print(f"[{category_name}] Hoàn tất tải ảnh: {success_count}/{total_images} thành công, " +
              f"{fail_count} thất bại, tốc độ: {images_per_second:.2f} ảnh/s")
        
        return img_map, image_report_data

    def _normalize_spec(self, spec_html):
        """
        Chuyển đổi mã sản phẩm trong bảng thông số kỹ thuật thành chữ hoa.
        """
        if not spec_html:
            return spec_html
        
        # Tìm và chuyển đổi mã sản phẩm thành chữ hoa
        soup = BeautifulSoup(spec_html, 'html.parser')
        for td in soup.find_all('td'):
            if td.text and any(keyword in td.text.lower() for keyword in ['mã', 'model', 'part no']):
                td.string = td.text.upper()
            
        return str(soup) 