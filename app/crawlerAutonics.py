"""
Autonics Product Crawler - Cào dữ liệu sản phẩm từ website Autonics.com
Phiên bản: 1.0
Tác giả: Auto-generated based on existing crawler patterns
Ngày tạo: $(date)
"""

import os
import time
import requests
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import concurrent.futures
from queue import Queue
import pandas as pd
from PIL import Image, ImageEnhance
from io import BytesIO
import json
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from app.webp_converter import WebPConverter
import threading

# Selenium imports for dynamic content
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def sanitize_folder_name(name):
    """Làm sạch tên folder để phù hợp với hệ điều hành"""
    # Loại bỏ các ký tự không hợp lệ
    clean_name = re.sub(r'[<>:"/\\|?*]', '_', name)
    clean_name = re.sub(r'[^\w\s-]', '', clean_name).strip()
    clean_name = re.sub(r'[-\s]+', '_', clean_name)
    return clean_name if clean_name else 'Unknown_Category'

def standardize_filename(code):
    """Chuẩn hóa mã sản phẩm thành tên file hợp lệ"""
    return re.sub(r'[\\/:*?"<>|,=\s]', '-', code).replace('-+', '-').strip('-').upper()

class AutonicsCrawler:
    """
    Crawler chuyên dụng cho website Autonics.com
    Cào dữ liệu cảm biến và thiết bị tự động hóa với xử lý đa luồng
    """
    
    def __init__(self, output_root=None, max_workers=8, max_retries=3, socketio=None):
        """
        Khởi tạo AutonicsCrawler
        
        Args:
            output_root: Thư mục gốc để lưu kết quả
            max_workers: Số luồng tối đa
            max_retries: Số lần thử lại khi request thất bại
            socketio: Socket.IO instance để emit tiến trình
        """
        self.output_root = output_root or os.path.join(os.getcwd(), "output_autonics")
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.socketio = socketio
        
        # Tạo thư mục output
        os.makedirs(self.output_root, exist_ok=True)
        
        # Base URLs
        self.base_url = "https://www.autonics.com"
        self.vietnam_base_url = "https://www.autonics.com/vn"
        
        # Cấu hình session với retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Headers mô phỏng trình duyệt thật
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        # Cấu hình Selenium WebDriver (tương tự FotekScraper)
        self.chrome_options = Options()
        self.chrome_options.add_argument('--headless')
        self.chrome_options.add_argument('--no-sandbox')
        self.chrome_options.add_argument('--disable-dev-shm-usage')
        self.chrome_options.add_argument('--disable-gpu')
        self.chrome_options.add_argument('--window-size=1920,1080')
        self.chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        # Disable logging để giảm noise
        self.chrome_options.add_argument('--log-level=3')
        self.chrome_options.add_argument('--disable-logging')
        self.chrome_options.add_argument('--disable-extensions')
        
        # Thống kê
        self.stats = {
            "categories_processed": 0,
            "series_found": 0,
            "products_found": 0,
            "products_processed": 0,
            "images_downloaded": 0,
            "failed_requests": 0,
            "failed_images": 0
        }
        
        logger.info("✅ Đã khởi tạo AutonicsCrawler với Selenium support")
    
    def get_driver(self):
        """Tạo một WebDriver Chrome mới (tương tự FotekScraper)"""
        try:
            driver = webdriver.Chrome(options=self.chrome_options)
            driver.implicitly_wait(10)
            return driver
        except Exception as e:
            logger.error(f"Không thể khởi tạo WebDriver: {e}")
            raise
    
    def close_driver(self, driver):
        """Đóng WebDriver"""
        try:
            driver.quit()
        except Exception as e:
            logger.warning(f"Lỗi khi đóng WebDriver: {e}")
    
    def emit_progress(self, percent, message, detail=""):
        """Emit tiến trình qua Socket.IO"""
        if self.socketio:
            self.socketio.emit('progress_update', {
                'percent': percent,
                'message': message,
                'detail': detail
            })
        else:
            print(f"[{percent}%] {message} - {detail}")
    
    def get_html_content(self, url, timeout=30):
        """Lấy nội dung HTML từ URL"""
        try:
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as e:
            self.stats["failed_requests"] += 1
            logger.error(f"Lỗi khi lấy HTML từ {url}: {str(e)}")
            return None
    
    def extract_series_from_category(self, category_url):
        """
        Trích xuất tất cả series từ trang category với xử lý phân trang
        Website Autonics sử dụng server-side rendering với Vue.js, data được embed trong window.__INIT_DATA__
        
        Args:
            category_url: URL của trang category
            
        Returns:
            list: Danh sách các series URLs và metadata
        """
        series_data = []
        page = 1
        
        while True:
            # Tạo URL với tham số page
            if '?' in category_url:
                paginated_url = f"{category_url}&page={page}"
            else:
                paginated_url = f"{category_url}?page={page}"
            
            self.emit_progress(
                0, 
                f"Đang thu thập series từ trang {page}",
                f"URL: {paginated_url}"
            )
            
            html = self.get_html_content(paginated_url)
            if not html:
                break
            
            # Extract data từ window.__INIT_DATA__
            current_page_series = self.extract_init_data_from_html(html)
            
            if not current_page_series:
                break
            
            # Thêm vào danh sách tổng
            for series in current_page_series:
                if series not in series_data:
                    series_data.append(series)
            
            # Kiểm tra có trang tiếp theo không bằng cách parse pagination info
            has_next = self.check_has_next_page(html, page)
            
            if not has_next:
                break
                
            page += 1
        
        # Convert thành series URLs
        series_urls = []
        for series in series_data:
            if series.get('urlNm'):
                series_url = f"{self.base_url}/vn/series/{series['urlNm']}"
                series_urls.append(series_url)
        
        self.stats["series_found"] += len(series_urls)
        logger.info(f"Tìm thấy {len(series_urls)} series từ {category_url}")
        logger.info(f"Series URLs: {series_urls[:5]}{'...' if len(series_urls) > 5 else ''}")
        return series_urls
    
    def extract_init_data_from_html(self, html):
        """
        Extract dữ liệu từ window.__INIT_DATA__ object trong HTML
        
        Args:
            html: HTML content
            
        Returns:
            list: Danh sách series data từ resultList
        """
        try:
            # Tìm script tag chứa window.__INIT_DATA__
            start_marker = 'window.__INIT_DATA__ = '
            start_idx = html.find(start_marker)
            
            if start_idx == -1:
                logger.warning("Không tìm thấy window.__INIT_DATA__ trong HTML")
                return []
            
            # Tìm điểm bắt đầu JSON
            json_start = start_idx + len(start_marker)
            
            # Tìm điểm kết thúc JSON (trước dấu ;)
            json_end = html.find(';\n', json_start)
            if json_end == -1:
                json_end = html.find(';', json_start)
            
            if json_end == -1:
                logger.warning("Không tìm thấy điểm kết thúc JSON")
                return []
            
            # Extract JSON string
            json_str = html[json_start:json_end].strip()
            
            # Parse JSON
            import json
            data = json.loads(json_str)
            
            # Lấy resultList
            result_list = data.get('resultList', [])
            
            logger.info(f"Extracted {len(result_list)} series từ __INIT_DATA__")
            
            return result_list
            
        except Exception as e:
            logger.error(f"Lỗi khi extract __INIT_DATA__: {str(e)}")
            return []
    
    def check_has_next_page(self, html, current_page):
        """
        Kiểm tra có trang tiếp theo không bằng cách parse pagination info từ __INIT_DATA__
        
        Args:
            html: HTML content
            current_page: Trang hiện tại
            
        Returns:
            bool: True nếu có trang tiếp theo
        """
        try:
            start_marker = 'window.__INIT_DATA__ = '
            start_idx = html.find(start_marker)
            
            if start_idx == -1:
                return False
            
            json_start = start_idx + len(start_marker)
            json_end = html.find(';\n', json_start)
            if json_end == -1:
                json_end = html.find(';', json_start)
            
            if json_end == -1:
                return False
            
            json_str = html[json_start:json_end].strip()
            
            import json
            data = json.loads(json_str)
            
            # Lấy pagination info
            pagination_info = data.get('paginationInfo', {})
            total_page_count = pagination_info.get('totalPageCount', 1)
            
            logger.info(f"Pagination: trang {current_page}/{total_page_count}")
            
            return current_page < total_page_count
            
        except Exception as e:
            logger.error(f"Lỗi khi check pagination: {str(e)}")
            return False
    
    def extract_products_from_series(self, series_url):
        """
        Trích xuất tất cả sản phẩm từ trang series với Selenium WebDriver và pagination handling
        Series pages sử dụng client-side rendering, cần Selenium để load đầy đủ
        
        Args:
            series_url: URL của trang series
            
        Returns:
            list: Danh sách các product URLs và metadata
        """
        logger.info(f"Bắt đầu extract products từ series: {series_url}")
        
        # Sử dụng Selenium thay vì requests (tương tự FotekScraper)
        driver = self.get_driver()
        all_products_data = []
        
        try:
            # Single page load để extract both expected count và max pages
            logger.info(f"🔍 Loading {series_url} để extract metadata...")
            driver.get(series_url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3)
            
            html = driver.page_source
            soup = BeautifulSoup(html, 'html.parser')
            
            # Extract expected count từ title
            expected_count = self.extract_count_from_soup(soup)
            logger.info(f"🎯 Expected product count từ title: {expected_count}")
            
            # Extract max pages từ same soup
            max_pages = self.extract_max_pages_from_soup(soup)
            logger.info(f"📄 Detected max pages: {max_pages}")
            
            # Xử lý phân trang - lặp qua tất cả các trang
            page = 1
            
            while page <= max_pages:
                # Tạo URL với page parameter
                if page == 1:
                    page_url = series_url
                else:
                    separator = '&' if '?' in series_url else '?'
                    page_url = f"{series_url}{separator}page={page}"
                
                logger.info(f"🔄 Đang xử lý trang {page}: {page_url}")
                
                driver.get(page_url)
                
                # Đợi page load xong
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                
                # Increased wait time for pages 3+ (dynamic loading issue)
                if page >= 3:
                    logger.info(f"⏳ Increased wait time for page {page} (potential dynamic loading)")
                    time.sleep(8)  # Longer wait for higher pages
                else:
                    time.sleep(3)
                    
                # Wait for series-model section to be fully loaded
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "section#series-model"))
                    )
                    logger.info(f"✅ series-model section loaded for page {page}")
                except Exception as e:
                    logger.warning(f"⚠️  series-model section not found within timeout for page {page}: {e}")
                    # Continue anyway, might still have data
                
                # Lấy HTML sau khi JavaScript render
                html = driver.page_source
                soup = BeautifulSoup(html, 'html.parser')
                
                # Tìm section chứa models
                series_model = soup.find('section', id='series-model')
                if not series_model:
                    logger.warning(f"Không tìm thấy section#series-model trong {page_url}")
                    break
                
                # Tìm tất cả model items
                model_items = series_model.find_all('li')
                
                # Filter ra chỉ những li có product links
                valid_items = []
                for item in model_items:
                    a_tag = item.find('a')
                    if a_tag and a_tag.get('href') and '/vn/model/' in a_tag.get('href'):
                        valid_items.append(item)
                
                logger.info(f"📦 Trang {page}: Tìm thấy {len(valid_items)} sản phẩm trong {len(model_items)} items")
                
                # Nếu không có sản phẩm nào, retry một lần trước khi stop
                if not valid_items:
                    if page <= 3:  # Only retry for early pages that should have data
                        logger.warning(f"⚠️  No products found on page {page}, retrying với longer wait...")
                        time.sleep(10)  # Extended wait for retry
                        
                        # Retry: reload page
                        driver.get(page_url)
                        WebDriverWait(driver, 15).until(
                            EC.presence_of_element_located((By.TAG_NAME, "body"))
                        )
                        time.sleep(8)
                        
                        # Re-parse after retry
                        html = driver.page_source
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        series_model = soup.find('section', id='series-model')
                        if series_model:
                            model_items = series_model.find_all('li')
                            valid_items = []
                            for item in model_items:
                                a_tag = item.find('a')
                                if a_tag and a_tag.get('href') and '/vn/model/' in a_tag.get('href'):
                                    valid_items.append(item)
                        
                        logger.info(f"🔄 RETRY: Trang {page} found {len(valid_items)} products after retry")
                    
                    if not valid_items:
                        logger.info(f"✅ Confirmed: Không có sản phẩm nào ở trang {page}, kết thúc pagination")
                        break
                
                # Extract products từ trang này
                page_products = []
                for item in valid_items:
                    try:
                        a_tag = item.find('a')
                        href = a_tag.get('href')
                        
                        # Tạo full URL
                        product_url = urljoin(self.base_url, href)
                        
                        # Lấy tên model
                        title_element = item.find('p', class_='title') or item.find('em', class_='title')
                        model_name = ''
                        if title_element:
                            model_name = title_element.get_text(strip=True)
                        
                        # Lấy model code từ URL hoặc title
                        model_code = model_name
                        if not model_code:
                            # Extract từ URL: /vn/model/BYS500-TDT1,2 -> BYS500-TDT1,2
                            url_parts = href.split('/')
                            if len(url_parts) > 0:
                                model_code = url_parts[-1]
                        
                        if model_code:
                            product_data = {
                                'url': product_url,
                                'name': model_name or model_code,
                                'series_url': series_url,
                                'model_code': model_code,
                                'page': page
                            }
                            
                            page_products.append(product_data)
                            
                    except Exception as e:
                        logger.warning(f"Lỗi khi extract model item: {str(e)}")
                        continue
                
                all_products_data.extend(page_products)
                logger.info(f"✅ Trang {page}: Extract được {len(page_products)} sản phẩm")
                
                # Kiểm tra xem có trang tiếp theo không
                paging_wrap = soup.find('div', class_='paging-wrap')
                if not paging_wrap:
                    logger.info("📄 Không tìm thấy pagination, kết thúc")
                    break
                
                # Tìm trang hiện tại và check next page
                current_page_link = paging_wrap.find('a', class_='current')
                if current_page_link:
                    current_page_num = current_page_link.get_text(strip=True)
                    logger.info(f"📍 Đang ở trang: {current_page_num}")
                
                # Check next page link
                next_links = paging_wrap.find_all('a')
                has_next = False
                for link in next_links:
                    link_text = link.get_text(strip=True)
                    # Tìm page number lớn hơn page hiện tại
                    if link_text.isdigit() and int(link_text) == page + 1:
                        has_next = True
                        break
                
                if not has_next:
                    logger.info(f"✅ Đã đến trang cuối cùng (trang {page})")
                    break
                
                page += 1
            
            self.stats["products_found"] += len(all_products_data)
            
            # Validation: So sánh actual vs expected count
            actual_count = len(all_products_data)
            if expected_count > 0:
                if actual_count == expected_count:
                    logger.info(f"✅ PERFECT MATCH: {actual_count}/{expected_count} sản phẩm")
                elif actual_count > expected_count:
                    logger.warning(f"⚠️  OVER-CRAWLED: {actual_count}/{expected_count} sản phẩm (+{actual_count - expected_count})")
                else:
                    logger.warning(f"⚠️  UNDER-CRAWLED: {actual_count}/{expected_count} sản phẩm (-{expected_count - actual_count})")
            
            logger.info(f"🎉 TỔNG CỘNG: Tìm thấy {actual_count} sản phẩm từ {page-1} trang của {series_url}")
            
            return all_products_data
            
        except Exception as e:
            logger.error(f"Lỗi khi extract products từ {series_url}: {str(e)}")
            return []
            
        finally:
            self.close_driver(driver)
    
    def extract_count_from_soup(self, soup):
        """
        Extract expected product count từ BeautifulSoup object
        <h3 class="sub-title col-4 col-m-12">PRFD Series Model <span class="fc0">(68)</span></h3>
        """
        try:
            title_element = soup.find('h3', class_='sub-title')
            if not title_element:
                return 0
            
            count_span = title_element.find('span', class_='fc0')
            if not count_span:
                return 0
            
            count_text = count_span.get_text(strip=True)
            count_match = re.search(r'\((\d+)\)', count_text)
            
            if count_match:
                expected_count = int(count_match.group(1))
                return expected_count
            else:
                return 0
        except Exception as e:
            logger.error(f"Lỗi extract count từ soup: {str(e)}")
            return 0
    
    def extract_max_pages_from_soup(self, soup):
        """
        Extract max pages từ BeautifulSoup object
        """
        try:
            paging_wrap = soup.find('div', class_='paging-wrap')
            if not paging_wrap:
                return 1
            
            page_numbers = []
            for link in paging_wrap.find_all('a'):
                link_text = link.get_text(strip=True)
                if link_text.isdigit():
                    page_numbers.append(int(link_text))
            
            if page_numbers:
                max_page = max(page_numbers)
                logger.info(f"✅ Found pagination numbers: {sorted(page_numbers)}, max = {max_page}")
                return max_page
            else:
                return 1
        except Exception as e:
            logger.error(f"Lỗi extract max pages từ soup: {str(e)}")
            return 50  # Fallback
    
    def get_expected_product_count(self, series_url, driver):
        """
        Extract expected product count từ title element
        <h3 class="sub-title col-4 col-m-12">PRFD Series Model <span class="fc0">(68)</span></h3>
        
        Args:
            series_url: URL của series page
            driver: Selenium WebDriver instance
            
        Returns:
            int: Expected product count, 0 nếu không tìm thấy
        """
        try:
            logger.info(f"🔍 Extracting expected count từ: {series_url}")
            driver.get(series_url)
            
            # Wait for page load
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3)
            
            html = driver.page_source
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find title element với pattern: "PRFD Series Model (68)"
            title_element = soup.find('h3', class_='sub-title')
            if not title_element:
                logger.warning("Không tìm thấy h3.sub-title element")
                return 0
            
            # Extract count từ span với class fc0
            count_span = title_element.find('span', class_='fc0')
            if not count_span:
                logger.warning("Không tìm thấy span.fc0 trong title")
                return 0
            
            # Parse count từ "(68)" format
            count_text = count_span.get_text(strip=True)
            count_match = re.search(r'\((\d+)\)', count_text)
            
            if count_match:
                expected_count = int(count_match.group(1))
                logger.info(f"✅ Found expected count: {expected_count}")
                return expected_count
            else:
                logger.warning(f"Cannot parse count từ: '{count_text}'")
                return 0
                
        except Exception as e:
            logger.error(f"Lỗi khi extract expected count: {str(e)}")
            return 0
    
    def detect_total_pages(self, series_url, driver):
        """
        Detect total number of pages từ pagination links
        
        Args:
            series_url: URL của series page  
            driver: Selenium WebDriver instance
            
        Returns:
            int: Total pages, default 50 nếu không detect được
        """
        try:
            logger.info(f"🔍 Detecting total pages từ: {series_url}")
            driver.get(series_url)
            
            # Wait for page load
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3)
            
            html = driver.page_source
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find pagination wrapper
            paging_wrap = soup.find('div', class_='paging-wrap')
            if not paging_wrap:
                logger.info("Không tìm thấy pagination, assume 1 page")
                return 1
            
            # Extract all page numbers
            page_numbers = []
            for link in paging_wrap.find_all('a'):
                link_text = link.get_text(strip=True)
                if link_text.isdigit():
                    page_numbers.append(int(link_text))
            
            if page_numbers:
                max_page = max(page_numbers)
                logger.info(f"✅ Detected max page: {max_page} từ pagination")
                return max_page
            else:
                logger.info("Không detect được page numbers, assume 1 page")
                return 1
                
        except Exception as e:
            logger.error(f"Lỗi khi detect total pages: {str(e)}")
            # Fallback: unlimited scanning với safety limit
            logger.info("⚠️  Fallback: Using safety limit 50 pages")
            return 50
    
