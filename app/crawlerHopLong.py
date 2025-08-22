"""
HopLong Product Crawler - Cào dữ liệu sản phẩm từ hoplongtech.com
Yêu cầu: Lấy Tên sản phẩm, Mã sản phẩm, Thông số kỹ thuật (toàn bộ), Hãng. 
- Dùng Selenium để load danh mục, áp dụng bộ lọc hãng
- Đa luồng khi cào chi tiết sản phẩm bằng requests
- Xuất Excel theo Danh mục + Hãng: "<Danh mục> <Hãng>.xlsx"
- Thêm hàng Copyright vào cuối mỗi bảng thông số kỹ thuật
"""

import os
import re
import time
import json
import logging
import concurrent.futures
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import pandas as pd

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

from app import utils, socketio


logger = logging.getLogger(__name__)


class HopLongCrawler:
    """Crawler dành cho hoplongtech.com"""

    def __init__(self, output_root: str | None = None, max_workers: int = 10, socketio_instance=None):
        self.base_url = "https://hoplongtech.com"
        self.output_root = output_root or os.path.join(os.getcwd(), "output_hoplong")
        os.makedirs(self.output_root, exist_ok=True)

        self.max_workers = max_workers
        self.socketio = socketio_instance or socketio

        # requests session - enhanced với connection pooling và retry
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none'
        })
        
        # Connection pooling và retry configuration
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            method_whitelist=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # selenium options - enhanced for stability
        self.chrome_options = Options()
        self.chrome_options.add_argument('--headless')
        self.chrome_options.add_argument('--no-sandbox')
        self.chrome_options.add_argument('--disable-dev-shm-usage')
        self.chrome_options.add_argument('--window-size=1920,1080')
        self.chrome_options.add_argument('--disable-gpu')
        self.chrome_options.add_argument('--log-level=3')
        # Anti-detection và stability flags
        self.chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        self.chrome_options.add_argument('--disable-extensions')
        self.chrome_options.add_argument('--disable-plugins')
        # NOTE: Bỏ --disable-javascript vì cần JavaScript cho Alpine.js
        # self.chrome_options.add_argument('--disable-images')  # Keep images for better interaction
        self.chrome_options.add_argument('--no-first-run')
        self.chrome_options.add_argument('--no-default-browser-check')
        self.chrome_options.add_argument('--disable-web-security')
        self.chrome_options.add_argument('--allow-running-insecure-content')
        self.chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        # Alpine.js support
        self.chrome_options.add_argument('--enable-javascript')
        self.chrome_options.add_argument('--allow-scripts')
        # Network optimization
        self.chrome_options.add_argument('--aggressive-cache-discard')
        self.chrome_options.add_argument('--memory-pressure-off')
        # Additional User-Agent để tránh detection
        self.chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        self.chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        self.chrome_options.add_experimental_option('useAutomationExtension', False)

        # stats
        self.stats = {
            "categories_processed": 0,
            "brands_processed": 0,
            "products_found": 0,
            "products_processed": 0,
            "errors": 0,
        }

    def emit_progress(self, percent: int | float, message: str, detail: str = "") -> None:
        data = {'percent': percent, 'message': message, 'detail': detail}
        try:
            if self.socketio:
                self.socketio.emit('progress_update', data)
            else:
                print(f"[{percent}%] {message} - {detail}")
        except Exception:
            pass

    def get_driver(self, retries: int = 3):
        """Khởi tạo WebDriver với retry logic và improved error handling"""
        def _create_driver():
            try:
                driver = webdriver.Chrome(options=self.chrome_options)
                driver.implicitly_wait(10)
                # Set timeouts
                driver.set_page_load_timeout(60)
                driver.set_script_timeout(30)
                
                # Anti-detection script
                driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                
                return driver
            except Exception as e:
                logger.error(f"Lỗi khởi tạo WebDriver: {e}")
                raise
        
        try:
            return self.retry_with_backoff(_create_driver, max_retries=retries, base_delay=2.0)
        except Exception as e:
            logger.error(f"Không thể khởi tạo WebDriver sau {retries} lần thử: {e}")
            raise

    def close_driver(self, driver):
        try:
            driver.quit()
        except Exception:
            pass
    
    def check_website_health(self, url: str, timeout: int = 15) -> bool:
        """
        Kiểm tra kết nối đến website trước khi thực hiện operations
        """
        try:
            response = self.session.head(url, timeout=timeout)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Website health check thất bại cho {url}: {e}")
            return False
    
    def retry_with_backoff(self, func, max_retries: int = 3, base_delay: float = 1.0):
        """
        Retry decorator với exponential backoff
        """
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                
                delay = base_delay * (2 ** attempt)
                error_type = type(e).__name__
                logger.warning(f"Attempt {attempt + 1} thất bại ({error_type}): {e}. Thử lại sau {delay}s...")
                
                # Emit progress cho user biết đang retry
                if hasattr(self, 'emit_progress'):
                    self.emit_progress(
                        0, 
                        f"Lỗi kết nối, đang thử lại...", 
                        f"Lần thử {attempt + 1}/{max_retries}"
                    )
                
                time.sleep(delay)
        
        raise Exception(f"Thất bại sau {max_retries} lần thử")

    # ======= CATEGORY / BRAND HELPERS =======
    def fetch_categories_via_selenium(self) -> list[dict]:
        """Trả về danh sách danh mục ở trang chủ: [{name, url}]"""
        driver = self.get_driver()
        try:
            driver.set_page_load_timeout(40)
            driver.get(self.base_url)
            # Thử selector chính
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, '.category-navigation__list ul li a'))
                )
                candidates = driver.find_elements(By.CSS_SELECTOR, '.category-navigation__list ul li a')
            except Exception:
                # Thử selector dự phòng: tất cả link category
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href^="/category/"]'))
                )
                candidates = driver.find_elements(By.CSS_SELECTOR, 'a[href^="/category/"]')

            results = []
            for a in candidates:
                try:
                    href = a.get_attribute('href') or ''
                    text = (a.text or '').strip()
                    if href and text:
                        if href.startswith('/'):
                            href = urljoin(self.base_url, href)
                        # Chỉ lấy đường dẫn category
                        if '/category/' in href:
                            results.append({"name": text, "url": href})
                except Exception:
                    continue
            # unique by url
            seen = set()
            unique = []
            for item in results:
                if item['url'] not in seen:
                    unique.append(item)
                    seen.add(item['url'])
            if unique:
                return unique
            # Fallback lần cuối bằng requests
            return self.fetch_categories_via_requests()
        except Exception:
            # Fallback requests nếu có bất kỳ lỗi nào
            return self.fetch_categories_via_requests()
        finally:
            self.close_driver(driver)

    def fetch_categories_via_requests(self) -> list[dict]:
        """Fallback: lấy danh mục từ trang /category bằng requests + BeautifulSoup."""
        try:
            url = urljoin(self.base_url, '/category')
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            results: list[dict] = []
            # Lấy tất cả link category hợp lệ
            for a in soup.select('a[href^="/category/"]'):
                href = a.get('href') or ''
                text = (a.get_text() or '').strip()
                if not href or not text:
                    continue
                if href.startswith('/'):
                    href = urljoin(self.base_url, href)
                # tránh các link rỗng, lặp
                if '/category/' in href and len(text) >= 2:
                    results.append({"name": text, "url": href})
            # unique
            seen = set()
            unique = []
            for item in results:
                if item['url'] not in seen:
                    unique.append(item)
                    seen.add(item['url'])
            return unique
        except Exception as e:
            logger.error(f"Fallback categories via requests lỗi: {e}")
            return []

    def fetch_brands_for_category(self, category_url: str) -> list[str]:
        """STRICT brand extraction - Chỉ lấy brands thực sự có trên trang."""
        driver = None
        brands: list[str] = []
        
        logger.info(f"🔍 FETCHING BRANDS for category: {category_url}")
        
        # Kiểm tra website health trước khi dùng Selenium
        if not self.check_website_health(category_url):
            logger.warning(f"Website không sẵn sàng, chuyển sang fallback method")
            return self.fetch_brands_via_requests(category_url)
        
        def _fetch_with_selenium():
            nonlocal driver, brands
            driver = self.get_driver()
            driver.get(category_url)
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
            time.sleep(1.5)
            
            # Extract brands và validation
            extracted_brands = self._extract_brands_from_page(driver)
            
            # STRICT validation - check only valid brands for this category
            logger.info(f"📋 Raw extracted brands: {extracted_brands}")
            
            # Filter out invalid brands và chỉ giữ brands có trên trang
            valid_brands = []
            for brand in extracted_brands:
                # Basic validation
                if (brand and len(brand.strip()) >= 2 and 
                    not any(word in brand.lower() for word in ['chọn', 'tìm', 'filter', 'search', 'all'])):
                    valid_brands.append(brand.strip())
            
            logger.info(f"✅ VALID brands after filtering: {valid_brands}")
            return valid_brands
        
        try:
            brands = self.retry_with_backoff(_fetch_with_selenium, max_retries=3, base_delay=2.0)
            
            # Final validation check
            if brands:
                logger.info(f"🎯 FINAL BRANDS for category: {brands}")
                # Đảm bảo chỉ trả về brands thực sự có trên trang
                return [b for b in brands if b and len(b.strip()) >= 2][:10]  # Limit to 10 brands max
            else:
                logger.warning("⚠️ No brands found with Selenium, trying fallback...")
                return self.fetch_brands_via_requests(category_url)
                
        except WebDriverException as e:
            if "ERR_CONNECTION_RESET" in str(e) or "net::" in str(e):
                logger.error(f"Lỗi kết nối mạng Selenium (ERR_CONNECTION_RESET): {e}")
                self.emit_progress(0, "Lỗi kết nối", "Chuyển sang phương pháp backup...")
            else:
                logger.error(f"Lỗi WebDriver: {e}")
            return self.fetch_brands_via_requests(category_url)
        except Exception as e:
            logger.error(f"Lỗi không xác định khi lấy brands bằng Selenium: {e}")
            return self.fetch_brands_via_requests(category_url)
        finally:
            if driver:
                self.close_driver(driver)
    
    def _extract_brands_from_page(self, driver) -> list[str]:
        """Trích xuất brands từ trang đã load với Alpine.js support"""
        brands: list[str] = []

        # Step 1: Mở filter panel 
        self._open_brand_filter_panel(driver)
        time.sleep(1.0)  # Wait for Alpine.js animations

        # Step 2: Wait for dropdown options to be visible
        try:
            # Wait for filter list options to appear với Alpine.js
            WebDriverWait(driver, 10).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "ul.filter-list__options li") or
                         d.find_elements(By.XPATH, "//div[contains(@class,'filter-box__item')]//li")
            )
            time.sleep(0.5)
        except Exception:
            logger.warning("Timeout waiting for brand filter options")

        # Step 3: Parse brands từ new Alpine.js structure
        selectors_to_try = [
            # New Alpine.js selectors
            {
                "name": "Alpine.js filter-list__options",
                "xpath": "//ul[contains(@class,'filter-list__options')]//li[contains(@wire:click, 'brand')]"
            },
            {
                "name": "Alpine.js filter-box__item li",
                "xpath": "//div[contains(@class,'filter-box__item')]//li[text()]"
            },
            # Old selectors fallback
            {
                "name": "Old filter structure",
                "xpath": "//div[contains(@class,'filter-list__button')]//span[contains(.,'Chọn hãng sản xuất')]/ancestor::*[contains(@class,'filter-list__button')][1]/following-sibling::*//li"
            },
            {
                "name": "General filter list",
                "xpath": "//ul[contains(@class,'filter') or contains(@class,'list')]//li[text()]"
            }
        ]

        for selector_info in selectors_to_try:
            try:
                nodes = driver.find_elements(By.XPATH, selector_info["xpath"])
                logger.debug(f"Tìm thấy {len(nodes)} nodes với selector: {selector_info['name']}")
                
                for node in nodes:
                    brand_text = (node.text or '').strip()
                    if brand_text:
                        # Clean up brand text (remove extra content)
                        brand_text = brand_text.split('\n')[0].strip()  # Take first line only
                        
                        # Validate brand name
                        if (2 <= len(brand_text) <= 40 and 
                            'Hãng' not in brand_text and 
                            'Tìm kiếm' not in brand_text and
                            'Chọn' not in brand_text and
                            brand_text not in brands):
                            brands.append(brand_text)
                            logger.debug(f"Added brand: {brand_text}")
                
                if brands:
                    logger.info(f"Successfully extracted {len(brands)} brands using {selector_info['name']}")
                    break
                    
            except Exception as e:
                logger.debug(f"Selector {selector_info['name']} failed: {e}")
                continue

        # Step 4: Fallback - try to get any visible text elements
        if not brands:
            logger.warning("No brands found with primary methods, trying fallback...")
            try:
                # Look for any li elements with brand-like text
                all_li = driver.find_elements(By.TAG_NAME, "li")
                for li in all_li:
                    if li.is_displayed():
                        text = (li.text or '').strip()
                        # Check if it looks like a brand name
                        if (2 <= len(text) <= 40 and 
                            text.isalpha() and 
                            'Hãng' not in text and 
                            'Tìm kiếm' not in text and
                            text not in brands):
                            brands.append(text)
                            if len(brands) >= 10:  # Limit fallback results
                                break
            except Exception as e:
                logger.debug(f"Fallback extraction failed: {e}")

        logger.info(f"Final extracted brands: {brands}")
        return brands

    def fetch_brands_via_requests(self, category_url: str) -> list[str]:
        """Fallback: cố gắng tìm danh sách hãng từ HTML (category hoặc /brands)."""
        brands: list[str] = []
        
        def _fetch_brands_from_category():
            resp = self.session.get(category_url, timeout=45, allow_redirects=True)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, 'html.parser')
        
        try:
            # 1) thử ngay trên trang danh mục với retry
            soup = self.retry_with_backoff(_fetch_brands_from_category, max_retries=3, base_delay=1.5)

            # tìm cụm gần chữ 'Chọn hãng sản xuất'
            candidates = []
            for el in soup.find_all(text=re.compile(r'Chọn\s*hãng\s*sản\s*xuất', re.IGNORECASE)):
                parent = el.parent
                # lấy các label/span/li gần đó
                for lbl in parent.find_all_next(['label', 'span', 'li'], limit=200):
                    txt = lbl.get_text(strip=True)
                    if txt:
                        candidates.append(txt)
            # lọc
            for t in candidates:
                if 2 <= len(t) <= 40 and 'Hãng' not in t and 'Tìm kiếm' not in t:
                    if t not in brands:
                        brands.append(t)

            if brands:
                return brands

            # 2) fallback toàn site brands
            def _fetch_from_brands_page():
                brands_url = urljoin(self.base_url, '/brands')
                resp2 = self.session.get(brands_url, timeout=45, allow_redirects=True)
                resp2.raise_for_status()
                return BeautifulSoup(resp2.text, 'html.parser')
            
            try:
                soup2 = self.retry_with_backoff(_fetch_from_brands_page, max_retries=2, base_delay=1.0)
                names = []
                for a in soup2.select('a[href^="/brands/"], a[href*="/brands/"]'):
                    txt = (a.get_text() or '').strip()
                    if txt:
                        names.append(txt)
                for t in names:
                    if 2 <= len(t) <= 40 and t not in brands:
                        brands.append(t)
            except Exception as fallback_error:
                logger.warning(f"Fallback brands page cũng thất bại: {fallback_error}")
            
            # giới hạn để UI hiển thị nhẹ
            return brands[:100]
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Lỗi kết nối requests khi lấy brands: {e}")
            self.emit_progress(0, "Lỗi kết nối", "Không thể kết nối đến website")
            return []
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout khi lấy brands: {e}")
            self.emit_progress(0, "Hết thời gian chờ", "Website phản hồi chậm")
            return []
        except Exception as e:
            logger.error(f"Fallback brands via requests lỗi: {e}")
            return brands

    def fetch_subcategories_for_category(self, category_url: str) -> list[dict]:
        """
        Lấy danh sách danh mục con từ trang danh mục chính
        Trả về [{name, url, product_count}]
        """
        def _fetch_with_requests():
            resp = self.session.get(category_url, timeout=45, allow_redirects=True)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, 'html.parser')
        
        subcategories: list[dict] = []
        try:
            # Sử dụng requests để lấy HTML
            soup = self.retry_with_backoff(_fetch_with_requests, max_retries=3, base_delay=1.5)
            
            # Tìm container chứa subcategories 
            cate_box = soup.select_one('div.cate-box.flex')
            if not cate_box:
                logger.warning(f"Không tìm thấy cate-box container trong {category_url}")
                return []
            
            # Parse từng subcategory item
            for item in cate_box.select('div.cate-list__item.flex-center-left'):
                try:
                    # Lấy link và title từ thẻ a
                    link_el = item.select_one('div.cate-name a')
                    if not link_el:
                        continue
                        
                    href = link_el.get('href', '').strip()
                    title = link_el.get('title', '').strip()
                    
                    if not href or not title:
                        continue
                    
                    # Đảm bảo URL đầy đủ
                    if href.startswith('/'):
                        href = urljoin(self.base_url, href)
                    
                    # Lấy số lượng sản phẩm
                    count_el = item.select_one('div.cate-name span')
                    product_count = 0
                    if count_el:
                        count_text = count_el.get_text(strip=True)
                        # Parse số từ text như "(8 Sản phẩm)"
                        import re
                        match = re.search(r'\((\d+)', count_text)
                        if match:
                            product_count = int(match.group(1))
                    
                    subcategories.append({
                        'name': title,
                        'url': href,
                        'product_count': product_count
                    })
                    
                except Exception as e:
                    logger.debug(f"Lỗi parse subcategory item: {e}")
                    continue
            
            logger.info(f"Tìm thấy {len(subcategories)} subcategories từ {category_url}")
            return subcategories
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Lỗi kết nối khi lấy subcategories: {e}")
            self.emit_progress(0, "Lỗi kết nối", "Không thể kết nối để lấy danh mục con")
            return []
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout khi lấy subcategories: {e}")
            self.emit_progress(0, "Hết thời gian chờ", "Website phản hồi chậm")
            return []
        except Exception as e:
            logger.error(f"Lỗi khi lấy subcategories từ {category_url}: {e}")
            return []

    def _open_brand_filter_panel(self, driver) -> None:
        """Click để mở phần 'Chọn hãng sản xuất' trong filter với Alpine.js support."""
        try:
            # Step 1: Click vào nút "Bộ lọc" chính để mở filter panel
            try:
                filter_btn = driver.find_element(By.CSS_SELECTOR, "div.filter-btn.responsive")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", filter_btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", filter_btn)
                logger.info("Đã click vào nút Bộ lọc chính")
                time.sleep(1.0)  # Wait for Alpine.js to show panel
            except Exception as e:
                logger.debug(f"Không tìm thấy filter button chính: {e}")
                # Fallback: thử old selector
                try:
                    filter_toggle = driver.find_element(By.XPATH, "//*[normalize-space()='Bộ lọc' or contains(.,'Bộ lọc')]")
                    driver.execute_script("arguments[0].click();", filter_toggle)
                    time.sleep(0.5)
                except Exception:
                    pass

            # Step 2: Click vào "Chọn hãng sản xuất" trong filter panel
            selectors_to_try = [
                # New selectors cho Alpine.js structure
                "//div[contains(@class,'filter-box__item')]//h4[contains(.,'Hãng sản xuất')]/following-sibling::*//span[contains(.,'Chọn hãng sản xuất')]",
                "//div[contains(@class,'filter-box__item')]//span[contains(.,'Chọn hãng sản xuất')]",
                # Old selectors fallback
                "//div[contains(@class,'filter-list__button')]//span[contains(.,'Chọn hãng sản xuất')]",
                "//*[contains(.,'Chọn hãng sản xuất')]"
            ]
            
            for selector in selectors_to_try:
                try:
                    button = driver.find_element(By.XPATH, selector)
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", button)
                    logger.info(f"Đã click vào 'Chọn hãng sản xuất' với selector: {selector}")
                    time.sleep(0.8)  # Wait for Alpine.js dropdown - optimized
                    return
                except Exception:
                    continue
            
            logger.warning("Không thể click vào 'Chọn hãng sản xuất'")
            
        except Exception as e:
            logger.error(f"Lỗi khi mở brand filter panel: {e}")

    def _apply_brand_filter(self, driver, brand_name: str) -> bool:
        """STRICT brand filter - Áp dụng filter theo brand với smart waits."""
        logger.info(f"🎯 APPLYING STRICT BRAND FILTER: {brand_name}")
        
        try:
            # Step 1: Clear existing filters trước khi áp dụng
            try:
                clear_buttons = driver.find_elements(By.CSS_SELECTOR, '.filter-selected .button-selected .close, .filter-selected .remove, .clear-filter')
                for btn in clear_buttons:
                    try:
                        driver.execute_script("arguments[0].click();", btn)
                        # OPTIMIZED: Smart wait instead of fixed sleep
                        WebDriverWait(driver, 3).until(
                            lambda d: not d.find_elements(By.CSS_SELECTOR, '.filter-selected .button-selected')
                        )
                        logger.info("🧹 Cleared existing filter")
                    except:
                        pass
            except:
                pass

            # Step 2: mở panel chọn hãng với smart wait
            self._open_brand_filter_panel(driver)
            # OPTIMIZED: Wait for panel to be actually visible instead of fixed time
            try:
                WebDriverWait(driver, 5).until(
                    EC.any_of(
                        EC.visibility_of_element_located((By.XPATH, "//div[contains(@class,'filter-box__item')]//li")),
                        EC.visibility_of_element_located((By.XPATH, "//ul[contains(@class,'filter-list__options')]//li"))
                    )
                )
            except TimeoutException:
                logger.warning("Panel might not be visible, continuing anyway")
                time.sleep(0.5)  # Minimal fallback wait
            
            # Step 3: Tìm và click brand với EXACT match only
            selectors_to_try = [
                # EXACT match selectors only - NO partial match
                f"//div[contains(@class,'filter-box__item')]//li[normalize-space()='{brand_name}']",
                f"//ul[contains(@class,'filter-list__options')]//li[normalize-space()='{brand_name}']",
                f"//*[self::label or self::span][normalize-space()='{brand_name}']",
            ]
            
            item = None
            used_selector = None
            
            # Debug: Log available brands first
            try:
                all_brand_elements = driver.find_elements(By.XPATH, "//div[contains(@class,'filter-box__item')]//li | //ul[contains(@class,'filter-list__options')]//li")
                available_brands = [elem.text.strip() for elem in all_brand_elements if elem.is_displayed() and elem.text.strip()]
                logger.info(f"📋 Available brands on page: {available_brands}")
                
                if brand_name not in available_brands:
                    logger.error(f"❌ BRAND '{brand_name}' NOT AVAILABLE on page! Available: {available_brands}")
                    return False
            except Exception as e:
                logger.warning(f"Cannot get available brands: {e}")
            
            for selector in selectors_to_try:
                try:
                    elements = driver.find_elements(By.XPATH, selector)
                    logger.debug(f"Selector '{selector}' tìm thấy {len(elements)} elements")
                    
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            element_text = element.text.strip()
                            
                            # STRICT EXACT MATCH ONLY
                            if element_text.lower() == brand_name.lower():
                                item = element
                                used_selector = selector
                                logger.info(f"✅ EXACT MATCH found for brand '{brand_name}' using selector: {selector}")
                                break
                            else:
                                logger.debug(f"❌ REJECTED element text: '{element_text}' (not exact match)")
                    
                    if item:
                        break
                        
                except Exception as e:
                    logger.debug(f"Selector '{selector}' failed: {e}")
                    continue
            
            if not item:
                logger.error(f"❌ CRITICAL: Cannot find EXACT MATCH element for brand '{brand_name}'")
                return False
            
            # Step 4: Click element với verification
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", item)
                # OPTIMIZED: Brief wait for scroll completion
                time.sleep(0.2)
                driver.execute_script("arguments[0].click();", item)
                logger.info(f"🖱️ Clicked brand '{brand_name}' element")
            except Exception as e:
                logger.error(f"❌ Failed to click brand element: {e}")
                return False
                
            # OPTIMIZED: Smart wait for filter application instead of fixed 2.0s
            try:
                WebDriverWait(driver, 8).until(
                    EC.any_of(
                        EC.presence_of_element_located((By.CSS_SELECTOR, '.filter-selected .button-selected span')),
                        EC.presence_of_element_located((By.CSS_SELECTOR, '.filter-selected .chip span')),
                        EC.url_changes(driver.current_url)
                    )
                )
                logger.info("✅ Filter application detected")
            except TimeoutException:
                logger.warning("Filter application timeout, continuing with verification")
                time.sleep(1.0)  # Minimal fallback

            # Step 5: STRICT verification - kiểm tra filter đã được áp dụng
            verification_passed = False
            try:
                # Check selected chips
                chips = driver.find_elements(By.CSS_SELECTOR, '.filter-selected .button-selected span, .filter-selected .chip span, .active-filter span')
                selected_chips = [c.text.strip() for c in chips if c.text.strip()]
                logger.info(f"🔍 Selected filter chips: {selected_chips}")
                
                # EXACT match verification
                verification_passed = any(brand_name.lower() == chip.lower() for chip in selected_chips)
                
                if verification_passed:
                    logger.info(f"✅ BRAND FILTER VERIFIED: '{brand_name}' is active")
                else:
                    logger.error(f"❌ BRAND FILTER VERIFICATION FAILED: '{brand_name}' not in active chips: {selected_chips}")
                    
            except Exception as e:
                logger.error(f"❌ Brand filter verification failed: {e}")
                verification_passed = False

            # Step 6: Additional verification - check URL or page content
            if verification_passed:
                try:
                    current_url = driver.current_url
                    if 'filter' in current_url or '?' in current_url:
                        logger.info(f"🔗 URL indicates filter applied: {current_url}")
                    
                    # OPTIMIZED: Smart wait for filtered results instead of fixed 1.5s
                    try:
                        WebDriverWait(driver, 5).until(
                            EC.any_of(
                                EC.staleness_of(driver.find_element(By.TAG_NAME, "body")),
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-list__item"))
                            )
                        )
                        logger.info("📦 Filtered results loaded")
                    except TimeoutException:
                        logger.debug("Results loading timeout, using minimal wait")
                        time.sleep(0.5)
                    
                except Exception as e:
                    logger.debug(f"URL verification failed: {e}")
            
            return verification_passed
            
        except Exception as e:
            logger.error(f"❌ CRITICAL ERROR in _apply_brand_filter for '{brand_name}': {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def _collect_product_links(self, driver) -> list[str]:
        """Thu thập toàn bộ link sản phẩm (có thể phải scroll/pagination)."""
        links: list[str] = []

        def snapshot() -> set[str]:
            result = set()
            
            # Selectors dựa trên HTML structure thực tế
            selectors_to_try = [
                # Chính xác từ HTML user cung cấp
                ".product-list__item .thumbnail a",     # Main selector từ HTML
                ".product-list__item .content h3 a",   # Alternative từ content
                ".product-list__item a[href*='/products/']",  # Direct products links
                "a[href^='/products/']",                # Links bắt đầu /products/
                "a[href*='/products/']",                # Links chứa /products/
                ".grid-list a[href*='/products/']",     # Trong grid-list container
                
                # Fallback patterns  
                "a[href^='/product/']",         # Original pattern
                "a[href*='/product/']",         # Contains /product/
                "a[href^='/san-pham/']",        # Vietnamese pattern
                "a[href*='/san-pham/']",        # Contains /san-pham/
                ".product-item a",              # Product item links
                ".product-card a",              # Product card links  
                ".product-list a",              # Product list links
                "a.product-link",               # Product link class
                ".col-product a",               # Column product links
                ".grid-item a",                 # Grid item links
                ".list-item a",                 # List item links
                "div[class*='product'] a",      # Divs with product class
            ]
            
            for selector in selectors_to_try:
                try:
                    items = driver.find_elements(By.CSS_SELECTOR, selector)
                    logger.debug(f"🔍 Selector '{selector}' tìm thấy {len(items)} elements")
                    
                    for a in items:
                        href = a.get_attribute('href') or ''
                        title = a.get_attribute('title') or a.text or ''
                        
                        if href:
                            # Optimize logging - chỉ log khi debug mode
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug(f"  Found link: {href} | Title: {title[:50]}")
                            
                            # Validate product URL patterns - ưu tiên /products/
                            is_valid = False
                            if '/products/' in href:  # Priority pattern
                                is_valid = True
                            elif any(pattern in href for pattern in ['/product/', '/san-pham/']):
                                is_valid = True
                            elif any(keyword in href.lower() for keyword in ['item', 'detail', 'view']) and 'hoplongtech.com' in href:
                                is_valid = True
                                
                            if is_valid:
                                # Ensure full URL và chính xác domain
                                if href.startswith('/'):
                                    href = urljoin(self.base_url, href)
                                elif not href.startswith('http'):
                                    continue
                                
                                # Chỉ lấy links từ hoplongtech.com
                                if 'hoplongtech.com' in href:
                                    result.add(href)
                                    if logger.isEnabledFor(logging.DEBUG):
                                        logger.debug(f"    ✅ Added to result set")
                                elif logger.isEnabledFor(logging.DEBUG):
                                    logger.debug(f"    ❌ Skipped - wrong domain: {href}")
                                    
                except Exception as e:
                    logger.debug(f"❌ Selector '{selector}' failed: {e}")
                    continue
            
            # Enhanced fallback với detailed logging
            if not result:
                logger.warning("🔄 Không tìm thấy product links với selectors, thử comprehensive fallback...")
                try:
                    all_links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
                    logger.info(f"🔍 Analyzing {len(all_links)} total links...")
                    
                    sample_links = []
                    for i, a in enumerate(all_links):
                        href = a.get_attribute('href') or ''
                        if href:
                            if i < 10:  # Log first 10 for debugging
                                sample_links.append(href)
                            
                            if (any(pattern in href for pattern in ['/products/', '/product/', '/san-pham/']) and 
                                'hoplongtech.com' in href):
                                if href.startswith('/'):
                                    href = urljoin(self.base_url, href)
                                result.add(href)
                    
                    logger.debug(f"Sample links found: {sample_links}")
                            
                except Exception as e:
                    logger.error(f"❌ Comprehensive fallback failed: {e}")
            
            logger.info(f"📦 Total unique product links found: {len(result)}")
            if result:
                logger.info(f"✅ Sample valid product links: {list(result)[:5]}")
            else:
                logger.warning("❌ No product links found - may need to debug page structure")
            
            return result

        seen = snapshot()
        links = list(seen)
        logger.info(f"🔍 Initial scan found {len(links)} product links")

        # OPTIMIZED: Smart scroll với dynamic detection
        if links:  # Only scroll if we found some links initially
            logger.info("🔄 Smart scrolling to load more products...")
            stagnant_rounds = 0
            last_scroll_position = 0
            
            for scroll_round in range(8):  # Reduced from 10 to 8
                # Get current scroll position
                current_position = driver.execute_script("return window.pageYOffset;")
                
                # Smart scroll with smooth scrolling
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                
                # OPTIMIZED: Adaptive wait based on content loading
                try:
                    # Wait for scroll completion or new content
                    WebDriverWait(driver, 3).until(
                        lambda d: d.execute_script("return window.pageYOffset;") > current_position
                    )
                    # Brief additional wait for dynamic content
                    time.sleep(0.4)  # Reduced from 0.8s
                except TimeoutException:
                    time.sleep(0.6)  # Fallback wait
                
                now = snapshot()
                if len(now) > len(seen):
                    seen = now
                    links = list(seen)
                    stagnant_rounds = 0
                    logger.debug(f"Scroll round {scroll_round + 1}: Found {len(links)} total links (+{len(now) - len(links)} new)")
                else:
                    stagnant_rounds += 1
                
                # Early exit if no progress for 2 rounds
                if stagnant_rounds >= 2:
                    logger.info("🛑 Stopped scrolling - no new links detected")
                    break
                    
                # Check if we've reached the bottom
                new_position = driver.execute_script("return window.pageYOffset;")
                if new_position == last_scroll_position:
                    logger.info("🛑 Reached page bottom")
                    break
                last_scroll_position = new_position

        # OPTIMIZED: Smart pagination với dynamic detection
        logger.info("📄 Smart pagination check...")
        for page_num in range(2):  # Reduced from 3 to 2 for efficiency
            try:
                # Try to find pagination button with multiple selectors
                next_btn = None
                for selector in [
                    "//a[contains(.,'Tiếp') or contains(.,'Sau') or contains(.,'Next')]",
                    "//button[contains(.,'Xem thêm') or contains(.,'Tải thêm')]",
                    "//a[contains(@class,'next') or contains(@class,'page-next')]",
                    "//button[contains(@class,'load-more')]"
                ]:
                    try:
                        next_btn = driver.find_element(By.XPATH, selector)
                        if next_btn.is_displayed() and next_btn.is_enabled():
                            break
                    except:
                        continue
                
                if not next_btn:
                    logger.info("🛑 No pagination button found")
                    break
                
                # Store initial link count
                initial_count = len(seen)
                
                # Click and wait for new content
                driver.execute_script("arguments[0].click();", next_btn)
                
                # OPTIMIZED: Smart wait for new content loading
                try:
                    WebDriverWait(driver, 5).until(
                        lambda d: len(snapshot()) > initial_count
                    )
                    # Brief wait for stabilization
                    time.sleep(0.5)  # Reduced from 1.0s
                except TimeoutException:
                    logger.warning("Pagination timeout, checking results anyway")
                    time.sleep(0.8)  # Fallback wait
                
                now = snapshot()
                if len(now) > len(seen):
                    seen = now
                    links = list(seen)
                    new_count = len(now) - initial_count
                    logger.info(f"📄 Page {page_num + 2}: Found {new_count} new links, total {len(links)}")
                else:
                    logger.info("🛑 No new content from pagination")
                    break
                    
            except Exception as e:
                logger.debug(f"Pagination error: {e}")
                break

        final_links = sorted(links)
        logger.info(f"✅ Final product links collected: {len(final_links)}")
        
        return final_links

    def _collect_product_links_bs4(self, url: str, session: requests.Session = None) -> list[str]:
        """Thu thập product links từ một trang sử dụng BeautifulSoup - version tối ưu."""
        if session is None:
            session = self.session
            
        try:
            logger.debug(f"🔍 Collecting product links from: {url}")
            
            # Fetch page content với retry
            def _fetch_page():
                resp = session.get(url, timeout=30)
                resp.raise_for_status()
                return resp.text
            
            html_content = self.retry_with_backoff(_fetch_page, max_retries=3, base_delay=1.5)
            soup = BeautifulSoup(html_content, 'html.parser')
            
            links = set()
            
            # Product link selectors - theo priority
            selectors_to_try = [
                # Priority selectors từ HTML structure thực tế
                ".product-list__item .thumbnail a",
                ".product-list__item .content h3 a", 
                ".product-list__item a[href*='/products/']",
                "a[href^='/products/']",
                "a[href*='/products/']",
                ".grid-list a[href*='/products/']",
                
                # Fallback patterns
                "a[href^='/product/']",
                "a[href*='/product/']", 
                "a[href^='/san-pham/']",
                "a[href*='/san-pham/']",
                ".product-item a",
                ".product-card a",
                ".product-list a",
                "a.product-link",
                ".col-product a",
                ".grid-item a",
                ".list-item a",
                "div[class*='product'] a"
            ]
            
            for selector in selectors_to_try:
                try:
                    elements = soup.select(selector)
                    logger.debug(f"🔍 Selector '{selector}' found {len(elements)} elements")
                    
                    for element in elements:
                        href = element.get('href', '').strip()
                        if not href:
                            continue
                            
                        # Validate product URL patterns
                        is_valid = False
                        if '/products/' in href:  # Priority pattern
                            is_valid = True
                        elif any(pattern in href for pattern in ['/product/', '/san-pham/']):
                            is_valid = True
                        elif any(keyword in href.lower() for keyword in ['item', 'detail', 'view']) and 'hoplongtech.com' in href:
                            is_valid = True
                            
                        if is_valid:
                            # Ensure full URL
                            if href.startswith('/'):
                                href = urljoin(self.base_url, href)
                            elif not href.startswith('http'):
                                continue
                                
                            # Only accept hoplongtech.com domain
                            if 'hoplongtech.com' in href:
                                links.add(href)
                                if logger.isEnabledFor(logging.DEBUG):
                                    logger.debug(f"  ✅ Added: {href}")
                                    
                except Exception as e:
                    logger.debug(f"❌ Selector '{selector}' failed: {e}")
                    continue
            
            # Enhanced fallback nếu không tìm thấy gì
            if not links:
                logger.warning("🔄 No product links found with primary selectors, trying comprehensive fallback...")
                try:
                    all_links = soup.find_all('a', href=True)
                    logger.debug(f"🔍 Analyzing {len(all_links)} total links...")
                    
                    for a in all_links:
                        href = a.get('href', '').strip()
                        if href and any(pattern in href for pattern in ['/products/', '/product/', '/san-pham/']):
                            if 'hoplongtech.com' in href:
                                if href.startswith('/'):
                                    href = urljoin(self.base_url, href)
                                links.add(href)
                                
                except Exception as e:
                    logger.error(f"❌ Comprehensive fallback failed: {e}")
            
            result = sorted(list(links))
            logger.info(f"📦 Found {len(result)} product links from: {url}")
            
            if result and logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"✅ Sample links: {result[:3]}")
            elif not result:
                logger.warning(f"❌ No product links found on page: {url}")
                
            return result
            
        except Exception as e:
            logger.error(f"❌ Error collecting product links from {url}: {e}")
            return []

    def _extract_pagination_info(self, soup: BeautifulSoup) -> dict:
        """
        Extract pagination information từ HTML structure:
        <div class="pagination-wrap">
            <div class="pagination-numbers__right">
                <span class="text">Trang</span>
                <p class="paginate-item">1</p>
                <span class="text">của 5</span>
            </div>
        </div>
        """
        pagination_info = {
            'current_page': 1,
            'total_pages': 1,
            'has_next': False,
            'next_button_available': False
        }
        
        try:
            # Tìm pagination container
            pagination_wrap = soup.select_one('div.pagination-wrap')
            if not pagination_wrap:
                logger.debug("❌ No pagination-wrap found")
                return pagination_info
            
            # Extract current page và total pages từ "Trang X của Y"
            pagination_right = pagination_wrap.select_one('.pagination-numbers__right')
            if pagination_right:
                try:
                    # Lấy current page từ <p class="paginate-item">
                    current_page_elem = pagination_right.select_one('p.paginate-item')
                    if current_page_elem:
                        current_page_text = current_page_elem.get_text(strip=True)
                        pagination_info['current_page'] = int(current_page_text)
                        logger.debug(f"📄 Current page: {pagination_info['current_page']}")
                    
                    # Lấy total pages từ text pattern "của X"
                    span_elements = pagination_right.find_all('span', class_='text')
                    for span in span_elements:
                        text = span.get_text(strip=True)
                        if text.startswith('của '):
                            total_pages_text = text.replace('của ', '').strip()
                            try:
                                pagination_info['total_pages'] = int(total_pages_text)
                                logger.debug(f"📄 Total pages: {pagination_info['total_pages']}")
                                break
                            except ValueError:
                                logger.debug(f"⚠️ Cannot parse total pages from: {total_pages_text}")
                                
                except Exception as e:
                    logger.debug(f"❌ Error parsing pagination numbers: {e}")
            
            # Check if có next button available
            pagination_left = pagination_wrap.select_one('.pagination-numbers__left')
            if pagination_left:
                # Tìm "Xem thêm" button
                next_button = pagination_left.select_one('button.paginate-next')
                if next_button:
                    # Check if button is not disabled
                    is_disabled = next_button.get('disabled') or 'disabled' in next_button.get('class', [])
                    pagination_info['next_button_available'] = not is_disabled
                    logger.debug(f"🔘 Next button available: {pagination_info['next_button_available']}")
            
            # Determine if has next page
            pagination_info['has_next'] = (
                pagination_info['current_page'] < pagination_info['total_pages'] or 
                pagination_info['next_button_available']
            )
            
            logger.info(f"📊 Pagination info: Page {pagination_info['current_page']}/{pagination_info['total_pages']}, Has next: {pagination_info['has_next']}")
            return pagination_info
            
        except Exception as e:
            logger.error(f"❌ Error extracting pagination info: {e}")
            return pagination_info

    def _fetch_all_pages_bs4(self, base_url: str, session: requests.Session = None, max_pages: int = 100) -> list[str]:
        """
        Fetch tất cả product links từ tất cả pages của filtered results sử dụng BeautifulSoup.
        base_url: URL của trang đầu tiên (đã có brand filter applied)
        """
        if session is None:
            session = self.session
            
        all_links = []
        current_page = 1
        consecutive_empty_pages = 0  # Safety valve
        
        try:
            logger.info(f"🔄 Starting multi-page fetch from: {base_url}")
            logger.info(f"🔧 Initial max_pages limit: {max_pages}")
            
            while current_page <= max_pages:
                try:
                    # IMPROVED: Construct page URL với advanced logic
                    if current_page == 1:
                        page_url = base_url
                        logger.info(f"📄 Fetching page {current_page} (base URL): {page_url}")
                    else:
                        # Multiple URL patterns to try
                        url_patterns = [
                            f"{base_url}{'&' if '?' in base_url else '?'}page={current_page}",
                            f"{base_url}{'&' if '?' in base_url else '?'}p={current_page}",
                            f"{base_url}/page/{current_page}",
                            f"{base_url}?page={current_page}" if '?' not in base_url else f"{base_url}&page={current_page}"
                        ]
                        
                        # Use first pattern for now, thêm logic để test multiple patterns nếu cần
                        page_url = url_patterns[0]
                        logger.info(f"📄 Fetching page {current_page}: {page_url}")
                        logger.debug(f"📄 Alternative URL patterns available: {url_patterns[1:]}")
                    
                    # Fetch page content với retry
                    def _fetch_page():
                        resp = session.get(page_url, timeout=30)
                        resp.raise_for_status()
                        return resp.text
                    
                    html_content = self.retry_with_backoff(_fetch_page, max_retries=3, base_delay=1.5)
                    soup = BeautifulSoup(html_content, 'html.parser')
                    
                    # Extract pagination info từ current page
                    pagination_info = self._extract_pagination_info(soup)
                    
                    # Collect product links từ current page
                    page_links = self._collect_product_links_bs4(page_url, session)
                    
                    if page_links:
                        # ENHANCED: Check for duplicates và track unique links
                        unique_page_links = [link for link in page_links if link not in all_links]
                        all_links.extend(unique_page_links)
                        
                        # Reset consecutive empty pages counter
                        consecutive_empty_pages = 0
                        
                        logger.info(f"✅ Page {current_page}: Found {len(page_links)} products ({len(unique_page_links)} new), Total: {len(all_links)}")
                        
                        # Additional debugging
                        if len(unique_page_links) != len(page_links):
                            logger.warning(f"⚠️ Page {current_page}: {len(page_links) - len(unique_page_links)} duplicate products detected")
                            
                        # Sample products for debugging
                        if logger.isEnabledFor(logging.DEBUG) and page_links:
                            logger.debug(f"📦 Sample products from page {current_page}: {page_links[:3]}")
                    else:
                        consecutive_empty_pages += 1
                        logger.warning(f"⚠️ Page {current_page}: No products found (consecutive empty: {consecutive_empty_pages})")
                        
                        # Safety valve: stop after 3 consecutive empty pages
                        if consecutive_empty_pages >= 3:
                            logger.warning(f"🛑 Stopping after {consecutive_empty_pages} consecutive empty pages - likely pagination ended")
                            break
                            
                        # If no products found on page 2+, it might mean pagination ended
                        if current_page > 1 and consecutive_empty_pages >= 2:
                            logger.warning(f"⚠️ Multiple empty pages detected, likely pagination ended")
                            break
                    
                    # ENHANCED: Check if we should continue với detailed logging
                    logger.debug(f"📊 Pagination check - Current: {pagination_info['current_page']}, Total: {pagination_info['total_pages']}, Has next: {pagination_info['has_next']}")
                    
                    if not pagination_info['has_next']:
                        logger.info(f"🏁 Pagination indicates last page reached: {current_page}/{pagination_info['total_pages']}")
                        break
                        
                    # Verify current page number matches expected
                    if pagination_info['current_page'] != current_page:
                        logger.warning(f"⚠️ Page number mismatch: expected {current_page}, got {pagination_info['current_page']}")
                    
                    # Update max_pages if we know total pages - KHÔNG giới hạn bằng max_pages ban đầu
                    if pagination_info['total_pages'] > 1:
                        # Allow crawling up to actual total pages, không bị giới hạn bởi max_pages
                        actual_max = pagination_info['total_pages']
                        logger.info(f"📊 Website has {actual_max} total pages, will crawl all of them")
                        # Chỉ cập nhật max_pages nếu nó lớn hơn giá trị hiện tại
                        if actual_max > max_pages:
                            max_pages = actual_max
                            logger.info(f"📊 Increased max_pages to: {max_pages} to cover all pages")
                    
                    current_page += 1
                    
                    # Small delay để tránh rate limiting
                    time.sleep(0.5)
                    
                except requests.exceptions.RequestException as e:
                    logger.error(f"❌ Network error on page {current_page}: {e}")
                    # Try next page nếu current page fail
                    current_page += 1
                    if current_page > max_pages:
                        break
                    time.sleep(2.0)  # Longer delay after error
                    continue
                    
                except Exception as e:
                    logger.error(f"❌ Error processing page {current_page}: {e}")
                    current_page += 1
                    if current_page > max_pages:
                        break
                    continue
            
            # Remove duplicates và sort với enhanced reporting
            unique_links = sorted(list(set(all_links)))
            pages_crawled = current_page - 1
            
            logger.info(f"🎯 MULTI-PAGE FETCH COMPLETED:")
            logger.info(f"   📄 Pages crawled: {pages_crawled}")
            logger.info(f"   📦 Total products found: {len(all_links)} (raw)")
            logger.info(f"   🔗 Unique products: {len(unique_links)}")
            logger.info(f"   ♻️ Duplicates removed: {len(all_links) - len(unique_links)}")
            
            # Additional insights
            if pages_crawled > 0:
                avg_products_per_page = len(unique_links) / pages_crawled
                logger.info(f"   📊 Average products per page: {avg_products_per_page:.1f}")
            
            # Warning if numbers seem low
            if len(unique_links) < 100:
                logger.warning(f"⚠️ Only {len(unique_links)} products found - this seems low, may indicate pagination issues")
            
            return unique_links
            
        except Exception as e:
            logger.error(f"❌ Critical error in multi-page fetch: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return all_links  # Return partial results

    # ======= PRODUCT DETAILS =======
    def _parse_specs_from_technical_div(self, technical_div: BeautifulSoup) -> list[tuple[str, str]]:
        """
        Trích xuất cặp (title, content) từ #technical theo structure:
        <ul>
            <li>
                <span class="title">Tên sản phẩm</span>
                <span class="content">Cảm biến tiệm cận E2B-M12KN05-WP-B2 2M OMI Omron</span>
            </li>
        </ul>
        """
        pairs: list[tuple[str, str]] = []
        if not technical_div:
            return pairs

        logger.debug("🔍 Parsing technical specifications...")
        
        # Extract từ ul/li/span structure theo HTML user cung cấp
        ul_elements = technical_div.find_all('ul')
        logger.debug(f"Found {len(ul_elements)} ul elements")
        
        for ul_idx, ul in enumerate(ul_elements):
            li_elements = ul.find_all('li', recursive=False)
            logger.debug(f"UL {ul_idx + 1}: Found {len(li_elements)} li elements")
            
            for li_idx, li in enumerate(li_elements):
                # Tìm span với class='title' và class='content'
                title_span = li.find('span', class_='title')
                content_span = li.find('span', class_='content')
                
                title = title_span.get_text(strip=True) if title_span else ''
                content = content_span.get_text(strip=True) if content_span else ''
                
                logger.debug(f"  LI {li_idx + 1}: '{title}' = '{content}'")
                
                # Chỉ thêm vào nếu có ít nhất title hoặc content
                if title or content:
                    pairs.append((title, content))
        
        # Fallback: nếu không tìm thấy gì bằng structure chính xác, thử các pattern khác
        if not pairs:
            logger.warning("🔄 No specs found with primary structure, trying fallback patterns...")
            
            # Thử tìm tất cả li elements có text
            all_li = technical_div.find_all('li')
            for li in all_li:
                text = li.get_text(strip=True)
                if ':' in text:
                    # Split theo dấu : đầu tiên
                    parts = text.split(':', 1)
                    if len(parts) == 2:
                        title, content = parts[0].strip(), parts[1].strip()
                        if title and content:
                            pairs.append((title, content))
                            logger.debug(f"  Fallback: '{title}' = '{content}'")
        
        logger.info(f"📊 Total specifications extracted: {len(pairs)}")
        return pairs

    def _build_specs_html(self, technical_div: BeautifulSoup, pairs: list[tuple[str, str]]) -> str:
        """Tạo HTML thông số kỹ thuật theo yêu cầu, thêm Copyright ở cuối."""
        # Nếu technical_div có table: giữ nguyên, chỉ thêm hàng Copyright vào cuối tbody
        if technical_div:
            table = technical_div.find('table')
            if table:
                # loại bỏ <col>/<colgroup>
                for col in table.find_all(['col', 'colgroup']):
                    col.decompose()
                tbody = table.find('tbody') or table
                tr = technical_div.new_tag('tr')
                td1 = technical_div.new_tag('td')
                td1['style'] = 'font-weight: bold;'
                td1.string = 'Copyright'
                td2 = technical_div.new_tag('td')
                td2.string = 'Haiphongtech.vn'
                tr.append(td1)
                tr.append(td2)
                tbody.append(tr)
                return str(table)

        # Fallback: dựng bảng 2 cột từ pairs
        rows = [
            '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;font-family:Arial;">',
            '<thead><tr style="background:#f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>',
            '<tbody>'
        ]
        for k, v in pairs:
            k_esc = (k or '').replace('<', '&lt;').replace('>', '&gt;')
            v_esc = (v or '').replace('<', '&lt;').replace('>', '&gt;')
            if k_esc or v_esc:
                rows.append(f'<tr><td><strong>{k_esc}</strong></td><td>{v_esc}</td></tr>')
        rows.append('<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>')
        rows.append('</tbody></table>')
        return '\n'.join(rows)

    def extract_product_details(self, product_url: str, category_name: str, expected_brand: str = None) -> dict | None:
        """STRICT extraction - Lấy chi tiết sản phẩm với validation nghiêm ngặt theo brand."""
        try:
            logger.debug(f"🔍 Extracting product details from: {product_url}")
            if expected_brand:
                logger.info(f"🎯 Expected brand: {expected_brand}")
                
            resp = self.session.get(product_url, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')

            # Tên sản phẩm: <h1 class="content-title">
            name_el = soup.select_one('h1.content-title')
            product_name = name_el.get_text(strip=True) if name_el else ''
            logger.debug(f"📝 Product name: {product_name}")

            # Mã sản phẩm: <p class="content-meta__sku">Mã sản phẩm: E2B-M12KN05-WP-B2 2M OMI</p>
            sku_el = soup.select_one('p.content-meta__sku')
            sku_text = sku_el.get_text(strip=True) if sku_el else ''
            # Lấy phần sau "Mã sản phẩm:" 
            product_code = re.sub(r'^\s*Mã\s*sản\s*phẩm\s*:\s*', '', sku_text, flags=re.IGNORECASE).strip()
            logger.debug(f"🏷️ Product code: {product_code}")

            # Hãng sản phẩm: <a class="content-meta__brand">
            brand_el = soup.select_one('a.content-meta__brand')
            actual_brand = brand_el.get_text(strip=True) if brand_el else ''
            logger.info(f"🏭 Actual brand from product: {actual_brand}")

            # STRICT BRAND VALIDATION
            if expected_brand:
                if actual_brand.lower() != expected_brand.lower():
                    logger.error(f"❌ BRAND MISMATCH! Expected: '{expected_brand}', Actual: '{actual_brand}' for product: {product_name}")
                    logger.error(f"❌ REJECTING PRODUCT: {product_url}")
                    return None  # REJECT this product
                else:
                    logger.info(f"✅ BRAND MATCH VERIFIED: {actual_brand}")

            # Thông số kỹ thuật: <div class="content-tab__detail" id="technical">
            tech_div = soup.select_one('div.content-tab__detail#technical')
            if not tech_div:
                # Fallback selectors
                tech_div = soup.select_one('div#technical') or soup.select_one('.content-tab__detail')
                
            logger.debug(f"🔧 Technical div found: {tech_div is not None}")

            # Extract specifications từ ul/li/span structure
            pairs = self._parse_specs_from_technical_div(tech_div) if tech_div else []
            specs_html = self._build_specs_html(tech_div, pairs)
            
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"📊 Specifications extracted: {len(pairs)} pairs")

            # Validation - đảm bảo có ít nhất tên sản phẩm
            if not product_name and not product_code:
                logger.warning(f"❌ No product name or code found for {product_url}")
                return None

            # Use actual brand from product page (đã được verified)
            data = {
                'Tên sản phẩm': product_name,
                'Mã sản phẩm': product_code,
                'Hãng': actual_brand,  # Use verified brand from product
                'Danh mục': category_name,
                'Link sản phẩm': product_url,
                'Thông số kỹ thuật HTML': specs_html,
            }
            
            logger.info(f"✅ Successfully extracted VERIFIED product: {product_name} (Brand: {actual_brand})")
            return data
            
        except Exception as e:
            logger.error(f"❌ Lỗi khi lấy chi tiết sản phẩm {product_url}: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            self.stats["errors"] += 1
            return None

    # ======= MAIN ORCHESTRATION =======
    def _category_name_from_html(self, driver) -> str:
        """Lấy tên danh mục từ HTML thực tế thay vì URL"""
        try:
            # Lấy từ <h1 class="title"> trong .list-heading__left
            title_element = driver.find_element(By.CSS_SELECTOR, '.list-heading__left h1.title')
            category_name = title_element.text.strip()
            if category_name:
                logger.info(f"📂 Category name from HTML: {category_name}")
                return category_name
        except Exception as e:
            logger.debug(f"Cannot get category from HTML: {e}")
        
        # Fallback: từ page title
        try:
            page_title = driver.title
            if page_title:
                # Extract meaningful part from title
                if '|' in page_title:
                    category_name = page_title.split('|')[0].strip()
                elif '-' in page_title:
                    category_name = page_title.split('-')[0].strip()
                else:
                    category_name = page_title.strip()
                logger.info(f"📂 Category name from title: {category_name}")
                return category_name
        except Exception:
            pass
        
        # Last fallback: từ URL
        try:
            current_url = driver.current_url
            path = urlparse(current_url).path.strip('/')
            last = path.split('/')[-1]
            category_name = last.replace('-', ' ').title()
            logger.info(f"📂 Category name from URL: {category_name}")
            return category_name
        except Exception:
            logger.warning("Cannot extract category name, using default")
            return 'Danh mục'

    def crawl_category_by_brands(self, category_url: str, brands: list[str]) -> str:
        """OPTIMIZED: Cào 1 danh mục cho nhiều hãng với parallel processing."""
        start = time.time()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        batch_folder = os.path.join(self.output_root, f"HopLong_{timestamp}")
        os.makedirs(batch_folder, exist_ok=True)

        # Sẽ lấy category name sau khi driver đã load trang
        category_name = "HopLong_Products"  # Temporary name
        category_dir = os.path.join(batch_folder, category_name)
        os.makedirs(category_dir, exist_ok=True)

        logger.info(f"=== OPTIMIZED CRAWL HopLong category: {category_name} ===")
        logger.info(f"URL: {category_url}")
        logger.info(f"Số lượng brands: {len(brands)}")
        logger.info(f"Output folder: {batch_folder}")
        
        self.emit_progress(0, f"Bắt đầu cào HopLong: {category_name}", category_url)

        # OPTIMIZATION DECISION: Choose parallel vs sequential based on brand count
        if len(brands) >= 2 and len(brands) <= 4:
            logger.info(f"🚀 PARALLEL MODE: Processing {len(brands)} brands concurrently")
            return self._crawl_brands_parallel_mode(category_url, brands, batch_folder, category_name, category_dir, start)
        else:
            logger.info(f"🔄 SEQUENTIAL MODE: {len(brands)} brands (parallel not optimal)")
            return self._crawl_brands_sequential_mode(category_url, brands, batch_folder, category_name, category_dir, start)

    def _crawl_brands_parallel_mode(self, category_url: str, brands: list[str], batch_folder: str, category_name: str, category_dir: str, start_time: float) -> str:
        """OPTIMIZED: Process multiple brands in parallel for major speed boost."""
        import threading
        
        # Shared data between threads
        shared_data = {
            "category_name": category_name,
            "category_dir": category_dir,
            "results_lock": threading.Lock()
        }
        
        def process_brand_parallel(brand_data):
            """Process single brand in parallel thread."""
            idx, brand = brand_data
            brand_display = brand.strip()
            if not brand_display:
                return None
                
            thread_id = threading.current_thread().name
            logger.info(f"[{thread_id}] 🔄 Processing brand {idx+1}/{len(brands)}: {brand_display}")
            
            try:
                # Update progress
                with shared_data["results_lock"]:
                    self.emit_progress(
                        5 + idx * (80/max(1, len(brands))), 
                        f"[Parallel] Đang xử lý: {brand_display}",
                        f"Thread: {thread_id[-8:]}"
                    )
                
                # Get product links for this brand
                product_links = self._get_brand_links_threadsafe(category_url, brand_display, idx, shared_data)
                
                # Process products for this brand
                results = self._process_brand_products_threadsafe(product_links, brand_display, idx, shared_data)
                
                # Export Excel for this brand
                if results:
                    excel_name = f"{shared_data['category_name']} {brand_display}.xlsx"
                    excel_path = os.path.join(shared_data['category_dir'], excel_name)
                    self._export_excel(results, excel_path)
                    
                    with shared_data["results_lock"]:
                        self.stats['brands_processed'] += 1
                        
                    logger.info(f"[{thread_id}] ✅ Completed {brand_display}: {len(results)} products")
                
                return {"brand": brand_display, "products": len(results), "success": True}
                
            except Exception as e:
                logger.error(f"[{thread_id}] ❌ Error processing {brand_display}: {e}")
                with shared_data["results_lock"]:
                    self.stats['errors'] += 1
                return {"brand": brand_display, "products": 0, "success": False, "error": str(e)}
        
        # PARALLEL EXECUTION
        max_parallel_brands = min(len(brands), 3)  # Max 3 concurrent brand processing
        logger.info(f"🔧 Using {max_parallel_brands} parallel brand workers")
        
        brand_data = [(idx, brand) for idx, brand in enumerate(brands)]
        brand_results = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel_brands, thread_name_prefix="Brand") as executor:
            futures = {executor.submit(process_brand_parallel, data): data for data in brand_data}
            
            for future in concurrent.futures.as_completed(futures):
                brand_data_item = futures[future]
                try:
                    result = future.result()
                    if result:
                        brand_results.append(result)
                except Exception as e:
                    logger.error(f"❌ Future exception for brand {brand_data_item}: {e}")
        
        # COMPLETION SUMMARY
        successful_brands = sum(1 for r in brand_results if r.get("success"))
        total_products = sum(r.get("products", 0) for r in brand_results)
        
        self.stats['categories_processed'] += 1
        duration = time.time() - start_time
        
        logger.info(f"=== PARALLEL CRAWL COMPLETED ===")
        logger.info(f"Category: {shared_data['category_name']}")
        logger.info(f"Duration: {duration:.1f}s")
        logger.info(f"Brands: {successful_brands}/{len(brands)} successful")
        logger.info(f"Products: {total_products} total")
        
        self.emit_progress(100, f"Parallel crawl hoàn thành: {shared_data['category_name']}", f"{duration:.1f}s - {total_products} sản phẩm")
        return batch_folder

    def _get_brand_links_threadsafe(self, category_url: str, brand_display: str, idx: int, shared_data: dict) -> list[str]:
        """OPTIMIZED: Thread-safe method to get product links for a brand."""
        def _crawl_brand():
            driver = self.get_driver()
            product_links: list[str] = []
            try:
                driver.get(category_url)
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
                time.sleep(0.8)  # Reduced wait time
                
                # Update category name from HTML (thread-safe, only first brand)
                if idx == 0:
                    with shared_data["results_lock"]:
                        try:
                            real_category_name = self._category_name_from_html(driver)
                            if real_category_name and real_category_name != "Danh mục":
                                old_category_dir = shared_data["category_dir"]
                                shared_data["category_name"] = real_category_name.replace('/', '-').replace('\\', '-')
                                shared_data["category_dir"] = os.path.join(os.path.dirname(old_category_dir), shared_data["category_name"])
                                
                                if old_category_dir != shared_data["category_dir"] and os.path.exists(old_category_dir):
                                    try:
                                        os.rename(old_category_dir, shared_data["category_dir"])
                                    except:
                                        os.makedirs(shared_data["category_dir"], exist_ok=True)
                                else:
                                    os.makedirs(shared_data["category_dir"], exist_ok=True)
                                    
                                logger.info(f"📂 Updated category name: {shared_data['category_name']}")
                        except Exception as e:
                            logger.debug(f"Cannot update category name: {e}")

                # Apply brand filter
                filter_success = self._apply_brand_filter(driver, brand_display)
                if not filter_success:
                    logger.error(f"❌ Brand filter failed for '{brand_display}'")
                    return []
                
                # HYBRID APPROACH: Get filtered URL từ Selenium, sau đó dùng BeautifulSoup
                filtered_url = driver.current_url
                logger.info(f"🔗 Filtered URL obtained: {filtered_url}")
                
                # Đóng driver sớm để tiết kiệm resource
                self.close_driver(driver)
                driver = None  # Mark as closed
                
                # Chuyển sang BeautifulSoup để thu thập product links với pagination
                logger.info(f"🔄 Switching to BeautifulSoup for multi-page collection: {brand_display}")
                
                # Sử dụng requests session để fetch tất cả pages
                product_links = self._fetch_all_pages_bs4(filtered_url, self.session)
                logger.info(f"📦 HYBRID: Found {len(product_links)} products for {brand_display}")
                
                with shared_data["results_lock"]:
                    self.stats['products_found'] += len(product_links)
                
                return product_links
                
            except Exception as e:
                logger.error(f"❌ Error getting links for {brand_display}: {e}")
                with shared_data["results_lock"]:
                    self.stats['errors'] += 1
                return []
            finally:
                if driver is not None:
                    self.close_driver(driver)
        
        try:
            return self.retry_with_backoff(_crawl_brand, max_retries=2, base_delay=2.0)
        except Exception as e:
            logger.error(f"❌ Failed to get links for {brand_display}: {e}")
            return []

    def _process_brand_products_threadsafe(self, product_links: list[str], brand_display: str, idx: int, shared_data: dict) -> list[dict]:
        """OPTIMIZED: Thread-safe method to process products for a brand."""
        results = []
        
        if not product_links:
            return results
            
        def task(url: str):
            return self.extract_product_details(url, shared_data["category_name"], expected_brand=brand_display)

        # OPTIMIZED: Dynamic worker calculation for thread safety
        optimal_workers = min(
            self.max_workers // 2,  # Reduce workers when in parallel brand mode
            len(product_links) // 2 + 1,
            12  # Max 12 workers per brand in parallel mode
        )
        
        logger.info(f"🔧 Using {optimal_workers} workers for {len(product_links)} products ({brand_display})")

        with concurrent.futures.ThreadPoolExecutor(max_workers=optimal_workers) as executor:
            futures = {executor.submit(task, url): url for url in product_links}
            
            completed_count = 0
            for fut in concurrent.futures.as_completed(futures):
                url = futures[fut]
                try:
                    item = fut.result()
                    if item:
                        results.append(item)
                        with shared_data["results_lock"]:
                            self.stats['products_processed'] += 1
                        
                    completed_count += 1
                    
                    # Thread-safe progress update
                    if completed_count % max(1, len(product_links) // 10) == 0 or completed_count == len(product_links):
                        with shared_data["results_lock"]:
                            self.emit_progress(
                                20 + idx * (60/4),  # Approximate progress for parallel mode
                                f"[{brand_display}] {completed_count}/{len(product_links)}",
                                f"{len(results)} thành công"
                            )
                except Exception as e:
                    logger.error(f"❌ Error processing {url}: {e}")
                    completed_count += 1
                    
        return results

    def _crawl_brands_sequential_mode(self, category_url: str, brands: list[str], batch_folder: str, category_name: str, category_dir: str, start_time: float) -> str:
        """OPTIMIZED: Sequential processing with enhanced performance."""
        # vòng theo từng brand với enhanced performance
        for idx, brand in enumerate(brands):
            brand_display = brand.strip()
            if not brand_display:
                continue

            self.emit_progress(5 + idx * (80/max(1, len(brands))), f"Đang áp dụng bộ lọc hãng: {brand_display}")

            # Mỗi brand mở một driver mới để trạng thái filter sạch
            logger.info(f"--- Bắt đầu crawl brand: {brand_display} ---")
            
            def _crawl_brand():
                driver = self.get_driver()
                product_links: list[str] = []
                try:
                    driver.get(category_url)
                    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))  # Giảm từ 30 xuống 20
                    time.sleep(1.0)  # Giảm từ 1.5s xuống 1.0s

                    # Lấy category name từ HTML thực tế (chỉ lần đầu)
                    if idx == 0:  # Chỉ lấy category name từ brand đầu tiên
                        nonlocal category_name, category_dir
                        try:
                            real_category_name = self._category_name_from_html(driver)
                            if real_category_name and real_category_name != "Danh mục":
                                # Update folder structure với tên thật
                                old_category_dir = category_dir
                                category_name = real_category_name.replace('/', '-').replace('\\', '-')  # Safe filename
                                category_dir = os.path.join(batch_folder, category_name)
                                
                                # Rename directory nếu cần
                                if old_category_dir != category_dir and os.path.exists(old_category_dir):
                                    try:
                                        os.rename(old_category_dir, category_dir)
                                    except:
                                        os.makedirs(category_dir, exist_ok=True)
                                else:
                                    os.makedirs(category_dir, exist_ok=True)
                                    
                                logger.info(f"📂 Updated category name: {category_name}")
                        except Exception as e:
                            logger.debug(f"Cannot update category name: {e}")

                    # STRICT brand filter application
                    filter_success = self._apply_brand_filter(driver, brand_display)
                    if not filter_success:
                        logger.error(f"❌ CRITICAL: Cannot apply brand filter for '{brand_display}' - SKIPPING this brand!")
                        self.emit_progress(
                            5 + idx * (80/max(1, len(brands))), 
                            f"❌ Brand filter failed: {brand_display}", 
                            "Skipping this brand - filter not working"
                        )
                        return []  # Return empty list - don't process this brand
                    
                    # HYBRID APPROACH: Get filtered URL từ Selenium, sau đó dùng BeautifulSoup
                    try:
                        filtered_url = driver.current_url
                        logger.info(f"🔗 Filtered URL obtained: {filtered_url}")
                    except:
                        filtered_url = category_url  # Fallback to original URL
                        logger.warning("⚠️ Cannot get filtered URL, using original category URL")

                    # Đóng driver sớm để tiết kiệm resource
                    self.close_driver(driver) 
                    driver = None  # Mark as closed
                    
                    # Chuyển sang BeautifulSoup để thu thập product links với pagination
                    logger.info(f"🔄 SEQUENTIAL: Switching to BeautifulSoup for multi-page collection: {brand_display}")
                    
                    # Sử dụng requests session để fetch tất cả pages
                    product_links = self._fetch_all_pages_bs4(filtered_url, self.session)
                    logger.info(f"📦 SEQUENTIAL HYBRID: Found {len(product_links)} products for {brand_display}")
                    
                    # Additional verification - make sure we have filtered results
                    if not product_links:
                        logger.warning(f"⚠️ No products found for brand '{brand_display}' - this may be correct or filter issue")
                    
                    return product_links
                except WebDriverException as e:
                    if "ERR_CONNECTION_RESET" in str(e) or "net::" in str(e):
                        logger.error(f"Lỗi kết nối mạng cho brand {brand_display}: {e}")
                        self.emit_progress(
                            5 + idx * (80/max(1, len(brands))), 
                            f"Lỗi kết nối cho {brand_display}", 
                            "Thử phương pháp backup..."
                        )
                        raise
                    else:
                        logger.error(f"Lỗi WebDriver cho brand {brand_display}: {e}")
                        raise
                finally:
                    if driver is not None:
                        self.close_driver(driver)
            
            try:
                product_links = self.retry_with_backoff(_crawl_brand, max_retries=2, base_delay=3.0)
                self.stats['products_found'] += len(product_links)
            except Exception as e:
                logger.error(f"Thất bại hoàn toàn khi crawl brand {brand_display}: {e}")
                self.stats['errors'] += 1
                self.emit_progress(
                    5 + idx * (80/max(1, len(brands))), 
                    f"Bỏ qua brand {brand_display}", 
                    f"Lỗi: {str(e)[:100]}..."
                )
                continue

            # OPTIMIZED: Enhanced multithreading với adaptive workers
            results: list[dict] = []
            if product_links:
                self.emit_progress(20 + idx * (60/max(1, len(brands))), f"Đang lấy chi tiết {len(product_links)} sản phẩm", brand_display)

                def task(url: str):
                    # Pass expected brand to extraction for validation
                    return self.extract_product_details(url, category_name, expected_brand=brand_display)

                # OPTIMIZED: Dynamic worker calculation for better performance
                optimal_workers = min(
                    self.max_workers,
                    len(product_links) // 2 + 1,  # At least 2 URLs per worker
                    20  # Max 20 workers to avoid overwhelming server
                )
                
                logger.info(f"🔧 Using {optimal_workers} workers for {len(product_links)} products")

                with concurrent.futures.ThreadPoolExecutor(max_workers=optimal_workers) as executor:
                    # OPTIMIZED: Submit all tasks at once for better scheduling
                    futures = {executor.submit(task, url): url for url in product_links}
                    
                    # Process results as they complete
                    completed_count = 0
                    for fut in concurrent.futures.as_completed(futures):
                        url = futures[fut]
                        try:
                            item = fut.result()
                            if item:
                                results.append(item)
                                self.stats['products_processed'] += 1
                                
                            completed_count += 1
                            
                            # OPTIMIZED: Less frequent progress updates for better performance  
                            if completed_count % max(1, len(product_links) // 20) == 0 or completed_count == len(product_links):
                                progress_pct = 20 + idx * (60/max(1, len(brands))) + (completed_count/max(1, len(product_links))) * (60/max(1, len(brands)))
                                self.emit_progress(
                                    progress_pct, 
                                    f"Đã xử lý {completed_count}/{len(product_links)} sản phẩm",
                                    f"{brand_display} - {len(results)} thành công"
                                )
                        except Exception as e:
                            logger.error(f"❌ Error processing {url}: {e}")
                            completed_count += 1

            # xuất excel cho brand này
            if results:
                excel_name = f"{category_name} {brand_display}.xlsx"
                excel_path = os.path.join(category_dir, excel_name)
                self._export_excel(results, excel_path)
                self.stats['brands_processed'] += 1

        self.stats['categories_processed'] += 1
        dur = time.time() - start_time
        
        # Log kết quả tổng kết
        logger.info(f"=== Hoàn thành crawl category: {category_name} ===")
        logger.info(f"Thời gian: {dur:.1f}s")
        logger.info(f"Brands processed: {self.stats['brands_processed']}")
        logger.info(f"Products found: {self.stats['products_found']}")
        logger.info(f"Products processed: {self.stats['products_processed']}")
        logger.info(f"Errors: {self.stats['errors']}")
        
        self.emit_progress(100, f"Hoàn thành danh mục: {category_name}", f"{dur:.1f}s - {self.stats['products_processed']} sản phẩm")
        return batch_folder

    def _export_excel(self, rows: list[dict], excel_path: str) -> None:
        df = pd.DataFrame(rows)
        os.makedirs(os.path.dirname(excel_path), exist_ok=True)
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='HopLong')
            ws = writer.sheets['HopLong']
            for column in ws.columns:
                max_len = 0
                col_letter = column[0].column_letter
                for cell in column:
                    try:
                        max_len = max(max_len, len(str(cell.value)))
                    except Exception:
                        pass
                ws.column_dimensions[col_letter].width = min(max_len + 2, 60)


