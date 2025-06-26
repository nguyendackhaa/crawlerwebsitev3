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
    Trích xuất series sản phẩm từ URL - CHỈ ÁP DỤNG CHO MỘT DANH MỤC CỤ THỂ
    Chỉ thực hiện phân loại chi tiết cho: https://baa.vn/vn/Category/cong-tac-den-bao-coi-bao-qlight_F_782/
    Các URL khác sẽ trả về None (không phân loại series)
    """
    if not url:
        return None
    
    # CHỈ áp dụng logic phân loại chi tiết cho link danh mục QLIGHT cụ thể
    target_url = "https://baa.vn/vn/Category/cong-tac-den-bao-coi-bao-qlight_F_782/"
    if url.strip().rstrip('/') != target_url.strip().rstrip('/'):
        return None
    
    # Logic phân loại chi tiết CHỈ cho URL QLIGHT
    print(f"🔍 Đang phân loại series cho URL QLIGHT: {url}")
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Danh sách các brands được hỗ trợ
        supported_brands = [
            'qlight', 'autonics', 'sick', 'omron', 'keyence', 'pepperl', 'fuchs',
            'balluff', 'turck', 'banner', 'contrinex', 'schneider', 'siemens', 
            'abb', 'mitsubishi', 'panasonic', 'hanyoung', 'fotek', 'idec',
            'phoenix', 'weidmuller', 'pilz', 'ifm', 'leuze', 'wenglor',
            'baumer', 'datalogic', 'cognex', 'keyence', 'festo'
        ]
        
        # Method 1: Kiểm tra HTML structure cho sản phẩm đơn lẻ
        product_symbol = soup.find('div', class_='product__symbol-header')
        if product_symbol:
            text = product_symbol.get_text(strip=True).upper()
            for brand in supported_brands:
                if brand.upper() in text:
                    # Tìm series pattern sau brand name
                    import re
                    pattern = rf'{brand.upper()}[\s\-]*([A-Z0-9]+(?:[A-Z0-9\-]*[A-Z0-9])?)'
                    match = re.search(pattern, text)
                    if match:
                        series = match.group(1)
                        if len(series) >= 2:
                            print(f"✅ Method 1 - Tìm thấy series từ product symbol: {series}_series")
                            return f"{series}_series"
            
        # Method 2: Trích xuất từ tiêu đề h1
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True).upper()
            # Pattern 1: "BRAND MODEL series" format
            for brand in supported_brands:
                if brand.upper() in title:
                    import re
                    # Tìm pattern sau brand name
                    pattern = rf'{brand.upper()}[\s\-]*([A-Z0-9]+(?:[A-Z0-9\-]*[A-Z0-9])?)'
                    match = re.search(pattern, title)
                    if match:
                        series = match.group(1)
                        if len(series) >= 2 and not series.isdigit():
                            print(f"✅ Method 2a - Tìm thấy series từ h1 title: {series}_series")
                            return f"{series}_series"
                    
                    # Pattern 2: Tìm series với "SERIES" keyword
                    series_pattern = r'(\w+)[\s\-]*SERIES'
                    series_match = re.search(series_pattern, title)
                    if series_match:
                        series = series_match.group(1)
                        if len(series) >= 2:
                            print(f"✅ Method 2b - Tìm thấy series từ 'SERIES' keyword: {series}_series")
                            return f"{series}_series"
            
        # Method 3: Trích xuất từ breadcrumb
        breadcrumb = soup.find('nav', class_='breadcrumb') or soup.find('ol', class_='breadcrumb')
        if breadcrumb:
            breadcrumb_text = breadcrumb.get_text(strip=True).upper()
            for brand in supported_brands:
                if brand.upper() in breadcrumb_text:
                    import re
                    pattern = rf'{brand.upper()}[\s\-]*([A-Z0-9]+(?:[A-Z0-9\-]*[A-Z0-9])?)'
                    match = re.search(pattern, breadcrumb_text)
                    if match:
                        series = match.group(1)
                        if len(series) >= 2:
                            print(f"✅ Method 3 - Tìm thấy series từ breadcrumb: {series}_series")
                            return f"{series}_series"
        
        # Method 4: Trích xuất từ URL patterns
        import re
        url_upper = url.upper()
        
        # Pattern 1: /brand-series_number/ hoặc /brand-series/
        for brand in supported_brands:
            pattern1 = rf'/{brand.upper()}-([A-Z0-9]+(?:[A-Z0-9\-]*[A-Z0-9])?)(?:_|\b)'
            match1 = re.search(pattern1, url_upper)
            if match1:
                series = match1.group(1)
                if len(series) >= 2:
                    print(f"✅ Method 4a - Tìm thấy series từ URL pattern 1: {series}_series")
                    return f"{series}_series"
            
            # Pattern 2: brand_series hoặc brand-series
            pattern2 = rf'{brand.upper()}[_\-]([A-Z0-9]+(?:[A-Z0-9\-]*[A-Z0-9])?)'
            match2 = re.search(pattern2, url_upper)
            if match2:
                series = match2.group(1)
                if len(series) >= 2 and not series.isdigit():
                    print(f"✅ Method 4b - Tìm thấy series từ URL pattern 2: {series}_series")
                    return f"{series}_series"
            
            # Pattern 3: series-brand format
            pattern3 = rf'([A-Z0-9]+(?:[A-Z0-9\-]*[A-Z0-9])?)-{brand.upper()}'
            match3 = re.search(pattern3, url_upper)
            if match3:
                series = match3.group(1)
                if len(series) >= 2:
                    print(f"✅ Method 4c - Tìm thấy series từ URL pattern 3: {series}_series")
                    return f"{series}_series"
        
        # Method 5: Kiểm tra meta tags
        meta_tags = soup.find_all('meta')
        for meta in meta_tags:
            content = meta.get('content', '').upper()
            if content:
                for brand in supported_brands:
                    if brand.upper() in content:
                        import re
                        pattern = rf'{brand.upper()}[\s\-]*([A-Z0-9]+(?:[A-Z0-9\-]*[A-Z0-9])?)'
                        match = re.search(pattern, content)
                        if match:
                            series = match.group(1)
                            if len(series) >= 2:
                                print(f"✅ Method 5 - Tìm thấy series từ meta tags: {series}_series")
                                return f"{series}_series"
        
        # Method 6: Fallback - tìm trong toàn bộ page content
        page_text = soup.get_text().upper()
        series_candidates = {}
        
        for brand in supported_brands:
            if brand.upper() in page_text:
                import re
                pattern = rf'{brand.upper()}[\s\-]*([A-Z0-9]+(?:[A-Z0-9\-]*[A-Z0-9])?)'
                matches = re.findall(pattern, page_text)
                for match in matches:
                    if len(match) >= 2 and not match.isdigit():
                        series_candidates[match] = series_candidates.get(match, 0) + 1
        
        if series_candidates:
            # Chọn series xuất hiện nhiều nhất
            most_common_series = max(series_candidates.items(), key=lambda x: x[1])
            series = most_common_series[0]
            print(f"✅ Method 6 - Tìm thấy series từ page content (frequency: {most_common_series[1]}): {series}_series")
            return f"{series}_series"
        
        # Trường hợp đặc biệt cho URL QLIGHT
        if 'QLIGHT' in url_upper or 'qlight' in url.lower():
            print("✅ Fallback - Phát hiện QLIGHT trong URL: QLIGHT_series")
            return "QLIGHT_series"
            
    except Exception as e:
        print(f"❌ Lỗi khi trích xuất series: {e}")
    
    print("⚠️ Không tìm thấy series cụ thể cho URL đặc biệt này")
    return None

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
                required_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'Tổng quan', 'URL']
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
                        url = futures[future]  # Lấy URL từ mapping
                        try:
                            info = future.result()
                            if info:
                                # Trích xuất thông tin series từ URL sản phẩm
                                try:
                                    product_series = extract_product_series(url)
                                    # Chỉ thêm field Series nếu có giá trị (không phải None)
                                    if product_series:
                                        info['Series'] = product_series
                                        print(f"[{cat_name}] Sản phẩm {info.get('Mã sản phẩm', 'N/A')} thuộc series: {product_series}")
                                    else:
                                        # Không thêm field Series cho các URL không hỗ trợ phân loại
                                        print(f"[{cat_name}] Sản phẩm {info.get('Mã sản phẩm', 'N/A')} không được phân loại theo series")
                                except Exception as e:
                                    print(f"[{cat_name}] Lỗi khi trích xuất series cho {url}: {str(e)}")
                                    # Không thêm field Series khi có lỗi
                                
                                # Kiểm tra xem sản phẩm có giá không để thống kê
                                product_price = info.get('Giá', '').strip()
                                
                                # Chuẩn hóa thông số kỹ thuật
                                info['Tổng quan'] = self._normalize_spec(info.get('Tổng quan', ''))
                                products.append(info)
                                
                                # Nhóm sản phẩm theo series (chỉ khi có Series)
                                series_name = info.get('Series')
                                if series_name:  # Chỉ nhóm khi có series
                                    if series_name not in series_products_map:
                                        series_products_map[series_name] = []
                                    series_products_map[series_name].append(info)
                                
                                # Lưu mã sản phẩm và URL để tải ảnh (nhóm theo series nếu có)
                                if info.get('Mã sản phẩm') and info.get('URL'):
                                    code_url_map[info['Mã sản phẩm']] = {
                                        'url': info['URL'],
                                        'series': series_name if series_name else None
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
                    
                    # Nhóm sản phẩm theo series (chỉ khi có Series)
                    series_name = info.get('Series')
                    if series_name:  # Chỉ nhóm khi có series
                        if series_name not in series_products_map:
                            series_products_map[series_name] = []
                        series_products_map[series_name].append(info)
                
                # Tạo thư mục cho từng series và lưu dữ liệu (chỉ khi có series)
                if series_products_map:  # Chỉ tạo khi có series
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
                
                if series_products_map:
                    print(f"[{cat_name}] Đã phân loại {len(products)} sản phẩm vào {len(series_products_map)} series")
                else:
                    print(f"[{cat_name}] Không có series nào được phát hiện, chỉ lưu dữ liệu tổng hợp")
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
            series_name = product.get('Series')
            # Chỉ thống kê khi có series
            if series_name:
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
        if series_stats:  # Chỉ tạo khi có series
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
            
            # Sheet thống kê series (chỉ tạo khi có series)
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
                # Sắp xếp theo series và mã sản phẩm (xử lý trường hợp không có series)
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
            
            # Thông báo kết quả tùy thuộc vào việc có series hay không
            if series_stats:
                completion_message = f'🎉 Hoàn thành! Đã cào được {len(all_products)} sản phẩm từ {len(series_stats)} series'
                detail_message = f'Thời gian: {total_time/60:.2f} phút • Tốc độ: {products_per_second:.2f} sp/s • File: {zip_filename}'
            else:
                completion_message = f'🎉 Hoàn thành! Đã cào được {len(all_products)} sản phẩm'
                detail_message = f'Thời gian: {total_time/60:.2f} phút • Tốc độ: {products_per_second:.2f} sp/s • File: {zip_filename}'
            
            # Chuẩn bị series_stats để hiển thị (nếu có)
            series_display = []
            if series_stats:
                series_display = [
                    {
                        'series_name': series_name,
                        'product_count': stats_info['So_luong'],
                        'success_rate': f"{(stats_info['Co_gia'] * 100 / stats_info['So_luong']):.1f}%" if stats_info['So_luong'] > 0 else "0%"
                    }
                    for series_name, stats_info in sorted(series_stats.items(), key=lambda x: x[1]['So_luong'], reverse=True)[:10]  # Top 10 series
                ]
            
            socketio.emit('progress_update', {
                'percent': 100, 
                'message': completion_message,
                'detail': detail_message,
                'completed': True,
                'download_ready': True,
                'download_info': download_info,
                'series_stats': series_display
            })
        
        # Ghi log tổng kết
        print(f"=== Thống kê cào dữ liệu BAA.vn ===")
        print(f"Tổng URL xử lý: {stats['urls_processed']}")
        print(f"Số danh mục: {stats['categories']}")
        if series_stats:
            print(f"Số series phát hiện: {len(series_stats)}")
        else:
            print(f"Không có series nào được phát hiện")
        print(f"Số sản phẩm đơn lẻ: {stats['single_products']}")
        print(f"Tổng sản phẩm tìm thấy: {stats['products_found']}")
        print(f"Sản phẩm xử lý thành công: {stats['products_processed']}")
        print(f"Sản phẩm không có giá: {stats['products_skipped']}")
        print(f"Sản phẩm lỗi: {stats['failed_products']}")
        print(f"Ảnh tải thành công: {stats['images_downloaded']}")
        print(f"Ảnh tải thất bại: {stats['failed_images']}")
        print(f"Thời gian xử lý: {total_time:.2f}s ({total_time/60:.2f} phút)")
        print(f"Tốc độ trung bình: {products_per_second:.2f} sản phẩm/giây")
        
        # Log chi tiết về các series (chỉ khi có)
        if series_stats:
            print(f"\n=== Chi tiết Series phát hiện ===")
            sorted_series = sorted(series_stats.items(), key=lambda x: x[1]['So_luong'], reverse=True)
            for series_name, stats_info in sorted_series:
                success_rate = (stats_info['Co_gia'] * 100 / stats_info['So_luong']) if stats_info['So_luong'] > 0 else 0
                print(f"- {series_name}: {stats_info['So_luong']} sản phẩm " +
                      f"({stats_info['Co_gia']} có giá, {stats_info['Khong_gia']} không có giá, " +
                      f"tỷ lệ có giá: {success_rate:.1f}%)")
        else:
            print(f"\n=== Không có series nào được phân loại ===")
            print("Chỉ phân loại series cho URL đặc biệt: https://baa.vn/vn/Category/cong-tac-den-bao-coi-bao-qlight_F_782/")
        
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
            batch_speed = batch_size / batch_elapsed if batch_elapsed > 0 else 0
            print(f"Batch {batch_idx+1}/{len(batches)} hoàn thành trong {batch_elapsed:.2f}s, " +
                  f"tốc độ: {batch_speed:.2f} trang/s, tìm thấy {batch_products} sản phẩm")
        
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
        """Tải ảnh sản phẩm với xử lý lỗi và retry thông minh, tạo một báo cáo duy nhất, nhóm theo series nếu có"""
        img_map = {}
        
        if not code_url_map:
            return img_map, []
        
        # Tạo thư mục hình ảnh cho danh mục chính nếu chưa tồn tại
        os.makedirs(anh_dir, exist_ok=True)
        
        # Tạo thư mục ảnh cho từng series (chỉ khi có series)
        series_img_dirs = {}
        if series_products_map:  # Chỉ tạo khi có series
            for series_name in series_products_map.keys():
                series_folder_name = sanitize_folder_name(series_name)
                series_dir = os.path.join(os.path.dirname(anh_dir), series_folder_name)
                series_img_dir = os.path.join(series_dir, "Anh")
                os.makedirs(series_img_dir, exist_ok=True)
                series_img_dirs[series_name] = series_img_dir
        
        # Log thông tin bắt đầu
        if series_img_dirs:
            print(f"[{category_name}] Bắt đầu tải {len(code_url_map)} ảnh sản phẩm vào {len(series_img_dirs)} series")
        else:
            print(f"[{category_name}] Bắt đầu tải {len(code_url_map)} ảnh sản phẩm vào thư mục chung (không có series)")
        
        # Thời gian bắt đầu
        start_time = time.time()
        
        # Chuẩn bị dữ liệu cho báo cáo tải ảnh (được hợp nhất với báo cáo chính)
        image_report_data = []
        
        def download_img_worker(item):
            """Worker function để tải ảnh với retry thông minh và lưu theo series nếu có"""
            code, url_info = item
            
            # Xử lý url_info (có thể là string hoặc dict)
            if isinstance(url_info, dict):
                url = url_info.get('url', '')
                series_name = url_info.get('series')
            else:
                url = url_info
                series_name = None
            
            if not url:
                return code, '', 'URL trống', None
            
            # Xác định thư mục ảnh theo series (nếu có) hoặc thư mục chung
            if series_name and series_name in series_img_dirs:
                target_img_dir = series_img_dirs[series_name]
            else:
                target_img_dir = anh_dir
                series_name = None  # Đảm bảo series_name là None nếu không có series
            
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
                        'Series': series_name if series_name else 'Không có',
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
                        # Thử tải ảnh vào thư mục series hoặc thư mục chung
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
                                        if series_name:
                                            success_msg = f"Tải thành công vào series {series_name}, kích thước: {os.path.getsize(image_path)} bytes"
                                        else:
                                            success_msg = f"Tải thành công vào thư mục chung, kích thước: {os.path.getsize(image_path)} bytes"
                                        
                                        # Trả về thông tin đầy đủ
                                        return code, image_path, success_msg, {
                                            'Mã sản phẩm': code,
                                            'URL': url,
                                            'Series': series_name if series_name else 'Không có',
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
                    'Series': series_name if series_name else 'Không có',
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
                    'Series': series_name if series_name else 'Không có',
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
                        series_name = report_entry.get('Series', 'Không có')
                    
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
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(spec_html, 'html.parser')
        for td in soup.find_all('td'):
            if td.text and any(keyword in td.text.lower() for keyword in ['mã', 'model', 'part no']):
                td.string = td.text.upper()
            
        return str(soup)