# Removed extract_products_from_init_data method - now using Selenium directly
    
    def extract_product_details(self, product_url):
        """
        Trích xuất thông tin chi tiết của sản phẩm
        Product detail page cũng sử dụng window.__INIT_DATA__ để embed data
        
        Args:
            product_url: URL của trang sản phẩm
            
        Returns:
            dict: Thông tin chi tiết sản phẩm
        """
        html = self.get_html_content(product_url)
        if not html:
            return None
        
        # Extract data từ __INIT_DATA__ trước
        init_data = self.extract_product_init_data(html)
        
        # Fallback to HTML parsing nếu không có __INIT_DATA__
        soup = BeautifulSoup(html, 'html.parser')
        
        # Khởi tạo dữ liệu sản phẩm
        product_data = {
            'url': product_url,
            'product_code': '',
            'product_name': '',
            'full_product_name': '',
            'image_url': '',
            'specifications': {},
            'category': '',
            'series': ''
        }
        
        try:
            # Ưu tiên data từ __INIT_DATA__ nếu có
            if init_data:
                model_info = init_data.get('modelVo', {})
                
                # Lấy mã sản phẩm
                product_data['product_code'] = model_info.get('modlCode', model_info.get('modelCode', ''))
                
                # Tạo tên sản phẩm theo thứ tự: Category + Model + Series + Brand
                name_parts = []
                
                # 1. Category
                category_info = init_data.get('categoryVo', {})
                category_name = ''
                if category_info:
                    category_name = category_info.get('ctgryNm', '')
                    if category_name:
                        product_data['category'] = category_name
                
                # 2. Model name
                model_name = model_info.get('modlNm', model_info.get('modelName', product_data['product_code']))
                if model_name:
                    product_data['product_name'] = model_name
                
                # 3. Series
                series_info = init_data.get('seriesVo', {})
                series_name = ''
                if series_info:
                    series_name = series_info.get('seriesNm', '')
                    if series_name:
                        product_data['series'] = series_name
                
                # Ghép tên theo thứ tự: Category + Model + Series + Brand
                if category_name:
                    name_parts.append(category_name)
                if model_name:
                    name_parts.append(model_name)
                if series_name:
                    name_parts.append(series_name)
                name_parts.append('AUTONICS')
                
                product_data['full_product_name'] = ' '.join(name_parts)
                
                # Image URL
                image_url = model_info.get('imageUrl', '')
                if image_url:
                    if image_url.startswith('/'):
                        product_data['image_url'] = f"{self.base_url}/web{image_url}"
                    else:
                        product_data['image_url'] = urljoin(self.base_url, image_url)
                
                # Specifications từ specList hoặc data structure khác
                spec_list = init_data.get('specList', [])
                for spec in spec_list:
                    spec_name = spec.get('specNm', spec.get('name', ''))
                    spec_value = spec.get('specValue', spec.get('value', ''))
                    if spec_name and spec_value:
                        product_data['specifications'][spec_name] = spec_value
                
                # Thêm các specs từ model info
                if model_info:
                    # Các field có thể chứa specs
                    spec_fields = ['modlSfe', 'modlSfeTwo', 'modlSfeThree', 'modlDc']
                    for field in spec_fields:
                        value = model_info.get(field, '')
                        if value:
                            product_data['specifications'][field.replace('modl', '').replace('Sfe', 'Feature')] = value
                
                logger.info(f"Extracted product details từ __INIT_DATA__ cho {product_data['product_code']}")
                return product_data
            
            # Fallback: Parse HTML nếu không có __INIT_DATA__
            logger.info(f"Fallback to HTML parsing cho {product_url}")
            
            # 1. Lấy mã sản phẩm
            title_box = soup.find('div', class_='title-box')
            if title_box:
                title_p = title_box.find('p', class_='title')
                if title_p:
                    product_data['product_code'] = title_p.get_text(strip=True)
            
            # 2. Lấy các phần tử để tạo tên sản phẩm đầy đủ theo thứ tự:
            # Category + Model Name + Series + Brand
            
            # 1. Lấy category từ link có data-categoryon
            category_name = ''
            category_link = soup.find('a', {'data-categoryon': True})
            if category_link:
                category_span = category_link.find('span')
                if category_span:
                    category_name = category_span.get_text(strip=True)
                    product_data['category'] = category_name
            
            # Fallback: tìm category link thông thường
            if not category_name:
                category_link = soup.find('a', href=lambda x: x and '/vn/product/category/' in x)
                if category_link:
                    category_span = category_link.find('span')
                    if category_span:
                        category_name = category_span.get_text(strip=True)
                        product_data['category'] = category_name
            
            # 2. Lấy model name từ li.current
            model_name = ''
            current_li = soup.find('li', class_='current')
            if current_li:
                model_name = current_li.get_text(strip=True)
                product_data['product_name'] = model_name
            
            # 3. Lấy series name từ link có span chứa "Series"
            series_name = ''
            # Tìm trong navigation breadcrumb
            nav_items = soup.find_all('li')
            for li in nav_items:
                a_tag = li.find('a')
                if a_tag and a_tag.get('href', '').startswith('javascript:'):
                    span = a_tag.find('span')
                    if span and 'Series' in span.get_text():
                        series_name = span.get_text(strip=True)
                        product_data['series'] = series_name
                        break
            
            # Ghép tên sản phẩm theo thứ tự: Category + Model + Series + Brand
            name_parts = []
            if category_name:
                name_parts.append(category_name)
            if model_name:
                name_parts.append(model_name)
            if series_name:
                name_parts.append(series_name)
            name_parts.append('AUTONICS')
            
            product_data['full_product_name'] = ' '.join(name_parts)
            
            # 3. Lấy ảnh sản phẩm
            img_box = soup.find('div', class_='img-box')
            if img_box:
                img_tag = img_box.find('img', id='img-chg')
                if img_tag and 'src' in img_tag.attrs:
                    img_src = img_tag['src']
                    product_data['image_url'] = urljoin(self.base_url, img_src)
            
            # 4. Lấy thông số kỹ thuật
            spec_table = soup.find('table', class_='table')
            if spec_table:
                rows = spec_table.find_all('tr')
                for row in rows:
                    th = row.find('th')
                    td = row.find('td')
                    if th and td:
                        key = th.get_text(strip=True)
                        value = td.get_text(strip=True)
                        product_data['specifications'][key] = value
            
            return product_data
            
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất thông tin sản phẩm từ {product_url}: {str(e)}")
            return None
    
    def extract_product_init_data(self, html):
        """
        Extract product data từ window.__INIT_DATA__ object
        
        Args:
            html: HTML content
            
        Returns:
            dict: Product data từ __INIT_DATA__
        """
        try:
            start_marker = 'window.__INIT_DATA__ = '
            start_idx = html.find(start_marker)
            
            if start_idx == -1:
                return None
            
            json_start = start_idx + len(start_marker)
            json_end = html.find(';\n', json_start)
            if json_end == -1:
                json_end = html.find(';', json_start)
            
            if json_end == -1:
                return None
            
            json_str = html[json_start:json_end].strip()
            
            import json
            data = json.loads(json_str)
            
            return data
            
        except Exception as e:
            logger.error(f"Lỗi khi extract product __INIT_DATA__: {str(e)}")
            return None
    
    def add_white_background_to_image(self, image, target_size=(800, 800)):
        """
        Thêm nền trắng vào ảnh và resize về kích thước target
        
        Args:
            image: PIL Image object
            target_size: Kích thước mục tiêu (width, height)
            
        Returns:
            PIL Image: Ảnh đã được xử lý
        """
        # Tạo ảnh nền trắng với kích thước target
        background = Image.new('RGB', target_size, (255, 255, 255))
        
        # Convert ảnh gốc sang RGBA nếu cần
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        
        # Tính toán tỷ lệ resize để fit vào target size
        img_ratio = min(target_size[0] / image.width, target_size[1] / image.height)
        new_size = (int(image.width * img_ratio), int(image.height * img_ratio))
        
        # Resize ảnh
        image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        # Tính toán vị trí để paste ảnh vào giữa nền trắng
        x = (target_size[0] - new_size[0]) // 2
        y = (target_size[1] - new_size[1]) // 2
        
        # Paste ảnh vào nền trắng
        background.paste(image, (x, y), image if image.mode == 'RGBA' else None)
        
        return background
    
    def download_and_process_image(self, image_url, save_path, product_code):
        """
        Tải và xử lý ảnh sản phẩm
        
        Args:
            image_url: URL của ảnh
            save_path: Đường dẫn lưu ảnh
            product_code: Mã sản phẩm
            
        Returns:
            bool: True nếu thành công
        """
        try:
            # Tải ảnh
            response = self.session.get(image_url, timeout=30)
            response.raise_for_status()
            
            # Mở ảnh từ bytes
            image = Image.open(BytesIO(response.content))
            
            # Thêm nền trắng
            processed_image = self.add_white_background_to_image(image)
            
            # Tạo tên file
            filename = f"{standardize_filename(product_code)}.webp"
            full_path = os.path.join(save_path, filename)
            
            # Tạo thư mục nếu chưa tồn tại
            os.makedirs(save_path, exist_ok=True)
            
            # Lưu ảnh tạm dưới dạng PNG
            temp_path = full_path.replace('.webp', '.png')
            processed_image.save(temp_path, 'PNG', quality=95)
            
            # Chuyển đổi sang WebP
            result = WebPConverter.convert_to_webp(
                input_path=temp_path,
                output_path=full_path,
                quality=90,
                lossless=False,
                method=6
            )
            
            # Xóa file tạm
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            if result['success']:
                self.stats["images_downloaded"] += 1
                return True
            else:
                self.stats["failed_images"] += 1
                return False
                
        except Exception as e:
            logger.error(f"Lỗi khi xử lý ảnh {image_url}: {str(e)}")
            self.stats["failed_images"] += 1
            return False
    
    def create_excel_with_specifications(self, products_data, output_path):
        """
        Tạo file Excel với thông số kỹ thuật theo định dạng yêu cầu
        
        Args:
            products_data: Danh sách dữ liệu sản phẩm
            output_path: Đường dẫn file Excel
        """
        excel_data = []
        
        for product in products_data:
            # Tạo bảng HTML thông số kỹ thuật
            specs_html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;"><thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
            
            # Thêm mã sản phẩm và tên sản phẩm
            specs_html += f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{product.get("product_code", "")}</td></tr>'
            specs_html += f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{product.get("full_product_name", "")}</td></tr>'
            
            # Thêm các thông số kỹ thuật
            for key, value in product.get('specifications', {}).items():
                specs_html += f'<tr><td style="font-weight: bold;">{key}</td><td>{value}</td></tr>'
            
            # Thêm copyright
            specs_html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
            specs_html += '</tbody></table>'
            
            # Tạo URL sản phẩm và link ảnh
            domain = "https://haiphongtech.vn/product/"
            image_base = "https://haiphongtech.vn/wp-content/uploads/temp-images/"
            
            product_code = product.get("product_code", "")
            clean_code = re.sub(r'[\\/:*?"<>|,=\s]', '-', product_code)
            clean_code = re.sub(r'-+', '-', clean_code).strip('-')
            
            slug = clean_code.lower()
            image_name = clean_code.upper()
            
            row_data = {
                'Tên sản phẩm': product.get('full_product_name', ''),
                'Mã sản phẩm': product.get('product_code', ''),
                'Đường dẫn sản phẩm': domain + slug if slug else '',
                'Thông số kỹ thuật': specs_html,
                'Link ảnh đã xử lý': image_base + image_name + '.webp' if image_name else '',
                'URL gốc': product.get('url', ''),
                'Danh mục': product.get('category', ''),
                'Series': product.get('series', '')
            }
            
            excel_data.append(row_data)
        
        # Tạo DataFrame và lưu Excel
        df = pd.DataFrame(excel_data)
        
        # Tạo thư mục nếu chưa tồn tại
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Products')
            
            # Điều chỉnh độ rộng cột
            worksheet = writer.sheets['Products']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
        
        logger.info(f"Đã tạo file Excel: {output_path}")
    
    def crawl_category(self, category_url):
        """
        Cào dữ liệu từ một category URL
        
        Args:
            category_url: URL của category
            
        Returns:
            tuple: (products_data, category_name)
        """
        # Lấy tên category từ URL
        category_name = category_url.split('/')[-1]
        category_name = sanitize_folder_name(category_name)
        
        self.emit_progress(10, f"Bắt đầu cào category: {category_name}")
        
        # 1. Lấy danh sách series
        self.emit_progress(20, "Đang thu thập danh sách series...")
        series_urls = self.extract_series_from_category(category_url)
        
        if not series_urls:
            logger.warning(f"Không tìm thấy series nào trong {category_url}")
            return [], category_name
        
        # 2. Lấy danh sách sản phẩm từ tất cả series
        self.emit_progress(40, f"Đang thu thập sản phẩm từ {len(series_urls)} series...")
        all_products_data = []
        
        for i, series_url in enumerate(series_urls):
            progress = 40 + (i / len(series_urls)) * 30
            self.emit_progress(progress, f"Đang xử lý series {i+1}/{len(series_urls)}")
            
            products_data = self.extract_products_from_series(series_url)
            all_products_data.extend(products_data)
        
        # 3. Lấy thông tin chi tiết sản phẩm với đa luồng
        self.emit_progress(70, f"Đang lấy thông tin chi tiết {len(all_products_data)} sản phẩm...")
        detailed_products = []
        
        def process_product(product_data):
            try:
                details = self.extract_product_details(product_data['url'])
                if details:
                    return details
            except Exception as e:
                logger.error(f"Lỗi khi xử lý sản phẩm {product_data['url']}: {str(e)}")
            return None
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(process_product, product) for product in all_products_data]
            
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                result = future.result()
                if result:
                    detailed_products.append(result)
                    self.stats["products_processed"] += 1
                
                progress = 70 + (i / len(futures)) * 20
                self.emit_progress(progress, f"Đã xử lý {i+1}/{len(futures)} sản phẩm")
        
        return detailed_products, category_name
    
    def crawl_products(self, category_urls):
        """
        Cào dữ liệu từ danh sách category URLs
        
        Args:
            category_urls: Danh sách URL categories
            
        Returns:
            str: Đường dẫn thư mục kết quả
        """
        start_time = time.time()
        
        # Tạo thư mục kết quả với timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"AutonicsProduct_{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        
        self.emit_progress(0, f"Bắt đầu cào dữ liệu từ {len(category_urls)} categories")
        
        for i, category_url in enumerate(category_urls):
            try:
                # Cào dữ liệu category
                products_data, category_name = self.crawl_category(category_url)
                
                if not products_data:
                    continue
                
                # Tạo thư mục cho category
                category_dir = os.path.join(result_dir, category_name)
                images_dir = os.path.join(category_dir, "images")
                os.makedirs(category_dir, exist_ok=True)
                os.makedirs(images_dir, exist_ok=True)
                
                # Tải ảnh với đa luồng
                self.emit_progress(90, f"Đang tải ảnh cho category {category_name}...")
                
                def download_image(product):
                    if product.get('image_url') and product.get('product_code'):
                        return self.download_and_process_image(
                            product['image_url'],
                            images_dir,
                            product['product_code']
                        )
                    return False
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    image_futures = [executor.submit(download_image, product) for product in products_data]
                    concurrent.futures.wait(image_futures)
                
                # Tạo file Excel
                excel_path = os.path.join(category_dir, f"{category_name}.xlsx")
                self.create_excel_with_specifications(products_data, excel_path)
                
                self.stats["categories_processed"] += 1
                
            except Exception as e:
                logger.error(f"Lỗi khi xử lý category {category_url}: {str(e)}")
        
        # Hoàn thành
        end_time = time.time()
        duration = end_time - start_time
        
        self.emit_progress(100, f"Hoàn thành! Đã xử lý {self.stats['categories_processed']} categories")
        
        # Log thống kê cuối cùng
        logger.info("=== THỐNG KÊ CRAWLER AUTONICS ===")
        logger.info(f"Thời gian thực hiện: {duration:.2f} giây")
        logger.info(f"Categories đã xử lý: {self.stats['categories_processed']}")
        logger.info(f"Series tìm thấy: {self.stats['series_found']}")
        logger.info(f"Sản phẩm tìm thấy: {self.stats['products_found']}")
        logger.info(f"Sản phẩm đã xử lý: {self.stats['products_processed']}")
        logger.info(f"Ảnh đã tải: {self.stats['images_downloaded']}")
        logger.info(f"Request thất bại: {self.stats['failed_requests']}")
        logger.info(f"Ảnh thất bại: {self.stats['failed_images']}")
        
        return result_dir

if __name__ == "__main__":
    # Test crawler
    crawler = AutonicsCrawler()
    test_urls = ["https://www.autonics.com/vn/product/category/Photoelectric"]
    result_dir = crawler.crawl_products(test_urls)
    print(f"Kết quả được lưu tại: {result_dir}")