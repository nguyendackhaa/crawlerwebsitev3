import os
import re
import zipfile
import traceback
import pandas as pd
import requests
import time
import math
import base64
from datetime import datetime
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import concurrent.futures
import threading
import queue
from flask import current_app
from . import utils
from .utils import is_valid_url

# Import cho AI vision
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("Google Generative AI không có sẵn. Vui lòng cài đặt: pip install google-generativeai")

class CrawlFotek:
    def __init__(self, socketio=None, upload_folder=None, gemini_api_key=None):
        """Khởi tạo CrawlFotek với socketio instance và upload_folder"""
        self.socketio = socketio
        self.upload_folder = upload_folder
        self.max_workers = 4  # Giảm số luồng để tránh quá tải server
        self.request_delay = 0.5  # Tăng thời gian delay
        self.request_timeout = 30
        self.max_retries = 3
        self.retry_delay = 2
        self.base_url = "https://www.fotek.com.tw"
        
        # Khởi tạo Gemini client
        self.gemini_model = None
        if GEMINI_AVAILABLE and gemini_api_key:
            try:
                genai.configure(api_key=gemini_api_key)
                self.gemini_model = genai.GenerativeModel('gemini-1.5-flash')
                print("Đã khởi tạo Gemini API thành công")
            except Exception as e:
                print(f"Lỗi khi khởi tạo Gemini API: {str(e)}")
                self.gemini_model = None
        elif GEMINI_AVAILABLE and not gemini_api_key:
            print("Cảnh báo: Chưa cung cấp API key cho Gemini. Chức năng đọc ảnh thông số kỹ thuật sẽ không hoạt động.")
    
    def emit_progress(self, percent, message, log=None):
        """Gửi cập nhật tiến trình qua socketio"""
        if self.socketio:
            payload = {
                'percent': percent,
                'message': message
            }
            if log:
                payload['log'] = log
            self.socketio.emit('fotek_progress', payload)
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [FOTEK {percent}%] {message}")
        if log:
            print(f"[{timestamp}] [FOTEK LOG] {log}")
    
    def get_available_categories(self):
        """Lấy danh sách tất cả danh mục có sẵn để người dùng chọn"""
        try:
            self.emit_progress(0, "Đang lấy danh sách danh mục...")
            categories = self._get_product_categories()
            
            if categories:
                return {
                    'success': True,
                    'categories': [{'name': cat['name'], 'url': cat['url']} for cat in categories],
                    'total': len(categories)
                }
            else:
                return {
                    'success': False,
                    'message': 'Không thể lấy danh sách danh mục'
                }
        except Exception as e:
            return {
                'success': False,
                'message': f'Lỗi: {str(e)}'
            }

    def get_series_in_category(self, category_url):
        """Lấy danh sách series trong một danh mục cụ thể"""
        try:
            self.emit_progress(0, f"Đang lấy danh sách series từ {category_url}")
            
            # Lấy series trong danh mục
            series_list = self._get_category_series(category_url)
            
            if series_list:
                return {
                    'success': True,
                    'series': [{'name': series['name'], 'url': series['url']} for series in series_list],
                    'total': len(series_list),
                    'category_url': category_url
                }
            else:
                return {
                    'success': False,
                    'message': 'Không tìm thấy series nào trong danh mục này'
                }
        except Exception as e:
            return {
                'success': False,
                'message': f'Lỗi: {str(e)}'
            }

    def process_fotek_crawl(self, priority_mode=False, priority_categories=None, selected_series=None):
        """Xử lý crawl dữ liệu từ website Fotek với tùy chọn cào ưu tiên và đa luồng"""
        try:
            if priority_mode and selected_series:
                self.emit_progress(0, f"Bắt đầu cào dữ liệu ưu tiên từ {len(selected_series)} series được chọn")
            elif priority_mode:
                self.emit_progress(0, f"Bắt đầu cào dữ liệu ưu tiên từ {len(priority_categories) if priority_categories else 0} danh mục")
            else:
                self.emit_progress(0, "Bắt đầu cào dữ liệu đầy đủ từ Fotek.com.tw")
            
            # Tạo thư mục kết quả
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode_prefix = 'priority_' if priority_mode else 'full_'
            if selected_series:
                mode_prefix += 'series_'
            result_dir = os.path.join(self.upload_folder, f'fotek_{mode_prefix}products_{timestamp}')
            os.makedirs(result_dir, exist_ok=True)
            
            # Bước 1: Chuẩn bị dữ liệu
            all_products = []
            category_info = []
            
            if selected_series:
                # Cào theo series cụ thể được chọn
                self.emit_progress(5, f"Xử lý {len(selected_series)} series được chọn")
                
                # Sử dụng đa luồng để xử lý nhiều series song song
                all_products, category_info = self._process_selected_series_parallel(
                    selected_series, result_dir
                )
                
            elif priority_mode and priority_categories:
                # Sử dụng danh mục được chọn
                categories = priority_categories
                self.emit_progress(5, f"Sử dụng {len(categories)} danh mục ưu tiên đã chọn")
                
                # Xử lý từng danh mục với đa luồng
                all_products, category_info = self._process_categories_parallel(
                    categories, result_dir
                )
            else:
                # Lấy tất cả danh mục từ trang product-category
                self.emit_progress(5, "Đang lấy danh sách danh mục sản phẩm")
                categories = self._get_product_categories()
                
                if not categories:
                    return False, "Không tìm thấy danh mục sản phẩm nào", None
                
                self.emit_progress(10, f"Đã tìm thấy {len(categories)} danh mục sản phẩm")
                
                # Xử lý tất cả danh mục với đa luồng
                all_products, category_info = self._process_categories_parallel(
                    categories, result_dir
                )
            
            # Bước 3: Tạo báo cáo tổng hợp
            self.emit_progress(90, "Đang tạo báo cáo tổng hợp")
            mode_type = "series" if selected_series else "danh mục"
            self._create_fotek_reports(result_dir, all_products, category_info, priority_mode, mode_type)
            
            # Bước 4: Tạo file ZIP
            self.emit_progress(95, "Đang nén kết quả")
            zip_filename = f'fotek_{mode_prefix}products_{timestamp}.zip'
            zip_path = os.path.join(self.upload_folder, zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, self.upload_folder)
                        zipf.write(file_path, arcname)
            
            mode_text = f"ưu tiên ({mode_type})" if priority_mode else "đầy đủ"
            self.emit_progress(100, f"Hoàn thành cào dữ liệu {mode_text}! Đã cào {len(all_products)} sản phẩm")
            
            return True, f"Đã cào thành công {len(all_products)} sản phẩm từ {len(category_info)} {mode_type} Fotek ({mode_text})", zip_path
            
        except Exception as e:
            error_message = str(e)
            self.emit_progress(0, f"Lỗi: {error_message}")
            print(f"Lỗi khi cào dữ liệu Fotek: {error_message}")
            traceback.print_exc()
            return False, f"Lỗi: {error_message}", None

    def _process_selected_series_parallel(self, selected_series, result_dir):
        """Xử lý các series được chọn với đa luồng"""
        all_products = []
        series_info = []
        
        # Nhóm series theo danh mục để tổ chức thư mục
        categories_map = {}
        for series in selected_series:
            category_name = series.get('category_name', 'Unknown_Category')
            if category_name not in categories_map:
                categories_map[category_name] = []
            categories_map[category_name].append(series)
        
        total_series = len(selected_series)
        processed_count = 0
        
        # Xử lý từng danh mục với các series của nó
        for category_name, category_series in categories_map.items():
            try:
                # Tạo thư mục cho danh mục
                category_dir = os.path.join(result_dir, self._sanitize_folder_name(category_name))
                os.makedirs(category_dir, exist_ok=True)
                
                # Sử dụng ThreadPoolExecutor để xử lý song song các series
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    # Tạo tasks cho các series
                    future_to_series = {}
                    for series in category_series:
                        future = executor.submit(self._process_single_series, series, category_dir, category_name)
                        future_to_series[future] = series
                    
                    # Thu thập kết quả
                    for future in concurrent.futures.as_completed(future_to_series):
                        series = future_to_series[future]
                        processed_count += 1
                        
                        try:
                            progress = 10 + (processed_count * 75 / total_series)
                            self.emit_progress(int(progress), 
                                             f"Đã xử lý {processed_count}/{total_series} series: {series['name']}")
                            
                            products = future.result()
                            if products:
                                all_products.extend(products)
                                
                                # Lưu dữ liệu series riêng
                                series_df = pd.DataFrame(products)
                                series_excel_path = os.path.join(category_dir, f"{series['name']}_Du_lieu.xlsx")
                                series_df.to_excel(series_excel_path, index=False, engine='openpyxl')
                            
                        except Exception as exc:
                            print(f"Lỗi khi xử lý series {series['name']}: {exc}")
                
                # Thêm thông tin danh mục
                category_products_count = sum(1 for p in all_products if p.get('Category') == category_name)
                series_info.append({
                    'Tên danh mục': category_name,
                    'Số series': len(category_series),
                    'Số sản phẩm': category_products_count
                })
                
            except Exception as e:
                print(f"Lỗi khi xử lý danh mục {category_name}: {str(e)}")
        
        return all_products, series_info

    def _process_single_series(self, series, category_dir, category_name):
        """Xử lý một series đơn lẻ"""
        try:
            # Tạo thư mục cho series
            series_dir = os.path.join(category_dir, self._sanitize_folder_name(series['name']))
            os.makedirs(series_dir, exist_ok=True)
            os.makedirs(os.path.join(series_dir, 'Anh'), exist_ok=True)
            
            # Lấy sản phẩm trong series
            products = self._get_series_products(series['url'], series_dir, series['name'])
            
            # Thêm thông tin danh mục vào mỗi sản phẩm
            for product in products:
                product['Category'] = category_name
            
            return products
            
        except Exception as e:
            print(f"Lỗi khi xử lý series {series['name']}: {str(e)}")
            return []

    def _process_categories_parallel(self, categories, result_dir):
        """Xử lý các danh mục với đa luồng cải thiện"""
        all_products = []
        category_info = []
        
        # Sử dụng ThreadPoolExecutor để xử lý song song
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(categories))) as executor:
            # Tạo tasks cho các danh mục
            future_to_category = {}
            for i, category in enumerate(categories):
                future = executor.submit(self._process_single_category, category, result_dir, i, len(categories))
                future_to_category[future] = category
            
            # Thu thập kết quả
            processed_count = 0
            for future in concurrent.futures.as_completed(future_to_category):
                category = future_to_category[future]
                processed_count += 1
                
                try:
                    progress = 10 + (processed_count * 75 / len(categories))
                    self.emit_progress(int(progress), 
                                     f"Đã hoàn thành danh mục {processed_count}/{len(categories)}: {category['name']}")
                    
                    category_products, category_data = future.result()
                    all_products.extend(category_products)
                    category_info.append(category_data)
                    
                except Exception as exc:
                    print(f"Lỗi khi xử lý danh mục {category['name']}: {exc}")
        
        return all_products, category_info

    def _process_single_category(self, category, result_dir, index, total):
        """Xử lý một danh mục đơn lẻ"""
        try:
            # Tạo thư mục cho danh mục
            category_dir = os.path.join(result_dir, self._sanitize_folder_name(category['name']))
            os.makedirs(category_dir, exist_ok=True)
            
            # Lấy series trong danh mục
            series_list = self._get_category_series(category['url'])
            
            category_products = []
            
            # Xử lý từng series trong danh mục
            for j, series in enumerate(series_list):
                try:
                    # Tạo thư mục cho series
                    series_dir = os.path.join(category_dir, self._sanitize_folder_name(series['name']))
                    os.makedirs(series_dir, exist_ok=True)
                    os.makedirs(os.path.join(series_dir, 'Anh'), exist_ok=True)
                    
                    # Lấy sản phẩm trong series
                    products = self._get_series_products(series['url'], series_dir, series['name'])
                    category_products.extend(products)
                    
                    # Lưu dữ liệu series riêng
                    if products:
                        series_df = pd.DataFrame(products)
                        series_excel_path = os.path.join(series_dir, f"{series['name']}_Du_lieu.xlsx")
                        series_df.to_excel(series_excel_path, index=False, engine='openpyxl')
                    
                    # Delay nhỏ để tránh quá tải server
                    time.sleep(self.request_delay * 0.5)  # Giảm delay khi đa luồng
                    
                except Exception as e:
                    print(f"Lỗi khi xử lý series {series['name']}: {str(e)}")
            
            # Lưu dữ liệu danh mục tổng hợp
            if category_products:
                category_df = pd.DataFrame(category_products)
                category_excel_path = os.path.join(category_dir, f"{category['name']}_Du_lieu.xlsx")
                category_df.to_excel(category_excel_path, index=False, engine='openpyxl')
            
            # Trả về thông tin danh mục
            category_data = {
                'Tên danh mục': category['name'],
                'URL danh mục': category['url'],
                'Số series': len(series_list),
                'Số sản phẩm': len(category_products)
            }
            
            return category_products, category_data
            
        except Exception as e:
            print(f"Lỗi khi xử lý danh mục {category['name']}: {str(e)}")
            return [], {
                'Tên danh mục': category['name'],
                'URL danh mục': category['url'],
                'Số series': 0,
                'Số sản phẩm': 0
            }
    
    def _get_product_categories(self):
        """Lấy danh sách danh mục từ trang product-category"""
        try:
            category_url = f"{self.base_url}/en-gb/product-category"
            soup = self._get_soup(category_url)
            
            if not soup:
                return []
            
            categories = []
            
            # Tìm các link danh mục trong trang
            # Thường có cấu trúc: <a href="/en-gb/product-category/68">...</a>
            category_links = soup.select('a[href*="/product-category/"]')
            
            for link in category_links:
                href = link.get('href')
                if href and '/product-category/' in href and href.count('/') >= 4:
                    # Lấy tên danh mục từ text của link hoặc từ element con
                    name = link.get_text(strip=True)
                    
                    # Nếu không có text, thử lấy từ element con
                    if not name:
                        img = link.find('img')
                        if img and img.get('alt'):
                            name = img.get('alt')
                    
                    if name and href not in [cat['url'] for cat in categories]:
                        full_url = urljoin(self.base_url, href)
                        categories.append({
                            'name': name.strip(),
                            'url': full_url
                        })
            
            # Loại bỏ trùng lặp và filter
            unique_categories = []
            seen_urls = set()
            
            for cat in categories:
                if cat['url'] not in seen_urls and cat['name']:
                    unique_categories.append(cat)
                    seen_urls.add(cat['url'])
            
            return unique_categories
            
        except Exception as e:
            print(f"Lỗi khi lấy danh mục sản phẩm: {str(e)}")
            traceback.print_exc()
            return []
    
    def _get_category_series(self, category_url):
        """Lấy danh sách series trong một danh mục"""
        try:
            soup = self._get_soup(category_url)
            
            if not soup:
                return []
            
            series_list = []
            
            # Tìm các series trong danh mục Temperature Controller
            # Cách 1: Tìm trong container chứa series của danh mục
            # Thường có trong thẻ div chứa nội dung chính của trang
            
            # Tìm các thẻ h4 hoặc h5 có chứa tên series
            series_titles = soup.find_all(['h4', 'h5', 'h3'], string=re.compile(r'Series|SERIES'))
            
            # Nếu không tìm thấy, thử tìm các link có chứa text series
            if not series_titles:
                series_links = soup.find_all('a', href=True)
                for link in series_links:
                    link_text = link.get_text(strip=True)
                    href = link.get('href')
                    
                    # Kiểm tra nếu text chứa "Series" và href chứa "/product-category/"
                    if ('Series' in link_text or 'SERIES' in link_text) and '/product-category/' in href:
                        # Loại bỏ các link không phải series (như language switches)
                        if not any(x in href for x in ['/zh-tw/', '/zh-cn/', '/en-gb/product-category/68']):
                            # Đảm bảo không trùng lặp
                            if href not in [s['url'] for s in series_list]:
                                full_url = urljoin(self.base_url, href)
                                series_list.append({
                                    'name': link_text.strip(),
                                    'url': full_url
                                })
            
            # Cách 2: Tìm trong phần More links
            more_links = soup.find_all('a', string='More')
            for more_link in more_links:
                # Tìm parent container của "More" link
                parent = more_link.find_parent(['div', 'section', 'article'])
                if parent:
                    # Tìm tiêu đề series trong parent
                    title_elem = parent.find(['h4', 'h5', 'h3'])
                    if title_elem:
                        title_text = title_elem.get_text(strip=True)
                        href = more_link.get('href')
                        
                        if href and 'Series' in title_text:
                            full_url = urljoin(self.base_url, href)
                            
                            # Kiểm tra không trùng lặp
                            if full_url not in [s['url'] for s in series_list]:
                                series_list.append({
                                    'name': title_text,
                                    'url': full_url
                                })
            
            # Cách 3: Tìm theo pattern cụ thể cho Temperature Controller
            if '/product-category/86' in category_url:  # Temperature Controller category
                temperature_series = [
                    {'name': 'NT Series PID Temperature Controller', 'pattern': 'NT.*PID.*Temperature'},
                    {'name': 'NT-4M Series 4 Channels Temperature Control Module', 'pattern': 'NT-4M.*4 Channels'},
                    {'name': 'MT Series PID Temperature Controller', 'pattern': 'MT.*PID.*Temperature'},
                    {'name': 'TC Series Temperature Controller', 'pattern': 'TC.*Temperature Controller'},
                    {'name': 'TDX & TDZ Series Temperature Transmitter', 'pattern': 'TDX.*TDZ.*Temperature'},
                    {'name': 'HT-RS Series Transmitter & Meter', 'pattern': 'HT-RS.*Transmitter'},
                    {'name': 'TR Series Temperature Transmitter', 'pattern': 'TR.*Temperature Transmitter'},
                    {'name': 'H5-AN Series Temperature Controller', 'pattern': 'H5-AN.*Temperature'},
                    {'name': 'DPM Series METER(DEW POINT & HUMIDITY & TEMPERATURE)', 'pattern': 'DPM.*DEW POINT'},
                    {'name': 'TS Series Temperature Sensor', 'pattern': 'TS.*Temperature Sensor'},
                    {'name': 'CT-6P Series PCB Type Current Detector', 'pattern': 'CT-6P.*Current Detector'},
                    {'name': 'HR Series Heat Runner Controller', 'pattern': 'HR.*Heat Runner'}
                ]
                
                # Tìm các series này trong trang
                for temp_series in temperature_series:
                    pattern = temp_series['pattern']
                    series_elements = soup.find_all(text=re.compile(pattern, re.IGNORECASE))
                    
                    for element in series_elements:
                        # Tìm link gần nhất
                        parent = element.parent
                        while parent and parent.name != 'a':
                            parent = parent.parent
                        
                        if parent and parent.name == 'a':
                            href = parent.get('href')
                            if href and '/product-category/' in href:
                                full_url = urljoin(self.base_url, href)
                                
                                # Kiểm tra không trùng lặp
                                if full_url not in [s['url'] for s in series_list]:
                                    series_list.append({
                                        'name': temp_series['name'],
                                        'url': full_url
                                    })
                                    break  # Chỉ lấy một kết quả cho mỗi series
            
            # Loại bỏ trùng lặp và filter
            unique_series = []
            seen_urls = set()
            
            for series in series_list:
                if series['url'] not in seen_urls and series['name']:
                    # Loại bỏ các link không phải series
                    if not any(x in series['url'] for x in ['/zh-tw/', '/zh-cn/']):
                        unique_series.append(series)
                        seen_urls.add(series['url'])
            
            return unique_series
            
        except Exception as e:
            print(f"Lỗi khi lấy series từ danh mục {category_url}: {str(e)}")
            return []
    
    def _get_series_products(self, series_url, series_dir, series_name):
        """Lấy sản phẩm từ một series"""
        try:
            soup = self._get_soup(series_url)
            
            if not soup:
                return []
            
            products = []
            
            # Tìm bảng chứa sản phẩm và các link "View specs"
            # Cách 1: Tìm trực tiếp các link có text "View specs"
            view_specs_links = []
            all_links = soup.find_all('a', href=True)
            
            for link in all_links:
                # Kiểm tra nếu link chứa "/product/" và text là "View specs"
                href = link.get('href', '')
                link_text = link.get_text(strip=True)
                
                if '/product/' in href and 'View specs' in link_text:
                    view_specs_links.append(link)
            
            # Cách 2: Nếu không tìm thấy, thử tìm trong bảng
            if not view_specs_links:
                # Tìm trong các bảng (table)
                tables = soup.find_all('table')
                for table in tables:
                    table_links = table.find_all('a', href=True)
                    for link in table_links:
                        href = link.get('href', '')
                        if '/product/' in href:
                            view_specs_links.append(link)
            
            # Cách 3: Nếu vẫn không tìm thấy, tìm tất cả link có "/product/"
            if not view_specs_links:
                product_links = soup.select('a[href*="/product/"]')
                view_specs_links = product_links
            
            print(f"Tìm thấy {len(view_specs_links)} liên kết sản phẩm trong series {series_name}")
            
            # Xử lý từng liên kết sản phẩm
            for i, link in enumerate(view_specs_links):
                try:
                    href = link.get('href')
                    if href:
                        product_url = urljoin(self.base_url, href)
                        
                        self.emit_progress(0, f"Đang xử lý sản phẩm {i+1}/{len(view_specs_links)} trong series {series_name}")
                        
                        # Lấy thông tin sản phẩm
                        product_info = self._extract_product_info(product_url, i + 1, series_dir, series_name)
                        if product_info:
                            products.append(product_info)
                            print(f"Đã trích xuất sản phẩm: {product_info.get('Mã sản phẩm', 'N/A')} - {product_info.get('Tên sản phẩm', 'N/A')}")
                        
                        # Delay để tránh quá tải server
                        time.sleep(self.request_delay)
                        
                except Exception as e:
                    print(f"Lỗi khi xử lý sản phẩm {i+1}: {str(e)}")
            
            return products
            
        except Exception as e:
            print(f"Lỗi khi lấy sản phẩm từ series {series_url}: {str(e)}")
            return []
    
    def _extract_product_info(self, product_url, index, series_dir, series_name):
        """Trích xuất thông tin chi tiết từ một sản phẩm"""
        try:
            soup = self._get_soup(product_url)
            
            if not soup:
                return None
            
            # Khởi tạo thông tin sản phẩm
            product_info = {
                'STT': index,
                'URL': product_url,
                'Series': series_name,
                'Tên sản phẩm': '',
                'Mã sản phẩm': '',
                'Type': '',
                'Ảnh sản phẩm': '',
                'Tổng quan': ''
            }
            
            # Lấy tên sản phẩm từ <li><span>product name:</span><p class="title-card">...</p></li>
            name_elem = None
            product_name_spans = soup.find_all('span', string=re.compile(r'product\s+name:', re.I))
            for span in product_name_spans:
                # Tìm thẻ p.title-card sau span
                parent_li = span.find_parent('li')
                if parent_li:
                    title_card = parent_li.find('p', class_='title-card')
                    if title_card:
                        name_elem = title_card
                        break
            
            # Nếu không tìm thấy theo cách trên, thử selector đơn giản hơn
            if not name_elem:
                name_elem = soup.select_one('p.title-card')
            
            if name_elem:
                product_info['Tên sản phẩm'] = name_elem.get_text(strip=True)
            
            # Lấy mã sản phẩm từ <li><span>model:</span> K3T-40MNB </li>
            model_spans = soup.find_all('span', string=re.compile(r'model:', re.I))
            for span in model_spans:
                parent_li = span.find_parent('li')
                if parent_li:
                    # Lấy text của li và trích xuất phần sau "model:"
                    li_text = parent_li.get_text(strip=True)
                    model_match = re.search(r'model:\s*(.+)', li_text, re.I)
                    if model_match:
                        product_info['Mã sản phẩm'] = model_match.group(1).strip()
                        break
            
            # Lấy Type/Series từ <li><span>Type:</span> K3 SERIES - Thru Beam Type </li>
            type_spans = soup.find_all('span', string=re.compile(r'Type:', re.I))
            for span in type_spans:
                parent_li = span.find_parent('li')
                if parent_li:
                    # Lấy text của li và trích xuất phần sau "Type:"
                    li_text = parent_li.get_text(strip=True)
                    type_match = re.search(r'Type:\s*(.+)', li_text, re.I)
                    if type_match:
                        product_info['Type'] = type_match.group(1).strip()
                        break
            
            # Lấy ảnh sản phẩm có nền trắng từ <a href="..." class="popup d-block box-img img-contain r-16-9">
            img_url = None
            
            # PHƯƠNG PHÁP 1: Tìm ảnh sản phẩm chính có nền trắng (ưu tiên cao nhất)
            # Tìm element có class chính xác như yêu cầu
            img_link = soup.select_one('a.popup.d-block.box-img.img-contain.r-16-9')
            if img_link:
                potential_url = img_link.get('href')
                if potential_url:
                    # Kiểm tra URL có phải ảnh sản phẩm có nền trắng không
                    if self._is_white_background_product_image(potential_url):
                        img_url = potential_url
                        print(f"✅ Tìm thấy ảnh sản phẩm nền trắng (Method 1): {img_url}")
                    else:
                        print(f"⚠️ URL không phải ảnh nền trắng (Method 1): {potential_url}")
            
            # PHƯƠNG PHÁP 2: Fallback - Tìm trong style background-image
            if not img_url and img_link:
                style = img_link.get('style', '')
                if 'background-image:url(' in style:
                    # Trích xuất URL từ background-image:url('...')
                    match = re.search(r"background-image:url\(['\"]?([^'\"]+)['\"]?\)", style)
                    if match:
                        potential_url = match.group(1)
                        if self._is_white_background_product_image(potential_url):
                            img_url = potential_url
                            print(f"✅ Tìm thấy ảnh sản phẩm nền trắng (Method 2 - style): {img_url}")
            
            # PHƯƠNG PHÁP 3: Tìm các link popup khác có ảnh nền trắng
            if not img_url:
                popup_links = soup.select('a.popup[href]')
                for link in popup_links:
                    potential_url = link.get('href')
                    if potential_url and self._is_white_background_product_image(potential_url):
                        img_url = potential_url
                        print(f"✅ Tìm thấy ảnh sản phẩm nền trắng (Method 3 - popup): {img_url}")
                        break
            
            # PHƯƠNG PHÁP 4: Fallback cuối - Tìm trong img src có pattern sản phẩm
            if not img_url:
                product_imgs = soup.select('img[src*="/catalog/product"], img[src*="/Item/"], img[src*="product"]')
                for img in product_imgs:
                    potential_url = img.get('src')
                    if potential_url and self._is_white_background_product_image(potential_url):
                        img_url = potential_url
                        print(f"✅ Tìm thấy ảnh sản phẩm nền trắng (Method 4 - img): {img_url}")
                        break
            
            # Xử lý URL ảnh tìm được
            if img_url:
                # Đảm bảo URL đầy đủ
                if not img_url.startswith('http'):
                    img_url = urljoin(self.base_url, img_url)
                
                product_info['Ảnh sản phẩm'] = img_url
                print(f"🎯 Sử dụng ảnh sản phẩm nền trắng: {img_url}")
                
                # Tải ảnh sản phẩm
                self._download_product_image(img_url, series_dir, product_info['Mã sản phẩm'])
            else:
                print(f"❌ Không tìm thấy ảnh sản phẩm nền trắng cho: {product_url}")
                product_info['Ảnh sản phẩm'] = "https://haiphongtech.vn/wp-content/uploads/2025/05/no-image.webp"
            
            # Tải ảnh Wiring Diagram và Dimensions nếu có mã sản phẩm
            if product_info['Mã sản phẩm']:
                self._download_wiring_diagram_and_dimensions(soup, series_dir, product_info['Mã sản phẩm'])
            
            # Lấy thông số kỹ thuật từ tab Specifications
            specs_html = self._extract_specifications(soup, product_info)
            product_info['Tổng quan'] = specs_html
            
            return product_info
            
        except Exception as e:
            print(f"Lỗi khi trích xuất thông tin sản phẩm {product_url}: {str(e)}")
            traceback.print_exc()
            return None
    
    def _extract_specifications(self, soup, product_info):
        """Trích xuất và xử lý thông số kỹ thuật"""
        try:
            # Tìm tab Specifications và ảnh thông số trong <div class="tab-content pt-4" id="myTabContent">
            specs_img = None
            
            # Tìm tab content với id="myTabContent"
            tab_content = soup.select_one('#myTabContent')
            if tab_content:
                # Tìm tab-pane đầu tiên (active show) hoặc có id="tab1"
                tab_pane = tab_content.select_one('.tab-pane.fade.active.show, #tab1')
                if tab_pane:
                    # Tìm ảnh thông số trong tab pane
                    specs_img = tab_pane.select_one('img[src*="spec"]')
                    if not specs_img:
                        # Thử tìm bất kỳ ảnh nào trong tab pane
                        specs_img = tab_pane.select_one('img')
            
            # Nếu không tìm thấy trong tab content, thử các selector khác
            if not specs_img:
                specs_img = soup.select_one('#tab1 img, .tab-pane img[src*="spec"], img[src*="SPE"]')
            
            if not specs_img:
                # Tạo bảng thông số cơ bản
                return self._create_basic_specs_table(product_info)
            
            # Lấy URL ảnh thông số
            spec_img_url = specs_img.get('src')
            if spec_img_url:
                # Đảm bảo URL đầy đủ
                if not spec_img_url.startswith('http'):
                    spec_img_url = urljoin(self.base_url, spec_img_url)
                
                print(f"Đang xử lý ảnh thông số kỹ thuật: {spec_img_url}")
                
                # Sử dụng AI để đọc ảnh và chuyển đổi thành bảng
                specs_data = self._extract_specs_from_image(spec_img_url)
                if specs_data:
                    return self._create_specs_table_from_ai_data(specs_data, product_info)
                else:
                    print("Không thể đọc ảnh bằng AI, tạo bảng cơ bản")
            
            # Nếu không thể xử lý ảnh, tạo bảng cơ bản
            return self._create_basic_specs_table(product_info)
            
        except Exception as e:
            print(f"Lỗi khi trích xuất thông số kỹ thuật: {str(e)}")
            return self._create_basic_specs_table(product_info)
    
    def _extract_specs_from_image(self, image_url):
        """Sử dụng AI để đọc thông số kỹ thuật từ ảnh"""
        try:
            if not self.gemini_model:
                print("Gemini API không có sẵn")
                return None
            
            # Tải ảnh
            response = requests.get(image_url, timeout=30)
            if response.status_code != 200:
                print(f"Không thể tải ảnh từ {image_url}")
                return None
            
            # Chuyển đổi ảnh thành PIL Image
            from PIL import Image
            import io
            image_data = Image.open(io.BytesIO(response.content))
            
            # Tạo prompt cho AI - cải thiện prompt để có kết quả tốt hơn
            prompt = """
            Bạn là một chuyên gia kỹ thuật điện tử. Hãy phân tích ảnh thông số kỹ thuật này của sản phẩm điện tử/công nghiệp và trích xuất tất cả thông tin thành định dạng bảng.
            
            Yêu cầu:
            1. Đọc tất cả text và số liệu trong ảnh một cách chính xác
            2. Chuyển đổi các thuật ngữ kỹ thuật sang tiếng Việt phù hợp (ví dụ: Operating voltage -> Điện áp hoạt động, Temperature range -> Phạm vi nhiệt độ)
            3. Giữ nguyên các giá trị số và đơn vị đo
            4. Chỉ trích xuất thông tin có trong ảnh, không thêm thông tin giả định
            5. Trả về dưới dạng danh sách các cặp [Thông số tiếng Việt, Giá trị]
            
            Format trả về chính xác:
            Thông số 1|Giá trị 1
            Thông số 2|Giá trị 2
            ...
            
            Ví dụ:
            Điện áp hoạt động|DC 12-24V
            Dòng tiêu thụ|≤20mA
            Phạm vi nhiệt độ|-25°C to +55°C
            """
            
            # Gọi API Gemini
            try:
                response = self.gemini_model.generate_content([prompt, image_data])
                
                # Xử lý kết quả
                if response and hasattr(response, 'text') and response.text:
                    content = response.text.strip()
                    print(f"Gemini response: {content}")
                    
                    # Parse kết quả thành danh sách các cặp thông số-giá trị
                    specs_data = []
                    for line in content.split('\n'):
                        line = line.strip()
                        if '|' in line and line:
                            parts = line.split('|', 1)
                            if len(parts) == 2:
                                param = parts[0].strip()
                                value = parts[1].strip()
                                if param and value:  # Chỉ thêm nếu cả hai đều có nội dung
                                    specs_data.append([param, value])
                    
                    print(f"Đã trích xuất {len(specs_data)} thông số từ ảnh")
                    return specs_data if specs_data else None
                else:
                    print("Không nhận được response từ Gemini")
                    return None
                
            except Exception as api_error:
                print(f"Lỗi khi gọi Gemini API: {str(api_error)}")
                # Thử với prompt đơn giản hơn
                try:
                    simple_prompt = "Hãy đọc và liệt kê tất cả thông tin trong ảnh này theo định dạng: Tên|Giá trị"
                    response = self.gemini_model.generate_content([simple_prompt, image_data])
                    
                    if response and hasattr(response, 'text') and response.text:
                        content = response.text.strip()
                        specs_data = []
                        for line in content.split('\n'):
                            line = line.strip()
                            if '|' in line and line:
                                parts = line.split('|', 1)
                                if len(parts) == 2:
                                    param = parts[0].strip()
                                    value = parts[1].strip()
                                    if param and value:
                                        specs_data.append([param, value])
                        
                        return specs_data if specs_data else None
                
                except Exception as fallback_error:
                    print(f"Lỗi khi sử dụng prompt đơn giản: {str(fallback_error)}")
                    return None
            
            return None
            
        except Exception as e:
            print(f"Lỗi khi sử dụng AI đọc ảnh: {str(e)}")
            traceback.print_exc()
            return None
    
    def _create_specs_table_from_ai_data(self, specs_data, product_info):
        """Tạo bảng HTML từ dữ liệu AI trích xuất"""
        try:
            html = '<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial;"><thead><tr><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
            
            # Thêm thông tin cơ bản
            html += f'<tr><td>Mã sản phẩm</td><td>{product_info.get("Mã sản phẩm", "")}</td></tr>'
            html += f'<tr><td>Tên sản phẩm</td><td>{product_info.get("Tên sản phẩm", "")}</td></tr>'
            
            # Thêm dữ liệu từ AI
            for spec, value in specs_data:
                html += f'<tr><td>{spec}</td><td>{value}</td></tr>'
            
            # Thêm copyright
            html += '<tr><td>Copyright</td><td>Haiphongtech.vn</td></tr>'
            html += '</tbody></table>'
            
            return html
            
        except Exception as e:
            print(f"Lỗi khi tạo bảng từ dữ liệu AI: {str(e)}")
            return self._create_basic_specs_table(product_info)
    
    def _create_basic_specs_table(self, product_info):
        """Tạo bảng thông số cơ bản khi không thể đọc ảnh"""
        html = '<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial;"><thead><tr><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
        
        html += f'<tr><td>Mã sản phẩm</td><td>{product_info.get("Mã sản phẩm", "")}</td></tr>'
        html += f'<tr><td>Tên sản phẩm</td><td>{product_info.get("Tên sản phẩm", "")}</td></tr>'
        html += f'<tr><td>Series</td><td>{product_info.get("Type", "")}</td></tr>'
        html += '<tr><td>Tiêu chuẩn</td><td>CE</td></tr>'
        html += '<tr><td>Copyright</td><td>Haiphongtech.vn</td></tr>'
        
        html += '</tbody></table>'
        return html
    
    def _download_product_image(self, image_url, series_dir, product_code):
        """Tải ảnh sản phẩm về thư mục với xử lý nền trắng cho ảnh có alpha channel"""
        try:
            if not image_url or not product_code:
                return None
            
            # Đảm bảo URL đầy đủ
            if not image_url.startswith('http'):
                image_url = urljoin(self.base_url, image_url)
            
            # Tạo tên file
            image_filename = f"{self._sanitize_folder_name(product_code)}.webp"
            image_path = os.path.join(series_dir, 'Anh', image_filename)
            
            print(f"🖼️ Đang tải ảnh sản phẩm: {image_url}")
            
            # Tải ảnh với headers phù hợp
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': self.base_url
            }
            
            response = requests.get(image_url, headers=headers, timeout=30)
            if response.status_code == 200:
                # Mở ảnh bằng PIL
                original_image = Image.open(BytesIO(response.content))
                
                print(f"📄 Ảnh gốc - Mode: {original_image.mode}, Size: {original_image.size}")
                
                # XỬ LÝ NỀN TRẮNG CHO TẤT CẢ ẢNH CÓ ALPHA CHANNEL
                processed_image = self._process_alpha_to_white_background(original_image, image_filename)
                
                # Tạo thư mục nếu chưa tồn tại
                os.makedirs(os.path.dirname(image_path), exist_ok=True)
                
                # Lưu ảnh dưới dạng WebP với chất lượng cao
                processed_image.save(image_path, "WEBP", quality=95, method=6)
                
                print(f"✅ Đã lưu ảnh sản phẩm: {image_filename}")
                return image_path
            else:
                print(f"❌ Lỗi HTTP {response.status_code} khi tải ảnh: {image_url}")
                return None
            
        except Exception as e:
            print(f"❌ Lỗi khi tải ảnh {image_url}: {str(e)}")
            return None

    def _get_soup(self, url):
        """Lấy nội dung trang web và trả về đối tượng BeautifulSoup"""
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
                    return None
    
    def _sanitize_folder_name(self, name):
        """Làm sạch tên thư mục"""
        # Loại bỏ ký tự đặc biệt
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
        # Loại bỏ khoảng trắng thừa
        sanitized = re.sub(r'\s+', '_', sanitized)
        # Giới hạn độ dài
        sanitized = sanitized[:100]
        return sanitized.strip('_')
    
    def _create_fotek_reports(self, result_dir, all_products, category_info, priority_mode=False, mode_type=""):
        """Tạo báo cáo tổng hợp cho Fotek"""
        try:
            mode_text = f"ưu tiên ({mode_type})" if priority_mode else "đầy đủ"
            
            # Tạo file báo cáo tổng hợp
            report_file = os.path.join(result_dir, f'Bao_cao_tong_hop_{mode_text}.xlsx')
            
            with pd.ExcelWriter(report_file, engine='openpyxl') as writer:
                # Sheet 1: Tất cả sản phẩm
                if all_products:
                    df_products = pd.DataFrame(all_products)
                    df_products.to_excel(writer, sheet_name='Du_lieu_san_pham', index=False)
                
                # Sheet 2: Thống kê danh mục
                if category_info:
                    df_categories = pd.DataFrame(category_info)
                    df_categories.to_excel(writer, sheet_name='Thong_ke_danh_muc', index=False)
                
                # Sheet 3: Thống kê tổng quan
                summary_data = {
                    'Chế độ cào': [f"Cào dữ liệu {mode_text}"],
                    'Tổng số danh mục': [len(category_info)],
                    'Tổng số sản phẩm': [len(all_products)],
                    'Thời gian cào': [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
                }
                
                if priority_mode:
                    summary_data['Ghi chú'] = ['Đây là kết quả cào danh mục ưu tiên được chọn']
                
                df_summary = pd.DataFrame(summary_data)
                df_summary.to_excel(writer, sheet_name='Tong_quan', index=False)
            
            print(f"Đã tạo báo cáo tổng hợp: {report_file}")
            
        except Exception as e:
            print(f"Lỗi khi tạo báo cáo: {str(e)}")
            traceback.print_exc()

    def _download_wiring_diagram_and_dimensions(self, soup, series_dir, product_code):
        """Tải ảnh Wiring Diagram và Dimensions từ các tab"""
        try:
            # Tạo thư mục Anh nếu chưa tồn tại
            anh_dir = os.path.join(series_dir, 'Anh')
            os.makedirs(anh_dir, exist_ok=True)
            
            print(f"Đang tìm Wiring Diagram và Dimensions cho sản phẩm: {product_code}")
            
            # Tìm tab content với id="myTabContent"
            tab_content = soup.select_one('#myTabContent')
            if not tab_content:
                print("Không tìm thấy tab content với id='myTabContent'")
                return
            
            # 1. TẢI WIRING DIAGRAM TỪ TAB2
            wiring_tab = tab_content.select_one('#tab2')
            if wiring_tab:
                print("Tìm thấy tab Wiring Diagram (#tab2)")
                wiring_img = wiring_tab.select_one('img')
                if wiring_img and wiring_img.get('src'):
                    wiring_img_url = wiring_img.get('src')
                    
                    # Đảm bảo URL đầy đủ
                    if not wiring_img_url.startswith('http'):
                        wiring_img_url = urljoin(self.base_url, wiring_img_url)
                    
                    # Tên file theo định dạng: [Mã sản phẩm]-WD
                    wiring_filename = f"{self._sanitize_folder_name(product_code)}-WD.webp"
                    wiring_path = os.path.join(anh_dir, wiring_filename)
                    
                    print(f"Đang tải Wiring Diagram: {wiring_img_url}")
                    success = self._download_image_from_url(wiring_img_url, wiring_path)
                    if success:
                        print(f"✅ Đã tải Wiring Diagram: {wiring_filename}")
                    else:
                        print(f"❌ Lỗi khi tải Wiring Diagram: {wiring_filename}")
                else:
                    print("Không tìm thấy ảnh trong tab Wiring Diagram")
            else:
                print("Không tìm thấy tab Wiring Diagram (#tab2)")
            
            # 2. TẢI DIMENSIONS TỪ TAB3  
            dimensions_tab = tab_content.select_one('#tab3')
            if dimensions_tab:
                print("Tìm thấy tab Dimensions (#tab3)")
                dimensions_img = dimensions_tab.select_one('img')
                if dimensions_img and dimensions_img.get('src'):
                    dimensions_img_url = dimensions_img.get('src')
                    
                    # Đảm bảo URL đầy đủ
                    if not dimensions_img_url.startswith('http'):
                        dimensions_img_url = urljoin(self.base_url, dimensions_img_url)
                    
                    # Tên file theo định dạng: [Mã sản phẩm]-DMS
                    dimensions_filename = f"{self._sanitize_folder_name(product_code)}-DMS.webp"
                    dimensions_path = os.path.join(anh_dir, dimensions_filename)
                    
                    print(f"Đang tải Dimensions: {dimensions_img_url}")
                    success = self._download_image_from_url(dimensions_img_url, dimensions_path)
                    if success:
                        print(f"✅ Đã tải Dimensions: {dimensions_filename}")
                    else:
                        print(f"❌ Lỗi khi tải Dimensions: {dimensions_filename}")
                else:
                    print("Không tìm thấy ảnh trong tab Dimensions")
            else:
                print("Không tìm thấy tab Dimensions (#tab3)")
                
        except Exception as e:
            print(f"Lỗi khi tải Wiring Diagram và Dimensions cho {product_code}: {str(e)}")
            traceback.print_exc()

    def _download_image_from_url(self, image_url, save_path):
        """Tải ảnh từ URL và lưu vào đường dẫn chỉ định (dành cho ảnh phụ như WD, DMS)"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': self.base_url
            }
            
            response = requests.get(image_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Tạo thư mục nếu chưa tồn tại
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            
            # Chuyển đổi sang WebP với xử lý mode phù hợp
            from PIL import Image
            from io import BytesIO
            
            image = Image.open(BytesIO(response.content))
            filename = os.path.basename(save_path)
            
            print(f"📸 Ảnh phụ {filename} - Mode gốc: {image.mode}")
            
            # Xử lý mode ảnh để tương thích WebP
            if image.mode == "RGBA":
                # Với ảnh phụ, có thể giữ alpha hoặc chuyển sang RGB tùy nhu cầu
                # Ở đây chuyển sang RGB với nền trắng để tương thích WordPress
                background = Image.new("RGBA", image.size, (255, 255, 255, 255))
                composite = Image.alpha_composite(background, image)
                final_image = composite.convert("RGB")
                print(f"🔄 Đã chuyển RGBA sang RGB (nền trắng) cho ảnh phụ {filename}")
                
            elif image.mode == "P":
                if "transparency" in image.info:
                    # Palette với transparency
                    rgba_image = image.convert("RGBA")
                    background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
                    composite = Image.alpha_composite(background, rgba_image)
                    final_image = composite.convert("RGB")
                    print(f"🔄 Đã chuyển Palette (có transparency) sang RGB cho ảnh phụ {filename}")
                else:
                    final_image = image.convert("RGB")
                    print(f"🔄 Đã chuyển Palette sang RGB cho ảnh phụ {filename}")
                    
            elif image.mode == "L":
                final_image = image.convert("RGB")
                print(f"🔄 Đã chuyển Grayscale sang RGB cho ảnh phụ {filename}")
                
            elif image.mode == "RGB":
                final_image = image
                print(f"✅ Giữ nguyên RGB cho ảnh phụ {filename}")
                
            else:
                final_image = image.convert("RGB")
                print(f"🔄 Đã chuyển {image.mode} sang RGB cho ảnh phụ {filename}")
            
            # Lưu với chất lượng vừa phải cho ảnh phụ
            final_image.save(save_path, "WEBP", quality=85, method=4)
            print(f"✅ Đã lưu ảnh phụ {filename} (WebP RGB)")
            return True
            
        except Exception as e:
            print(f"❌ Lỗi khi tải ảnh từ {image_url}: {str(e)}")
            return False

    def _is_white_background_product_image(self, url):
        """Kiểm tra xem URL có phải là ảnh sản phẩm có nền trắng không"""
        try:
            if not url or not isinstance(url, str):
                return False
            
            url_lower = url.lower()
            
            # 1. KIỂM TRA PATTERN URL FOTEK SẢN PHẨM CÓ NỀN TRẮNG
            # Pattern: /image/catalog/product/Item/ (như TC-NT-10R.png)
            fotek_product_patterns = [
                '/image/catalog/product/item/',
                '/image/catalog/product/',
                '/catalog/product/item/',
                '/catalog/product/'
            ]
            
            has_product_pattern = any(pattern in url_lower for pattern in fotek_product_patterns)
            
            # 2. KIỂM TRA EXTENSION FILE ẢNH
            valid_extensions = ['.png', '.jpg', '.jpeg', '.webp']
            has_valid_extension = any(url_lower.endswith(ext) or ext + '?' in url_lower for ext in valid_extensions)
            
            # 3. LOẠI TRỪ CÁC ẢNH KHÔNG PHẢI SẢN PHẨM CHÍNH
            # Loại trừ ảnh thông số kỹ thuật, wiring diagram, dimensions (chỉ check trong filename)
            exclude_keywords = [
                'specification', 'speci',  # Thông số kỹ thuật (bỏ 'spec' để tránh conflict)
                'wiring', 'wire', 'wd',    # Sơ đồ đấu nối
                'dimension', 'dims', 'dms', # Kích thước
                'manual', 'catalog',       # Sách hướng dẫn
                'drawing', 'schematic',    # Bản vẽ
                'connection', 'install',   # Hướng dẫn lắp đặt
                'thumbnail', 'thumb',      # Ảnh nhỏ
                'icon', 'logo'             # Icon, logo
            ]
            
            # Chỉ kiểm tra exclude keywords trong filename (không check toàn bộ URL path)
            filename = url.split('/')[-1].split('?')[0].lower()
            has_exclude_keyword = any(keyword in filename for keyword in exclude_keywords)
            
            # 4. KIỂM TRA PATTERN ẢNH SẢN PHẨM FOTEK
            # Ảnh sản phẩm Fotek thường có tên file là mã sản phẩm (VD: TC-NT-10R.png)
            product_code_pattern = re.match(r'^[A-Za-z0-9]{1,4}[-_]?[A-Za-z0-9]{1,10}[-_]?[A-Za-z0-9]*\.(png|jpg|jpeg|webp)$', filename)
            
            # 5. ĐÁNH GIÁ CUỐI CÙNG
            is_product_image = (
                has_product_pattern and 
                has_valid_extension and 
                not has_exclude_keyword and
                len(filename) > 5  # Tên file không quá ngắn
            )
            
            # Bonus: Nếu có pattern mã sản phẩm nhưng PHẢI có product path
            if product_code_pattern and has_product_pattern:
                is_product_image = True
                print(f"🎯 URL có pattern mã sản phẩm Fotek với product path: {filename}")
            elif product_code_pattern and not has_product_pattern:
                is_product_image = False
                print(f"❌ URL có pattern mã sản phẩm nhưng KHÔNG có product path: {filename}")
            
            if is_product_image:
                print(f"✅ URL hợp lệ - Ảnh sản phẩm nền trắng: {url}")
            else:
                reason = []
                if not has_product_pattern:
                    reason.append("không có pattern product")
                if not has_valid_extension:
                    reason.append("extension không hợp lệ")
                if has_exclude_keyword:
                    matching_keywords = [k for k in exclude_keywords if k in filename]
                    reason.append(f"có từ khóa loại trừ: {matching_keywords}")
                print(f"❌ URL không hợp lệ ({', '.join(reason)}): {url}")
            
            return is_product_image
            
        except Exception as e:
            print(f"Lỗi khi kiểm tra ảnh sản phẩm có nền trắng: {str(e)}")
            return False 

    def _process_alpha_to_white_background(self, image, image_filename):
        """Xử lý ảnh có alpha channel: chuyển nền trong suốt thành nền trắng RGB WebP"""
        try:
            original_mode = image.mode
            print(f"🔍 Đang kiểm tra ảnh {image_filename} - Mode: {original_mode}")
            
            # BƯỚC 1: Kiểm tra mode của ảnh và xử lý alpha channel
            if original_mode == "RGBA":
                print(f"📸 Phát hiện ảnh RGBA (có alpha channel) - Đang xử lý nền trắng...")
                
                # Tạo nền trắng với alpha channel theo yêu cầu
                white_background = Image.new("RGBA", image.size, (255, 255, 255, 255))
                print(f"⚪ Đã tạo nền trắng RGBA kích thước: {image.size}")
                
                # Sử dụng alpha_composite để gộp ảnh gốc lên nền trắng
                composite_image = Image.alpha_composite(white_background, image)
                print(f"🎨 Đã composite ảnh lên nền trắng bằng alpha_composite")
                
                # Chuyển sang RGB để loại bỏ alpha channel hoàn toàn
                final_image = composite_image.convert("RGB")
                print(f"✅ Đã chuyển đổi sang RGB (loại bỏ alpha) - Đã xử lý ảnh {image_filename} → OK (nền trắng)")
                
                return final_image
                
            elif original_mode == "P":
                print(f"🔍 Phát hiện ảnh Palette mode - Kiểm tra transparency...")
                
                # Kiểm tra nếu ảnh palette có transparency
                if "transparency" in image.info:
                    print(f"🎭 Ảnh Palette có transparency - Đang xử lý nền trắng...")
                    
                    # Chuyển sang RGBA để xử lý transparency
                    rgba_image = image.convert("RGBA")
                    
                    # Tạo nền trắng và composite
                    white_background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
                    composite_image = Image.alpha_composite(white_background, rgba_image)
                    
                    # Chuyển sang RGB để loại bỏ alpha
                    final_image = composite_image.convert("RGB")
                    print(f"✅ Đã xử lý ảnh Palette với transparency - Đã xử lý ảnh {image_filename} → OK (nền trắng)")
                    
                    return final_image
                else:
                    print(f"📝 Ảnh Palette không có transparency - Chuyển sang RGB")
                    final_image = image.convert("RGB")
                    print(f"✅ Đã chuyển đổi sang RGB - Đã xử lý ảnh {image_filename} → OK (giữ nguyên)")
                    
                    return final_image
                    
            elif original_mode in ("RGB", "L"):
                print(f"📷 Ảnh đã có nền solid ({original_mode}) - Không cần xử lý alpha")
                
                if original_mode == "L":
                    final_image = image.convert("RGB")
                    print(f"✅ Đã chuyển grayscale sang RGB - Đã xử lý ảnh {image_filename} → OK (chuyển RGB)")
                else:
                    final_image = image
                    print(f"✅ Giữ nguyên ảnh RGB - Đã xử lý ảnh {image_filename} → OK (giữ nguyên)")
                
                return final_image
                
            else:
                print(f"⚠️ Mode ảnh không được hỗ trợ: {original_mode} - Chuyển sang RGB")
                final_image = image.convert("RGB")
                print(f"✅ Đã chuyển sang RGB mode - Đã xử lý ảnh {image_filename} → OK (fallback RGB)")
                
                return final_image
                
        except Exception as e:
            print(f"❌ Lỗi khi xử lý ảnh {image_filename}: {str(e)}")
            traceback.print_exc()
            
            # Fallback: chỉ chuyển sang RGB
            try:
                if image.mode in ("RGBA", "P"):
                    final_image = image.convert("RGB")
                    print(f"⚠️ Fallback - Đã xử lý ảnh {image_filename} → OK (fallback RGB)")
                    return final_image
                else:
                    print(f"⚠️ Fallback - Đã xử lý ảnh {image_filename} → OK (giữ nguyên)")
                    return image
            except:
                print(f"❌ Lỗi nghiêm trọng khi xử lý ảnh {image_filename}")
                return image 