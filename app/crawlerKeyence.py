"""
Keyence Product Crawler - Cào dữ liệu sản phẩm từ website keyence.com.vn
Phiên bản: 1.0
Tác giả: Auto-generated based on OmronCrawler
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

def standardize_filename_keyence(code):
    """
    Chuẩn hóa mã sản phẩm Keyence thành tên file hợp lệ
    Chuyển từ JavaScript logic sang Python
    """
    if not code:
        return ""
    
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

class KeyenceCrawler:
    """
    Crawler chuyên dụng cho website keyence.com.vn
    Cào dữ liệu sản phẩm với xử lý đa luồng và discontinued products
    """
    
    def __init__(self, output_root=None, max_workers=8, max_retries=3, socketio=None):
        """
        Khởi tạo KeyenceCrawler
        
        Args:
            output_root: Thư mục gốc để lưu kết quả
            max_workers: Số luồng tối đa
            max_retries: Số lần thử lại khi request thất bại
            socketio: Socket.IO instance để emit tiến trình
        """
        self.output_root = output_root or os.path.join(os.getcwd(), "output_keyence")
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.socketio = socketio
        
        # Tạo thư mục output
        os.makedirs(self.output_root, exist_ok=True)
        
        # Base URLs
        self.base_url = "https://www.keyence.com.vn"
        
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
        
        # Cấu hình Selenium WebDriver cho Keyence
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
            "failed_images": 0
        }
        
        logger.info("✅ Đã khởi tạo KeyenceCrawler với Selenium support")
    
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
    
    def _parse_keyence_specs(self, soup):
        """
        Parse thông số kỹ thuật Keyence theo chuẩn hãng với BeautifulSoup
        Áp dụng logic đơn giản và chính xác theo yêu cầu user
        
        Args:
            soup: BeautifulSoup object của product page
            
        Returns:
            list: Danh sách specs items [{"key": str, "value": str, "attributeid": str}]
        """
        try:
            # Tìm tất cả rows trong specs table
            rows = soup.select('div.specTable-block table tr')
            
            items = []
            for tr in rows:
                key_main = tr.select_one('td.specTable-clm-0')
                key_sub = tr.select_one('td.specTable-clm-1')
                val_td = tr.select_one('td.specTable-clm-4')  # cột giá trị
                
                # Nếu không có cột giá trị, thử tìm trong các cột khác (PS-26 format)
                if not val_td:
                    val_td = tr.select_one('td.specTable-clm-2')  # PS-26 style
                    if not val_td:
                        val_td = tr.select_one('td.specTable-clm-1')  # 2-column simple
                
                # Bỏ các hàng không có ô key chính hoặc ô giá trị
                if not key_main or not val_td:
                    continue
                
                # Text gọn + xử lý xuống dòng <br>
                def text_of(el):
                    if not el:
                        return ""
                    # Join các text fragments với ' ; ' 
                    return ' ; '.join([s.strip() for s in el.stripped_strings])
                
                key = text_of(key_main)
                if key_sub and key_sub.get_text(strip=True):
                    key = f"{key} — {text_of(key_sub)}"
                
                value = text_of(val_td)
                # Chuyển đổi các ký tự trống thành empty string
                if value in ["―", "—", "-"]:
                    value = ""
                
                # Chỉ thêm nếu có key và value hợp lệ
                if key and key != value:
                    items.append({
                        "key": key,
                        "value": value,
                        "attributeid": val_td.get("attributeid", "")  # giữ lại id nếu cần map
                    })
            
            logger.info(f"✅ Parsed {len(items)} specification items theo chuẩn hãng")
            return items
            
        except Exception as e:
            logger.error(f"❌ Lỗi khi parse Keyence specs: {str(e)}")
            return []
    
    def _parse_keyence_footnotes(self, soup):
        """
        Parse footnotes từ specTable-foot rows
        
        Args:
            soup: BeautifulSoup object của product page
            
        Returns:
            dict: Footnotes {attributeid: content}
        """
        try:
            footnotes = {}
            footer_rows = soup.select('div.specTable-block table tr.specTable-foot')
            
            for footer_row in footer_rows:
                cells = footer_row.find_all('td')
                if cells:
                    footnote_content = ' ; '.join([s.strip() for s in cells[0].stripped_strings])
                    attributeid = cells[0].get('attributeid', 'footnotes')
                    footnotes[attributeid] = footnote_content
            
            return footnotes
            
        except Exception as e:
            logger.debug(f"Lỗi khi parse footnotes: {str(e)}")
            return {}

    def _extract_original_specs_html(self, soup):
        """
        Trích xuất nguyên khối HTML phần Thông số kỹ thuật từ trang Keyence.
        Đồng thời tiêm inline-style tối thiểu để đảm bảo hiển thị đẹp khi không có CSS của Keyence.

        Args:
            soup: BeautifulSoup của product page

        Returns:
            str: HTML phần specs đã được chuẩn hóa; rỗng nếu không tìm thấy
        """
        try:
            specs_div = soup.find('div', class_='prd-specsTable')
            if not specs_div:
                return ''

            # Lấy section chứa specs nếu có
            section = specs_div.find_parent('section') or specs_div

            # Xác thực tiêu đề là "Thông số kỹ thuật" nếu tồn tại
            h2 = section.find('h2') if hasattr(section, 'find') else None
            if h2 and 'thông số' not in h2.get_text(strip=True).lower():
                # Không đúng section specs
                return ''

            # Tiêm inline styles tối thiểu để nhìn giống website khi thiếu CSS gốc
            self._inject_inline_styles_to_keyence_specs(section)

            return str(section)
        except Exception:
            return ''

    def _inject_inline_styles_to_keyence_specs(self, section):
        """
        Thêm inline-style cơ bản vào table/td/th để hiển thị giống website trong môi trường không có CSS gốc.
        Modify in-place.
        """
        try:
            table = section.find('table') if hasattr(section, 'find') else None
            if not table:
                return

            # Thêm style cho table
            table_attrs = table.get('style', '')
            add_table_style = 'border-collapse:collapse;width:100%;'
            if add_table_style not in table_attrs:
                table['style'] = (table_attrs + ';' + add_table_style).strip(';')

            # Thêm border/padding cho tất cả ô
            for cell in table.find_all(['td', 'th']):
                cell_style = cell.get('style', '')
                add_cell_style = 'border:1px solid #e5e5e5;padding:8px;vertical-align:top;'
                if add_cell_style not in cell_style:
                    cell['style'] = (cell_style + ';' + add_cell_style).strip(';')

            # Làm nổi bật header nếu có thead
            thead = table.find('thead')
            if thead:
                for th in thead.find_all('th'):
                    th_style = th.get('style', '')
                    add_th_style = 'background:#f7f7f7;font-weight:600;'
                    if add_th_style not in th_style:
                        th['style'] = (th_style + ';' + add_th_style).strip(';')
        except Exception:
            # An toàn: không chặn workflow nếu styling thất bại
            pass
    
    def clean_specs(self, html: str) -> str:
        """
        Làm sạch HTML thông số kỹ thuật theo yêu cầu:
        - Chỉ giữ lại duy nhất "rowspan" và "colspan" trên mọi thẻ trong bảng
        - Xóa toàn bộ <col>/<colgroup>
        - Tái tạo tối thiểu: <section><h2>Thông số kỹ thuật</h2><table>...</table></section>
        - Chèn một hàng bản quyền ngay trước các hàng footnotes (tr.specTable-foot) nếu có,
          hoặc ở cuối bảng nếu không có footnotes.
        - Sau khi làm sạch, tiêm inline-style tối thiểu để đường kẻ là nét liền.
        """
        try:
            if not html:
                return ""

            soup = BeautifulSoup(html, "html.parser")

            # Tìm tiêu đề H2 và bảng đầu tiên ngay sau đó
            h2 = soup.find("h2", string=lambda s: s and "Thông số kỹ thuật" in s)
            table = h2.find_next("table") if h2 else None
            if not (h2 and table):
                return ""

            # Ghi nhận vị trí của hàng foot đầu tiên để chèn bản quyền phía trên
            all_rows = table.find_all("tr")
            foot_index = None
            for idx, row in enumerate(all_rows):
                classes = row.get("class", []) or []
                if any(cls == "specTable-foot" for cls in classes):
                    foot_index = idx
                    break

            # Loại bỏ <col> và <colgroup>
            for col in table.find_all(["col", "colgroup"]):
                col.decompose()

            # Hàm loại bỏ mọi attribute trừ rowspan/colspan
            def strip_attrs(tag):
                if not getattr(tag, "attrs", None):
                    return
                keep = {}
                for key in ("rowspan", "colspan"):
                    if key in tag.attrs:
                        keep[key] = tag.attrs[key]
                tag.attrs = keep

            # Strip attributes trên table và toàn bộ con
            strip_attrs(table)
            for t in table.find_all(True):
                strip_attrs(t)

            # Tạo soup tối thiểu và gắn table đã làm sạch
            out = BeautifulSoup(features="html.parser")
            sec = out.new_tag("section")
            h2_min = out.new_tag("h2")
            h2_min.string = h2.get_text(strip=True)
            sec.append(h2_min)

            # Sao chép table sang out-soup
            table_copy_soup = BeautifulSoup(str(table), "html.parser")
            table_copy = table_copy_soup.find("table")

            # Chèn hàng bản quyền
            # Tìm tbody hoặc dùng table nếu không có
            tbody = table_copy.find("tbody")
            if not tbody:
                tbody = table_copy

            # Tạo <tr> bản quyền với style đậm cho ô đầu tiên
            tr_c = table_copy_soup.new_tag("tr")
            td_label = table_copy_soup.new_tag("td")
            td_label["style"] = "font-weight: bold;"
            td_label.string = "Copyright"
            td_value = table_copy_soup.new_tag("td")
            td_value.string = "Haiphongtech.vn"
            tr_c.append(td_label)
            tr_c.append(td_value)

            # Xác định vị trí chèn: trước hàng foot đầu tiên nếu có
            rows_copy = table_copy.find_all("tr")
            if foot_index is not None and foot_index < len(rows_copy):
                # Tìm đúng node hàng theo index để chèn trước
                target_row = rows_copy[foot_index]
                target_row.insert_before(tr_c)
            else:
                tbody.append(tr_c)

            sec.append(table_copy)
            out.append(sec)

            # Tiêm inline styles tối thiểu sau khi làm sạch và chèn bản quyền
            styled_soup = BeautifulSoup(out.prettify(), "html.parser")
            section_tag = styled_soup.find("section") or styled_soup
            self._inject_inline_styles_to_keyence_specs(section_tag)
            return str(styled_soup)

        except Exception as e:
            logger.debug(f"Lỗi khi clean specs HTML: {e}")
            return ""
    
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
        Trích xuất tất cả series từ trang category Keyence với Selenium
        Bao gồm cả discontinued series bằng cách click switch
        
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
                
                # Đợi trang load xong
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".prd-seriesCard-link"))
                    )
                except TimeoutException:
                    logger.warning(f"⏰ Timeout khi đợi page load, thử fallback method")
                    return self.extract_series_fallback(category_url)
                
                # Tìm tất cả normal series links
                normal_series_links = driver.find_elements(By.CSS_SELECTOR, "a.prd-seriesCard-link")
                
                for link_element in normal_series_links:
                    try:
                        href = link_element.get_attribute('href')
                        
                        # Extract series name từ linkLabel
                        link_label = link_element.find_element(By.CSS_SELECTOR, ".prd-seriesCard-linkLabel")
                        series_name = link_label.text.strip()
                        
                        if href and series_name:
                            # Convert relative URL thành absolute URL
                            full_url = urljoin(self.base_url, href)
                            
                            # Tránh trùng lặp
                            if not any(s['url'] == full_url for s in series_data):
                                series_data.append({
                                    'name': series_name,
                                    'url': full_url,
                                    'type': 'normal'
                                })
                                logger.debug(f"✅ Tìm thấy series: {series_name} - {full_url}")
                            
                    except Exception as e:
                        logger.warning(f"⚠️ Lỗi khi extract normal series link: {str(e)}")
                        continue
                
                # Click discontinued switch để hiển thị discontinued series
                try:
                    discontinued_switch = driver.find_element(
                        By.CSS_SELECTOR, 
                        "button[data-controller*='switch-discontinued']"
                    )
                    if discontinued_switch:
                        # Scroll đến switch
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", discontinued_switch)
                        time.sleep(1)
                        
                        # Click switch để hiển thị discontinued series
                        driver.execute_script("arguments[0].click();", discontinued_switch)
                        logger.info("✅ Đã click switch để hiển thị discontinued series")
                        time.sleep(3)  # Đợi content load
                        
                        # Tìm discontinued series links
                        discontinued_series_links = driver.find_elements(By.CSS_SELECTOR, "a.prd-seriesCardDiscontinued")
                        
                        for link_element in discontinued_series_links:
                            try:
                                href = link_element.get_attribute('href')
                                
                                # Extract series name từ title
                                title_element = link_element.find_element(By.CSS_SELECTOR, ".prd-seriesCardDiscontinued-title")
                                series_name = title_element.text.strip()
                                
                                if href and series_name:
                                    # Convert relative URL thành absolute URL
                                    full_url = urljoin(self.base_url, href)
                                    
                                    # Tránh trùng lặp
                                    if not any(s['url'] == full_url for s in series_data):
                                        series_data.append({
                                            'name': series_name + " (Ngưng sản xuất)",
                                            'url': full_url,
                                            'type': 'discontinued'
                                        })
                                        logger.debug(f"✅ Tìm thấy discontinued series: {series_name}")
                                
                            except Exception as e:
                                logger.warning(f"⚠️ Lỗi khi extract discontinued series link: {str(e)}")
                                continue
                        
                        logger.info(f"✅ Tìm thấy {len(discontinued_series_links)} discontinued series")
                        
                except NoSuchElementException:
                    logger.info("Không tìm thấy discontinued switch")
                except Exception as e:
                    logger.warning(f"Lỗi khi xử lý discontinued switch: {str(e)}")
                
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
        Khi Selenium gặp vấn đề
        
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
            
            # Tìm normal series links
            normal_series = soup.find_all('a', class_='prd-seriesCard-link')
            for link in normal_series:
                try:
                    href = link.get('href')
                    link_label = link.find(class_='prd-seriesCard-linkLabel')
                    series_name = link_label.text.strip() if link_label else ""
                    
                    if href and series_name:
                        full_url = urljoin(self.base_url, href)
                        series_data.append({
                            'name': series_name,
                            'url': full_url,
                            'type': 'normal'
                        })
                        logger.debug(f"🔍 Fallback tìm thấy: {series_name} - {full_url}")
                        
                except Exception as e:
                    logger.debug(f"Lỗi khi xử lý normal series fallback: {str(e)}")
                    continue
            
            # Tìm discontinued series links
            discontinued_series = soup.find_all('a', class_='prd-seriesCardDiscontinued')
            for link in discontinued_series:
                try:
                    href = link.get('href')
                    title_element = link.find(class_='prd-seriesCardDiscontinued-title')
                    series_name = title_element.text.strip() if title_element else ""
                    
                    if href and series_name:
                        full_url = urljoin(self.base_url, href)
                        series_data.append({
                            'name': series_name + " (Ngưng sản xuất)",
                            'url': full_url,
                            'type': 'discontinued'
                        })
                        logger.debug(f"🔍 Fallback tìm thấy discontinued: {series_name}")
                        
                except Exception as e:
                    logger.debug(f"Lỗi khi xử lý discontinued series fallback: {str(e)}")
                    continue
            
            logger.info(f"🎯 Fallback method tìm thấy {len(series_data)} series")
            return series_data
            
        except Exception as e:
            logger.error(f"❌ Lỗi trong fallback method: {str(e)}")
            return []

    def extract_products_from_series(self, series_url):
        """
        Extract tất cả products từ series models page với xử lý discontinued
        Sử dụng Selenium để click nút show discontinued models
        
        Args:
            series_url: URL của series page
            
        Returns:
            list: Danh sách product URLs và metadata
        """
        driver = None
        products_data = []
        max_retries = 2
        
        # Tạo models URL bằng cách thêm /models/ vào cuối
        models_url = series_url.rstrip('/') + '/models/'
        
        for attempt in range(max_retries):
            try:
                logger.debug(f"🔄 Thử lần {attempt + 1}/{max_retries} extract products từ {models_url}")
                
                # Tạo driver mới cho mỗi attempt
                if driver:
                    self.close_driver(driver)
                driver = self.get_driver()
                
                self.emit_progress(
                    20, 
                    f"Đang thu thập sản phẩm từ series (lần thử {attempt + 1})",
                    f"URL: {models_url}"
                )
                
                # Load trang models với timeout
                driver.set_page_load_timeout(45)
                driver.get(models_url)
                
                # Đợi trang load hoàn toàn
                time.sleep(3)
                
                # Đợi cho models page load
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".prd-layout-modelIndexHeader"))
                    )
                except TimeoutException:
                    logger.warning(f"⏰ Timeout khi đợi models page load, thử fallback method")
                    return self.extract_products_fallback(models_url)
                
                # Click discontinued models switch để hiển thị tất cả models
                try:
                    discontinued_switch = driver.find_element(
                        By.CSS_SELECTOR, 
                        ".prd-layout-modelIndexHeader button[data-controller*='switch-discontinued']"
                    )
                    if discontinued_switch:
                        # Kiểm tra switch có đang OFF không (aria-checked="false")
                        is_checked = discontinued_switch.get_attribute('aria-checked')
                        if is_checked == 'false':
                            # Scroll đến switch
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", discontinued_switch)
                            time.sleep(1)
                            
                            # Click switch để hiển thị discontinued models
                            driver.execute_script("arguments[0].click();", discontinued_switch)
                            logger.info("✅ Đã click switch để hiển thị discontinued models")
                            time.sleep(4)  # Đợi content load
                        else:
                            logger.info("✅ Switch đã được bật sẵn")
                            
                except NoSuchElementException:
                    logger.info("Không tìm thấy discontinued models switch")
                except Exception as e:
                    logger.warning(f"Lỗi khi xử lý discontinued models switch: {str(e)}")
                
                # Lấy tất cả product links từ models page
                # Thử các selector khác nhau để tìm product links
                product_links = []
                
                selectors = [
                    "a[href*='/models/'][href$='/']",  # Links có /models/ và kết thúc bằng /
                    "a[href*='products/'][href*='models']",  # Links chứa products và models
                    ".prd-modelCard a",  # Model card links
                    ".prd-model-link",  # Model links
                    "a[data-ga-label*='model']"  # Links có GA label model
                ]
                
                for selector in selectors:
                    try:
                        links = driver.find_elements(By.CSS_SELECTOR, selector)
                        if links:
                            product_links = links
                            logger.info(f"✅ Tìm thấy {len(links)} product links với selector: {selector}")
                            break
                    except Exception as e:
                        logger.debug(f"Lỗi với selector {selector}: {str(e)}")
                        continue
                
                # Nếu không tìm thấy bằng selector, thử tìm tất cả links chứa model
                if not product_links:
                    all_links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
                    for link in all_links:
                        href = link.get_attribute('href')
                        if href and '/models/' in href and href != models_url:
                            product_links.append(link)
                    logger.info(f"🔍 Fallback: Tìm thấy {len(product_links)} product links")
                
                for link_element in product_links:
                    try:
                        href = link_element.get_attribute('href')
                        
                        # Lấy product name từ text hoặc alt attribute
                        product_name = ""
                        try:
                            product_name = link_element.text.strip()
                            if not product_name:
                                # Thử lấy từ alt attribute của img trong link
                                img = link_element.find_element(By.CSS_SELECTOR, "img")
                                product_name = img.get_attribute('alt') or ""
                        except:
                            pass
                        
                        if href and href != models_url:
                            # Filter chỉ lấy link sản phẩm thật sự
                            if (('/models/' in href) 
                                and href.count('/') >= 6  # Đảm bảo có đủ depth cho product URL
                                and not href.endswith('/models/')  # Không phải models root
                                and 'category' not in href.lower()):
                                
                                # Convert relative URL thành absolute URL
                                if href.startswith('/'):
                                    full_url = urljoin(self.base_url, href)
                                elif not href.startswith('http'):
                                    full_url = urljoin(models_url, href)
                                else:
                                    full_url = href
                                
                                # Extract product code từ URL (phần cuối)
                                product_code = full_url.rstrip('/').split('/')[-1]
                                if not product_name:
                                    product_name = product_code
                                
                                products_data.append({
                                    'name': product_name,
                                    'code': product_code,
                                    'url': full_url
                                })
                                logger.debug(f"✅ Tìm thấy sản phẩm: {product_name} ({product_code}) - {full_url}")
                            
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
                logger.error(f"❌ Lỗi lần thử {attempt + 1} khi extract products từ {models_url}: {str(e)}")
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
                    products_data = self.extract_products_fallback(models_url)
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
        logger.info(f"🎯 Tổng cộng tìm thấy {len(products_data)} sản phẩm từ {models_url}")
        
        return products_data

    def extract_products_fallback(self, models_url):
        """
        Fallback method để extract products sử dụng requests + BeautifulSoup
        Khi Selenium gặp vấn đề với models page
        
        Args:
            models_url: URL của models page
            
        Returns:
            list: Danh sách product URLs và metadata
        """
        try:
            logger.info(f"🔄 Sử dụng fallback method cho models: {models_url}")
            
            html = self.get_html_content(models_url)
            if not html:
                return []
            
            soup = BeautifulSoup(html, 'html.parser')
            products_data = []
            
            # Tìm tất cả links có thể là products
            potential_links = soup.find_all('a', href=True)
            
            for link in potential_links:
                try:
                    href = link.get('href')
                    text = link.get_text(strip=True)
                    
                    if href and href != models_url:
                        # Filter links sản phẩm với tiêu chí mới
                        if (('/models/' in href)
                            and href.count('/') >= 6
                            and not href.endswith('/models/')
                            and 'category' not in href.lower()):
                            
                            # Convert thành absolute URL
                            if href.startswith('/'):
                                full_url = urljoin(self.base_url, href)
                            elif href.startswith('http'):
                                full_url = href
                            else:
                                full_url = urljoin(models_url, href)
                            
                            # Extract product code từ URL
                            product_code = full_url.rstrip('/').split('/')[-1]
                            product_name = text if text else product_code
                            
                            # Kiểm tra không trùng lặp
                            if not any(p['url'] == full_url for p in products_data):
                                products_data.append({
                                    'name': product_name,
                                    'code': product_code,
                                    'url': full_url
                                })
                                logger.debug(f"Fallback tìm thấy: {product_name} ({product_code})")
                        
                except Exception as e:
                    logger.debug(f"Lỗi khi xử lý link fallback: {str(e)}")
                    continue
            
            logger.info(f"Fallback method tìm thấy {len(products_data)} sản phẩm")
            return products_data
            
        except Exception as e:
            logger.error(f"Lỗi trong products fallback method: {str(e)}")
            return []

    def extract_product_details(self, product_url):
        """
        Extract chi tiết sản phẩm từ product page Keyence
        Lấy tên sản phẩm, mã sản phẩm, thông số kỹ thuật và ảnh sản phẩm
        
        Args:
            product_url: URL của product page
            
        Returns:
            dict: Thông tin chi tiết sản phẩm
        """
        try:
            self.emit_progress(
                50, 
                f"Đang cào thông tin sản phẩm Keyence",
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
                'specs_html_original': '',
                'original_url': product_url
            }
            
            # 1. Lấy mã sản phẩm từ span.prd-utility-body-medium
            product_code_element = soup.find('span', class_='prd-utility-body-medium prd-utility-block')
            if product_code_element:
                product_data['product_code'] = product_code_element.get_text(strip=True)
            
            # 2. Lấy các thành phần để tạo tên sản phẩm theo yêu cầu chính xác
            # Phần 1: Category từ breadcrumb navigation - chính xác theo yêu cầu
            category_name = ''
            
            # Tìm element chính xác như user chỉ định:
            # <a class="prd-inlineLink prd-utility-focusRing" href="/products/sensor/photoelectric/">
            # <span class="prd-inlineLink-label">Cảm biến quang điện</span></a>
            
            # Ưu tiên tìm link chính xác với class "prd-inlineLink prd-utility-focusRing"
            exact_link = soup.find('a', class_='prd-inlineLink prd-utility-focusRing')
            if exact_link:
                label_span = exact_link.find('span', class_='prd-inlineLink-label')
                if label_span:
                    category_name = label_span.get_text(strip=True)
                    # Loại bỏ "Trang chủ" nếu đó là kết quả
                    if category_name.lower() in ['trang chủ', 'home']:
                        category_name = ''
            
            # Fallback: tìm trong tất cả breadcrumb links nếu chưa có
            if not category_name:
                prd_inline_links = soup.find_all('a', class_='prd-inlineLink')
                
                # First pass: tìm exact match "Cảm biến quang điện"
                for link in prd_inline_links:
                    label_span = link.find('span', class_='prd-inlineLink-label')
                    if label_span:
                        text = label_span.get_text(strip=True)
                        if text.lower() == 'cảm biến quang điện':
                            category_name = text
                            break
                
                # Second pass: tìm specific sensor types nếu chưa có exact match
                if not category_name:
                    specific_sensors = ['cảm biến sợi quang', 'cảm biến laser', 'cảm biến tiệm cận', 
                                      'cảm biến vị trí', 'cảm biến hình ảnh', 'cảm biến áp suất', 
                                      'cảm biến nhiệt độ', 'cảm biến đo mức']
                    for link in prd_inline_links:
                        label_span = link.find('span', class_='prd-inlineLink-label')
                        if label_span:
                            text = label_span.get_text(strip=True)
                            if text.lower() in specific_sensors:
                                category_name = text
                                break
                
                # Third pass: fallback to general "cảm biến"
                if not category_name:
                    for link in prd_inline_links:
                        label_span = link.find('span', class_='prd-inlineLink-label')
                        if label_span:
                            text = label_span.get_text(strip=True)
                            if text.lower() == 'cảm biến':
                                category_name = text
                                break
                
                # Fallback cuối: lấy link đầu tiên không phải "Trang chủ"
                if not category_name:
                    for link in prd_inline_links:
                        label_span = link.find('span', class_='prd-inlineLink-label')
                        if label_span:
                            text = label_span.get_text(strip=True)
                            if text.lower() not in ['trang chủ', 'home', 'products', 'sản phẩm']:
                                category_name = text
                                break
            
            product_data['category'] = category_name
            
            # Phần 3: Mô tả từ span.prd-utility-heading-1
            description = ''
            description_element = soup.find('span', class_='prd-utility-heading-1 prd-utility-marginBottom-2 prd-utility-block')
            if not description_element:
                description_element = soup.find('span', class_='prd-utility-heading-1')
            if not description_element:
                description_element = soup.find('h1', class_='prd-utility-heading-1')
            
            if description_element:
                description = description_element.get_text(strip=True)
            
            # 3. Ghép tên sản phẩm theo thứ tự chính xác: Category + Product Code + Description + KEYENCE
            name_parts = []
            if category_name:
                name_parts.append(category_name)
            if product_data['product_code']:
                name_parts.append(product_data['product_code'])
            if description:
                name_parts.append(description)
            name_parts.append('KEYENCE')
            
            product_name = ' '.join(name_parts)
            product_data['product_name'] = product_name
            product_data['full_product_name'] = product_name
            
            # 5. Lấy ảnh sản phẩm từ img.prd-modelIntroduction-image
            img_element = soup.find('img', class_='prd-modelIntroduction-image')
            if img_element and img_element.get('src'):
                image_src = img_element['src']
                # Convert relative URL thành absolute URL
                if image_src.startswith('/'):
                    product_data['image_url'] = urljoin(self.base_url, image_src)
                else:
                    product_data['image_url'] = image_src
            
            # 6. Lấy thông số kỹ thuật theo chuẩn hãng với BeautifulSoup đơn giản
            product_data['specifications'] = []
            product_data['footnotes'] = {}
            
            # Lưu nguyên khối HTML <section> thông số kỹ thuật của trang gốc (ưu tiên dùng)
            original_specs_html = self._extract_original_specs_html(soup)
            # Làm sạch theo yêu cầu và chèn bản quyền trước footnotes nếu có
            cleaned_specs_html = self.clean_specs(original_specs_html) if original_specs_html else ''
            product_data['specs_html_original'] = cleaned_specs_html or original_specs_html or ''

            # Parse specs theo chuẩn hãng
            specs_items = self._parse_keyence_specs(soup)
            product_data['specifications'] = specs_items
            
            # Parse footnotes riêng biệt  
            footnotes = self._parse_keyence_footnotes(soup)
            product_data['footnotes'] = footnotes
            
            # 7. Extract series name từ URL nếu chưa có
            if not product_data.get('series'):
                # Series name thường là phần trước /models/ trong URL
                if '/models/' in product_url:
                    series_part = product_url.split('/models/')[0]
                    series_name = series_part.split('/')[-1].upper()
                    product_data['series'] = series_name
            
            logger.info(f"✅ Đã extract thông tin sản phẩm: {product_data['product_code']}")
            return product_data
            
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất thông tin sản phẩm từ {product_url}: {str(e)}")
            return None

    def process_image_with_white_background(self, image_url, save_path, product_code):
        """
        Tải và xử lý ảnh sản phẩm Keyence, thêm nền trắng và chuyển sang WebP
        Keyence images thường không có nền, cần thêm white background
        
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
            original_image = Image.open(BytesIO(response.content))
            
            # Chỉ thêm nền trắng nếu cần, giữ nguyên kích thước gốc
            processed_image = self.add_white_background_keyence(original_image)
            
            # Tạo tên file theo logic standardize_filename_keyence
            filename = f"{standardize_filename_keyence(product_code)}.webp"
            full_path = os.path.join(save_path, filename)
            
            # Tạo thư mục nếu chưa tồn tại
            os.makedirs(save_path, exist_ok=True)
            
            # Lưu trực tiếp sang WebP để giữ nguyên chất lượng
            processed_image.save(full_path, 'WebP', quality=95, method=6)
            result = True
            
            if result:
                self.stats["images_downloaded"] += 1
                logger.info(f"✅ Đã tải và chuyển đổi ảnh Keyence: {filename}")
                return True
            else:
                self.stats["failed_images"] += 1
                logger.error(f"❌ Lỗi chuyển đổi ảnh WebP: {filename}")
                return False
                
        except Exception as e:
            self.stats["failed_images"] += 1
            logger.error(f"❌ Lỗi khi tải ảnh Keyence từ {image_url}: {str(e)}")
            return False

    def add_white_background_keyence(self, image):
        """
        Chỉ thêm nền trắng vào ảnh Keyence nếu cần, giữ nguyên kích thước gốc
        Keyence images thường transparent, cần white background cho WordPress
        
        Args:
            image: PIL Image object
            
        Returns:
            PIL Image: Ảnh đã được xử lý với nền trắng, giữ nguyên kích thước
        """
        try:
            # Lấy kích thước gốc
            original_size = image.size
            
            # Nếu ảnh đã có nền RGB rồi thì return luôn
            if image.mode == 'RGB':
                return image
            
            # Convert ảnh sang RGBA để preserve transparency
            if image.mode != 'RGBA':
                image = image.convert('RGBA')
            
            # Tạo ảnh nền trắng với kích thước gốc
            background = Image.new('RGB', original_size, (255, 255, 255))
            
            # Paste ảnh gốc lên nền trắng (giữ nguyên kích thước)
            background.paste(image, (0, 0), image)
            
            return background
            
        except Exception as e:
            logger.error(f"Lỗi khi thêm white background: {str(e)}")
            # Return ảnh gốc convert sang RGB nếu có lỗi
            return image.convert('RGB') if image.mode != 'RGB' else image

    def create_excel_with_keyence_specs(self, products_data, excel_path):
        """
        Tạo file Excel với thông số kỹ thuật theo định dạng Keyence
        Tạo bảng HTML specifications theo format yêu cầu với Copyright Haiphongtech.vn
        
        Args:
            products_data: Danh sách dữ liệu sản phẩm
            excel_path: Đường dẫn file Excel
            
        Returns:
            bool: True nếu thành công
        """
        try:
            if not products_data:
                logger.warning("Không có dữ liệu sản phẩm Keyence để xuất Excel")
                return False
            
            # Chuẩn bị dữ liệu cho DataFrame
            excel_data = []
            
            for product in products_data:
                # Tạo bảng HTML specifications theo format yêu cầu
                specs_html = self.create_keyence_specifications_table_html(product)
                
                excel_row = {
                    'Mã sản phẩm': product.get('product_code', ''),
                    'Tên sản phẩm tiếng Anh': product.get('product_name', ''),
                    'Tên sản phẩm tiếng Việt': product.get('full_product_name', ''),
                    'Category': product.get('category', ''),
                    'Series': product.get('series', ''),
                    'Link sản phẩm': product.get('original_url', ''),
                    'Link ảnh': product.get('image_url', ''),
                    'Thông số kỹ thuật HTML': specs_html,
                    'Số lượng thông số': len(product.get('specifications', [])),
                    'Số lượng footnotes': len(product.get('footnotes', {}))
                }
                excel_data.append(excel_row)
            
            # Tạo DataFrame
            df = pd.DataFrame(excel_data)
            
            # Tạo thư mục nếu chưa tồn tại
            os.makedirs(os.path.dirname(excel_path), exist_ok=True)
            
            # Xuất ra Excel với formatting
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Keyence_Products')
                
                # Lấy worksheet để format
                worksheet = writer.sheets['Keyence_Products']
                
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
            
            logger.info(f"✅ Đã tạo file Excel Keyence: {excel_path} với {len(products_data)} sản phẩm")
            return True
            
        except Exception as e:
            logger.error(f"❌ Lỗi khi tạo file Excel Keyence {excel_path}: {str(e)}")
            return False

    def create_keyence_specifications_table_html(self, product):
        """
        Trả về nguyên khối HTML thông số kỹ thuật từ website nếu có.
        Nếu không có, fallback tạo bảng 2-column chuẩn "Thông số / Giá trị".
        
        Args:
            product: Dữ liệu sản phẩm Keyence
            
        Returns:
            str: HTML table string 2-column
        """
        try:
            # 1) Ưu tiên dùng HTML gốc từ website nếu đã lưu
            original_html = product.get('specs_html_original', '')
            if original_html:
                return original_html

            # 2) Fallback: tạo bảng 2-column đơn giản
            product_code = product.get('product_code', '')
            specifications = product.get('specifications', [])
            footnotes = product.get('footnotes', {})
            
            # Bắt đầu với bảng 2-column chuẩn
            rows = [
                '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;font-family:Arial;">',
                '<thead><tr style="background:#f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>',
                '<tbody>'
            ]
            
            # Thêm mã sản phẩm đầu tiên
            rows.append(f'<tr><td><strong>Mẫu</strong></td><td>{product_code}</td></tr>')
            
            # Thêm tất cả specifications từ parsed data
            for item in specifications:
                key = item.get('key', '')
                value = item.get('value', '')
                
                if key and value:
                    # Escape HTML để tránh conflict
                    key_escaped = key.replace('<', '&lt;').replace('>', '&gt;')
                    value_escaped = value.replace('<', '&lt;').replace('>', '&gt;')
                    
                    rows.append(f'<tr><td><strong>{key_escaped}</strong></td><td>{value_escaped}</td></tr>')
            
            # Thêm footnotes nếu có
            if footnotes:
                footnote_content = ""
                for attributeid, content in footnotes.items():
                    footnote_content += content + " "
                
                footnote_content = footnote_content.strip()
                if footnote_content:
                    footnote_escaped = footnote_content.replace('<', '&lt;').replace('>', '&gt;')
                    rows.append(f'<tr><td><strong>Ghi chú</strong></td><td>{footnote_escaped}</td></tr>')
            
            # Thêm Copyright
            rows.append('<tr><td><strong>Copyright</strong></td><td>Haiphongtech.vn</td></tr>')
            
            # Đóng bảng
            rows.append('</tbody></table>')
            
            return '\n'.join(rows)
            
        except Exception as e:
            logger.error(f"Lỗi khi tạo HTML table cho sản phẩm Keyence: {str(e)}")
            return ""

    def crawl_products(self, category_urls):
        """
        Method chính để cào dữ liệu sản phẩm Keyence từ danh sách category URLs
        Full workflow: Category → Series → Products → Details → Images → Excel
        
        Args:
            category_urls: Danh sách URLs của các categories
            
        Returns:
            str: Đường dẫn thư mục chứa kết quả
        """
        start_time = time.time()
        timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
        result_dir = os.path.join(self.output_root, f"KeyenceProducts_{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        
        self.emit_progress(0, "Bắt đầu cào dữ liệu Keyence", f"Sẽ xử lý {len(category_urls)} categories")
        
        # Process each category với retry logic
        for i, category_url in enumerate(category_urls):
            category_success = False
            max_category_retries = 2
            
            for category_attempt in range(max_category_retries):
                logger.info(f"🔄 Category attempt {category_attempt + 1}/{max_category_retries} for: {category_url}")
                category_success = self._process_single_keyence_category(
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
        logger.info("=== THỐNG KÊ CRAWLER KEYENCE ===")
        logger.info(f"Thời gian thực hiện: {duration:.2f} giây")
        logger.info(f"Categories đã xử lý: {self.stats['categories_processed']}")
        logger.info(f"Series tìm thấy: {self.stats['series_found']}")
        logger.info(f"Sản phẩm tìm thấy: {self.stats['products_found']}")
        logger.info(f"Sản phẩm đã xử lý: {self.stats['products_processed']}")
        logger.info(f"Ảnh đã tải: {self.stats['images_downloaded']}")

        logger.info(f"Request thất bại: {self.stats['failed_requests']}")
        logger.info(f"Ảnh thất bại: {self.stats['failed_images']}")
        
        return result_dir

    def _process_single_keyence_category(self, category_url, category_index, total_categories, result_dir):
        """
        Process một category Keyence với error handling và retry logic
        Full workflow để tạo folder cấu trúc theo yêu cầu
        
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
            
            # Tạo thư mục images theo yêu cầu
            images_dir = os.path.join(category_dir, "images")
            os.makedirs(images_dir, exist_ok=True)
            
            # Progress calculation cải thiện
            category_progress_base = int((category_index / total_categories) * 90)
            
            self.emit_progress(
                category_progress_base,
                f"Đang xử lý category [{category_index+1}/{total_categories}]: {category_name}",
                f"URL: {category_url}"
            )
            
            # Lấy danh sách series (bao gồm discontinued)
            logger.info(f"📋 Đang thu thập series từ category: {category_name}")
            series_list = self.extract_series_from_category(category_url)
            
            if not series_list:
                logger.warning(f"⚠️ Không tìm thấy series nào từ {category_url}")
                return False
            
            logger.info(f"✅ Tìm thấy {len(series_list)} series trong category: {category_name}")
            
            # Collect tất cả products từ các series với đa luồng
            all_products_data = []
            
            def process_keyence_series(series_info):
                """Process một series Keyence và trả về danh sách products với details"""
                try:
                    series_url = series_info['url']
                    series_name = series_info['name']
                    series_type = series_info.get('type', 'normal')
                    
                    self.emit_progress(
                        category_progress_base + 20,
                        f"Đang xử lý series: {series_name} ({series_type})",
                        series_url
                    )
                    
                    # Lấy danh sách products từ series
                    products_list = self.extract_products_from_series(series_url)
                    
                    if not products_list:
                        logger.warning(f"Không tìm thấy products nào từ series {series_name}")
                        return []
                    
                    # Extract chi tiết cho từng product
                    series_products = []
                    for product_info in products_list:  # Lấy toàn bộ sản phẩm
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
                future_to_series = {executor.submit(process_keyence_series, series): series for series in series_list}
                
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
            
            # Tải ảnh với đa luồng và white background processing
            def download_keyence_image(product):
                """Download và xử lý ảnh cho một sản phẩm Keyence"""
                try:
                    if product.get('image_url') and product.get('product_code'):
                        return self.process_image_with_white_background(
                            product['image_url'],
                            images_dir,
                            product['product_code']
                        )
                except Exception as e:
                    logger.error(f"Lỗi khi tải ảnh cho {product.get('product_code', 'Unknown')}: {str(e)}")
                    return False
            
            # Download ảnh với đa luồng
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                image_futures = [executor.submit(download_keyence_image, product) for product in all_products_data]
                concurrent.futures.wait(image_futures)
            
            # Tạo file Excel với Keyence specs
            excel_path = os.path.join(category_dir, f"{category_name}.xlsx")
            excel_success = self.create_excel_with_keyence_specs(all_products_data, excel_path)
            
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
    # Test crawler với photoelectric category
    crawler = KeyenceCrawler()
    test_urls = ["https://www.keyence.com.vn/products/sensor/photoelectric/"]
    result_dir = crawler.crawl_products(test_urls)
    print(f"Kết quả được lưu tại: {result_dir}")
