"""
Omron Product Crawler - Cào dữ liệu sản phẩm từ website industrial.omron.co.uk
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
from selenium.webdriver.common.action_chains import ActionChains

# Gemini AI imports for translation
import google.generativeai as genai

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
    """Chuẩn hóa mã sản phẩm thành tên file hợp lệ theo logic Google Apps Script"""
    # Kiểm tra add-on kit
    had_add_on_kit = re.search(r'add[\s\-]*on[\s\-]*kit', code, re.IGNORECASE)
    
    # Làm sạch chuỗi loại bỏ ghi chú coating, addon...
    clean_code = re.sub(r'\(with special coating\)', '', code, flags=re.IGNORECASE)
    clean_code = re.sub(r'\[with special coating\]', '', clean_code, flags=re.IGNORECASE)
    clean_code = re.sub(r'add[\s\-]*on[\s\-]*kit', '', clean_code, flags=re.IGNORECASE).strip()
    
    # Chuẩn hóa như standardize_filename
    def standardize(s):
        result = re.sub(r'[\\/:*?"<>|,=\s]', '-', s)  # Replace invalid chars with dash
        result = re.sub(r'-+', '-', result)  # Replace multiple dashes with single dash
        result = re.sub(r'[^a-zA-Z0-9\-_]', '', result)  # Keep only valid chars
        return result.strip('-')
    
    result = standardize(clean_code.upper())
    if had_add_on_kit:
        result += "-ADK"
    
    return result

class OmronCrawler:
    """
    Crawler chuyên dụng cho website industrial.omron.co.uk
    Cào dữ liệu cảm biến và thiết bị tự động hóa với xử lý đa luồng
    """
    
    # URL mapping để tự động sửa các category URLs phổ biến
    URL_MAPPINGS = {
        'proximity-sensors': 'inductive-sensors',
        'encoders': 'rotary-encoders', 
        'limit-switches': 'mechanical-sensors-limit-switches',
        'fiber-optic': 'fiber-optic-sensors-and-amplifiers',
        'plc': 'programmable-logic-controllers',
    }
    
    def __init__(self, output_root=None, max_workers=8, max_retries=3, socketio=None, gemini_api_key=None):
        """
        Khởi tạo OmronCrawler
        
        Args:
            output_root: Thư mục gốc để lưu kết quả
            max_workers: Số luồng tối đa
            max_retries: Số lần thử lại khi request thất bại
            socketio: Socket.IO instance để emit tiến trình
            gemini_api_key: API key cho Gemini AI translation
        """
        self.output_root = output_root or os.path.join(os.getcwd(), "output_omron")
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.socketio = socketio
        
        # Tạo thư mục output
        os.makedirs(self.output_root, exist_ok=True)
        
        # Base URLs
        self.base_url = "https://industrial.omron.co.uk"
        
        # Khởi tạo Gemini AI
        self.gemini_model = None
        api_key = gemini_api_key or os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        
        if api_key:
            try:
                genai.configure(api_key=api_key)
                self.gemini_model = genai.GenerativeModel('gemini-1.5-flash')
                logger.info("✅ Đã khởi tạo Gemini AI thành công")
            except Exception as e:
                logger.error(f"❌ Lỗi khởi tạo Gemini AI: {str(e)}")
                self.gemini_model = None
        else:
            logger.warning("⚠️ Không có Gemini API key, sẽ bỏ qua việc dịch tự động")
            logger.info("💡 Để sử dụng dịch tự động, hãy thiết lập biến môi trường GEMINI_API_KEY")
        
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
            'Accept-Language': 'en-GB,en;q=0.9,vi;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        # Cấu hình Selenium WebDriver
        self.chrome_options = Options()
        self.chrome_options.add_argument('--headless')
        self.chrome_options.add_argument('--no-sandbox')
        self.chrome_options.add_argument('--disable-dev-shm-usage')
        self.chrome_options.add_argument('--disable-gpu')
        self.chrome_options.add_argument('--window-size=1920,1080')
        self.chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        # Disable các features có thể gây overlay/popup
        self.chrome_options.add_argument('--disable-extensions')
        self.chrome_options.add_argument('--disable-plugins')
        self.chrome_options.add_argument('--disable-javascript-dialogs')
        self.chrome_options.add_argument('--disable-notifications')
        self.chrome_options.add_argument('--disable-infobars')
        self.chrome_options.add_argument('--disable-popup-blocking')
        self.chrome_options.add_argument('--disable-default-apps')
        self.chrome_options.add_argument('--no-first-run')
        self.chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        
        # Disable logging để giảm noise
        self.chrome_options.add_argument('--log-level=3')
        self.chrome_options.add_argument('--disable-logging')
        
        # Preferences để disable tutorial và overlay
        prefs = {
            "profile.default_content_setting_values": {
                "notifications": 2,
                "popups": 2
            },
            "profile.managed_default_content_settings": {
                "images": 2
            }
        }
        self.chrome_options.add_experimental_option("prefs", prefs)
        
        # Thống kê
        self.stats = {
            "categories_processed": 0,
            "series_found": 0,
            "products_found": 0,
            "products_processed": 0,
            "images_downloaded": 0,
            "failed_requests": 0,
            "failed_images": 0,
            "translations_completed": 0
        }
        
        logger.info("✅ Đã khởi tạo OmronCrawler với Selenium support và Gemini AI")
    
    def get_driver(self):
        """Tạo một WebDriver Chrome mới"""
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
    
    def setup_gemini_ai(self, api_key):
        """
        Thiết lập Gemini AI với API key
        
        Args:
            api_key: Gemini API key
            
        Returns:
            bool: True nếu thiết lập thành công
        """
        if not api_key:
            logger.error("❌ API key không được cung cấp")
            return False
            
        try:
            genai.configure(api_key=api_key)
            self.gemini_model = genai.GenerativeModel('gemini-1.5-flash')
            logger.info("✅ Đã thiết lập Gemini AI thành công")
            return True
        except Exception as e:
            logger.error(f"❌ Lỗi thiết lập Gemini AI: {str(e)}")
            self.gemini_model = None
            return False
    
    def translate_with_gemini(self, text, target_language="Vietnamese"):
        """
        Dịch text sang target_language sử dụng Gemini AI
        
        Args:
            text: Text cần dịch
            target_language: Ngôn ngữ đích
            
        Returns:
            str: Text đã được dịch
        """
        # Kiểm tra điều kiện cơ bản
        if not text or not text.strip():
            return text
            
        # Nếu không có Gemini model, log và trả về text gốc
        if not self.gemini_model:
            logger.warning("Gemini AI chưa được thiết lập - sẽ sử dụng text gốc")
            return text
        
        try:
            # Tối ưu prompt để dịch tốt hơn
            prompt = f"""Dịch đoạn text kỹ thuật sau sang tiếng Việt. 
Giữ nguyên:
- Mã sản phẩm, model number
- Đơn vị đo lường (mm, V, A, etc.)
- Số liệu kỹ thuật
- Tên thương hiệu

Chỉ dịch:
- Mô tả sản phẩm
- Thuật ngữ kỹ thuật
- Tính năng và đặc điểm

Text cần dịch: "{text}"

Trả về chỉ bản dịch tiếng Việt, không thêm giải thích:"""

            logger.debug(f"Đang dịch text: {text[:100]}...")
            response = self.gemini_model.generate_content(prompt)
            
            if response and response.text:
                translated_text = response.text.strip()
                self.stats["translations_completed"] += 1
                logger.debug(f"Dịch thành công: {text[:50]} -> {translated_text[:50]}")
                return translated_text
            else:
                logger.warning(f"Không nhận được response từ Gemini cho text: {text[:50]}...")
                return text
                
        except Exception as e:
            logger.error(f"Lỗi khi dịch với Gemini cho text '{text[:50]}...': {str(e)}")
            return text

    def fix_category_url(self, url):
        """
        Tự động sửa URL category nếu không hợp lệ
        
        Args:
            url: URL gốc
            
        Returns:
            str: URL đã được sửa (nếu cần)
        """
        if '/en/products/' not in url:
            return url
            
        # Extract category name từ URL
        category_slug = url.split('/en/products/')[-1].strip('/')
        
        # Kiểm tra trong mapping table
        if category_slug in self.URL_MAPPINGS:
            new_category = self.URL_MAPPINGS[category_slug]
            new_url = url.replace(f'/en/products/{category_slug}', f'/en/products/{new_category}')
            logger.info(f"🔄 Auto-fixed URL: {category_slug} -> {new_category}")
            self.emit_progress(0, f"URL đã được sửa", f"{category_slug} -> {new_category}")
            return new_url
            
        return url
    
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
        Trích xuất tất cả series từ trang category với Selenium
        Phân tích phần tử HTML để lấy các link series sản phẩm
        
        Args:
            category_url: URL của trang category
            
        Returns:
            list: Danh sách các series URLs và metadata
        """
        driver = None
        series_data = []
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                logger.debug(f"🔄 Thử lần {attempt + 1}/{max_retries} extract series từ {category_url}")
                
                # Tạo driver mới cho mỗi attempt
                if driver:
                    self.close_driver(driver)
                driver = self.get_driver()
                
                self.emit_progress(
                    10, 
                    f"Đang thu thập series từ category (lần thử {attempt + 1})",
                    f"URL: {category_url}"
                )
                
                # Load trang category với timeout
                driver.set_page_load_timeout(30)
                driver.get(category_url)
                
                # Đợi trang load xong với timeout ngắn hơn
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".inputgroup.shortened"))
                    )
                except TimeoutException:
                    logger.warning(f"⏰ Timeout khi đợi page load, thử fallback method")
                    # Thử lấy series bằng requests nếu Selenium timeout
                    return self.extract_series_fallback(category_url)
                
                # Tìm các series links trong fieldset.products
                series_links = driver.find_elements(By.CSS_SELECTOR, "fieldset.products a[href*='/en/products/']")
                
                if not series_links:
                    # Thử selector fallback trong inputgroup
                    series_links = driver.find_elements(By.CSS_SELECTOR, ".inputgroup a[href*='/en/products/']")
                    
                if not series_links:
                    # Thử selector legacy
                    series_links = driver.find_elements(By.CSS_SELECTOR, ".inputgroup.shortened a[href*='/en/products/']")
                
                for link_element in series_links:
                    try:
                        href = link_element.get_attribute('href')
                        series_name = link_element.text.strip()
                        
                        if href and series_name:
                            # Convert relative URL thành absolute URL
                            full_url = urljoin(self.base_url, href)
                            
                            # Tránh trùng lặp
                            if not any(s['url'] == full_url for s in series_data):
                                series_data.append({
                                    'name': series_name,
                                    'url': full_url
                                })
                                logger.debug(f"✅ Tìm thấy series: {series_name} - {full_url}")
                            
                    except Exception as e:
                        logger.warning(f"⚠️ Lỗi khi extract series link: {str(e)}")
                        continue
                
                # Nếu tìm thấy series, break khỏi retry loop
                if series_data:
                    break
                    
                # Nếu không tìm thấy series, thử lại
                if attempt < max_retries - 1:
                    logger.warning(f"🔄 Không tìm thấy series, thử lại sau 2 giây...")
                    time.sleep(2)
                    
            except Exception as e:
                logger.error(f"❌ Lỗi lần thử {attempt + 1} khi extract series từ {category_url}: {str(e)}")
                if attempt < max_retries - 1:
                    logger.info("🔄 Sẽ thử lại...")
                    time.sleep(3)
                    continue
                else:
                    logger.error("💥 Đã hết số lần thử, fallback sang requests method")
                    return self.extract_series_fallback(category_url)
            finally:
                # Đảm bảo driver được đóng sau mỗi attempt
                if driver and attempt == max_retries - 1:  # Chỉ đóng ở attempt cuối
                    self.close_driver(driver)
                    driver = None
        
        # Cleanup final driver nếu còn
        if driver:
            self.close_driver(driver)
        
        self.stats["series_found"] += len(series_data)
        logger.info(f"🎯 Tổng cộng tìm thấy {len(series_data)} series từ {category_url}")
        
        return series_data
    
    def extract_series_fallback(self, category_url):
        """
        Fallback method để extract series sử dụng requests + BeautifulSoup
        Khi Selenium gặp vấn đề với overlay hoặc dynamic content
        
        Args:
            category_url: URL của category page
            
        Returns:
            list: Danh sách series URLs và metadata
        """
        try:
            logger.info(f"🔄 Sử dụng fallback method cho category: {category_url}")
            
            html = self.get_html_content(category_url)
            if not html:
                return []
            
            soup = BeautifulSoup(html, 'html.parser')
            series_data = []
            
            # Tìm fieldset.products trước tiên
            products_fieldset = soup.find('fieldset', class_='products')
            if products_fieldset:
                # Tìm tất cả series links trong fieldset.products
                potential_links = products_fieldset.find_all('a', href=True)
                logger.info(f"🎯 Tìm thấy fieldset.products với {len(potential_links)} links")
            else:
                # Fallback: tìm tất cả links trong page
                potential_links = soup.find_all('a', href=True)
                logger.info(f"⚠️ Không tìm thấy fieldset.products, fallback với {len(potential_links)} links")
            
            for link in potential_links:
                try:
                    href = link.get('href')
                    text = link.get_text(strip=True)
                    
                    if href and text:
                        # Filter links series - ưu tiên links trong fieldset.products
                        if (('/en/products/' in href or 'products/' in href)
                            and len(text) <= 50  # Series names thường ngắn
                            and len(text) > 1
                            and not text.lower() in ['products', 'home', 'back', 'next', 'more', 'specifications', 'ordering info']
                            and href.count('/') <= 5):  # Series URLs thường ngắn hơn product URLs
                            
                            # Convert thành absolute URL
                            if href.startswith('/'):
                                full_url = urljoin(self.base_url, href)
                            else:
                                full_url = href
                            
                            # Kiểm tra không trùng lặp
                            if not any(s['url'] == full_url for s in series_data):
                                series_data.append({
                                    'name': text,
                                    'url': full_url
                                })
                                logger.debug(f"🔍 Fallback tìm thấy: {text} - {full_url}")
                        
                except Exception as e:
                    logger.debug(f"Lỗi khi xử lý link fallback: {str(e)}")
                    continue
            
            # Lọc và sort để có kết quả tốt nhất
            series_data = series_data[:20]  # Giới hạn để tránh quá nhiều false positive
            
            logger.info(f"🎯 Fallback method tìm thấy {len(series_data)} series")
            return series_data
            
        except Exception as e:
            logger.error(f"❌ Lỗi trong fallback method: {str(e)}")
            return []
    
    # Placeholder methods - sẽ được implement trong các bước tiếp theo
    def extract_products_from_series(self, series_url):
        """
        Extract tất cả products từ series với xử lý 'Show more products'
        Sử dụng Selenium để click nút "Show more products" và lấy tất cả sản phẩm
        
        Args:
            series_url: URL của series page
            
        Returns:
            list: Danh sách product URLs và metadata
        """
        driver = None
        products_data = []
        max_retries = 2
        
        for attempt in range(max_retries):
            try:
                logger.debug(f"🔄 Thử lần {attempt + 1}/{max_retries} extract products từ {series_url}")
                
                # Tạo driver mới cho mỗi attempt
                if driver:
                    self.close_driver(driver)
                driver = self.get_driver()
                
                self.emit_progress(
                    20, 
                    f"Đang thu thập sản phẩm từ series (lần thử {attempt + 1})",
                    f"URL: {series_url}"
                )
                
                # Load trang series với timeout
                driver.set_page_load_timeout(45)  # Series pages có thể nặng hơn
                driver.get(series_url)
                
                # Đợi trang load hoàn toàn
                time.sleep(3)
                
                # Đóng tutorial overlay nếu có
                try:
                    # Tìm và click overlay để đóng tutorial
                    overlay = driver.find_element(By.CSS_SELECTOR, ".introjs-overlay")
                    if overlay.is_displayed():
                        overlay.click()
                        logger.info("Đã đóng tutorial overlay")
                        time.sleep(2)
                except NoSuchElementException:
                    logger.debug("Không có tutorial overlay")
                except Exception as e:
                    logger.debug(f"Lỗi khi đóng overlay: {str(e)}")
                
                # Chờ cho Features view load và click
                try:
                    # Đợi element có thể click
                    features_view = WebDriverWait(driver, 15).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "#feature-view"))
                    )
                    
                    # Scroll đến element để đảm bảo nó visible
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", features_view)
                    time.sleep(1)
                    
                    # Thử click bằng JavaScript để bypass overlay
                    driver.execute_script("arguments[0].click();", features_view)
                    time.sleep(3)
                    
                    logger.info("Đã chuyển sang Features view")
                except TimeoutException:
                    logger.info("Không tìm thấy Features view, tiếp tục với view hiện tại")
                except Exception as e:
                    logger.warning(f"Lỗi khi click Features view: {str(e)}, thử tiếp tục")
                
                # Xử lý nút "Show more products" để load tất cả sản phẩm
                max_attempts = 5  # Giới hạn số lần click
                click_attempt = 0
                
                while click_attempt < max_attempts:
                    try:
                        # Tìm nút "Show all products" hoặc "Show more products"
                        show_more_buttons = driver.find_elements(By.CSS_SELECTOR, "a#show-all-products, a[id*='show'], a[class*='show-more']")
                        
                        if show_more_buttons:
                            button = show_more_buttons[0]
                            if button.is_displayed() and button.is_enabled():
                                # Scroll đến button
                                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                                time.sleep(1)
                                
                                # Thử click bằng JavaScript để bypass overlay
                                driver.execute_script("arguments[0].click();", button)
                                logger.info(f"Đã click 'Show more products' lần {click_attempt + 1}")
                                
                                # Đợi content load
                                time.sleep(4)
                                click_attempt += 1
                            else:
                                break
                        else:
                            logger.info("Không tìm thấy nút 'Show more products'")
                            break
                            
                    except Exception as e:
                        logger.warning(f"Lỗi khi click 'Show more products' lần {click_attempt + 1}: {str(e)}")
                        break
                
                # Tìm products trong table.details theo cấu trúc mới
                product_links = []
                
                # Thử tìm table với class details trước
                try:
                    table_selector = "table.details, table[class*='col-0'][class*='col-4'][class*='col-7'][class*='col-9']"
                    product_table = driver.find_element(By.CSS_SELECTOR, table_selector)
                    if product_table:
                        # Tìm tất cả product links trong table
                        product_links = product_table.find_elements(By.CSS_SELECTOR, "td.product-name a, .product-name a")
                        logger.info(f"🎯 Tìm thấy {len(product_links)} sản phẩm trong table.details")
                except NoSuchElementException:
                    logger.info("Không tìm thấy table.details, thử các selector khác")
                    
                # Nếu không tìm thấy trong table, thử các selector backup
                if not product_links:
                    selectors = [
                        "a[data-ga-action='Product clicked']",
                        "a[href*='/en/products/'][href*='-']",
                        ".product-item a",
                        ".product-link"
                    ]
                    
                    for selector in selectors:
                        try:
                            links = driver.find_elements(By.CSS_SELECTOR, selector)
                            if links:
                                product_links = links
                                logger.info(f"Fallback: Tìm thấy {len(links)} product links với selector: {selector}")
                                break
                        except Exception as e:
                            logger.debug(f"Lỗi với selector {selector}: {str(e)}")
                            continue
                
                for link_element in product_links:
                    try:
                        href = link_element.get_attribute('href')
                        product_name = link_element.text.strip()
                        
                        if href and product_name:
                            # Convert relative/absolute URL thành absolute URL
                            if href.startswith('/'):
                                # Absolute path như /en/products/h3dt-a1-24-240vac-dc
                                full_url = urljoin(self.base_url, href)
                            elif href.startswith('http'):
                                # Full URL
                                full_url = href
                            else:
                                # Relative path như H3DT-A1-24-240VAC-DC -> /en/products/h3dt-a1-24-240vac-dc
                                full_url = urljoin(series_url + '/', href.lower())
                            
                            # Kiểm tra URL có hợp lệ
                            if (len(product_name) > 2 and 
                                ('products' in full_url or 'omron' in full_url) and
                                len(href) > 3):  # Tránh links rỗng hoặc quá ngắn
                                
                                products_data.append({
                                    'name': product_name,
                                    'url': full_url
                                })
                                logger.debug(f"✅ Tìm thấy sản phẩm: {product_name} - {full_url}")
                            
                    except Exception as e:
                        logger.warning(f"Lỗi khi extract product link: {str(e)}")
                        continue
                
                # Nếu tìm thấy products, break khỏi retry loop
                if products_data:
                    break
                    
                # Nếu không tìm thấy products, thử lại
                if attempt < max_retries - 1:
                    logger.warning(f"🔄 Không tìm thấy products, thử lại sau 3 giây...")
                    time.sleep(3)
                    
            except Exception as e:
                logger.error(f"❌ Lỗi lần thử {attempt + 1} khi extract products từ {series_url}: {str(e)}")
                if attempt < max_retries - 1:
                    logger.info("🔄 Sẽ thử lại...")
                    time.sleep(5)
                    continue
                else:
                    logger.error("💥 Đã hết số lần thử, fallback sang requests method")
                    # Cleanup driver trước khi fallback
                    if driver:
                        self.close_driver(driver)
                        driver = None
                    products_data = self.extract_products_fallback(series_url)
                    break
            finally:
                # Đảm bảo driver được đóng sau mỗi attempt
                if driver and attempt == max_retries - 1:  # Chỉ đóng ở attempt cuối
                    self.close_driver(driver)
                    driver = None
        
        # Cleanup final driver nếu còn
        if driver:
            self.close_driver(driver)
        
        self.stats["products_found"] += len(products_data)
        logger.info(f"🎯 Tổng cộng tìm thấy {len(products_data)} sản phẩm từ {series_url}")
        
        return products_data
    
    def extract_products_fallback(self, series_url):
        """
        Fallback method để extract products sử dụng requests + BeautifulSoup
        Khi Selenium gặp vấn đề với overlay hoặc dynamic content
        
        Args:
            series_url: URL của series page
            
        Returns:
            list: Danh sách product URLs và metadata
        """
        try:
            logger.info(f"Đang thử fallback method cho {series_url}")
            
            html = self.get_html_content(series_url)
            if not html:
                return []
            
            soup = BeautifulSoup(html, 'html.parser')
            products_data = []
            
            # Tìm table.details trước tiên
            product_table = soup.find('table', class_='details')
            if not product_table:
                # Thử tìm table có các class col
                product_table = soup.find('table', class_=lambda x: x and 'col-0' in x and 'col-4' in x)
            
            if product_table:
                # Tìm products trong table
                product_rows = product_table.find_all('tr', class_='filtered')
                logger.info(f"🎯 Tìm thấy table.details với {len(product_rows)} sản phẩm")
                
                for row in product_rows:
                    try:
                        product_cell = row.find('td', class_='product-name')
                        if product_cell:
                            link = product_cell.find('a')
                            if link and link.get('href') and link.text.strip():
                                href = link.get('href')
                                text = link.text.strip()
                                
                                # Convert thành absolute URL
                                if href.startswith('/'):
                                    full_url = urljoin(self.base_url, href)
                                elif href.startswith('http'):
                                    full_url = href
                                else:
                                    full_url = urljoin(series_url + '/', href.lower())
                                
                                products_data.append({
                                    'name': text,
                                    'url': full_url
                                })
                                logger.debug(f"Table fallback tìm thấy: {text} - {full_url}")
                    except Exception as e:
                        logger.debug(f"Lỗi khi xử lý row trong table: {str(e)}")
                        continue
            else:
                # Fallback: tìm tất cả links trong page
                logger.info("⚠️ Không tìm thấy table.details, fallback tìm tất cả links")
                potential_links = soup.find_all('a', href=True)
                
                for link in potential_links:
                    try:
                        href = link.get('href')
                        text = link.get_text(strip=True)
                        
                        if href and text:
                            # Filter links sản phẩm với tiêu chí mới
                            if (len(text) > 2
                                and len(href) > 3
                                and not text.lower() in ['products', 'home', 'back', 'next', 'specifications', 'ordering info']):
                                
                                # Convert thành absolute URL
                                if href.startswith('/'):
                                    full_url = urljoin(self.base_url, href)
                                elif href.startswith('http'):
                                    full_url = href
                                else:
                                    full_url = urljoin(series_url + '/', href.lower())
                                
                                # Kiểm tra không trùng lặp
                                if not any(p['url'] == full_url for p in products_data):
                                    products_data.append({
                                        'name': text,
                                        'url': full_url
                                    })
                                    logger.debug(f"General fallback tìm thấy: {text} - {full_url}")
                        
                    except Exception as e:
                        logger.debug(f"Lỗi khi xử lý link fallback: {str(e)}")
                        continue
            
            logger.info(f"Fallback method tìm thấy {len(products_data)} sản phẩm")
            return products_data
            
        except Exception as e:
            logger.error(f"Lỗi trong fallback method: {str(e)}")
            return []
    
    def extract_product_details(self, product_url):
        """
        Extract chi tiết sản phẩm từ product page
        Lấy tên sản phẩm, mã sản phẩm, thông số kỹ thuật và ảnh sản phẩm
        
        Args:
            product_url: URL của product page
            
        Returns:
            dict: Thông tin chi tiết sản phẩm
        """
        try:
            self.emit_progress(
                50, 
                f"Đang cào thông tin sản phẩm",
                f"URL: {product_url}"
            )
            
            html = self.get_html_content(product_url)
            if not html:
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            
            product_data = {
                'product_code': '',
                'product_name': '',
                'full_product_name': '',
                'category': '',
                'series': '',
                'image_url': '',
                'specifications': {},
                'original_url': product_url
            }
            
            # 1. Lấy mã sản phẩm từ <h1>
            h1_element = soup.find('h1')
            if h1_element:
                product_data['product_code'] = h1_element.get_text(strip=True)
            
            # 2. Lấy các phần tử để tạo tên sản phẩm đầy đủ
            # Tìm breadcrumb navigation để lấy category và series
            breadcrumb_items = soup.find_all('li', class_='without-dropdown')
            
            category_name = ''
            series_name = ''
            
            for li in breadcrumb_items:
                a_tag = li.find('a')
                if a_tag and a_tag.find('span'):
                    span_text = a_tag.find('span').get_text(strip=True)
                    href = a_tag.get('href', '')
                    
                    # Xác định category (thường là link đầu tiên sau products)
                    if '/en/products/' in href and not any(char.isdigit() for char in span_text.lower()):
                        if not category_name and span_text.lower() not in ['products', 'home']:
                            category_name = span_text
                            product_data['category'] = category_name
                    
                    # Xác định series (thường là link có tên ngắn và có chữ cái + số)
                    elif len(span_text) <= 10 and any(char.isalnum() for char in span_text):
                        series_name = span_text
                        product_data['series'] = series_name
            
            # 3. Ghép tên sản phẩm theo thứ tự: Category + Product Code + Series + OMRON
            name_parts = []
            if category_name:
                name_parts.append(category_name)
            if product_data['product_code']:
                name_parts.append(product_data['product_code'])
            if series_name:
                name_parts.append(f"series {series_name}")
            name_parts.append('OMRON')
            
            english_name = ' '.join(name_parts)
            product_data['product_name'] = english_name
            
            # Dịch tên sản phẩm sang tiếng Việt sử dụng Gemini
            vietnamese_name = self.translate_with_gemini(english_name)
            product_data['full_product_name'] = vietnamese_name
            
            # 4. Lấy ảnh sản phẩm từ figure > a.image-link > img
            figure_element = soup.find('figure')
            if figure_element:
                img_link = figure_element.find('a', class_='image-link')
                if img_link:
                    img_tag = img_link.find('img')
                    if img_tag and img_tag.get('src'):
                        image_src = img_tag['src']
                        # Sử dụng URL chất lượng cao từ href nếu có
                        high_quality_url = img_link.get('href')
                        if high_quality_url:
                            product_data['image_url'] = high_quality_url
                        else:
                            product_data['image_url'] = image_src
            
            # 5. Lấy thông số kỹ thuật từ bảng Specifications
            spec_table = soup.find('table', class_='one')
            if spec_table:
                rows = spec_table.find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        key = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)
                        if key and value:
                            # Dịch key và value sang tiếng Việt
                            vietnamese_key = self.translate_with_gemini(key)
                            vietnamese_value = self.translate_with_gemini(value)
                            product_data['specifications'][vietnamese_key] = vietnamese_value
            
            return product_data
            
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất thông tin sản phẩm từ {product_url}: {str(e)}")
            return None
    
    def download_and_process_image(self, image_url, save_path, product_code):
        """
        Tải và xử lý ảnh sản phẩm, chuyển sang WebP
        Sử dụng logic tương tự Google Apps Script để tạo tên file
        
        Args:
            image_url: URL của ảnh
            save_path: Đường dẫn lưu ảnh
            product_code: Mã sản phẩm
            
        Returns:
            bool: True nếu thành công
        """
        try:
            if not image_url or not product_code:
                return False
            
            # Tải ảnh
            response = self.session.get(image_url, timeout=30)
            response.raise_for_status()
            
            # Mở ảnh từ bytes
            image = Image.open(BytesIO(response.content))
            
            # Thêm nền trắng và resize
            processed_image = self.add_white_background_to_image(image)
            
            # Tạo tên file theo logic standardize_filename
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
            
            if result:
                self.stats["images_downloaded"] += 1
                logger.info(f"✅ Đã tải và chuyển đổi ảnh: {filename}")
                return True
            else:
                self.stats["failed_images"] += 1
                logger.error(f"❌ Lỗi chuyển đổi ảnh WebP: {filename}")
                return False
                
        except Exception as e:
            self.stats["failed_images"] += 1
            logger.error(f"❌ Lỗi khi tải ảnh từ {image_url}: {str(e)}")
            return False
    
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
    
    def create_excel_with_specifications(self, products_data, excel_path):
        """
        Tạo file Excel với thông số kỹ thuật theo định dạng yêu cầu
        Tạo bảng HTML specifications theo format cụ thể của người dùng
        
        Args:
            products_data: Danh sách dữ liệu sản phẩm
            excel_path: Đường dẫn file Excel
            
        Returns:
            bool: True nếu thành công
        """
        try:
            if not products_data:
                logger.warning("Không có dữ liệu sản phẩm để xuất Excel")
                return False
            
            # Chuẩn bị dữ liệu cho DataFrame
            excel_data = []
            
            for product in products_data:
                # Tạo bảng HTML specifications theo format yêu cầu
                specs_html = self.create_specifications_table_html(product)
                
                excel_row = {
                    'Mã sản phẩm': product.get('product_code', ''),
                    'Tên sản phẩm tiếng Anh': product.get('product_name', ''),
                    'Tên sản phẩm tiếng Việt': product.get('full_product_name', ''),
                    'Category': product.get('category', ''),
                    'Series': product.get('series', ''),
                    'Link sản phẩm': product.get('original_url', ''),
                    'Link ảnh': product.get('image_url', ''),
                    'Thông số kỹ thuật HTML': specs_html,
                    'Số lượng thông số': len(product.get('specifications', {}))
                }
                excel_data.append(excel_row)
            
            # Tạo DataFrame
            df = pd.DataFrame(excel_data)
            
            # Tạo thư mục nếu chưa tồn tại
            os.makedirs(os.path.dirname(excel_path), exist_ok=True)
            
            # Xuất ra Excel với formatting
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Products')
                
                # Lấy worksheet để format
                worksheet = writer.sheets['Products']
                
                # Auto-adjust column widths
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    
                    adjusted_width = min(max_length + 2, 50)  # Giới hạn width tối đa
                    worksheet.column_dimensions[column_letter].width = adjusted_width
            
            logger.info(f"✅ Đã tạo file Excel: {excel_path} với {len(products_data)} sản phẩm")
            return True
            
        except Exception as e:
            logger.error(f"❌ Lỗi khi tạo file Excel {excel_path}: {str(e)}")
            return False
    
    def create_specifications_table_html(self, product):
        """
        Tạo bảng HTML thông số kỹ thuật theo format cụ thể
        
        Args:
            product: Dữ liệu sản phẩm
            
        Returns:
            str: HTML table string
        """
        try:
            product_code = product.get('product_code', '')
            product_name = product.get('full_product_name', '')
            specifications = product.get('specifications', {})
            
            # Bắt đầu tạo HTML table theo format yêu cầu
            html = '''<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">
<thead>
<tr style="background-color: #f2f2f2;">
<th>Thông số</th>
<th>Giá trị</th>
</tr>
</thead>
<tbody>
<tr>
<td style="font-weight: bold;">Mã sản phẩm</td>
<td>{}</td>
</tr>
<tr>
<td style="font-weight: bold;">Tên sản phẩm</td>
<td>{}</td>
</tr>'''.format(product_code, product_name)
            
            # Thêm các thông số kỹ thuật
            for key, value in specifications.items():
                html += f'''
<tr>
<td style="font-weight: bold;">{key}</td>
<td>{value}</td>
</tr>'''
            
            # Thêm Copyright ở cuối bảng
            html += '''
<tr>
<td style="font-weight: bold;">Copyright</td>
<td>Haiphongtech.vn</td>
</tr>
</tbody>
</table>'''
            
            return html
            
        except Exception as e:
            logger.error(f"Lỗi khi tạo HTML table cho sản phẩm: {str(e)}")
            return ""
    
    def crawl_products(self, category_urls):
        """
        Method chính để cào dữ liệu sản phẩm từ danh sách category URLs
        
        Args:
            category_urls: Danh sách URLs của các categories
            
        Returns:
            str: Đường dẫn thư mục chứa kết quả
        """
        start_time = time.time()
        timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
        result_dir = os.path.join(self.output_root, f"OmronProduct_{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        
        self.emit_progress(0, "Bắt đầu cào dữ liệu Omron", f"Sẽ xử lý {len(category_urls)} categories")
        
        # Process each category with retry logic
        for i, original_url in enumerate(category_urls):
            # Tự động sửa URL nếu cần
            category_url = self.fix_category_url(original_url)
            if category_url != original_url:
                self.emit_progress(5, f"URL đã được sửa", f"'{original_url.split('/')[-1]}' -> '{category_url.split('/')[-1]}'")
                logger.info(f"🔧 Fixed URL: {original_url} -> {category_url}")
            category_success = False
            max_category_retries = 2
            
            for category_attempt in range(max_category_retries):
                logger.info(f"🔄 Category attempt {category_attempt + 1}/{max_category_retries} for: {category_url}")
                category_success = self._process_single_category(
                    category_url, i, len(category_urls), result_dir
                )
                
                if category_success:
                    logger.info(f"✅ Category thành công: {category_url}")
                    break
                elif category_attempt < max_category_retries - 1:
                    logger.warning(f"⚠️ Category attempt {category_attempt + 1} thất bại, thử lại sau 10 giây...")
                    time.sleep(10)
                else:
                    logger.error(f"❌ Category thất bại hoàn toàn sau {max_category_retries} lần thử: {category_url}")
        
        # Hoàn thành
        end_time = time.time()
        duration = end_time - start_time
        
        self.emit_progress(100, f"Hoàn thành! Đã xử lý {self.stats['categories_processed']} categories")
        
        # Log thống kê cuối cùng
        logger.info("=== THỐNG KÊ CRAWLER OMRON ===")
        logger.info(f"Thời gian thực hiện: {duration:.2f} giây")
        logger.info(f"Categories đã xử lý: {self.stats['categories_processed']}")
        logger.info(f"Series tìm thấy: {self.stats['series_found']}")
        logger.info(f"Sản phẩm tìm thấy: {self.stats['products_found']}")
        logger.info(f"Sản phẩm đã xử lý: {self.stats['products_processed']}")
        logger.info(f"Ảnh đã tải: {self.stats['images_downloaded']}")
        logger.info(f"Bản dịch hoàn thành: {self.stats['translations_completed']}")
        logger.info(f"Request thất bại: {self.stats['failed_requests']}")
        logger.info(f"Ảnh thất bại: {self.stats['failed_images']}")
        
        return result_dir
    
    def _process_single_category(self, category_url, category_index, total_categories, result_dir):
        """
        Process một category với error handling và retry logic
        
        Args:
            category_url: URL của category
            category_index: Index của category trong danh sách
            total_categories: Tổng số categories
            result_dir: Thư mục chứa kết quả
            
        Returns:
            bool: True nếu thành công, False nếu thất bại
        """
        try:
            logger.info(f"🔄 Bắt đầu xử lý category {category_index+1}/{total_categories}: {category_url}")
            
            # Extract category name từ URL
            category_name = self.extract_category_name_from_url(category_url)
            category_dir = os.path.join(result_dir, sanitize_folder_name(category_name))
            os.makedirs(category_dir, exist_ok=True)
            
            # Tạo thư mục images
            images_dir = os.path.join(category_dir, "images")
            os.makedirs(images_dir, exist_ok=True)
            
            # Progress calculation cải thiện
            category_progress_base = int((category_index / total_categories) * 90)  # Reserve 10% for final steps
            
            self.emit_progress(
                category_progress_base,
                f"Đang xử lý category [{category_index+1}/{total_categories}]: {category_name}",
                f"URL: {category_url}"
            )
            
            # Lấy danh sách series
            logger.info(f"📋 Đang thu thập series từ category: {category_name}")
            series_list = self.extract_series_from_category(category_url)
            
            if not series_list:
                logger.warning(f"⚠️ Không tìm thấy series nào từ {category_url}")
                return False
            
            logger.info(f"✅ Tìm thấy {len(series_list)} series trong category: {category_name}")
            
            # Collect tất cả products từ các series với đa luồng
            all_products_data = []
            
            def process_series(series_info):
                """Process một series và trả về danh sách products"""
                try:
                    series_url = series_info['url']
                    series_name = series_info['name']
                    
                    self.emit_progress(
                        category_progress_base + 20,
                        f"Đang xử lý series: {series_name}",
                        series_url
                    )
                    
                    # Lấy danh sách products từ series
                    products_list = self.extract_products_from_series(series_url)
                    
                    if not products_list:
                        logger.warning(f"Không tìm thấy products nào từ series {series_name}")
                        return []
                    
                    # Extract chi tiết cho từng product
                    series_products = []
                    for product_info in products_list:
                        try:
                            product_details = self.extract_product_details(product_info['url'])
                            if product_details:
                                series_products.append(product_details)
                                self.stats["products_processed"] += 1
                        except Exception as e:
                            logger.error(f"Lỗi khi xử lý product {product_info['url']}: {str(e)}")
                            continue
                    
                    return series_products
                    
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý series {series_info['url']}: {str(e)}")
                    return []
            
            # Xử lý series với đa luồng
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(series_list))) as executor:
                future_to_series = {executor.submit(process_series, series): series for series in series_list}
                
                for future in concurrent.futures.as_completed(future_to_series):
                    series = future_to_series[future]
                    try:
                        series_products = future.result()
                        all_products_data.extend(series_products)
                        logger.info(f"Hoàn thành series {series['name']}: {len(series_products)} sản phẩm")
                    except Exception as e:
                        logger.error(f"Lỗi khi xử lý series {series['name']}: {str(e)}")
            
            if not all_products_data:
                logger.warning(f"Không có dữ liệu sản phẩm nào từ category {category_name}")
                return False
            
            self.emit_progress(
                category_progress_base + 50,
                f"Đang tải ảnh cho {len(all_products_data)} sản phẩm",
                f"Category: {category_name}"
            )
            
            # Tải ảnh với đa luồng
            def download_image(product):
                """Download ảnh cho một sản phẩm"""
                try:
                    if product.get('image_url') and product.get('product_code'):
                        return self.download_and_process_image(
                            product['image_url'],
                            images_dir,
                            product['product_code']
                        )
                except Exception as e:
                    logger.error(f"Lỗi khi tải ảnh cho {product.get('product_code', 'Unknown')}: {str(e)}")
                    return False
            
            # Download ảnh với đa luồng
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                image_futures = [executor.submit(download_image, product) for product in all_products_data]
                concurrent.futures.wait(image_futures)
            
            # Tạo file Excel
            excel_path = os.path.join(category_dir, f"{category_name}.xlsx")
            excel_success = self.create_excel_with_specifications(all_products_data, excel_path)
            
            if excel_success:
                logger.info(f"✅ Đã hoàn thành category: {category_name}")
                logger.info(f"📊 Tổng kết category {category_name}: {len(all_products_data)} sản phẩm, {self.stats['images_downloaded']} ảnh")
            else:
                logger.warning(f"⚠️ Hoàn thành category {category_name} nhưng không tạo được Excel")
            
            self.stats["categories_processed"] += 1
            return True
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"❌ Lỗi nghiêm trọng khi xử lý category {category_url}: {str(e)}")
            logger.error(f"Chi tiết lỗi: {error_details}")
            
            # Emit error
            self.emit_progress(
                int((category_index / total_categories) * 90),
                f"Lỗi khi xử lý category {category_index+1}/{total_categories}",
                f"Lỗi: {str(e)}"
            )
            
            # Đảm bảo cleanup resources nếu có
            try:
                # Force garbage collection để clean up memory
                import gc
                gc.collect()
            except:
                pass
            
            return False
    
    def extract_category_name_from_url(self, url):
        """Extract tên category từ URL"""
        try:
            # Parse URL để lấy phần cuối
            path = urlparse(url).path
            # Lấy phần cuối sau dấu '/' cuối cùng
            category_name = path.strip('/').split('/')[-1]
            # Chuyển thành title case và thay thế dấu gạch ngang
            return category_name.replace('-', ' ').title()
        except Exception as e:
            logger.warning(f"Không thể extract category name từ {url}: {str(e)}")
            return "Unknown_Category"

if __name__ == "__main__":
    # Test crawler
    crawler = OmronCrawler()
    test_urls = ["https://industrial.omron.co.uk/en/products/photoelectric-sensors"]
    result_dir = crawler.crawl_products(test_urls)
    print(f"Kết quả được lưu tại: {result_dir}")