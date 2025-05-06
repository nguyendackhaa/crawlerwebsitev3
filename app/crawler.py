import requests
from bs4 import BeautifulSoup
import pandas as pd
import tempfile
import re
import time
import random
import os
from urllib.parse import urljoin, urlparse, quote
import logging
from app import socketio
from datetime import datetime
from PIL import Image
import io
import traceback
from werkzeug.utils import secure_filename
import openpyxl
import hashlib
import threading
from urllib.parse import parse_qs
from io import BytesIO
import json
from openpyxl import Workbook
import urllib3

# Headers giả lập trình duyệt để tránh bị chặn
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Connection': 'keep-alive',
    'Referer': 'https://google.com'
}

def get_html_content(url, headers=None):
    """
    Tải nội dung HTML từ URL
    
    Args:
        url (str): URL cần tải nội dung
        headers (dict, optional): Headers cho request
        
    Returns:
        str: Nội dung HTML hoặc None nếu có lỗi
    """
    if headers is None:
        headers = HEADERS
    
    try:
        # Tắt cảnh báo SSL không an toàn
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Lỗi khi tải nội dung từ {url}: {e}")
        return None

def is_product_url(url):
    """Kiểm tra xem URL có phải là URL sản phẩm hợp lệ không"""
    # Phân tích URL
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    
    # Loại bỏ các URL không phải BAA.vn
    if 'baa.vn' not in parsed_url.netloc.lower():
        return False
    
    # Loại bỏ các URL tin tức, thông tin, v.v. trước tiên
    if '/tin-tuc/' in path or '/news/' in path or '/thong-tin/' in path or '/information/' in path:
        return False
        
    # Kiểm tra URL có chứa các phần tử của liên kết sản phẩm
    if '/san-pham/' in path or '/product/' in path:
            # Mẫu URL: baa.vn/vn/san-pham/bo-dieu-khien-nhiet-do-tuong-tu-autonics-tom-f3rj4c_61459
            if '/san-pham/' in path:
                # Kiểm tra có phải URL sản phẩm không dựa trên pattern thông thường
                # 1. Kiểm tra pattern có _NUMBER ở cuối
                if re.search(r'_\d+$', path.rstrip('/')):
                    return True
                # 2. Kiểm tra pattern có tên model/mã sản phẩm ở cuối
                parts = path.rstrip('/').split('/')
                if len(parts) > 0 and re.search(r'[a-zA-Z0-9]+-[a-zA-Z0-9]+', parts[-1]):
                    return True
            return True
    
    return False

def is_category_url(url):
    """
    Kiểm tra xem URL có phải là URL danh mục hay không
    """
    # Xử lý riêng cho URL đèn tháp LED
    led_tower_url = "den-thap-led-sang-tinh-chop-nhay-d45mm-qlight-st45l-and-st45ml-series_4779"
    if led_tower_url in url:
        print(f"Đã phát hiện URL đèn tháp LED: {url}")
        return True
    
    # Các mẫu regex để phát hiện URL danh mục
    baa_category_patterns = [
        r'\/category\/', 
        r'\/danh-muc\/', 
        r'baa\.vn.*\/vn\/[^/]+\/.*-page-\d+',
        r'haiphongtech.+\/danh-muc'
    ]
    
    for pattern in baa_category_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            # Kiểm tra thêm nếu là url từ BAA.vn
            if 'baa.vn' in url:
                parts = url.split('/')
                # Kiểm tra xem phần cuối của URL có phải dạng xyz-abc hay không
                if len(parts) > 0 and re.search(r'[a-zA-Z0-9]+-[a-zA-Z0-9]+', parts[-1]):
                    return True
            return True
    
    return False

def extract_category_links(category_urls):
    """
    Trích xuất liên kết sản phẩm từ URL danh mục
    """
    all_product_urls = []
    processed_urls = set()
    
    for i, url in enumerate(category_urls):
        try:
            # Kiểm tra đã xử lý URL này chưa
            if url in processed_urls:
                print(f"Bỏ qua URL đã xử lý: {url}")
                continue
                
            # Đánh dấu đã xử lý
            processed_urls.add(url)
            
            # Kiểm tra URL đèn tháp LED đặc biệt
            is_led_page = False
            led_tower_url = "den-thap-led-sang-tinh-chop-nhay-d45mm-qlight-st45l-and-st45ml-series_4779"
            if led_tower_url in url:
                is_led_page = True
                print(f"Xử lý URL đèn tháp LED đặc biệt: {url}")
                
                # Lấy HTML của trang
                html = get_html_content(url)
                if not html:
                    print(f"Không thể lấy nội dung từ {url}")
                    continue
                
                # Phân tích HTML
                soup = BeautifulSoup(html, 'html.parser')
                
                print(f"Đang xử lý trang đèn tháp LED: {url}")
                
                # Tìm tất cả các card sản phẩm
                product_cards = soup.select('a.card.product__card')
                if product_cards:
                    print(f"Tìm thấy {len(product_cards)} thẻ a.card.product__card")
                    
                    # Lấy href từ các thẻ a
                    for card in product_cards:
                        href = card.get('href')
                        if href:
                            # Đảm bảo URL đầy đủ
                            if not href.startswith('http'):
                                parsed_url = urlparse(url)
                                full_url = f"{parsed_url.scheme}://{parsed_url.netloc}{href}"
                            else:
                                full_url = href
                                
                            # Thêm vào danh sách nếu chưa có
                            if full_url not in all_product_urls:
                                all_product_urls.append(full_url)
                                print(f"Đã thêm URL sản phẩm: {full_url}")
                else:
                    print("Không tìm thấy thẻ a.card.product__card, thử các selector khác")
                    
                    # Tìm các liên kết sản phẩm khác nếu không tìm thấy a.card.product__card
                    product_links = soup.select('a.product-item-link, a.product__card, a.product_item, a.product-item')
                    if product_links:
                        print(f"Tìm thấy {len(product_links)} liên kết sản phẩm thay thế")
                        
                        for link in product_links:
                            href = link.get('href')
                            if href:
                                # Đảm bảo URL đầy đủ
                                if not href.startswith('http'):
                                    if href.startswith('/'):
                                        parsed_url = urlparse(url)
                                        full_url = f"{parsed_url.scheme}://{parsed_url.netloc}{href}"
                                    else:
                                        full_url = url.rstrip('/') + '/' + href
                                else:
                                    full_url = href
                                
                                # Thêm vào danh sách nếu chưa có
                                if full_url not in all_product_urls and '/san-pham/' in full_url:
                                    all_product_urls.append(full_url)
                                    print(f"Đã thêm URL sản phẩm thay thế: {full_url}")
                
                # Kiểm tra tìm thấy sản phẩm hoặc thông báo lỗi
                if len(all_product_urls) == 0:
                    print("CẢNH BÁO: Không tìm thấy sản phẩm nào trên trang đèn tháp LED!")
                    
                    # Các thẻ trực tiếp chứa sản phẩm (debug)
                    debug_products = soup.select('.product__card')
                    print(f"DEBUG: Tìm thấy {len(debug_products)} thẻ .product__card")
                    
                    for dp in debug_products:
                        if dp.name == 'a' and dp.has_attr('href'):
                            print(f"DEBUG: Liên kết: {dp.get('href')}")
                
                print(f"Đã tìm thấy {len(all_product_urls)} URL sản phẩm từ trang đèn tháp LED.")
                
                # Tìm liên kết phân trang để xử lý các trang tiếp theo
                pagination_links = soup.select('ul.pagination li a')
                if not pagination_links:
                    pagination_links = soup.select('.pages a, .pagination a, a[href*="page="], a[href*="/page/"]')
                
                for page_link in pagination_links:
                    page_text = page_link.get_text(strip=True)
                    page_url = page_link.get('href')
                    
                    # Bỏ qua các liên kết không phải số trang
                    if not re.match(r'^\d+$', page_text):
                        continue
                        
                    # Bỏ qua liên kết không có href hoặc liên kết không phải số trang
                    if not page_url or not re.match(r'^\d+$', page_text):
                        continue
                        
                    # Đảm bảo URL đầy đủ
                    if not page_url.startswith('http'):
                        if page_url.startswith('/'):
                            parsed_url = urlparse(url)
                            page_url = f"{parsed_url.scheme}://{parsed_url.netloc}{page_url}"
                        else:
                            page_url = url.rstrip('/') + '/' + page_url
                            
                    # Bỏ qua trang hiện tại
                    if page_url == url:
                        continue
                        
                    # Thêm vào danh sách URL cần xử lý nếu chưa xử lý
                    if page_url not in processed_urls:
                        print(f"Thêm trang phân trang: {page_url}")
                        category_urls.append(page_url)
                
            else:
                # Xử lý các URL danh mục thông thường
                product_urls = extract_product_urls(url)
                
                # Thêm vào danh sách chính
                for product_url in product_urls:
                    if product_url not in all_product_urls:
                        all_product_urls.append(product_url)
        
        except Exception as e:
            print(f"Lỗi khi xử lý URL danh mục {url}: {str(e)}")
            print(traceback.format_exc())
    
    print(f"Tổng cộng tìm thấy {len(all_product_urls)} liên kết sản phẩm độc nhất")
    return all_product_urls

def extract_product_info(url, required_fields=None, index=1):
    """
    Trích xuất thông tin sản phẩm từ URL
    
    Args:
        url (str): URL của trang sản phẩm
        required_fields (list): Danh sách các trường thông tin cần lấy
        index (int): STT của sản phẩm trong danh sách
        
    Returns:
        dict: Thông tin sản phẩm đã trích xuất
    """
    
    if not required_fields:
        required_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Tổng quan', 'Giá']
    
    print(f"Đang trích xuất thông tin từ {url}")
    
    # Số lần thử tối đa
    max_retries = 3
    current_retry = 0
    
    while current_retry < max_retries:
        try:
            # Tải nội dung trang
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            html_content = response.text
            
            # Parse HTML
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Khởi tạo kết quả
            product_info = {
                'STT': index,
                'URL': url
            }
            
            # 1. Trích xuất tên sản phẩm
            product_name = ""
            
            # Thử các CSS selector khác nhau
            name_selectors = [
                '.product__info .product-detail h1',
                '.product__name',
                '.product-name h1',
                '.pdp-name',
                'h1.product-title'
            ]
            
            for selector in name_selectors:
                name_element = soup.select_one(selector)
                if name_element:
                    product_name = name_element.text.strip()
                    break
            
            if not product_name:
                print(f"Không tìm thấy tên sản phẩm trong URL {url}")
            else:
                print(f"Tên sản phẩm: {product_name}")
                product_info['Tên sản phẩm'] = product_name
            
            # 2. Trích xuất mã sản phẩm
            product_code = ""
            
            # Thử các CSS selector khác nhau
            code_selectors = [
                '.product__symbol__value',
                '.product-sku',
                '.sku',
                '[itemprop="sku"]',
                '.product-id'
            ]
            
            for selector in code_selectors:
                code_element = soup.select_one(selector)
                if code_element:
                    product_code = code_element.text.strip()
                    break
            
            # Nếu không tìm thấy, thử trích xuất từ URL
            if not product_code:
                print(f"Không tìm thấy mã sản phẩm trong trang, thử trích xuất từ URL {url}")
                # Lấy phần cuối của URL
                url_path = urlparse(url).path
                path_parts = url_path.split("/")
                if path_parts:
                    last_part = path_parts[-1]
                    # Loại bỏ các số ID, giữ lại phần mã sản phẩm
                    product_code = re.sub(r'_\d+$', '', last_part)
            
            if product_code:
                print(f"Mã sản phẩm: {product_code}")
                product_info['Mã sản phẩm'] = product_code
                
            # Thêm mới: Trích xuất giá sản phẩm
            product_price = ""
            
            # Tìm phần tử div chứa giá
            price_element = soup.select_one('div.product__card--price span.fw-bold.text-danger.text-start')
            if price_element:
                product_price = price_element.text.strip()
                print(f"Giá sản phẩm: {product_price}")
                product_info['Giá'] = product_price
            else:
                # Thử một số CSS selector khác
                price_selectors = [
                    '.product__card--price span',
                    '.product-price',
                    '.price-box .price',
                    '.special-price .price',
                    '[data-price-type="finalPrice"] .price'
                ]
                
                for selector in price_selectors:
                    price_element = soup.select_one(selector)
                    if price_element:
                        product_price = price_element.text.strip()
                        print(f"Giá sản phẩm: {product_price}")
                        product_info['Giá'] = product_price
                        break
                        
            if not product_price:
                print(f"Không tìm thấy giá sản phẩm trong URL {url}")
                product_info['Giá'] = ""
            
            # 3. Trích xuất thông số kỹ thuật
            specs_table_html = '<table class="specs-table" border="1" cellpadding="5" style="border-collapse: collapse;"><tbody>'
            
            # Tìm bảng thông số
            specs_found = False
            params_count = 0
            
            # Kiểm tra nếu có tab điều khiển
            spec_tabs = soup.select('.feature__metadata-nav')
            has_tabs = len(spec_tabs) > 0
            
            if has_tabs:
                # Nếu có các tab, tìm các tab thông số
                spec_sections = soup.select('.feature__metadata--tab')
                
                for section in spec_sections:
                    # Kiểm tra nếu là phần thông số kỹ thuật
                    header = section.select_one('.feature__metadata-header-text')
                    if header and ('Specifications' in header.text or 'Thông số' in header.text):
                        print(f"Đã tìm thấy tab thông số: {header.text}")
                        
                        # Tìm bảng thông số trong tab
                        tables = section.select('table')
                        if tables:
                            for table in tables:
                                # Lấy các hàng trong bảng
                                rows = table.select('tr')
                                for row in rows:
                                    # Mỗi hàng có hai cột: tên thông số và giá trị
                                    cells = row.select('td')
                                    if len(cells) >= 2:
                                        param_name_cell = cells[0]
                                        param_value_cell = cells[1]
                                        
                                        # Lấy tên tham số
                                        param_name = ""
                                        label = param_name_cell.select_one('label')
                                        if label:
                                            param_name = label.text.strip()
                                        else:
                                            param_name = param_name_cell.text.strip()
                                        
                                        # Lấy giá trị tham số
                                        param_value = extract_full_value(param_value_cell)
                                        
                                        # Thêm vào bảng HTML (tránh các tham số rỗng)
                                        if param_name and param_value and param_name.lower() not in ['thông số', 'parameter', 'giá trị', 'value']:
                                            specs_table_html += f'<tr><td>{param_name}</td><td>{param_value}</td></tr>'
                                            params_count += 1
            
            # Đóng bảng HTML
            specs_table_html += '</tbody></table>'
            
            # Thêm vào dữ liệu nếu có thông số
            if params_count > 0:
                specs_found = True
                product_info['Tổng quan'] = specs_table_html
                print(f"Đã tìm thấy {params_count} thông số kỹ thuật")
            else:
                print("Không tìm thấy thông số kỹ thuật")
            
            # Lọc lại kết quả theo các trường yêu cầu
            filtered_info = {}
            for field in required_fields:
                if field in product_info:
                    filtered_info[field] = product_info[field]
                else:
                    filtered_info[field] = ""  # Giá trị trống cho trường không tìm thấy
            
            return filtered_info
            
        except requests.exceptions.RequestException as e:
            current_retry += 1
            print(f"Lỗi tải trang (lần thử {current_retry}): {str(e)}")
            if current_retry < max_retries:
                print(f"Thử lại trong 3 giây...")
                time.sleep(3)
            else:
                print(f"Đã thử {max_retries} lần, bỏ qua URL {url}")
                
                # Trả về dữ liệu tối thiểu
                minimal_info = {
                    'STT': index,
                    'URL': url
                }
                
                # Thêm các trường còn lại
                for field in required_fields:
                    if field not in minimal_info:
                        minimal_info[field] = ""
                
                return minimal_info

def extract_full_value(value_cell):
    """
    Trích xuất giá trị đầy đủ từ một ô td, bao gồm cả phần moreellipses và morecontent
    """
    # Kiểm tra cấu trúc HTML ban đầu
    html_content = str(value_cell)
    
    # Danh sách các trường đặc biệt cần xử lý riêng
    special_fields = [
        "Phụ kiện", "Module", "Terminal", "DeviceNet", "CANopen", "Fieldbus", 
        "mounting", "Chế độ điều khiển", "Khả năng bảo vệ", "Ứng dụng", 
        "Kết nối", "Chứng nhận", "Tiêu chuẩn", "Cấp bảo vệ"
    ]
    
    # Kiểm tra xem đây có phải là trường đặc biệt không
    is_special_field = False
    for field in special_fields:
        if field in html_content:
            is_special_field = True
            break
    
    # Kiểm tra nếu có cả phần hiển thị ngắn và phần mở rộng
    has_more = value_cell.select_one('.moreellipses') and value_cell.select_one('.morecontent')
    
    if has_more:
        # Phương pháp 1: Truy cập trực tiếp vào các phần tử DOM
        visible_text = ""
        morecontent_text = ""
        
        # Lấy trực tiếp text từ span đầu tiên (phần hiển thị)
        first_span = value_cell.select_one('div > span')
        if first_span:
            visible_text = first_span.get_text(strip=True)
            # Xóa bỏ nội dung của moreellipses trong phần hiển thị nếu có
            visible_text = re.sub(r'\[\.\.\.\]', '', visible_text)
            visible_text = visible_text.strip()
        
        # Lấy nội dung trong morecontent
        morecontent = value_cell.select_one('.morecontent')
        if morecontent:
            # Lấy nội dung từ span đầu tiên trong morecontent
            morecontent_span = morecontent.select_one('span')
            if morecontent_span:
                morecontent_text = morecontent_span.get_text(strip=True)
        
        # Nếu là trường đặc biệt (ứng dụng, chế độ điều khiển, khả năng bảo vệ, v.v.)
        if is_special_field:
            # Xử lý các trường hợp đặc biệt
            if visible_text and morecontent_text:
                # Phân tách thành các phần riêng biệt, xử lý cả dấu phẩy và cả dấu chấm
                visible_parts = [part.strip() for part in re.split(r',|\.\s+', visible_text) if part.strip()]
                morecontent_parts = [part.strip() for part in re.split(r',|\.\s+', morecontent_text) if part.strip()]
                
                # Tạo danh sách các phần không trùng lặp
                combined_parts = []
                
                # Thêm các phần từ visible_text
                for part in visible_parts:
                    if part and part not in combined_parts:
                        # Kiểm tra xem phần này có chứa chuỗi con "mode" bị lặp không
                        if "mode" in part.lower():
                            # Loại bỏ trường hợp "mal voltage mode" hoặc tương tự
                            if re.search(r'^\w{1,4}\s+\w+\s+mode$', part.lower()):
                                continue
                        combined_parts.append(part)
                
                # Thêm các phần từ morecontent_text mà không trùng lặp
                for part in morecontent_parts:
                    # Kiểm tra xem phần này có phải là một phần của phần nào trong combined_parts không
                    is_duplicate = False
                    
                    # Kiểm tra phần text có bị trùng lặp một phần với các phần khác không
                    for existing_part in combined_parts:
                        # Kiểm tra các trường hợp trùng lặp
                        if (part in existing_part or existing_part in part or 
                            # Kiểm tra xem có chung từ khóa không (với "protection", "mode")
                            (("protection" in part.lower() and "protection" in existing_part.lower()) and 
                             (part.lower().split()[0] == existing_part.lower().split()[0])) or
                            (("mode" in part.lower() and "mode" in existing_part.lower()) and 
                             any(w in existing_part.lower() for w in part.lower().split() if w != "mode"))):
                            is_duplicate = True
                            break
                    
                    # Kiểm tra trường hợp đặc biệt
                    if "mode" in part.lower() and re.search(r'^\w{1,4}\s+\w+\s+mode$', part.lower()):
                        is_duplicate = True
                    
                    # Trường hợp "ply" trong "supply"
                    if len(part) <= 3 and any(part.lower() in existing.lower() for existing in combined_parts):
                        is_duplicate = True
                    
                    if not is_duplicate and part and part not in combined_parts:
                        combined_parts.append(part)
                
                # Nối lại thành một chuỗi hoàn chỉnh
                full_value = ", ".join(combined_parts)
            else:
                # Nếu một trong hai phần trống, lấy phần còn lại
                full_value = visible_text if visible_text else morecontent_text
        else:
            # Các trường hợp thông thường
            if visible_text and morecontent_text:
                # Nếu phần hiển thị đã có dấu phẩy ở cuối
                if visible_text.endswith(','):
                    full_value = visible_text + ' ' + morecontent_text
                else:
                    # Kiểm tra xem phần đầu có nằm trong phần sau không
                    if visible_text in morecontent_text:
                        full_value = morecontent_text
                    else:
                        # Kiểm tra xem có phần trùng lặp không
                        combined_parts = []
                        visible_parts = [part.strip() for part in visible_text.split(',')]
                        morecontent_parts = [part.strip() for part in morecontent_text.split(',')]
                        
                        # Thêm các phần từ visible_text
                        for part in visible_parts:
                            if part and part not in combined_parts:
                                combined_parts.append(part)
                        
                        # Thêm các phần từ morecontent_text
                        for part in morecontent_parts:
                            is_duplicate = False
                            for existing_part in combined_parts:
                                if part in existing_part or existing_part in part:
                                    is_duplicate = True
                                    break
                            
                            if not is_duplicate and part and part not in combined_parts:
                                combined_parts.append(part)
                        
                        full_value = ", ".join(combined_parts)
            else:
                full_value = visible_text if visible_text else morecontent_text
        
        # Làm sạch kết quả
        full_value = re.sub(r'\[\.\.\.\]', '', full_value)  # Loại bỏ [...]
        full_value = re.sub(r'Hiển thị (thêm|bớt)', '', full_value)  # Loại bỏ "Hiển thị thêm/bớt"
        full_value = re.sub(r'\s+', ' ', full_value).strip()  # Chuẩn hóa khoảng trắng
        
        # Loại bỏ trùng lặp một lần nữa - so sánh các phần phân tách bởi dấu phẩy
        parts = [part.strip() for part in full_value.split(',')]
        unique_parts = []
        for part in parts:
            if part and part not in unique_parts and not any(
                part in unique and len(part) < len(unique) for unique in unique_parts
            ):
                # Kiểm tra đặc biệt cho các trường có từ khóa "mode" hoặc "protection"
                if "mode" in part.lower() or "protection" in part.lower():
                    duplicate = False
                    for existing in unique_parts:
                        if (("mode" in existing.lower() and "mode" in part.lower()) and
                            any(word in existing.lower() for word in part.lower().split() if word != "mode")):
                            duplicate = True
                            break
                        if (("protection" in existing.lower() and "protection" in part.lower()) and
                            any(word in existing.lower() for word in part.lower().split() if word != "protection")):
                            duplicate = True
                            break
                    
                    if not duplicate:
                        unique_parts.append(part)
                else:
                    unique_parts.append(part)
        
        full_value = ", ".join(unique_parts)
        
        # Nếu đã tìm được giá trị đầy đủ, trả về kết quả
        if full_value:
            return full_value
    
    # Phương pháp 2: Phân tích trực tiếp HTML nếu phương pháp trên không thành công
    if is_special_field and has_more:
        try:
            # Phân tích HTML bằng cách xác định các phần tử con
            # Regex để tìm nội dung trong span đầu tiên và morecontent
            visible_match = re.search(r'<span[^>]*>(.*?)<span class="moreellipses"', html_content, re.DOTALL)
            morecontent_match = re.search(r'<span class="morecontent"><span[^>]*>(.*?)</span>', html_content, re.DOTALL)
            
            visible_text = ""
            morecontent_text = ""
            
            # Lấy nội dung từ các phần đã tìm thấy
            if visible_match:
                visible_text = BeautifulSoup(visible_match.group(1), 'html.parser').get_text(strip=True)
            
            if morecontent_match:
                morecontent_text = BeautifulSoup(morecontent_match.group(1), 'html.parser').get_text(strip=True)
            
            # Kết hợp nội dung và loại bỏ trùng lặp
            if visible_text and morecontent_text:
                # Phân tách thành các phần
                visible_parts = [part.strip() for part in re.split(r',|\.\s+', visible_text) if part.strip()]
                morecontent_parts = [part.strip() for part in re.split(r',|\.\s+', morecontent_text) if part.strip()]
                
                # Tạo danh sách các phần không trùng lặp
                combined_parts = []
                
                # Thêm các phần từ visible_text
                for part in visible_parts:
                    if part and part not in combined_parts:
                        # Kiểm tra trường hợp đặc biệt
                        if "mode" in part.lower() and re.search(r'^\w{1,4}\s+\w+\s+mode$', part.lower()):
                            continue
                        combined_parts.append(part)
                
                # Thêm các phần từ morecontent_text mà không trùng lặp
                for part in morecontent_parts:
                    is_duplicate = False
                    for existing_part in combined_parts:
                        if (part in existing_part or existing_part in part or
                            (("protection" in part.lower() and "protection" in existing_part.lower()) and
                             any(w in existing_part.lower() for w in part.lower().split() if w != "protection")) or
                            (("mode" in part.lower() and "mode" in existing_part.lower()) and
                             any(w in existing_part.lower() for w in part.lower().split() if w != "mode"))):
                            is_duplicate = True
                            break
                    
                    # Kiểm tra trường hợp đặc biệt
                    if "mode" in part.lower() and re.search(r'^\w{1,4}\s+\w+\s+mode$', part.lower()):
                        is_duplicate = True
                    
                    # Trường hợp "ply" trong "supply"
                    if len(part) <= 3 and any(part.lower() in existing.lower() for existing in combined_parts):
                        is_duplicate = True
                    
                    if not is_duplicate and part and part not in combined_parts:
                        combined_parts.append(part)
                
                # Nối lại thành chuỗi
                full_value = ", ".join(combined_parts)
            else:
                full_value = visible_text if visible_text else morecontent_text
            
            # Làm sạch kết quả
            full_value = re.sub(r'\s+', ' ', full_value).strip()
            
            if full_value:
                return full_value
        except Exception as e:
            print(f"Lỗi khi phân tích HTML: {str(e)}")
    
    # Phương pháp 3: Tìm tất cả các span
    all_spans = []
    for span in value_cell.select('span'):
        # Bỏ qua các phần tử UI
        skip_classes = ['moreellipses', 'morelink']
        if not any(cls in span.get('class', []) for cls in skip_classes) and span.get('role') != 'button':
            span_text = span.get_text(strip=True)
            # Kiểm tra trường hợp đặc biệt
            if is_special_field and "mode" in span_text.lower() and re.search(r'^\w{1,4}\s+\w+\s+mode$', span_text.lower()):
                continue
                
            if span_text and span_text not in all_spans:  # Thêm kiểm tra trùng lặp
                all_spans.append(span_text)
    
    if all_spans:
        # Nối tất cả các span lại với nhau
        full_text = ', '.join(all_spans)
        # Làm sạch text
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        
        # Loại bỏ trùng lặp
        parts = [part.strip() for part in full_text.split(',')]
        unique_parts = []
        for part in parts:
            if part and part not in unique_parts and not any(
                part in unique and len(part) < len(unique) for unique in unique_parts
            ):
                # Kiểm tra đặc biệt cho các trường có từ khóa "mode" hoặc "protection"
                if "mode" in part.lower() or "protection" in part.lower():
                    duplicate = False
                    for existing in unique_parts:
                        if (("mode" in existing.lower() and "mode" in part.lower()) and
                            any(word in existing.lower() for word in part.lower().split() if word != "mode")):
                            duplicate = True
                            break
                        if (("protection" in existing.lower() and "protection" in part.lower()) and
                            any(word in existing.lower() for word in part.lower().split() if word != "protection")):
                            duplicate = True
                            break
                    
                    if not duplicate:
                        unique_parts.append(part)
                else:
                    unique_parts.append(part)
        
        return ", ".join(unique_parts)
    
    # Phương pháp 4: Lấy tất cả text từ ô và làm sạch
    all_text = value_cell.get_text(strip=True)
    all_text = re.sub(r'\[\.\.\.\]', '', all_text)
    all_text = re.sub(r'Hiển thị (thêm|bớt)', '', all_text)
    all_text = re.sub(r'\s+', ' ', all_text).strip()
    
    # Loại bỏ trùng lặp từ all_text
    parts = [part.strip() for part in all_text.split(',')]
    unique_parts = []
    for part in parts:
        if part and part not in unique_parts and not any(
            part in unique and len(part) < len(unique) for unique in unique_parts
        ):
            # Kiểm tra đặc biệt với từ "mode" hoặc "protection"
            if "mode" in part.lower() or "protection" in part.lower():
                duplicate = False
                for existing in unique_parts:
                    if (("mode" in existing.lower() and "mode" in part.lower()) and
                        any(word in existing.lower() for word in part.lower().split() if word != "mode")):
                        duplicate = True
                        break
                    if (("protection" in existing.lower() and "protection" in part.lower()) and
                        any(word in existing.lower() for word in part.lower().split() if word != "protection")):
                        duplicate = True
                        break
                
                if not duplicate:
                    unique_parts.append(part)
            else:
                # Trường hợp "ply" trong "supply"
                if len(part) <= 3 and any(part.lower() in existing.lower() for existing in unique_parts):
                    continue
                unique_parts.append(part)
    
    return ", ".join(unique_parts)

def extract_product_urls(url):
    """
    Trích xuất tất cả URL sản phẩm từ một URL danh mục
    """
    from urllib.parse import urlparse  # Thêm import urlparse ở đây
    
    product_urls = []
    processed_pages = set()
    pages_to_process = [url]
    
    is_led_page = "den-thap-led-sang-tinh-chop-nhay-d45mm-qlight-st45l-and-st45ml-series_4779" in url
    is_baa_special_page = "baa.vn/vn/Category/" in url
    
    while pages_to_process:
        current_url = pages_to_process.pop(0)
        
        # Bỏ qua trang đã xử lý
        if current_url in processed_pages:
            continue
            
        # Đánh dấu đã xử lý
        processed_pages.add(current_url)
        
        print(f"Đang xử lý URL danh mục: {current_url}")
        
        # Lấy nội dung HTML
        html = get_html_content(current_url)
        if not html:
            print(f"Không thể tải nội dung từ {current_url}")
            continue
            
        # Phân tích HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # Xác định nếu đây là trang danh sách sản phẩm từ BAA.vn
        is_product_list_page = False
        if 'baa.vn' in current_url.lower():
            is_product_list_page = True
            print(f"Đây là trang danh mục BAA.vn: {current_url}")
        
        # Khi xử lý trang BAA.vn
        if is_product_list_page:
            # DEBUG: In ra cấu trúc HTML để kiểm tra
            print("Phân tích cấu trúc HTML trang BAA.vn:")
            
            # Tìm trực tiếp các thẻ a trong card product
            product_links = []
            
            # Chiến lược 1: Tìm các thẻ a trong .card.product__card
            card_links = soup.select('.card.product__card a[href]')
            if card_links:
                product_links.extend(card_links)
                print(f"Chiến lược 1: Tìm thấy {len(card_links)} liên kết trong .card.product__card")
            
            # Chiến lược 2: Tìm các thẻ a trong h3.product__name
            name_links = soup.select('h3.product__name a[href]')
            if name_links:
                product_links.extend(name_links)
                print(f"Chiến lược 2: Tìm thấy {len(name_links)} liên kết trong h3.product__name")
            
            # Chiến lược 3: Tìm tất cả các thẻ a có href chứa '/product/'
            product_href_links = soup.select('a[href*="/product/"]')
            if product_href_links:
                product_links.extend(product_href_links)
                print(f"Chiến lược 3: Tìm thấy {len(product_href_links)} liên kết chứa '/product/'")
            
            # Chiến lược 4: Tìm tất cả các thẻ a có href chứa '/san-pham/'
            san_pham_links = soup.select('a[href*="/san-pham/"]')
            if san_pham_links:
                product_links.extend(san_pham_links)
                print(f"Chiến lược 4: Tìm thấy {len(san_pham_links)} liên kết chứa '/san-pham/'")
            
            # Loại bỏ trùng lặp bằng cách kiểm tra href
            unique_product_links = {}
            for link in product_links:
                href = link.get('href')
                if href and ('/product/' in href or '/san-pham/' in href):
                    unique_product_links[href] = link
            
            print(f"Tổng cộng tìm thấy {len(unique_product_links)} sản phẩm độc nhất trên trang BAA.vn")
            
            # Xử lý các sản phẩm
            for href, link in unique_product_links.items():
                # Đảm bảo URL đầy đủ
                if not href.startswith('http'):
                    if href.startswith('/'):
                        parsed_url = urlparse(current_url)
                        full_url = f"{parsed_url.scheme}://{parsed_url.netloc}{href}"
                    else:
                        full_url = f"{current_url.rstrip('/')}/{href}"
                else:
                    full_url = href
                
                if full_url not in product_urls:
                    product_urls.append(full_url)
                    print(f"Thêm URL sản phẩm từ BAA.vn: {full_url}")
            
            # Xử lý phân trang cho BAA.vn
            # Kiểm tra nếu có phân trang
            pagination = soup.select('.pagination li a[href]')
            if pagination:
                print(f"Tìm thấy {len(pagination)} liên kết phân trang với bộ chọn .pagination li a[href]")
                for page_link in pagination:
                    # Lấy URL trang
                    page_url = page_link.get('href')
                    if not page_url:
                        continue
                    
                    # Bỏ qua trang hiện tại
                    if page_url == current_url:
                        continue
                        
                    # Đảm bảo URL đầy đủ
                    if not page_url.startswith('http'):
                        if page_url.startswith('/'):
                            parsed_url = urlparse(current_url)
                            page_url = f"{parsed_url.scheme}://{parsed_url.netloc}{page_url}"
                        else:
                            page_url = f"{current_url.rstrip('/')}/{page_url}"
                    
                    # Bỏ qua các trang đã xử lý
                    if page_url in processed_pages:
                        continue
                    
                    # Trang số/phân trang
                    pages_to_process.append(page_url)
                    print(f"Thêm URL phân trang (cách 1): {page_url}")
            
            # Thử tìm các nút phân trang khác nếu chưa tìm thấy
            if not pagination:
                # Tìm các liên kết có chứa "page" trong href
                page_links = soup.select('a[href*="page"]')
                if page_links:
                    print(f"Tìm thấy {len(page_links)} liên kết phân trang với bộ chọn a[href*='page']")
                    for page_link in page_links:
                        page_url = page_link.get('href')
                        if page_url and page_url != current_url and page_url not in processed_pages:
                            # Đảm bảo URL đầy đủ
                            if not page_url.startswith('http'):
                                if page_url.startswith('/'):
                                    parsed_url = urlparse(current_url)
                                    page_url = f"{parsed_url.scheme}://{parsed_url.netloc}{page_url}"
                                else:
                                    page_url = f"{current_url.rstrip('/')}/{page_url}"
                            
                            pages_to_process.append(page_url)
                            print(f"Thêm URL phân trang (cách 2): {page_url}")
            
            # Tạo các URL phân trang dựa trên mẫu cho BAA.vn nếu vẫn không tìm thấy
            if is_baa_special_page and '/page/' not in current_url:
                # Kiểm tra số trang thực tế từ thông tin pagination trước khi tạo URL phân trang
                pagination_numbers = []
                
                # Tìm các nút số trang
                page_nums = soup.select('.pagination li a')
                for page_num_link in page_nums:
                    # Lấy text của liên kết phân trang
                    page_text = page_num_link.get_text(strip=True)
                    # Kiểm tra nếu text là số trang
                    if page_text and page_text.isdigit():
                        pagination_numbers.append(int(page_text))
                
                # Nếu tìm thấy các số trang, chỉ tạo URL cho các trang thực tế
                if pagination_numbers:
                    max_page = max(pagination_numbers)
                    base_url = current_url.rstrip('/')
                    
                    # Chỉ tạo URL cho các trang từ 2 đến max_page
                    for page_num in range(2, max_page + 1):
                        page_url = f"{base_url}/page/{page_num}/"
                        if page_url not in processed_pages:
                            pages_to_process.append(page_url)
                            print(f"Thêm URL phân trang (dựa trên số trang thực tế): {page_url}")
                else:
                    # Kiểm tra xem có nút "Trang sau" hay không
                    next_page = soup.select_one('.pagination li.next a, .pagination a.next, a.next')
                    if next_page:
                        # Chỉ tạo URL cho trang tiếp theo
                        base_url = current_url.rstrip('/')
                        page_url = f"{base_url}/page/2/"
                        if page_url not in processed_pages:
                            pages_to_process.append(page_url)
                            print(f"Thêm URL phân trang (dựa trên nút Trang sau): {page_url}")
                    else:
                        print("Không tìm thấy phân trang, không tạo URL phân trang tự động")
        else:
            # Xử lý cho các trang không phải BAA.vn như trước
            product_containers = []
            
            # Cấu trúc 1: Các container sản phẩm phổ biến
            containers = soup.select('.product-list-item, .product-item, .product-items, .products-grid')
            if containers:
                product_containers.extend(containers)
                print(f"Tìm thấy {len(containers)} container sản phẩm")
            
            # Cấu trúc 2: Grid hoặc list sản phẩm
            list_containers = soup.select('.products-grid, .products-list, .listing, .product-listing')
            if list_containers:
                product_containers.extend(list_containers)
                print(f"Tìm thấy {len(list_containers)} grid/list sản phẩm")
            
            # Cấu trúc 3: Card sản phẩm - đặc biệt xử lý cho đèn tháp LED
            card_containers = soup.select('.product-card, .product-item-info, .card.product__card, a.card.product__card')
            if card_containers:
                product_containers.extend(card_containers)
                print(f"Tìm thấy {len(card_containers)} sản phẩm từ card")
            
            # Nếu không tìm thấy container, thử tìm trực tiếp các liên kết sản phẩm
            if not product_containers:
                # Tìm tất cả liên kết có thể là liên kết sản phẩm
                all_product_links = soup.select('a[href*="/san-pham/"], a[href*="/product/"], a.product-item-link, a.product_link')
                
                # Xử lý từng liên kết
                for link in all_product_links:
                    href = link.get('href')
                    if not href:
                        continue
                        
                    # Đảm bảo URL đầy đủ
                    product_url = href
                    if not product_url.startswith('http'):
                        if product_url.startswith('/'):
                            parsed_url = urlparse(current_url)
                            product_url = f"{parsed_url.scheme}://{parsed_url.netloc}{product_url}"
                        else:
                            product_url = f"{current_url.rstrip('/')}/{product_url}"
                    
                    # Bỏ qua các URL không liên quan - cho URL đèn tháp LED thì cho qua tất cả
                    if not is_led_page and ('/category/' in product_url.lower() or '/danh-muc/' in product_url.lower() or '/page/' in product_url):
                        continue
                        
                    # Thêm vào danh sách nếu chưa có
                    if product_url not in product_urls:
                        # Với URL đèn tháp LED, bỏ qua kiểm tra is_product_url
                        if is_led_page or is_product_url(product_url):
                            product_urls.append(product_url)
                
            # Xử lý phân trang (tìm các liên kết đến các trang tiếp theo)
            pagination_links = soup.select('ul.pagination li a, .pages a')
            for page_link in pagination_links:
                # Lấy URL trang
                page_url = page_link.get('href')
                if not page_url:
                    continue
                    
                # Đảm bảo URL đầy đủ
                if not page_url.startswith('http'):
                    if page_url.startswith('/'):
                        parsed_url = urlparse(current_url)
                        page_url = f"{parsed_url.scheme}://{parsed_url.netloc}{page_url}"
                    else:
                        page_url = f"{current_url.rstrip('/')}/{page_url}"
                
                # Bỏ qua các trang đã xử lý
                if page_url in processed_pages:
                    continue
                    
                # Kiểm tra URL có phải là URL danh mục không
                if is_category_url(page_url):
                    pages_to_process.append(page_url)
                    print(f"Thêm URL phân trang: {page_url}")
    
    print(f"Đã xử lý {len(processed_pages)} trang, tìm thấy {len(product_urls)} URL sản phẩm")
    return product_urls

def get_product_info(url, required_fields=None):
    """
    Trích xuất thông tin sản phẩm dựa trên mẫu URL
    """
    product_info_list = []
    
    # Kiểm tra nếu URL là trang danh mục
    if is_category_url(url):
        # Trích xuất tất cả URL sản phẩm hợp lệ từ trang danh mục
        product_urls = extract_product_urls(url)
        
        # Xử lý từng URL sản phẩm hợp lệ
        for i, product_url in enumerate(product_urls):
            product_info = extract_product_info(product_url, required_fields, i+1)
            if product_info:
                # Đảm bảo trường URL được loại bỏ nếu không có trong required_fields
                if required_fields and 'URL' not in required_fields and 'URL' in product_info:
                    product_info.pop('URL')
                product_info_list.append(product_info)
    
    # Kiểm tra nếu URL là trang sản phẩm hợp lệ
    elif is_product_url(url):
        product_info = extract_product_info(url, required_fields, 1)
        if product_info:
            # Đảm bảo trường URL được loại bỏ nếu không có trong required_fields
            if required_fields and 'URL' not in required_fields and 'URL' in product_info:
                product_info.pop('URL')
            product_info_list.append(product_info)
    
    return product_info_list

def scrape_product_info(product_urls, excel_template_path):
    """Thu thập thông tin từ danh sách URL sản phẩm và xuất ra file Excel"""
    # Lọc các URL là URL sản phẩm hợp lệ
    valid_product_urls = [url for url in product_urls if is_product_url(url)]
    print(f"Tìm thấy {len(valid_product_urls)} URL sản phẩm hợp lệ từ {len(product_urls)} URL đầu vào")
    
    # Gửi thông báo bắt đầu
    socketio.emit('progress_update', {'percent': 0, 'message': f'Bắt đầu thu thập thông tin từ {len(valid_product_urls)} sản phẩm'})
    
    # Các trường cần thu thập - thêm cột Giá trước cột Tổng quan
    required_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'Tổng quan']
    
    print(f"Các trường cần thu thập: {required_fields}")
    
    all_products_info = []
    total_products = len(valid_product_urls)
    
    for i, url in enumerate(valid_product_urls):
        # Tính toán tiến trình
        progress = int((i / total_products) * 100)
        # Gửi cập nhật tiến trình
        socketio.emit('progress_update', {
            'percent': progress, 
            'message': f'Đang xử lý sản phẩm {i+1}/{total_products}: {url}'
        })
        
        try:
            # Thu thập thông tin cơ bản
            print(f"Đang thu thập thông tin từ: {url}")
            response = requests.get(url, headers=HEADERS)
            response.raise_for_status()
            
            # Parse HTML content
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract product name
            product_name_element = soup.select_one('.product__info .product-detail h1')
            if not product_name_element:
                product_name_element = soup.select_one('.product__name')
            if not product_name_element:
                product_name_element = soup.select_one('h1')
            product_name = product_name_element.text.strip() if product_name_element else 'Unknown'
            
            # Extract product code
            product_code = ''
            sku_element = soup.select_one('.product__symbol__value')
            if sku_element:
                product_code = sku_element.text.strip()
            if not product_code:
                sku_element = soup.select_one('.model-container .model')
                if sku_element:
                    product_code = sku_element.text.strip()
            
            # Extract product price
            product_price = ''
            # 1. Ưu tiên tìm giá từ phần tử span.product__price-print với data-root
            price_element_with_data = soup.select_one('span.product__price-print[data-root]')
            if price_element_with_data and 'data-root' in price_element_with_data.attrs:
                data_root = price_element_with_data.get('data-root')
                if data_root:
                    try:
                        price_value = int(data_root)
                        formatted_price = f"{price_value:,}".replace(",", ".")
                        
                        # Tìm đơn vị tiền tệ
                        price_unit = ""
                        
                        # Tìm trong cùng cell hoặc gần kề
                        parent_td = price_element_with_data.find_parent('td')
                        if parent_td:
                            unit_element = parent_td.select_one('span.product__price-unit')
                            if unit_element:
                                price_unit = unit_element.text.strip()
                        
                        # Nếu không tìm thấy, tìm element kế tiếp
                        if not price_unit:
                            next_element = price_element_with_data.find_next_sibling()
                            if next_element and 'product__price-unit' in next_element.get('class', []):
                                price_unit = next_element.text.strip()
                        
                        # Nếu vẫn không tìm thấy
                        if not price_unit:
                            unit_element = soup.select_one('span.product__price-unit')
                            if unit_element:
                                price_unit = unit_element.text.strip()
                            else:
                                price_unit = "₫"  # Mặc định
                        
                        # Định dạng giá cuối cùng
                        product_price = formatted_price + price_unit
                        print(f"Giá từ data-root: {product_price}")
                    except ValueError as e:
                        print(f"Lỗi khi xử lý giá từ data-root: {str(e)}")
            
            # 2. Nếu không tìm thấy giá từ data-root, thử các vị trí thông thường
            if not product_price:
                price_selectors = [
                    'div.product__card--price span.fw-bold.text-danger.text-start',
                    '.product__card--price span',
                    '.product__card--price .fw-bold',
                    '.product-price',
                    '.product__price-print',
                    '.price-box .price',
                    '.special-price .price',
                    '[data-price-type="finalPrice"] .price'
                ]
                
                for selector in price_selectors:
                    price_element = soup.select_one(selector)
                    if price_element:
                        price_text = price_element.text.strip()
                        
                        # Tìm đơn vị tiền tệ
                        price_unit = ""
                        unit_element = soup.select_one('span.product__price-unit')
                        if unit_element:
                            price_unit = unit_element.text.strip()
                        
                        # Định dạng giá
                        if price_unit and price_unit not in price_text:
                            product_price = price_text + price_unit
                        else:
                            product_price = price_text
                        
                        print(f"Giá sản phẩm: {product_price}")
                        break
            
            # 3. Làm sạch giá sản phẩm
            product_price = clean_price(product_price)
            
            # Tạo bảng HTML để lưu thông số kỹ thuật
            specs_table_html = '<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial;"><thead><tr><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
            
            # Biến lưu trữ số thông số đã thu thập
            params_count = 0
            
            # Tìm bảng thông số kỹ thuật
            specs_table = soup.select_one('table.feature__metadata--tab.active')
            if not specs_table:
                specs_table = soup.select_one('table.feature__metadata--tab')
            if not specs_table:
                specs_table = soup.select_one('.product-detail-params table')
            
            # Xử lý bảng thông số kỹ thuật nếu tìm thấy
            if specs_table:
                rows = specs_table.select('tbody tr')
                for row in rows:
                    cells = row.select('td')
                    if len(cells) >= 2:
                        # Lấy tên tham số
                        param_name_cell = cells[0]
                        param_value_cell = cells[1]
                        
                        # Lấy tên từ label hoặc text trực tiếp
                        label = param_name_cell.select_one('label')
                        param_name = label.text.strip() if label else param_name_cell.text.strip()
                        
                        # Xử lý giá trị với các nội dung bị ẩn trong morecontent
                        full_value = extract_full_value(param_value_cell)
                        
                        # Thêm vào bảng HTML
                        specs_table_html += f'<tr><td>{param_name}</td><td>{full_value}</td></tr>'
                        params_count += 1
            
            # Đóng bảng HTML
            specs_table_html += '</tbody></table>'
            
            # Nếu không có thông số nào được thu thập, hiển thị thông báo
            if params_count == 0:
                specs_table_html = '<p>Không tìm thấy thông số kỹ thuật cho sản phẩm này.</p>'
            
            # Thông tin debug
            print(f"Sản phẩm: {product_name}")
            print(f"Mã sản phẩm: {product_code}")
            print(f"Giá sản phẩm: {product_price}")
            print(f"Số thông số kỹ thuật thu thập được: {params_count}")
            print(f"Độ dài HTML thông số kỹ thuật: {len(specs_table_html)} ký tự")
            
            # Thông tin cơ bản của sản phẩm
            product_info = {
                'STT': i + 1,
                'Mã sản phẩm': product_code,
                'Tên sản phẩm': product_name,
                'Giá': product_price,
                'Tổng quan': specs_table_html
            }
            
            all_products_info.append(product_info)
            
        except Exception as e:
            print(f"Lỗi khi xử lý {url}: {str(e)}")
            socketio.emit('progress_update', {
                'percent': progress,
                'message': f'Lỗi khi xử lý {url}: {str(e)}'
            })
    
    # Gửi thông báo hoàn thành
    socketio.emit('progress_update', {
        'percent': 100, 
        'message': 'Đã hoàn thành việc thu thập thông tin'
    })
    
    print(f"Đã thu thập thông tin từ {len(all_products_info)} sản phẩm.")
    
    # Tạo DataFrame từ thông tin đã thu thập
    results_df = pd.DataFrame(all_products_info)
    
    # Đảm bảo có đủ các cột cần thiết
    for field in required_fields:
        if field not in results_df.columns:
            results_df[field] = ""
    
    # Chỉ giữ lại các cột theo thứ tự
    results_df = results_df[required_fields]
    
    # Tạo file Excel tạm để lưu kết quả
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
    temp_file.close()
    
    # Thiết lập writer với options để định dạng tốt hơn
    writer = pd.ExcelWriter(temp_file.name, engine='openpyxl')
    
    # Ghi DataFrame vào Excel
    results_df.to_excel(writer, index=False, sheet_name='Sản phẩm')
    
    # Lấy worksheet
    workbook = writer.book
    worksheet = writer.sheets['Sản phẩm']
    
    # Định dạng các cột
    # Đặt chiều rộng cho cột STT
    worksheet.column_dimensions['A'].width = 5
    # Đặt chiều rộng cho cột Mã sản phẩm
    worksheet.column_dimensions['B'].width = 20
    # Đặt chiều rộng cho cột Tên sản phẩm
    worksheet.column_dimensions['C'].width = 40
    # Đặt chiều rộng cho cột Giá
    worksheet.column_dimensions['D'].width = 15
    # Đặt chiều rộng cho cột Tổng quan
    worksheet.column_dimensions['E'].width = 80
    
    # Lưu file
    writer.close()
    
    return temp_file.name 

def search_autonics_product(product_code):
    """
    Tìm kiếm sản phẩm trên website Autonics theo mã sản phẩm
    
    Args:
        product_code (str): Mã sản phẩm cần tìm
        
    Returns:
        str: URL của sản phẩm nếu tìm thấy, None nếu không tìm thấy
    """
    try:
        # Xây dựng URL tìm kiếm
        search_url = f"https://www.autonics.com/vn/search/total?keyword={product_code}"
        print(f"Tìm kiếm sản phẩm {product_code} trên Autonics.com: {search_url}")
        
        # Gửi request đến trang tìm kiếm
        response = requests.get(search_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Tìm các sản phẩm trong kết quả tìm kiếm
        product_items = soup.select('.product-item')
        
        if not product_items:
            print(f"Không tìm thấy sản phẩm {product_code} trên Autonics.com")
            return None
        
        # Lấy liên kết của sản phẩm đầu tiên
        first_product = product_items[0]
        product_link = first_product.select_one('a')
        
        if not product_link:
            print(f"Không tìm thấy liên kết cho sản phẩm {product_code}")
            return None
        
        # Xây dựng URL đầy đủ
        product_url = product_link.get('href')
        if not product_url.startswith('http'):
            product_url = f"https://www.autonics.com{product_url}"
        
        print(f"Đã tìm thấy sản phẩm {product_code}: {product_url}")
        return product_url
    
    except Exception as e:
        print(f"Lỗi khi tìm kiếm sản phẩm {product_code}: {str(e)}")
        return None

def get_product_url(product_code):
    """
    Tạo URL sản phẩm từ mã sản phẩm
    
    Args:
        product_code (str): Mã sản phẩm
        
    Returns:
        str: URL của sản phẩm nếu tìm thấy, None nếu không tìm thấy
    """
    try:
        # Làm sạch mã sản phẩm
        clean_code = product_code.strip()
        
        # Cách 1: Tạo URL trực tiếp Autonics
        direct_url = f"https://www.autonics.com/vn/model/{clean_code}"
        print(f"Thử URL Autonics trực tiếp: {direct_url}")
        
        # Kiểm tra URL trực tiếp
        try:
            response = requests.head(direct_url, headers=HEADERS, timeout=5)
            if response.status_code == 200:
                print(f"URL Autonics trực tiếp hợp lệ: {direct_url}")
                return direct_url
        except:
            pass
        
        # Cách 2: Tìm kiếm trên Autonics
        autonics_url = search_autonics_product(clean_code)
        if autonics_url:
            return autonics_url
        
        # Cách 3: Thử các mẫu URL BAA.vn
        baa_patterns = [
            f"https://baa.vn/vn/san-pham/bo-chuyen-doi-nhiet-do-autonics-{clean_code.lower()}",
            f"https://baa.vn/vn/san-pham/cam-bien-autonics-{clean_code.lower()}",
            f"https://baa.vn/vn/san-pham/cam-bien-tinh-tien-autonics-{clean_code.lower()}",
            f"https://baa.vn/vn/san-pham/cam-bien-quang-autonics-{clean_code.lower()}",
            f"https://baa.vn/vn/san-pham/bo-dieu-khien-nhiet-do-autonics-{clean_code.lower()}",
            f"https://baa.vn/vn/san-pham/dong-ho-hien-thi-autonics-{clean_code.lower()}",
            f"https://baa.vn/vn/san-pham/bo-dem-autonics-{clean_code.lower()}",
            f"https://baa.vn/vn/san-pham/man-hinh-logic-autonics-{clean_code.lower()}"
        ]
        
        for url in baa_patterns:
            try:
                print(f"Thử URL BAA.vn: {url}")
                response = requests.head(url, timeout=3)
                if response.status_code == 200 and 'baa.vn' in response.url and 'tim-kiem' not in response.url:
                    print(f"URL BAA.vn hợp lệ: {response.url}")
                    return response.url
            except:
                continue
        
        # Cách 4: Tìm kiếm trên BAA.vn
        try:
            search_url = f"https://baa.vn/tim-kiem?q={clean_code}"
            print(f"Tìm kiếm trên BAA.vn: {search_url}")
            
            response = requests.get(search_url, timeout=5)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                product_links = soup.select('.product-name a, .product-item a.name, .product-list a')
                
                for link in product_links:
                    href = link.get('href')
                    link_text = link.text.strip()
                    
                    if href and clean_code.lower() in link_text.lower():
                        if not href.startswith('http'):
                            href = urljoin('https://baa.vn/', href)
                        print(f"Tìm thấy sản phẩm trên BAA.vn: {href}")
                        return href
        except:
            pass
        
        print(f"Không tìm thấy URL cho sản phẩm {clean_code}")
        return None
        
    except Exception as e:
        print(f"Lỗi khi tạo URL sản phẩm cho {product_code}: {str(e)}")
        return None

def extract_product_image(product_url):
    """Trích xuất hình ảnh sản phẩm từ trang chi tiết sản phẩm"""
    try:
        # Làm sạch URL trước khi truy cập
        clean_url = product_url.strip()
        
        # Xử lý URL có chứa kí tự đặc biệt như dấu ngoặc
        if '(' in clean_url or ')' in clean_url:
            # Trích xuất mã sản phẩm từ URL (phần trước dấu ngoặc)
            product_code = clean_url.split('/')[-1].split('(')[0].strip()
            # Tạo URL mới không chứa dấu ngoặc
            clean_url = f"https://www.autonics.com/vn/model/{product_code}"
            print(f"URL có chứa kí tự đặc biệt, đã chuyển sang: {clean_url}")
        else:
            # Lấy mã sản phẩm từ URL
            product_code = clean_url.split('/')[-1]
        
        # Tải trang chi tiết sản phẩm không giới hạn timeout
        try:
            response = requests.get(clean_url, headers=HEADERS)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Lỗi khi tải trang sản phẩm {clean_url}: {str(e)}")
            
            # Giới hạn chỉ tìm kiếm thêm nếu không thể mở URL trực tiếp
            alternative_url = search_autonics_product(product_code)
            if alternative_url:
                print(f"Tìm thấy URL thay thế: {alternative_url}")
                response = requests.get(alternative_url, headers=HEADERS)
                response.raise_for_status()
            else:
                print(f"Không tìm thấy URL thay thế cho {product_code}")
                return None
        
        # Parse HTML - Sử dụng html.parser nhanh hơn lxml
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Tìm hình ảnh sản phẩm chính với bộ selector tối ưu
        # Ưu tiên các selector phổ biến nhất
        img_element = None
        
        # Selector chính - thử trước tiên và nhanh nhất
        primary_selectors = [
            '.img img#img-chg',
            '.product-detail-image img',
            '.product-image img'
        ]
        
        # Thử các selector chính trước
        for selector in primary_selectors:
            img_element = soup.select_one(selector)
            if img_element and img_element.get('src'):
                break
        
        # Nếu không tìm thấy, thử tìm theo mã sản phẩm trong thuộc tính src/alt
        if not img_element:
            # Tìm tất cả các ảnh
            all_images = soup.select('img')
            
            # Ưu tiên tìm ảnh theo mã sản phẩm
            for img in all_images:
                src = img.get('src', '')
                alt = img.get('alt', '')
                
                # Kiểm tra nếu mã sản phẩm có trong src hoặc alt
                if product_code.lower() in src.lower() or product_code.lower() in alt.lower():
                    img_element = img
                    print(f"Tìm thấy ảnh theo mã sản phẩm: {product_code}")
                    break
            
            # Nếu vẫn không tìm thấy, tìm theo các từ khóa phổ biến
            if not img_element:
                keywords = ['product', 'item', 'main', 'gallery', 'detail']
                for img in all_images:
                    src = img.get('src', '')
                    for keyword in keywords:
                        if keyword in src.lower():
                            img_element = img
                            print(f"Tìm thấy ảnh theo từ khóa: {keyword}")
                            break
                    if img_element:
                        break
            
            # Nếu vẫn không tìm thấy, lấy ảnh đầu tiên có kích thước hợp lý
            if not img_element:
                for img in all_images:
                    src = img.get('src', '')
                    if src and not ('icon' in src.lower() or 'logo' in src.lower()):
                        width = img.get('width', '0')
                        height = img.get('height', '0')
                        
                        # Nếu có thuộc tính width, height > 100px thì có thể là ảnh sản phẩm
                        try:
                            if int(width) > 100 and int(height) > 100:
                                img_element = img
                                print(f"Tìm thấy ảnh có kích thước lớn: {width}x{height}")
                                break
                        except ValueError:
                            # Nếu không chuyển được sang số, bỏ qua
                            pass
                
                # Nếu vẫn không tìm được, lấy ảnh đầu tiên có src hợp lệ
                if not img_element:
                    for img in all_images:
                        src = img.get('src', '')
                        if src and src.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                            img_element = img
                            print(f"Tìm thấy ảnh đầu tiên có định dạng hợp lệ")
                            break
        
        # Nếu không tìm thấy ảnh
        if not img_element or not img_element.get('src'):
            print(f"Không tìm thấy hình ảnh trong trang {clean_url}")
            return None
        
        # Lấy URL hình ảnh và xử lý để chắc chắn là URL đầy đủ
        img_url = img_element.get('src')
        if not img_url.startswith('http'):
            # Xử lý URL tương đối
            base_url = "https://www.autonics.com"
            if img_url.startswith('//'):
                img_url = 'https:' + img_url
            elif img_url.startswith('/'):
                img_url = base_url + img_url
            else:
                img_url = urljoin(base_url, img_url)
            
        print(f"Đã tìm thấy ảnh sản phẩm {product_code}: {img_url}")
        return {
            'url': img_url,
            'code': product_code
        }
    
    except Exception as e:
        print(f"Lỗi khi trích xuất hình ảnh từ {product_url}: {str(e)}")
        return None

def download_product_image(img_info, output_folder):
    """Tải về hình ảnh sản phẩm"""
    img_path = None
    try:
        if not img_info or not img_info.get('url'):
            print("Không có thông tin ảnh để tải")
            return None
        
        # Tạo tên file ảnh theo định dạng mới
        current_date = datetime.now()
        year = current_date.year
        month = current_date.month
        
        # Tạo đường dẫn mới theo định dạng yêu cầu
        new_path = f"/wp-content/uploads/{year}/{month:02d}/{img_info['code']}.webp"
        
        # Tạo tên file an toàn - loại bỏ tất cả các kí tự không an toàn cho tên file
        safe_name = re.sub(r'[^\w\-_]', '_', img_info['code'])
        img_filename = f"{safe_name}.webp"
        
        # Tạo cấu trúc thư mục đầy đủ trong output_folder
        year_month_folder = os.path.join(output_folder, str(year), f"{month:02d}")
        os.makedirs(year_month_folder, exist_ok=True)
        
        # Đường dẫn đầy đủ để lưu file
        img_path = os.path.join(year_month_folder, img_filename)
        
        print(f"Đang tải ảnh từ: {img_info['url']}")
        print(f"Lưu vào: {img_path}")
        
        # Tải ảnh với timeout cao hơn
        max_retries = 2
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                # Tạo session mới để thêm các header cần thiết
                session = requests.Session()
                session.headers.update(HEADERS)
                
                # Thêm referer vào header để tránh bị chặn
                session.headers.update({
                    'Referer': 'https://www.autonics.com/',
                    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
                })
                
                # Tải ảnh với stream=True và timeout 10s
                response = session.get(img_info['url'], stream=True, timeout=10)
                response.raise_for_status()
                
                # Kiểm tra Content-Type
                content_type = response.headers.get('Content-Type', '')
                if not content_type.startswith('image/'):
                    print(f"Lỗi: URL không trả về hình ảnh (Content-Type: {content_type})")
                    # Thử lại nếu chưa đạt số lần thử tối đa
                    if retry_count == max_retries - 1:
                        return None
                    retry_count += 1
                    time.sleep(0.5)
                    continue
                    
                # Kiểm tra kích thước file
                content_length = int(response.headers.get('Content-Length', 0))
                if content_length < 100:  # Ảnh quá nhỏ có thể là lỗi
                    print(f"Cảnh báo: Kích thước ảnh quá nhỏ ({content_length} bytes)")
                    if content_length == 0:
                        print("Lỗi: File ảnh rỗng")
                        # Thử lại nếu chưa đạt số lần thử tối đa
                        if retry_count == max_retries - 1:
                            return None
                        retry_count += 1
                        time.sleep(0.5)
                        continue
                
                # Lưu ảnh với xử lý lỗi
                try:
                    with open(img_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    
                    # Kiểm tra kích thước file đã tải
                    file_size = os.path.getsize(img_path)
                    if file_size < 100:  # File quá nhỏ, có thể là lỗi
                        print(f"Cảnh báo: File đã tải có kích thước nhỏ: {file_size} bytes")
                        if file_size == 0:
                            print("Lỗi: File ảnh đã tải bị rỗng, sẽ thử lại")
                            os.remove(img_path)
                            # Thử lại nếu chưa đạt số lần thử tối đa
                            if retry_count == max_retries - 1:
                                return None
                            retry_count += 1
                            time.sleep(0.5)
                            continue
                    
                    # Xác nhận thành công
                    print(f"Đã tải thành công: {img_filename} ({file_size} bytes)")
                    return {
                        'path': img_path,
                        'url': f"https://haiphongtech.vn{new_path}"
                    }
                except Exception as e:
                    print(f"Lỗi khi lưu file: {str(e)}")
                    # Xóa file lỗi nếu có
                    if os.path.exists(img_path):
                        os.remove(img_path)
                    # Thử lại nếu chưa đạt số lần thử tối đa
                    if retry_count == max_retries - 1:
                        raise e
                    retry_count += 1
                    last_error = e
                    time.sleep(0.5)
                    continue
            
            except Exception as e:
                print(f"Lỗi khi tải ảnh (lần {retry_count+1}): {str(e)}")
                # Thử lại nếu chưa đạt số lần thử tối đa
                if retry_count == max_retries - 1:
                    raise e
                retry_count += 1
                last_error = e
                time.sleep(0.5)
                continue
        
        # Nếu đã thử tối đa nhưng vẫn thất bại
        if last_error:
            print(f"Đã thử {max_retries} lần nhưng không thành công: {str(last_error)}")
        return None
    
    except Exception as e:
        print(f"Lỗi khi tải hình ảnh: {str(e)}")
        # Xóa file lỗi nếu có
        if img_path and os.path.exists(img_path):
            try:
                os.remove(img_path)
                print(f"Đã xóa file lỗi: {img_path}")
            except:
                print(f"Không thể xóa file lỗi: {img_path}")
        return None

def download_jpg_product_image(img_info, output_folder):
    """Tải về hình ảnh sản phẩm chất lượng cao dưới định dạng JPG"""
    img_path = None
    try:
        if not img_info or not img_info.get('url'):
            print("Không có thông tin ảnh để tải")
            return None
        
        # Tạo tên file ảnh theo định dạng mới
        current_date = datetime.now()
        year = current_date.year
        month = current_date.month
        
        # Tạo đường dẫn mới theo định dạng yêu cầu
        new_path = f"/wp-content/uploads/{year}/{month:02d}/{img_info['code']}.jpg"
        
        # Tạo tên file an toàn - loại bỏ tất cả các kí tự không an toàn cho tên file
        safe_name = re.sub(r'[^\w\-_]', '_', img_info['code'])
        img_filename = f"{safe_name}.jpg"
        
        # Tạo cấu trúc thư mục đầy đủ trong output_folder
        year_month_folder = os.path.join(output_folder, str(year), f"{month:02d}")
        os.makedirs(year_month_folder, exist_ok=True)
        
        # Đường dẫn đầy đủ để lưu file
        img_path = os.path.join(year_month_folder, img_filename)
        
        print(f"Đang tải ảnh JPG chất lượng cao từ: {img_info['url']}")
        print(f"Lưu vào: {img_path}")
        
        # Tải ảnh với timeout cao hơn
        max_retries = 2
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                # Tạo session mới để thêm các header cần thiết
                session = requests.Session()
                session.headers.update(HEADERS)
                
                # Thêm referer vào header để tránh bị chặn
                session.headers.update({
                    'Referer': 'https://www.autonics.com/',
                    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
                })
                
                # Tải ảnh với stream=True và timeout 10s
                response = session.get(img_info['url'], stream=True, timeout=10)
                response.raise_for_status()
                
                # Kiểm tra Content-Type
                content_type = response.headers.get('Content-Type', '')
                if not content_type.startswith('image/'):
                    print(f"Lỗi: URL không trả về hình ảnh (Content-Type: {content_type})")
                    # Thử lại nếu chưa đạt số lần thử tối đa
                    if retry_count == max_retries - 1:
                        return None
                    retry_count += 1
                    time.sleep(0.5)
                    continue
                    
                # Kiểm tra kích thước file
                content_length = int(response.headers.get('Content-Length', 0))
                if content_length < 100:  # Ảnh quá nhỏ có thể là lỗi
                    print(f"Cảnh báo: Kích thước ảnh quá nhỏ ({content_length} bytes)")
                    if content_length == 0:
                        print("Lỗi: File ảnh rỗng")
                        # Thử lại nếu chưa đạt số lần thử tối đa
                        if retry_count == max_retries - 1:
                            return None
                        retry_count += 1
                        time.sleep(0.5)
                        continue
                
                # Đọc dữ liệu ảnh vào bộ nhớ
                image_data = io.BytesIO(response.content)
                
                try:
                    # Xử lý ảnh bằng Pillow
                    with Image.open(image_data) as img:
                        # Chuyển đổi sang RGB nếu cần
                        if img.mode in ('RGBA', 'P'):
                            img = img.convert('RGB')
                        
                        # Đảm bảo thư mục tồn tại
                        os.makedirs(os.path.dirname(img_path), exist_ok=True)
                        
                        # Lưu ảnh dưới định dạng JPEG với chất lượng cao nhất
                        img.save(img_path, 'JPEG', quality=100, optimize=True, subsampling=0)
                        
                    # Kiểm tra kích thước file đã tải
                    file_size = os.path.getsize(img_path)
                    if file_size < 100:  # File quá nhỏ, có thể là lỗi
                        print(f"Cảnh báo: File đã tải có kích thước nhỏ: {file_size} bytes")
                        if file_size == 0:
                            print("Lỗi: File ảnh đã tải bị rỗng, sẽ thử lại")
                            os.remove(img_path)
                            # Thử lại nếu chưa đạt số lần thử tối đa
                            if retry_count == max_retries - 1:
                                return None
                            retry_count += 1
                            time.sleep(0.5)
                            continue
                    
                    # Xác nhận thành công
                    print(f"Đã tải và lưu thành công ảnh JPG chất lượng cao: {img_filename} ({file_size} bytes)")
                    return {
                        'path': img_path,
                        'url': f"https://haiphongtech.vn{new_path}"
                    }
                except Exception as e:
                    print(f"Lỗi khi xử lý ảnh: {str(e)}")
                    # Xóa file lỗi nếu có
                    if os.path.exists(img_path):
                        os.remove(img_path)
                    # Thử lại nếu chưa đạt số lần thử tối đa
                    if retry_count == max_retries - 1:
                        raise e
                    retry_count += 1
                    last_error = e
                    time.sleep(0.5)
                    continue
            
            except Exception as e:
                print(f"Lỗi khi tải ảnh (lần {retry_count+1}): {str(e)}")
                # Thử lại nếu chưa đạt số lần thử tối đa
                if retry_count == max_retries - 1:
                    raise e
                retry_count += 1
                last_error = e
                time.sleep(0.5)
                continue
        
        # Nếu đã thử tối đa nhưng vẫn thất bại
        if last_error:
            print(f"Đã thử {max_retries} lần nhưng không thành công: {str(last_error)}")
        return None
    
    except Exception as e:
        print(f"Lỗi khi tải hình ảnh: {str(e)}")
        # Xóa file lỗi nếu có
        if img_path and os.path.exists(img_path):
            try:
                os.remove(img_path)
                print(f"Đã xóa file lỗi: {img_path}")
            except:
                print(f"Không thể xóa file lỗi: {img_path}")
        return None

def download_autonics_images(product_codes, output_folder):
    """Tải nhiều hình ảnh sản phẩm từ danh sách mã sản phẩm"""
    # Đảm bảo thư mục đầu ra tồn tại
    os.makedirs(output_folder, exist_ok=True)
    
    total_products = len(product_codes)
    successful_downloads = 0
    failed_downloads = 0
    skipped_codes = 0
    download_results = []
    
    # Gửi thông báo bắt đầu
    socketio.emit('progress_update', {
        'percent': 0, 
        'message': f'Bắt đầu tải {total_products} hình ảnh sản phẩm...'
    })
    
    # Lập danh sách các mã sản phẩm hợp lệ và đã làm sạch
    clean_product_codes = []
    for product_code in product_codes:
        code = product_code.strip()
        if not code:  # Bỏ qua các dòng trống
            skipped_codes += 1
            continue
        
        # Làm sạch mã sản phẩm
        clean_code = re.sub(r'[\(\)\s]+', '', code)
        if clean_code != code:
            print(f"Đã làm sạch mã sản phẩm: {code} -> {clean_code}")
        
        clean_product_codes.append(clean_code)
    
    # Số lượng sản phẩm thực tế
    actual_total = len(clean_product_codes)
    
    for i, product_code in enumerate(clean_product_codes):
        # Tính tiến trình
        progress = int((i / actual_total) * 100)
        socketio.emit('progress_update', {
            'percent': progress,
            'message': f'Đang xử lý sản phẩm {product_code} ({i+1}/{actual_total})'
        })
        
        # Kết quả ban đầu
        result = {
            'product_code': product_code,
            'status': 'Thất bại',
            'image_path': None,
            'image_url': None,
            'error': None
        }
        
        try:
            # Tạo URL sản phẩm
            product_url = f"https://www.autonics.com/vn/model/{product_code}"
            print(f"Đang truy cập URL: {product_url}")
            
            # Trích xuất thông tin hình ảnh
            img_info = extract_product_image(product_url)
            
            if not img_info:
                result['error'] = 'Không tìm thấy hình ảnh'
                failed_downloads += 1
                print(f"Không tìm thấy hình ảnh cho sản phẩm {product_code}")
            else:
                # Tải hình ảnh
                img_result = download_product_image(img_info, output_folder)
                
                if img_result:
                    result['status'] = 'Thành công'
                    result['image_path'] = img_result['path']
                    result['image_url'] = img_result['url']
                    successful_downloads += 1
                    print(f"Tải thành công ảnh cho sản phẩm {product_code}")
                else:
                    result['error'] = 'Không thể tải hình ảnh'
                    failed_downloads += 1
                    print(f"Tải ảnh thất bại cho sản phẩm {product_code}")
            
            # Thêm kết quả vào danh sách
            download_results.append(result)
            
        except Exception as e:
            error_msg = str(e)
            result['error'] = error_msg
            download_results.append(result)
            failed_downloads += 1
            print(f"Lỗi khi xử lý sản phẩm {product_code}: {error_msg}")
            
            # Tiếp tục với sản phẩm tiếp theo
            continue
    
    # Gửi thông báo hoàn thành
    completion_message = f'Hoàn thành! Đã tải {successful_downloads}/{actual_total} hình ảnh'
    if failed_downloads > 0:
        completion_message += f', {failed_downloads} thất bại'
    if skipped_codes > 0:
        completion_message += f', {skipped_codes} bỏ qua'
        
    socketio.emit('progress_update', {
        'percent': 100,
        'message': completion_message
    })
    
    # Tạo báo cáo kết quả
    summary = {
        'total': actual_total,
        'successful': successful_downloads,
        'failed': failed_downloads,
        'skipped': skipped_codes,
        'output_folder': output_folder,
        'results': download_results
    }
    
    # Tạo file Excel báo cáo kết quả
    try:
        report_path = os.path.join(output_folder, 'download_report.xlsx')
        report_df = pd.DataFrame(download_results)
        report_df.to_excel(report_path, index=False)
        print(f"Đã tạo báo cáo tại: {report_path}")
    except Exception as e:
        print(f"Lỗi khi tạo báo cáo: {str(e)}")
    
    return summary

def download_autonics_jpg_images(product_codes, output_folder):
    """Tải nhiều hình ảnh sản phẩm chất lượng cao dưới định dạng JPG từ danh sách mã sản phẩm"""
    # Đảm bảo thư mục đầu ra tồn tại
    os.makedirs(output_folder, exist_ok=True)
    
    total_products = len(product_codes)
    successful_downloads = 0
    failed_downloads = 0
    skipped_codes = 0
    download_results = []
    
    # Gửi thông báo bắt đầu
    socketio.emit('progress_update', {
        'percent': 0, 
        'message': f'Bắt đầu tải {total_products} hình ảnh JPG chất lượng cao...'
    })
    
    # Lập danh sách các mã sản phẩm hợp lệ và đã làm sạch
    clean_product_codes = []
    for product_code in product_codes:
        code = product_code.strip()
        if not code:  # Bỏ qua các dòng trống
            skipped_codes += 1
            continue
        
        # Làm sạch mã sản phẩm
        clean_code = re.sub(r'[\(\)\s]+', '', code)
        if clean_code != code:
            print(f"Đã làm sạch mã sản phẩm: {code} -> {clean_code}")
        
        clean_product_codes.append(clean_code)
    
    # Số lượng sản phẩm thực tế
    actual_total = len(clean_product_codes)
    
    for i, product_code in enumerate(clean_product_codes):
        # Tính tiến trình
        progress = int((i / actual_total) * 100)
        socketio.emit('progress_update', {
            'percent': progress,
            'message': f'Đang xử lý sản phẩm {product_code} ({i+1}/{actual_total})'
        })
        
        # Đặt giới hạn thời gian xử lý tối đa 10 giây cho mỗi sản phẩm
        start_time = time.time()
        
        # Kết quả ban đầu
        result = {
            'product_code': product_code,
            'status': 'Thất bại',
            'image_path': None,
            'image_url': None,
            'error': None
        }
        
        try:
            # Tạo URL sản phẩm
            product_url = f"https://www.autonics.com/vn/model/{product_code}"
            print(f"Đang truy cập URL: {product_url}")
            
            # Trích xuất thông tin hình ảnh
            img_info = extract_product_image(product_url)
            
            if not img_info:
                result['error'] = 'Không tìm thấy hình ảnh'
                failed_downloads += 1
                print(f"Không tìm thấy hình ảnh cho sản phẩm {product_code}")
            else:
                # Tải hình ảnh JPG chất lượng cao
                img_result = download_jpg_product_image(img_info, output_folder)
                
                if img_result:
                    result['status'] = 'Thành công'
                    result['image_path'] = img_result['path']
                    result['image_url'] = img_result['url']
                    successful_downloads += 1
                    print(f"Tải thành công ảnh JPG chất lượng cao cho sản phẩm {product_code}")
                else:
                    result['error'] = 'Không thể tải hình ảnh'
                    failed_downloads += 1
                    print(f"Tải ảnh JPG thất bại cho sản phẩm {product_code}")
            
            # Kiểm tra thời gian xử lý
            elapsed_time = time.time() - start_time
            print(f"Đã xử lý sản phẩm {product_code} trong {elapsed_time:.2f} giây")
            
            # Thêm kết quả vào danh sách
            download_results.append(result)
            
            # Tạm dừng nếu còn thời gian trong giới hạn 10 giây
            remaining_time = 10 - elapsed_time
            if remaining_time > 0 and remaining_time < 1:
                time.sleep(remaining_time)  # Chỉ chờ nếu còn ít hơn 1 giây
            
        except Exception as e:
            error_msg = str(e)
            result['error'] = error_msg
            download_results.append(result)
            failed_downloads += 1
            print(f"Lỗi khi xử lý sản phẩm {product_code}: {error_msg}")
            
            # Kiểm tra thời gian đã trôi qua
            elapsed_time = time.time() - start_time
            print(f"Xử lý thất bại sau {elapsed_time:.2f} giây")
            
            # Tiếp tục với sản phẩm tiếp theo
            continue
    
    # Gửi thông báo hoàn thành
    completion_message = f'Hoàn thành! Đã tải {successful_downloads}/{actual_total} hình ảnh JPG chất lượng cao'
    if failed_downloads > 0:
        completion_message += f', {failed_downloads} thất bại'
    if skipped_codes > 0:
        completion_message += f', {skipped_codes} bỏ qua'
        
    socketio.emit('progress_update', {
        'percent': 100,
        'message': completion_message
    })
    
    # Tạo báo cáo kết quả
    summary = {
        'total': actual_total,
        'successful': successful_downloads,
        'failed': failed_downloads,
        'skipped': skipped_codes,
        'output_folder': output_folder,
        'results': download_results
    }
    
    # Tạo file Excel báo cáo kết quả
    try:
        report_path = os.path.join(output_folder, 'download_report.xlsx')
        report_df = pd.DataFrame(download_results)
        report_df.to_excel(report_path, index=False)
        print(f"Đã tạo báo cáo tại: {report_path}")
    except Exception as e:
        print(f"Lỗi khi tạo báo cáo: {str(e)}")
    
    return summary 

def extract_product_documents(product_url_or_code):
    """
    Trích xuất danh sách các tài liệu PDF từ trang BAA.vn - phiên bản tối ưu
    
    Hàm này tìm kiếm tài liệu PDF từ website BAA.vn bằng cách:
    1. Truy cập trang sản phẩm
    2. Phân tích DOM để tìm các link PDF trong các phần tử như Link_download và feature__metadata__link--download
    
    Cải tiến:
    - Tăng timeout lên 60 giây
    - Tập trung vào các selector cụ thể trong HTML
    - Xử lý lỗi chi tiết
    """
    # Cache đơn giản để lưu trữ kết quả tìm kiếm
    cache_key = str(product_url_or_code).strip().lower()
    static_cache = getattr(extract_product_documents, 'cache', {})
    if cache_key in static_cache:
        print(f"Lấy kết quả từ cache cho: {product_url_or_code}")
        return static_cache[cache_key]
    
    try:
        # 1. Xác định URL sản phẩm
        product_url = None
        
        # Nếu là URL, sử dụng trực tiếp
        if isinstance(product_url_or_code, str) and product_url_or_code.startswith('http'):
            product_url = product_url_or_code
            print(f"Sử dụng URL trực tiếp: {product_url}")
        else:
            # Nếu là mã sản phẩm, tìm URL tương ứng
            product_url = get_product_url(product_url_or_code)
            if not product_url:
                print(f"Không tìm thấy URL sản phẩm cho mã: {product_url_or_code}")
                static_cache[cache_key] = []
                return []
        
        # 2. Tải trang sản phẩm với timeout tăng lên 60 giây
        print(f"Đang tải trang sản phẩm: {product_url}")
        
        # Tạo session với User-Agent giống trình duyệt
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Referer': 'https://baa.vn/'
        })
        
        # Tải trang với timeout 60 giây
        product_response = session.get(product_url, timeout=60)
        product_html = product_response.text
        
        # Parse HTML
        product_soup = BeautifulSoup(product_html, 'html.parser')
        
        # 3. Tìm kiếm các link PDF
        document_links = []
        
        # Tìm kiếm các liên kết tải tài liệu theo mẫu trong ví dụ
        # a. Tìm Link_download
        download_links = product_soup.select('a#Link_download[href$=".pdf"]')
        for link in download_links:
            href = link.get('href')
            if href:
                # Tìm tên file từ các phần tử liên quan
                parent_tr = link.find_parent('tr')
                if parent_tr:
                    filelink_elem = parent_tr.select_one('.filelink')
                    if filelink_elem:
                        name = filelink_elem.text.strip()
                    else:
                        name = href.split('/')[-1]
                else:
                    name = href.split('/')[-1]
                
                document_links.append({
                    'url': href,
                    'name': name
                })
                print(f"Đã tìm thấy link tải tài liệu (#Link_download): {name} - {href}")
        
        # b. Tìm .feature__metadata__link--download
        feature_links = product_soup.select('.feature__metadata__link--download[href$=".pdf"], span.feature__metadata__link--download')
        for link in feature_links:
            href = link.get('href')
            
            # Trường hợp span chứa href
            if not href and isinstance(link, Tag):
                href = link.get('href')
            
            # Một số trường hợp span không có href trực tiếp
            if not href:
                parent = link.parent
                if parent and parent.name == 'a':
                    href = parent.get('href')
                elif parent:
                    a_tag = parent.find('a')
                    if a_tag:
                        href = a_tag.get('href')
            
            if href and '.pdf' in href.lower():
                # Tìm tên file
                filelink_elem = link.select_one('.filelink')
                if filelink_elem:
                    name = filelink_elem.text.strip()
                else:
                    name = href.split('/')[-1]
                
                # Đảm bảo URL đầy đủ
                if not href.startswith('http'):
                    href = urljoin(product_url, href)
                
                document_links.append({
                    'url': href,
                    'name': name
                })
                print(f"Đã tìm thấy link tải tài liệu (.feature__metadata__link--download): {name} - {href}")
        
        # c. Tìm kiếm trong các bảng có chứa liên kết tài liệu
        catalog_tables = product_soup.select('.product-tab-content table, .tab-content table')
        for table in catalog_tables:
            for row in table.find_all('tr'):
                # Tìm các ô có chứa icon file và link PDF
                file_cells = row.select('td i.bi-file-earmark-text, td i.bi-file-pdf, td .filelink')
                if file_cells:
                    # Tìm link download trong hàng này
                    download_link = row.select_one('a[href$=".pdf"]')
                    if download_link:
                        href = download_link.get('href')
                        
                        # Tìm tên file
                        filelink_elem = row.select_one('.filelink')
                        if filelink_elem:
                            name = filelink_elem.text.strip()
                        else:
                            name = href.split('/')[-1]
                        
                        # Đảm bảo URL đầy đủ
                        if not href.startswith('http'):
                            href = urljoin(product_url, href)
                        
                        document_links.append({
                            'url': href,
                            'name': name
                        })
                        print(f"Đã tìm thấy link tải tài liệu (tìm trong bảng): {name} - {href}")
        
        # d. Kiểm tra tất cả các link có chứa PDF
        all_pdf_links = product_soup.select('a[href$=".pdf"]')
        for link in all_pdf_links:
            href = link.get('href')
            if href and 'document' in href.lower():
                name = link.text.strip()
                if not name:
                    name = href.split('/')[-1]
                
                # Đảm bảo URL đầy đủ
                if not href.startswith('http'):
                    href = urljoin(product_url, href)
                
                document_links.append({
                    'url': href,
                    'name': name
                })
                print(f"Đã tìm thấy link PDF khác: {name} - {href}")
        
        # 4. Loại bỏ các link trùng lặp
        unique_links = []
        added_urls = set()
        
        for link in document_links:
            if link['url'] not in added_urls:
                added_urls.add(link['url'])
                unique_links.append(link)
        
        print(f"Tìm thấy {len(unique_links)} tài liệu PDF không trùng lặp")
        
        # Lưu vào cache
        static_cache[cache_key] = unique_links
        
        return unique_links
    
    except Exception as e:
        print(f"Lỗi khi trích xuất tài liệu từ {product_url_or_code}: {str(e)}")
        traceback.print_exc()
        # Khởi tạo cache nếu chưa có
        if not hasattr(extract_product_documents, 'cache'):
            extract_product_documents.cache = {}
        static_cache[cache_key] = []
        return []

# Khởi tạo cache cho hàm
extract_product_documents.cache = {}

def download_product_document(doc_info, output_folder):
    """
    Tải tài liệu PDF từ URL và lưu vào thư mục đầu ra
    
    Args:
        doc_info (dict): Thông tin tài liệu cần tải
            - url: URL của tài liệu
            - name: Tên tài liệu (nếu không có sẽ lấy từ URL)
        output_folder (str): Đường dẫn thư mục đầu ra
        
    Returns:
        dict: Thông tin kết quả tải tài liệu
            - success: True nếu tải thành công, False nếu thất bại
            - path: Đường dẫn đến file đã tải (nếu thành công)
            - filename: Tên file đã tải (nếu thành công)
            - error: Thông báo lỗi (nếu thất bại)
            - url: URL của tài liệu
            - name: Tên gốc của tài liệu
    """
    try:
        # Tạo thư mục đầu ra nếu chưa tồn tại
        os.makedirs(output_folder, exist_ok=True)
        
        # Lấy URL tài liệu
        doc_url = doc_info['url']
        
        # Tạo tên file an toàn từ tên tài liệu
        doc_name = doc_info.get('name', '')
        if not doc_name:
            doc_name = doc_url.split('/')[-1]
        
        # Loại bỏ các ký tự không hợp lệ trong tên file
        safe_filename = re.sub(r'[\\/*?:"<>|]', '', doc_name)
        # Thêm .pdf nếu chưa có
        if not safe_filename.lower().endswith('.pdf'):
            safe_filename += '.pdf'
        
        # Tạo đường dẫn đầy đủ đến file đích
        file_path = os.path.join(output_folder, safe_filename)
        
        print(f"Đang tải tài liệu từ: {doc_url}")
        print(f"Lưu vào: {file_path}")
        
        # Tải tài liệu với số lần thử lại
        max_retries = 5
        current_retry = 0
        success = False
        error_msg = ""
        
        while current_retry < max_retries and not success:
            try:
                # Tạo session với User-Agent của trình duyệt
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                    'Accept': 'application/pdf,application/x-pdf,application/octet-stream,*/*',
                    'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Connection': 'keep-alive',
                    'Referer': 'https://baa.vn/'
                })
                
                # Tạo một stream request để tải file mà không cần đọc toàn bộ vào bộ nhớ
                response = session.get(doc_url, stream=True, timeout=60)  # Tăng timeout lên 60 giây
                
                # Kiểm tra content-type
                content_type = response.headers.get('Content-Type', '').lower()
                if 'application/pdf' not in content_type and not (content_type.startswith('application/') or 'octet-stream' in content_type):
                    print(f"CẢNH BÁO: Nội dung không phải PDF (Content-Type: {content_type})")
                    
                    # Kiểm tra nếu header đầu tiên của file là %PDF-
                    first_chunk = next(response.iter_content(chunk_size=10), None)
                    if first_chunk and not first_chunk.startswith(b'%PDF-'):
                        raise Exception(f"Nội dung tải về không phải là file PDF hợp lệ (Content-Type: {content_type})")
                
                # Kiểm tra content-length
                content_length = response.headers.get('Content-Length')
                if content_length:
                    content_length = int(content_length)
                    if content_length < 1000:
                        raise Exception(f"File quá nhỏ ({content_length} bytes), có thể không phải file PDF hợp lệ")
                    
                # Lưu file
                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                # Kiểm tra kích thước file
                file_size = os.path.getsize(file_path)
                if file_size < 1000:
                    raise Exception(f"File quá nhỏ ({file_size} bytes), có thể không phải file PDF hợp lệ")
                
                # Kiểm tra nội dung file
                with open(file_path, 'rb') as f:
                    header = f.read(5)
                    # Kiểm tra header PDF
                    if not header.startswith(b'%PDF-'):
                        raise Exception("File không có signature PDF hợp lệ (%PDF-)")
                
                print(f"Tải tài liệu thành công: {file_path} ({file_size} bytes)")
                success = True
                
                # Ngừng vòng lặp nếu thành công
                break
                
            except Exception as e:
                current_retry += 1
                error_msg = str(e)
                
                # Tăng thời gian chờ mỗi lần thử lại
                wait_time = current_retry * 2
                print(f"Lỗi khi tải tài liệu (lần thử {current_retry}/{max_retries}): {error_msg}")
                print(f"Thử lại sau {wait_time} giây...")
                
                if os.path.exists(file_path):
                    os.remove(file_path)
                
                time.sleep(wait_time)
        
        if success:
            return {
                'success': True,
                'path': file_path,  # Đổi từ filepath thành path
                'filename': safe_filename,
                'url': doc_url,
                'name': doc_name
            }
        else:
            return {
                'success': False,
                'error': error_msg,
                'url': doc_url,
                'name': doc_name
            }
    
    except Exception as e:
        error_msg = str(e)
        print(f"Lỗi khi tải tài liệu: {error_msg}")
        traceback.print_exc()
        
        return {
            'success': False,
            'error': error_msg,
            'url': doc_info.get('url', ''),
            'name': doc_info.get('name', '')
        }

def download_product_documents(product_codes_or_urls, output_folder='output_documents'):
    """
    Tải tất cả tài liệu cho danh sách mã sản phẩm hoặc URL
    
    Args:
        product_codes_or_urls (list): Danh sách mã sản phẩm hoặc URL
        output_folder (str): Thư mục đầu ra
        
    Returns:
        dict: Báo cáo về quá trình tải với các thông tin:
            - total_products: Tổng số sản phẩm
            - successful_products: Số sản phẩm tải thành công
            - failed_products: Số sản phẩm tải thất bại
            - product_results: Kết quả chi tiết cho mỗi sản phẩm
            - skipped_codes: Danh sách mã đã bị bỏ qua
            - output_folder: Thư mục đầu ra
    """
    # Kiểm tra input
    if not product_codes_or_urls:
        print("Không có mã sản phẩm hoặc URL nào được cung cấp")
        return None
    
    # Tạo thư mục đầu ra nếu chưa tồn tại
    os.makedirs(output_folder, exist_ok=True)
    
    # Khởi tạo biến theo dõi
    total_products = len(product_codes_or_urls)
    successful_products = 0
    failed_products = 0
    skipped_codes = []
    product_results = {}
    
    # Duyệt qua từng mã sản phẩm hoặc URL
    for i, item in enumerate(product_codes_or_urls, 1):
        print(f"\n[{i}/{total_products}] Đang xử lý: {item}")
        
        # Xác định xem đầu vào là URL hay mã sản phẩm
        if item.startswith('http'):
            product_url = item
            # Trích xuất mã sản phẩm từ URL nếu có thể
            try:
                parts = product_url.split('/')
                product_code = parts[-1] if parts[-1] else parts[-2]
                
                # Trích xuất tên sản phẩm từ URL (với URL từ baa.vn)
                product_name = ""
                if 'baa.vn' in product_url:
                    # Tìm tên sản phẩm từ phần cuối URL - thường có dạng ten-san-pham_xyz
                    product_code_parts = product_code.split('_')
                    if len(product_code_parts) > 0:
                        product_slug = product_code_parts[0]
                        # Chuyển slug thành tên đẹp hơn
                        product_name = ' '.join([part.capitalize() for part in product_slug.split('-')])
                
                # Nếu không thể trích xuất tên từ URL, thử tải trang sản phẩm
                if not product_name or product_name == product_code:
                    try:
                        # Tạo session với User-Agent giống trình duyệt
                        session = requests.Session()
                        session.headers.update({
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
                        })
                        response = session.get(product_url, timeout=30)
                        soup = BeautifulSoup(response.text, 'html.parser')
                        
                        # Thử tìm tên sản phẩm từ các selector thường gặp
                        name_selectors = [
                            'h1.product-title', '.product-name h1', '.page-title h1', 
                            '.product__name', '.product-detail h1', '.product-title'
                        ]
                        
                        for selector in name_selectors:
                            name_elem = soup.select_one(selector)
                            if name_elem:
                                product_name = name_elem.text.strip()
                                print(f"  Tìm thấy tên sản phẩm: {product_name}")
                                break
                    except Exception as e:
                        print(f"  Không thể trích xuất tên sản phẩm từ trang: {str(e)}")
            except:
                product_code = f"product_{i}"
                product_name = f"Product {i}"
        else:
            product_code = item
            product_url = get_product_url(product_code)
            product_name = f"Product {product_code}"  # Default name
            
            # Thử lấy tên sản phẩm từ URL
            if product_url:
                try:
                    # Tạo session với User-Agent giống trình duyệt
                    session = requests.Session()
                    session.headers.update({
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
                    })
                    response = session.get(product_url, timeout=30)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Thử tìm tên sản phẩm từ các selector thường gặp
                    name_selectors = [
                        'h1.product-title', '.product-name h1', '.page-title h1', 
                        '.product__name', '.product-detail h1', '.product-title'
                    ]
                    
                    for selector in name_selectors:
                        name_elem = soup.select_one(selector)
                        if name_elem:
                            product_name = name_elem.text.strip()
                            print(f"  Tìm thấy tên sản phẩm: {product_name}")
                            break
                except Exception as e:
                    print(f"  Không thể trích xuất tên sản phẩm từ trang: {str(e)}")
        
        # Bỏ qua nếu không tìm thấy URL sản phẩm
        if not product_url:
            print(f"  Không thể tạo URL cho mã sản phẩm: {product_code}")
            skipped_codes.append(product_code)
            product_results[product_code] = {
                'product_name': product_name,
                'success': False,
                'message': 'Không thể tạo URL sản phẩm',
                'documents': [],
                'failed_documents': [],
                'successful_documents': 0,
                'total_documents': 0,
                'failed_documents_count': 0
            }
            continue
        
        # Tạo thư mục cho sản phẩm - tất cả tài liệu của cùng một sản phẩm sẽ được lưu trong cùng một thư mục
        product_folder = os.path.join(output_folder, product_code)
        os.makedirs(product_folder, exist_ok=True)
        
        try:
            # Trích xuất liên kết tài liệu
            document_links = extract_product_documents(product_url)
            
            if not document_links:
                print(f"  Không tìm thấy tài liệu nào cho {product_code}")
                failed_products += 1
                product_results[product_code] = {
                    'product_name': product_name,
                    'success': False,
                    'message': 'Không tìm thấy tài liệu',
                    'documents': [],
                    'failed_documents': [],
                    'successful_documents': 0,
                    'total_documents': 0,
                    'failed_documents_count': 0
                }
                continue
            
            print(f"  Tìm thấy {len(document_links)} tài liệu")
            
            # Tải tài liệu và theo dõi kết quả
            documents = []
            failed_documents = []
            
            for j, doc_link in enumerate(document_links, 1):
                print(f"  Đang tải [{j}/{len(document_links)}]: {doc_link}")
                result = download_product_document(doc_link, product_folder)
                
                if result['success']:
                    documents.append(result)
                    print(f"    ✓ Đã tải: {result['path']}")
                else:
                    failed_documents.append(result)
                    print(f"    ✗ Lỗi: {result['error']}")
            
            # Cập nhật số liệu thống kê
            successful_documents = len(documents)
            failed_documents_count = len(failed_documents)
            
            if successful_documents > 0:
                successful_products += 1
                success = True
                message = f"Tải thành công {successful_documents}/{len(document_links)} tài liệu"
            else:
                failed_products += 1
                success = False
                message = "Không tài liệu nào được tải thành công"
            
            # Lưu kết quả cho sản phẩm này
            product_results[product_code] = {
                'product_name': product_name,
                'success': success,
                'message': message,
                'documents': documents,
                'failed_documents': failed_documents,
                'successful_documents': successful_documents,
                'total_documents': len(document_links),
                'failed_documents_count': failed_documents_count
            }
            
            # In kết quả tạm thời
            print(f"  Kết quả: {successful_documents} thành công, {failed_documents_count} thất bại")
            
        except Exception as e:
            print(f"  Lỗi khi tải tài liệu cho {product_code}: {str(e)}")
            traceback.print_exc()
            failed_products += 1
            
            product_results[product_code] = {
                'product_name': product_name,
                'success': False,
                'message': f"Lỗi: {str(e)}",
                'documents': [],
                'failed_documents': [],
                'successful_documents': 0,
                'total_documents': 0,
                'failed_documents_count': 0
            }
    
    # Tạo báo cáo tổng hợp
    print("\n--- Kết quả tải tài liệu ---")
    print(f"Tổng số sản phẩm: {total_products}")
    print(f"Thành công: {successful_products}")
    print(f"Thất bại: {failed_products}")
    print(f"Bỏ qua: {len(skipped_codes)}")
    
    # Tạo cấu trúc báo cáo
    report = {
        'total_products': total_products,
        'successful_products': successful_products,
        'failed_products': failed_products,
        'product_results': product_results,
        'skipped_codes': skipped_codes,
        'output_folder': output_folder
    }
    
    # Tạo báo cáo Excel
    try:
        create_documents_report(report, output_folder)
    except Exception as e:
        print(f"Lỗi khi tạo báo cáo Excel: {str(e)}")
    
    return report

def create_documents_report(report_data, output_folder):
    """
    Tạo báo cáo Excel cho quá trình tải tài liệu
    
    Args:
        report_data (dict): Dữ liệu báo cáo
        output_folder (str): Thư mục đầu ra
    """
    report_path = os.path.join(output_folder, 'documents_report.xlsx')
    
    try:
        # Tạo DataFrame đơn giản hợp nhất tất cả thông tin
        all_data = []
        
        # Thêm hàng tiêu đề với thông tin tổng quan
        all_data.append({
            'Loại': 'THÔNG TIN TỔNG QUAN',
            'Tên sản phẩm': '',
            'Mã sản phẩm': '',
            'Tài liệu đã tải': '',
            'Trạng thái': ''
        })
        
        # Thêm thông tin tổng quan
        all_data.append({
            'Loại': 'Tổng số sản phẩm',
            'Tên sản phẩm': '',
            'Mã sản phẩm': '',
            'Tài liệu đã tải': '',
            'Trạng thái': str(report_data['total_products'])
        })
        
        all_data.append({
            'Loại': 'Sản phẩm thành công',
            'Tên sản phẩm': '',
            'Mã sản phẩm': '',
            'Tài liệu đã tải': '',
            'Trạng thái': str(report_data['successful_products'])
        })
        
        all_data.append({
            'Loại': 'Sản phẩm thất bại',
            'Tên sản phẩm': '',
            'Mã sản phẩm': '',
            'Tài liệu đã tải': '',
            'Trạng thái': str(report_data['failed_products'])
        })
        
        all_data.append({
            'Loại': 'Mã bị bỏ qua',
            'Tên sản phẩm': '',
            'Mã sản phẩm': '',
            'Tài liệu đã tải': '',
            'Trạng thái': str(len(report_data['skipped_codes']))
        })
        
        all_data.append({
            'Loại': 'Thư mục đầu ra',
            'Tên sản phẩm': '',
            'Mã sản phẩm': '',
            'Tài liệu đã tải': '',
            'Trạng thái': report_data['output_folder']
        })
        
        # Thêm một hàng trống để phân tách
        all_data.append({
            'Loại': '',
            'Tên sản phẩm': '',
            'Mã sản phẩm': '',
            'Tài liệu đã tải': '',
            'Trạng thái': ''
        })
        
        # Thêm tiêu đề cho phần chi tiết sản phẩm và tài liệu
        all_data.append({
            'Loại': 'CHI TIẾT SẢN PHẨM VÀ TÀI LIỆU',
            'Tên sản phẩm': '',
            'Mã sản phẩm': '',
            'Tài liệu đã tải': '',
            'Trạng thái': ''
        })
        
        # Thêm dữ liệu từng sản phẩm và tài liệu
        for product_code, result in report_data['product_results'].items():
            product_name = result.get('product_name', '')
            success_docs = result.get('documents', [])
            
            if not success_docs:
                # Nếu không có tài liệu thành công, thêm hàng với thông tin sản phẩm nhưng không có tài liệu
                all_data.append({
                    'Loại': 'Sản phẩm',
                    'Tên sản phẩm': product_name,
                    'Mã sản phẩm': product_code,
                    'Tài liệu đã tải': 'Không có tài liệu',
                    'Trạng thái': result.get('message', 'Không có tài liệu')
                })
            else:
                # Nếu có tài liệu, kết hợp tất cả tài liệu của sản phẩm vào một hàng
                document_names = []
                document_paths = []
                
                for doc in success_docs:
                    document_names.append(doc.get('name', ''))
                    document_paths.append(doc.get('path', ''))
                
                # Nối tất cả tài liệu thành một chuỗi, ngăn cách bởi dấu ";"
                all_docs = "; ".join([f"{name} ({os.path.basename(path)})" for name, path in zip(document_names, document_paths)])
                
                all_data.append({
                    'Loại': 'Sản phẩm',
                    'Tên sản phẩm': product_name,
                    'Mã sản phẩm': product_code,
                    'Tài liệu đã tải': all_docs,
                    'Trạng thái': f"Thành công ({len(success_docs)} tài liệu)"
                })
        
        # Tạo DataFrame từ tất cả dữ liệu
        all_df = pd.DataFrame(all_data)
        
        # Lưu vào file Excel với một sheet duy nhất
        with pd.ExcelWriter(report_path, engine='openpyxl') as writer:
            all_df.to_excel(writer, sheet_name='Báo cáo tài liệu', index=False)
            
            # Tùy chỉnh độ rộng cột
            worksheet = writer.sheets['Báo cáo tài liệu']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min((max_length + 2), 100)  # Giới hạn độ rộng tối đa là 100
                worksheet.column_dimensions[column_letter].width = adjusted_width
            
            # Thêm định dạng cho các tiêu đề
            bold_font = openpyxl.styles.Font(bold=True)
            title_fill = openpyxl.styles.PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")
            
            # Áp dụng định dạng cho tiêu đề cột
            for cell in worksheet[1]:
                cell.font = bold_font
                cell.fill = title_fill
            
            # Áp dụng định dạng cho tiêu đề các phần
            for row_idx, row in enumerate(worksheet.iter_rows(), 1):
                if row[0].value == 'THÔNG TIN TỔNG QUAN' or row[0].value == 'CHI TIẾT SẢN PHẨM VÀ TÀI LIỆU':
                    for cell in row:
                        cell.font = bold_font
                        cell.fill = title_fill
            
            # Thiết lập wrap text cho cột tài liệu
            for row in worksheet.iter_rows(min_row=2):
                doc_cell = row[3]  # Cột 'Tài liệu đã tải' là cột thứ 4 (index 3)
                doc_cell.alignment = openpyxl.styles.Alignment(wrapText=True, vertical='top')
        
        print(f"Đã tạo báo cáo Excel tại: {report_path}")
        return True
    
    except Exception as e:
        print(f"Lỗi khi tạo báo cáo Excel: {str(e)}")
        traceback.print_exc()
        return False

def extract_product_code_from_url(url):
    """
    Trích xuất mã sản phẩm từ URL sản phẩm BAA.vn
    
    Args:
        url (str): URL sản phẩm
        
    Returns:
        str: Mã sản phẩm hoặc None nếu không tìm thấy
    """
    try:
        # Mẫu 1: URL có dạng example.com/path/product-name_12345
        pattern1 = r'[^/]+_(\d+)/?$'
        match1 = re.search(pattern1, url)
        if match1:
            return match1.group(1)
            
        # Mẫu 2: URL có dạng example.com/path/MODEL-NUMBER
        parts = url.strip('/').split('/')
        if parts:
            last_part = parts[-1]
            
            # Tìm phần cuối cùng sau dấu gạch ngang
            code_match = re.search(r'.*[-_]([a-zA-Z0-9-]+)$', last_part)
            if code_match:
                return code_match.group(1)
                
            # Nếu không có dấu gạch ngang, lấy phần cuối
            return last_part
            
        return None
    except Exception as e:
        print(f"Lỗi khi trích xuất mã sản phẩm: {str(e)}")
        return None

def standardize_product_code(code):
    """
    Chuẩn hóa mã sản phẩm để tạo tên file an toàn trên tất cả hệ điều hành
    """
    if not code:
        return "unknown_product"
    
    # Chuyển đổi tất cả về chữ hoa và loại bỏ khoảng trắng ở đầu/cuối
    standardized = str(code).upper().strip()
    
    # Thay thế các ký tự không hợp lệ cho tên file Windows
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ' ', ',', '.', ';', '&', '%', '$', '@', '!', '=', '+', '~', '`']
    for char in invalid_chars:
        standardized = standardized.replace(char, '-')
    
    # Thay thế nhiều dấu gạch ngang liên tiếp thành một dấu gạch ngang
    standardized = re.sub(r'-+', '-', standardized)
    
    # Loại bỏ dấu gạch ngang ở đầu và cuối nếu có
    standardized = standardized.strip('-')
    
    # Đảm bảo tên file không rỗng
    if not standardized:
        return "unknown_product"
    
    return standardized

def get_random_headers():
    """Tạo các header ngẫu nhiên để tránh bị chặn"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Connection': 'keep-alive',
        'Referer': 'https://google.com'
    }
    return headers

def download_baa_product_images(product_urls, output_folder=None):
    """
    Tải ảnh từ các sản phẩm BAA.vn và chuyển sang WebP kích thước gốc
    
    Args:
        product_urls: Danh sách URL sản phẩm cần tải ảnh
        output_folder: Thư mục lưu ảnh (tạo tự động nếu không có)
        
    Returns:
        dict: Kết quả tải ảnh
            - 'total': Tổng số URL được xử lý
            - 'success': Số lượng tải thành công
            - 'failed': Số lượng tải thất bại
            - 'image_paths': Danh sách đường dẫn ảnh đã tải
    """
    # Import các thư viện cần thiết
    import pandas as pd
    from datetime import datetime
    
    # Tạo thư mục lưu nếu chưa có
    if output_folder is None:
        output_folder = 'baa_images'
        
    os.makedirs(output_folder, exist_ok=True)
    
    # Khởi tạo kết quả
    results = {
        'total': len(product_urls),
        'success': 0,
        'failed': 0,
        'image_paths': [],
        'report_data': []  # Dữ liệu báo cáo Excel
    }
    
    # Headers để tránh bị chặn
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
    }
    
    # Các mẫu regex để tìm thông tin sản phẩm
    product_code_pattern = re.compile(r'([A-Za-z0-9-]+)', re.IGNORECASE)
    
    # Xử lý từng URL sản phẩm
    for index, url in enumerate(product_urls):
        # Khởi tạo các biến lưu thông tin cho báo cáo
        report_item = {
            'STT': index + 1,
            'URL': url,
            'Mã sản phẩm': '',
            'Trạng thái': 'Thất bại',
            'Lý do lỗi': '',
            'Đường dẫn ảnh': '',
            'Kích thước ảnh': '',
            'Thời gian xử lý': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        try:
            # Bỏ qua URL không hợp lệ
            if not url.strip() or not url.startswith('http'):
                results['failed'] += 1
                report_item['Lý do lỗi'] = 'URL không hợp lệ'
                results['report_data'].append(report_item)
                print(f"Bỏ qua URL không hợp lệ: {url}")
                continue
                
            print(f"Đang xử lý [{index+1}/{results['total']}]: {url}")
            
            # Gửi yêu cầu HTTP
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Phân tích HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Lấy mã sản phẩm từ URL
            product_code = ""
            try:
                url_parts = url.split('_')
                if len(url_parts) > 1 and url_parts[-1].isdigit():
                    name_parts = url_parts[-2].split('-')
                    if len(name_parts) > 0:
                        product_code = name_parts[-1].upper()
                        print(f"  Mã sản phẩm từ URL: {product_code}")
            except:
                pass
            
            # Nếu không lấy được từ URL, thử lấy từ H1
            if not product_code:
                h1_text = soup.find('h1')
                if h1_text:
                    h1_text = h1_text.text.strip()
                    h1_match = product_code_pattern.search(h1_text)
                    if h1_match:
                        product_code = h1_match.group(1)
                        print(f"  Mã sản phẩm từ H1: {product_code}")
            
            # Nếu vẫn không tìm được mã, dùng timestamp làm mã
            if not product_code:
                product_code = f"unknown-{int(time.time())}"
                print(f"  Không thể xác định mã sản phẩm, sử dụng: {product_code}")
            
            # Cập nhật mã sản phẩm cho báo cáo
            report_item['Mã sản phẩm'] = product_code
            
            # Tìm các hình ảnh sản phẩm
            images_found = False
            image_urls = []
            img_sizes = []  # Lưu kích thước ảnh
            
            # PHƯƠNG PHÁP 1: TÌM TRONG THẺ DIV.MODAL-BODY-IMAGE.ACTIVE (ƯU TIÊN CAO NHẤT)
            # Tìm div.modal-body-image.active
            active_modal = soup.select_one('div.modal-body-image.active')
            if active_modal:
                # Tìm trực tiếp thẻ img bên trong
                img_tag = active_modal.find('img')
                if img_tag and img_tag.get('src'):
                    src = img_tag.get('src')
                    if src and src.strip() and not 'LOGO-BAA' in src:
                        # Kiểm tra xem có phải ảnh đã resize không
                        if '/ResizeHinhAnh.ashx' in src:
                            # Trích xuất đường dẫn gốc từ tham số fileName
                            original_path_match = re.search(r'fileName=([^&]+)', src)
                            if original_path_match:
                                original_path = original_path_match.group(1)
                                # Chuyển đổi URL encoding nếu cần
                                original_path = original_path.replace('%2F', '/')
                                # Thay đổi kích thước thành 800px nếu có thể
                                full_size_url = re.sub(r'/\d+/', '/800/', original_path)
                                image_urls.append(full_size_url)
                                print(f"  ✓ Tìm thấy ảnh gốc từ div.modal-body-image.active: {full_size_url}")
                            else:
                                image_urls.append(src)
                                print(f"  ✓ Tìm thấy ảnh trong div.modal-body-image.active (không thể lấy ảnh gốc): {src}")
                        else:
                            # Kiểm tra và chuyển đổi sang kích thước đầy đủ nếu có số trong đường dẫn
                            full_size_url = re.sub(r'/(\d+)/', '/800/', src)
                            image_urls.append(full_size_url)
                            print(f"  ✓ Tìm thấy ảnh trong div.modal-body-image.active: {full_size_url}")
            
            # PHƯƠNG PHÁP 2: TÌM TRONG TẤT CẢ CÁC DIV.MODAL-BODY-IMAGE
            if not image_urls:
                modal_images = soup.select('div.modal-body-image')
                for modal in modal_images:
                    img_tag = modal.find('img')
                    if img_tag and img_tag.get('src'):
                        src = img_tag.get('src')
                        if src and src.strip() and not 'LOGO-BAA' in src:
                            # Kiểm tra xem có phải ảnh đã resize không
                            if '/ResizeHinhAnh.ashx' in src:
                                # Trích xuất đường dẫn gốc từ tham số fileName
                                original_path_match = re.search(r'fileName=([^&]+)', src)
                                if original_path_match:
                                    original_path = original_path_match.group(1)
                                    # Chuyển đổi URL encoding nếu cần
                                    original_path = original_path.replace('%2F', '/')
                                    # Thay đổi kích thước thành 800px nếu có thể
                                    full_size_url = re.sub(r'/\d+/', '/800/', original_path)
                                    image_urls.append(full_size_url)
                                    print(f"  ✓ Tìm thấy ảnh gốc từ div.modal-body-image: {full_size_url}")
                                else:
                                    image_urls.append(src)
                                    print(f"  ✓ Tìm thấy ảnh trong div.modal-body-image (không thể lấy ảnh gốc): {src}")
                            else:
                                # Kiểm tra và chuyển đổi sang kích thước đầy đủ nếu có số trong đường dẫn
                                full_size_url = re.sub(r'/(\d+)/', '/800/', src)
                                image_urls.append(full_size_url)
                                print(f"  ✓ Tìm thấy ảnh trong div.modal-body-image: {full_size_url}")
            
            # Nếu không tìm thấy ảnh từ modal-body-image, thử các phương pháp khác
            
            # PHƯƠNG PHÁP 3: TÌM TRONG THẺ META OG:IMAGE
            if not image_urls:
                og_image = soup.find('meta', property='og:image')
                if og_image:
                    img_url = og_image.get('content', '')
                    if img_url and img_url.strip():
                        # Bỏ qua nếu là logo
                        if not 'LOGO-BAA' in img_url:
                            # Kiểm tra xem có phải ảnh đã resize không
                            if '/ResizeHinhAnh.ashx' in img_url:
                                # Trích xuất đường dẫn gốc từ tham số fileName
                                original_path_match = re.search(r'fileName=([^&]+)', img_url)
                                if original_path_match:
                                    original_path = original_path_match.group(1)
                                    # Chuyển đổi URL encoding nếu cần
                                    original_path = original_path.replace('%2F', '/')
                                    # Thay đổi kích thước thành 800px nếu có thể
                                    full_size_url = re.sub(r'/\d+/', '/800/', original_path)
                                    image_urls.append(full_size_url)
                                    print(f"  ✓ Tìm thấy ảnh gốc từ og:image: {full_size_url}")
                                else:
                                    image_urls.append(img_url)
                                    print(f"  ✓ Tìm thấy ảnh trong og:image (không thể lấy ảnh gốc): {img_url}")
                            else:
                                # Kiểm tra và chuyển đổi sang kích thước đầy đủ nếu có số trong đường dẫn
                                full_size_url = re.sub(r'/(\d+)/', '/800/', img_url)
                                image_urls.append(full_size_url)
                                print(f"  ✓ Tìm thấy ảnh trong og:image: {full_size_url}")
            
            # Các phương pháp tìm khác nếu cần
            # ... (code phương pháp khác nếu cần)
            
            # Nếu không tìm thấy ảnh nào, thông báo thất bại
            if not image_urls:
                results['failed'] += 1
                report_item['Lý do lỗi'] = 'Không tìm thấy ảnh sản phẩm'
                results['report_data'].append(report_item)
                print(f"  ✗ Không tìm thấy ảnh cho URL: {url}")
                continue
            
            # Chuẩn hóa các URL ảnh
            normalized_urls = []
            base_url = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(url))
            
            for img_url in image_urls:
                # Chuyển URLs tương đối thành tuyệt đối
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                elif not img_url.startswith(('http://', 'https://')):
                    img_url = urljoin(base_url, img_url)
                
                # Lọc các URL trùng lặp
                if img_url not in normalized_urls and img_url.strip():
                    normalized_urls.append(img_url)
            
            # Tải và lưu ảnh
            saved_images = []
            
            for i, img_url in enumerate(normalized_urls):
                try:
                    # Tạo tên file ảnh
                    img_filename = f"{product_code}.webp" if i == 0 else f"{product_code}_{i+1}.webp"
                    img_path = os.path.join(output_folder, img_filename)
                    
                    print(f"  → Đang tải ảnh: {img_url}")
                    
                    # Tải ảnh
                    img_response = requests.get(img_url, headers=headers, timeout=15)
                    img_response.raise_for_status()
                    
                    # Xử lý ảnh
                    img = Image.open(BytesIO(img_response.content))
                    img = img.convert("RGB")  # Đảm bảo chế độ màu tương thích
                    
                    # Lưu thông tin kích thước ảnh
                    img_size = f"{img.width}x{img.height}"
                    img_sizes.append(img_size)
                    
                    # Lưu ảnh dưới dạng WebP với chất lượng cao
                    img.save(img_path, 'WEBP', quality=95)
                    
                    # Thêm vào danh sách ảnh đã lưu
                    saved_images.append(img_path)
                    images_found = True
                    
                    print(f"  ✓ Đã lưu: {img_filename} ({img_size})")
                    
                    # Cập nhật dữ liệu báo cáo
                    report_item['Đường dẫn ảnh'] = img_path
                    report_item['Kích thước ảnh'] = img_size
                    
                    # Nếu đã tìm và lưu được ảnh đầu tiên, dừng lại
                    break
                    
                except Exception as e:
                    print(f"  ✗ Lỗi khi tải ảnh {img_url}: {str(e)}")
                    report_item['Lý do lỗi'] = f"Lỗi khi tải ảnh: {str(e)}"
                    continue
            
            # Cập nhật kết quả
            if images_found:
                results['success'] += 1
                results['image_paths'].extend(saved_images)
                report_item['Trạng thái'] = 'Thành công'
            else:
                results['failed'] += 1
                report_item['Lý do lỗi'] = 'Không tải được ảnh'
                print(f"  ✗ Không tải được ảnh cho URL: {url}")
            
            # Thêm vào dữ liệu báo cáo
            results['report_data'].append(report_item)
            
            # Nghỉ giữa các yêu cầu để tránh bị chặn
            time.sleep(0.5)
            
        except Exception as e:
            results['failed'] += 1
            report_item['Lý do lỗi'] = f"Lỗi: {str(e)}"
            results['report_data'].append(report_item)
            print(f"Lỗi khi xử lý URL {url}: {str(e)}")
            traceback.print_exc()
    
    # Tạo file báo cáo Excel
    try:
        report_df = pd.DataFrame(results['report_data'])
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        
        # Tạo thư mục reports nếu chưa có
        reports_folder = os.path.join(os.path.dirname(output_folder), "reports")
        os.makedirs(reports_folder, exist_ok=True)
        
        # Lưu báo cáo vào thư mục reports riêng biệt
        report_path = os.path.join(reports_folder, f"report_images_{timestamp}.xlsx")
        
        # Tạo writer Excel với định dạng
        writer = pd.ExcelWriter(report_path, engine='openpyxl')
        report_df.to_excel(writer, index=False, sheet_name='Báo cáo tải ảnh')
        
        # Lấy sheet để định dạng
        worksheet = writer.sheets['Báo cáo tải ảnh']
        
        # Điều chỉnh độ rộng cột
        for idx, col in enumerate(report_df.columns):
            max_len = max(
                report_df[col].astype(str).map(len).max(),
                len(str(col))
            ) + 2
            # Đặt giới hạn độ rộng cột
            max_len = min(max_len, 50)
            # Chuyển đổi sang đơn vị Excel
            worksheet.column_dimensions[chr(65 + idx)].width = max_len
        
        # Lưu file Excel
        writer.close()
        
        print(f"Đã tạo báo cáo Excel: {report_path}")
        results['report_file'] = report_path
    except Exception as e:
        print(f"Lỗi khi tạo báo cáo Excel: {str(e)}")
        traceback.print_exc()
    
    print(f"\nKết quả tải ảnh BAA:")
    print(f"- Tổng số URL: {results['total']}")
    print(f"- Thành công: {results['success']}")
    print(f"- Thất bại: {results['failed']}")
    print(f"- Tổng số ảnh đã tải: {len(results['image_paths'])}")
    
    return results

def resize_image_to_square(image, size=800):
    """
    Thay đổi kích thước ảnh thành hình vuông có kích thước được chỉ định
    
    Args:
        image (PIL.Image): Đối tượng ảnh Pillow
        size (int): Kích thước mong muốn cho ảnh vuông
        
    Returns:
        PIL.Image: Ảnh đã được thay đổi kích thước thành hình vuông
    """
    # Lấy kích thước ảnh gốc
    width, height = image.size
    
    # Tạo ảnh vuông mới với nền trắng
    square_img = Image.new('RGBA' if image.mode == 'RGBA' else 'RGB', (size, size), (255, 255, 255))
    
    # Tính toán tỷ lệ để giữ nguyên tỷ lệ khung hình gốc
    if width > height:
        new_width = size
        new_height = int(height * size / width)
        resized_img = image.resize((new_width, new_height), Image.LANCZOS)
        # Căn giữa theo chiều dọc
        paste_y = (size - new_height) // 2
        square_img.paste(resized_img, (0, paste_y))
    else:
        new_height = size
        new_width = int(width * size / height)
        resized_img = image.resize((new_width, new_height), Image.LANCZOS)
        # Căn giữa theo chiều ngang
        paste_x = (size - new_width) // 2
        square_img.paste(resized_img, (paste_x, 0))
    
    return square_img

def download_baa_product_images_fixed(product_urls, output_folder=None):
    try:
        # Chuyển đổi input thành list nếu nhận được string
        if isinstance(product_urls, str):
            product_urls = [product_urls]
        
        # Tạo thư mục output nếu không được cung cấp
        if not output_folder:
            # Tạo output folder trong thư mục hiện tại
            output_folder = os.path.join(os.getcwd(), "output_images")
        
        # Đảm bảo thư mục output tồn tại
        os.makedirs(output_folder, exist_ok=True)
        
        print(f"Thư mục lưu ảnh: {output_folder}")
        
        # Khởi tạo kết quả trả về
        results = {
            'total': len(product_urls),
            'success': 0,
            'failed': 0,
            'image_paths': [],
            'report_data': [],
            'report_file': None
        }
        
        # Lặp qua từng URL sản phẩm
        for i, url in enumerate(product_urls, 1):
            print(f"\n[{i}/{len(product_urls)}] Đang xử lý URL: {url}")
            
            # Khởi tạo dữ liệu báo cáo cho URL này
            report_item = {
                'STT': i,
                'URL': url,
                'Mã sản phẩm': '',
                'Đường dẫn ảnh': '',
                'Kích thước ảnh': '',
                'Trạng thái': 'Thất bại',
                'Lý do lỗi': '',
                'Thời gian xử lý': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            images_found = False
            
            try:
                # Lấy nội dung trang sản phẩm
                headers = get_random_headers()
                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                
                # Sử dụng hàm trích xuất URL ảnh và mã sản phẩm từ HTML
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Trích xuất mã sản phẩm
                product_code = None
                product_symbol = soup.select_one('span.product__symbol__value')
                if product_symbol and product_symbol.text.strip():
                    product_code = product_symbol.text.strip()
                    print(f"  ✓ Tìm thấy mã sản phẩm từ span.product__symbol__value: {product_code}")
                
                # Ưu tiên sử dụng mã sản phẩm từ HTML
                if product_code:
                    original_product_code = product_code
                    print(f"  ✓ Sử dụng mã sản phẩm từ HTML: {product_code}")
                else:
                    # Nếu không tìm được mã từ HTML, thử trích xuất từ URL
                    product_code = extract_product_code_from_url(url)
                    original_product_code = product_code
                    print(f"  ✓ Sử dụng mã sản phẩm từ URL: {product_code}")
                
                # Chuẩn hóa mã sản phẩm cho tên file - thay thế các ký tự đặc biệt
                if product_code:
                    # Lưu lại mã sản phẩm gốc cho báo cáo
                    report_product_code = product_code
                    # Chuẩn hóa mã sản phẩm để sử dụng làm tên file
                    product_code = standardize_product_code(product_code)
                    # Thay thế các ký tự không hợp lệ cho tên file
                    product_code = product_code.replace('/', '-').replace('\\', '-').replace(':', '-')
                    product_code = product_code.replace('*', '-').replace('?', '-').replace('"', '-')
                    product_code = product_code.replace('<', '-').replace('>', '-').replace('|', '-')
                else:
                    report_product_code = f"unknown_product_{i}"
                    product_code = f"unknown_product_{i}"
                
                # Ghi nhận mã sản phẩm vào báo cáo
                report_item['Mã sản phẩm'] = report_product_code
                
                # PHƯƠNG PHÁP 1: Ưu tiên tìm ảnh 800px từ div.modal-body-image.active
                img_url = None
                
                # Tìm tất cả image tags trong modal-body-image.active với class w-100 h-100
                modal_active_img = soup.select('div.modal-body-image.active img.w-100.h-100')
                if modal_active_img:
                    for img in modal_active_img:
                        if img.get('src'):
                            src = img.get('src')
                            if src and 'LOGO-BAA' not in src:
                                # Đảm bảo là ảnh kích thước 800
                                if '800' in src:
                                    img_url = src
                                    print(f"  ✓ Tìm thấy ảnh 800px từ modal-body-image.active: {img_url}")
                                    break
                                else:
                                    # Nếu không có kích thước 800, thử chuyển đổi
                                    pattern = r'/(series|model)/(\d+)/(\d+)/'
                                    match = re.search(pattern, src)
                                    if match:
                                        type_folder = match.group(1)  # series hoặc model
                                        folder_id = match.group(2)    # ID thư mục (ví dụ: 274)
                                        img_url = re.sub(pattern, f'/{type_folder}/{folder_id}/800/', src)
                                        print(f"  ✓ Chuyển đổi ảnh từ modal-body-image.active sang kích thước 800px: {img_url}")
                                        break
                
                # PHƯƠNG PHÁP 2: Tìm trong div.modal-body__view-image
                if not img_url:
                    modal_view_img = soup.select('div.modal-body__view-image img.w-100.h-100')
                    for img in modal_view_img:
                        if img.get('src'):
                            src = img.get('src')
                            if src and 'LOGO-BAA' not in src:
                                # Ưu tiên ảnh 800px
                                if '800' in src:
                                    img_url = src
                                    print(f"  ✓ Tìm thấy ảnh 800px từ div.modal-body__view-image: {img_url}")
                                    break
                                else:
                                    # Nếu không có kích thước 800, thử chuyển đổi
                                    pattern = r'/(series|model)/(\d+)/(\d+)/'
                                    match = re.search(pattern, src)
                                    if match:
                                        type_folder = match.group(1)  # series hoặc model
                                        folder_id = match.group(2)    # ID thư mục (ví dụ: 274)
                                        img_url = re.sub(pattern, f'/{type_folder}/{folder_id}/800/', src)
                                        print(f"  ✓ Chuyển đổi ảnh từ div.modal-body__view-image sang kích thước 800px: {img_url}")
                                        break
                
                # PHƯƠNG PHÁP 3: Nếu không tìm thấy, lấy từ img.btn-image-view-360 và chuyển sang 800
                if not img_url:
                    main_image = soup.select_one('img.btn-image-view-360')
                    if main_image and main_image.get('src'):
                        src = main_image.get('src')
                        if src and 'LOGO-BAA' not in src:
                            # Chuyển từ kích thước nhỏ (thường là 300) sang 800
                            pattern = r'/(series|model)/(\d+)/(\d+)/'
                            match = re.search(pattern, src)
                            if match:
                                type_folder = match.group(1)  # series hoặc model
                                folder_id = match.group(2)    # ID thư mục (ví dụ: 274)
                                img_url = re.sub(pattern, f'/{type_folder}/{folder_id}/800/', src)
                                print(f"  ✓ Chuyển đổi ảnh từ img.btn-image-view-360 sang kích thước 800px: {img_url}")
                            else:
                                img_url = src
                                print(f"  ✓ Tìm thấy ảnh từ img.btn-image-view-360: {img_url}")
                
                # PHƯƠNG PHÁP 4: Nếu vẫn không tìm thấy, thử og:image
                if not img_url:
                    og_image = soup.find('meta', property='og:image')
                    if og_image:
                        img_url = og_image.get('content', '')
                        if img_url and 'LOGO-BAA' not in img_url:
                            # Chuyển từ kích thước nhỏ (thường là 300) sang 800
                            pattern = r'/(series|model)/(\d+)/(\d+)/'
                            match = re.search(pattern, img_url)
                            if match:
                                type_folder = match.group(1)  # series hoặc model
                                folder_id = match.group(2)    # ID thư mục (ví dụ: 274)
                                img_url = re.sub(pattern, f'/{type_folder}/{folder_id}/800/', img_url)
                                print(f"  ✓ Chuyển đổi ảnh từ og:image sang kích thước 800px: {img_url}")
                            else:
                                print(f"  ✓ Tìm thấy ảnh từ og:image: {img_url}")
                
                # Nếu không tìm thấy ảnh nào, thông báo thất bại
                if not img_url:
                    results['failed'] += 1
                    report_item['Lý do lỗi'] = 'Không tìm thấy ảnh sản phẩm'
                    results['report_data'].append(report_item)
                    print(f"  ✗ Không tìm thấy ảnh cho URL: {url}")
                    continue
                
                # Chuẩn hóa URL ảnh
                base_url = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(url))
                
                # Chuyển URL tương đối thành tuyệt đối
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                elif not img_url.startswith(('http://', 'https://')):
                    img_url = urljoin(base_url, img_url)
                
                print(f"  → Lưu ảnh vào thư mục: {output_folder}")
                
                # Thử tải ảnh - giữ nguyên định dạng ảnh
                try:
                    # Tạo tên file ảnh dựa trên mã sản phẩm 
                    img_filename = f"{product_code}.webp"
                    img_path = os.path.join(output_folder, img_filename)
                    
                    print(f"  → Đang tải ảnh: {img_url}")
                    
                    # Tải ảnh
                    img_response = requests.get(img_url, headers=headers, timeout=20)
                    img_response.raise_for_status()
                    
                    # Kiểm tra MIME type để đảm bảo đây là ảnh
                    content_type = img_response.headers.get('Content-Type', '')
                    if not content_type.startswith('image/'):
                        raise ValueError(f"Không phải file ảnh: {content_type}")
                    
                    # Xử lý ảnh
                    img = Image.open(BytesIO(img_response.content))
                    img = img.convert("RGB")  # Đảm bảo chế độ màu tương thích
                    
                    # Lưu thông tin kích thước ảnh
                    img_size = f"{img.width}x{img.height}"
                    
                    # Lưu ảnh dưới dạng WebP với chất lượng cao
                    img.save(img_path, 'WEBP', quality=95)
                    
                    # Cập nhật kết quả
                    results['success'] += 1
                    results['image_paths'].append(img_path)
                    report_item['Trạng thái'] = 'Thành công'
                    report_item['Đường dẫn ảnh'] = img_path
                    report_item['Kích thước ảnh'] = img_size
                    
                    print(f"  ✓ Đã lưu: {img_filename} ({img_size})")
                    
                except requests.exceptions.HTTPError as e:
                    print(f"  ✗ Lỗi HTTP khi tải ảnh: {str(e)}")
                    # Thử lại với ảnh 300px nếu 800px không tồn tại
                    if '404' in str(e) and '800' in img_url:
                        try:
                            # Thử lại với kích thước 300px
                            img_url_300 = re.sub(r'/800/', '/300/', img_url)
                            print(f"  → Thử lại với ảnh kích thước 300px: {img_url_300}")
                            
                            # Tải ảnh kích thước 300px
                            img_response = requests.get(img_url_300, headers=headers, timeout=15)
                            img_response.raise_for_status()
                            
                            # Kiểm tra MIME type
                            content_type = img_response.headers.get('Content-Type', '')
                            if not content_type.startswith('image/'):
                                raise ValueError(f"Không phải file ảnh: {content_type}")
                            
                            # Xử lý ảnh
                            img = Image.open(BytesIO(img_response.content))
                            img = img.convert("RGB")
                            
                            # Lưu thông tin kích thước ảnh
                            img_size = f"{img.width}x{img.height}"
                            
                            # Lưu ảnh dưới dạng WebP với chất lượng cao
                            img.save(img_path, 'WEBP', quality=95)
                            
                            # Cập nhật kết quả
                            results['success'] += 1
                            results['image_paths'].append(img_path)
                            report_item['Trạng thái'] = 'Thành công'
                            report_item['Đường dẫn ảnh'] = img_path
                            report_item['Kích thước ảnh'] = img_size
                            
                            print(f"  ✓ Đã lưu ảnh kích thước 300px: {img_filename} ({img_size})")
                        except Exception as inner_e:
                            results['failed'] += 1
                            report_item['Lý do lỗi'] = f"Lỗi khi tải ảnh: {str(e)} và {str(inner_e)}"
                    else:
                        results['failed'] += 1
                        report_item['Lý do lỗi'] = f"Lỗi HTTP: {str(e)}"
                
                except Exception as e:
                    print(f"  ✗ Lỗi khi tải ảnh: {str(e)}")
                    results['failed'] += 1
                    report_item['Lý do lỗi'] = f"Lỗi khi tải ảnh: {str(e)}"
                
                # Thêm vào dữ liệu báo cáo
                results['report_data'].append(report_item)
                
                # Nghỉ giữa các yêu cầu để tránh bị chặn
                time.sleep(0.5)
                
            except Exception as e:
                results['failed'] += 1
                report_item['Lý do lỗi'] = f"Lỗi: {str(e)}"
                results['report_data'].append(report_item)
                print(f"Lỗi khi xử lý URL {url}: {str(e)}")
                traceback.print_exc()
        
        # Tạo báo cáo Excel
        try:
            print("\nĐang tạo báo cáo Excel...")
            
            # Tạo DataFrame từ dữ liệu báo cáo
            df = pd.DataFrame(results['report_data'])
            
            # Tạo đường dẫn file báo cáo
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            report_filename = f"baa_image_report_{timestamp}.xlsx"
            report_path = os.path.join(output_folder, report_filename)
            
            # Lưu vào file Excel
            df.to_excel(report_path, index=False, sheet_name='Báo cáo tải ảnh')
            
            # Mở workbook để định dạng
            workbook = openpyxl.load_workbook(report_path)
            sheet = workbook.active
            
            # Định dạng các cột
            for idx, col in enumerate(df.columns, 1):
                # Đặt độ rộng cột
                column_letter = openpyxl.utils.get_column_letter(idx)
                if col == 'URL' or col == 'Đường dẫn ảnh':
                    sheet.column_dimensions[column_letter].width = 50
                elif col == 'Lý do lỗi':
                    sheet.column_dimensions[column_letter].width = 30
                else:
                    sheet.column_dimensions[column_letter].width = 20
                    
                # Định dạng tiêu đề
                header_cell = sheet.cell(row=1, column=idx)
                header_cell.font = openpyxl.styles.Font(bold=True)
                header_cell.alignment = openpyxl.styles.Alignment(horizontal='center')
            
            # Tạo bảng tóm tắt
            summary_row = sheet.max_row + 3
            sheet.cell(row=summary_row, column=1).value = "Tổng số URL:"
            sheet.cell(row=summary_row, column=2).value = results['total']
            
            sheet.cell(row=summary_row+1, column=1).value = "Tải thành công:"
            sheet.cell(row=summary_row+1, column=2).value = results['success']
            
            sheet.cell(row=summary_row+2, column=1).value = "Tải thất bại:"
            sheet.cell(row=summary_row+2, column=2).value = results['failed']
            
            sheet.cell(row=summary_row+3, column=1).value = "Tổng số ảnh đã tải:"
            sheet.cell(row=summary_row+3, column=2).value = len(results['image_paths'])
            
            # Đặt độ rộng cho cột
            sheet.column_dimensions['A'].width = 20
            sheet.column_dimensions['B'].width = 10
            
            # In đậm tiêu đề tóm tắt
            for i in range(4):
                cell = sheet.cell(row=summary_row+i, column=1)
                cell.font = openpyxl.styles.Font(bold=True)
            
            # Lưu lại workbook
            workbook.save(report_path)
            
            # Thêm đường dẫn file báo cáo vào kết quả
            results['report_file'] = report_path
            print(f"Đã tạo báo cáo Excel: {report_path}")
            
        except Exception as e:
            print(f"Lỗi khi tạo báo cáo Excel: {str(e)}")
            traceback.print_exc()
        
        # Trả về kết quả
        return results
        
    except Exception as e:
        print(f"Lỗi trong quá trình tải ảnh: {str(e)}")
        traceback.print_exc()
        return {
            'total': len(product_urls) if isinstance(product_urls, list) else 1, 
            'success': 0,
            'failed': len(product_urls) if isinstance(product_urls, list) else 1,
            'image_paths': [],
            'report_data': [],
            'report_file': None
        }

def extract_baa_image_url_from_html(html_content):
    """
    Trích xuất URL ảnh sản phẩm từ HTML
    """
    try:
        # Tạo đối tượng BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Trích xuất URL ảnh và mã sản phẩm
        image_urls = []
        product_code = None
        
        # Kiểm tra có phần tử image-view-360 không
        img_element = soup.select_one('img.btn-image-view-360')
        if img_element and 'src' in img_element.attrs:
            img_url = img_element['src']
            if img_url:
                # Chuyển đổi URL sang kích thước lớn
                full_url = convert_to_large_image_url(img_url)
                image_urls.append(full_url)
                print(f"Đã tìm thấy ảnh chính: {full_url}")
        
        # Tìm mã sản phẩm
        product_code_element = soup.select_one('.product__symbol__value')
        if product_code_element:
            product_code = product_code_element.text.strip()
            print(f"Mã sản phẩm: {product_code}")
        
        return {
            'image_urls': image_urls,
            'product_code': product_code
        }
        
    except Exception as e:
        print(f"Lỗi khi trích xuất URL ảnh: {str(e)}")
        return {'image_urls': [], 'product_code': None}

def clean_price(price_str):
    """
    Làm sạch giá sản phẩm: 
    - Giữ lại số 
    - Giữ lại đơn vị tiền tệ (đ, ₫, VND)
    - Loại bỏ các ký tự không cần thiết khác
    
    Args:
        price_str (str): Chuỗi giá ban đầu
    
    Returns:
        str: Chuỗi giá đã làm sạch
    """
    if not price_str:
        return ""
    
    # Giữ nguyên chuỗi nếu có đơn vị tiền tệ
    if "₫" in price_str or "đ" in price_str or "VND" in price_str:
        return price_str.strip()
    
    # Thử chuyển về số
    try:
        # Loại bỏ tất cả ký tự không phải số
        price_digits = re.sub(r'[^\d]', '', price_str)
        if price_digits:
            # Định dạng lại số với dấu phân cách hàng nghìn
            price_val = int(price_digits)
            return f"{price_val:,}₫".replace(",", ".")
        return price_str.strip()
    except:
        return price_str.strip()

def extract_product_price(url, index=1):
    """
    Chỉ trích xuất mã sản phẩm và giá từ URL
    
    Args:
        url (str): URL của trang sản phẩm
        index (int): STT của sản phẩm trong danh sách
        
    Returns:
        dict: Thông tin mã và giá sản phẩm
    """
    print(f"Đang trích xuất giá từ {url}")
    
    # Số lần thử tối đa
    max_retries = 3
    current_retry = 0
    
    while current_retry < max_retries:
        try:
            # Tải nội dung trang không giới hạn timeout
            response = requests.get(url, headers=HEADERS)
            response.raise_for_status()
            html_content = response.text
            
            # Parse HTML
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Khởi tạo kết quả
            product_info = {
                'STT': index,
                'URL': url,
                'Mã sản phẩm': "",
                'Giá': ""
            }
            
            # Trích xuất mã sản phẩm
            product_code = ""
            code_element = soup.select_one('.product__symbol__value')
            if code_element:
                product_code = code_element.text.strip()
            
            # Nếu không tìm thấy bằng phương pháp thông thường, thử các phương pháp khác
            if not product_code:
                # Thử các CSS selector khác
                code_selectors = [
                    '.product-sku',
                    '.sku',
                    '[itemprop="sku"]',
                    '.product-id'
                ]
                
                for selector in code_selectors:
                    code_element = soup.select_one(selector)
                    if code_element:
                        product_code = code_element.text.strip()
                        break
                
                # Nếu vẫn không tìm thấy, trích xuất từ URL hoặc tên sản phẩm
                if not product_code:
                    # Trích xuất từ URL
                    url_path = urlparse(url).path
                    path_parts = url_path.split("/")
                    if path_parts:
                        last_part = path_parts[-1]
                        # Loại bỏ các số ID, giữ lại phần mã sản phẩm
                        product_code = re.sub(r'_\d+$', '', last_part)
                    
                    # Trích xuất từ tên sản phẩm
                    if not product_code:
                        product_name = ""
                        name_element = soup.select_one('.product__name, h1.product-title')
                        if name_element:
                            product_name = name_element.text.strip()
                            code_match = re.search(r'([A-Z]{2,3}-[A-Z0-9]{3,5}(?:-[A-Z0-9]{4,5})+)', product_name)
                            if code_match:
                                product_code = code_match.group(1)
            
            if product_code:
                print(f"Mã sản phẩm: {product_code}")
                product_info['Mã sản phẩm'] = product_code
            
            # ------ PHẦN XỬ LÝ GIÁ SẢN PHẨM ------
            # 1. ƯU TIÊN SỐ 1: Trích xuất từ phần tử span.product__price-print có thuộc tính data-root
            price_element_with_data = soup.select_one('span.product__price-print[data-root]')
            if price_element_with_data and 'data-root' in price_element_with_data.attrs:
                data_root = price_element_with_data.get('data-root')
                if data_root:
                    try:
                        price_value = int(data_root)
                        formatted_price = f"{price_value:,}".replace(",", ".")
                        
                        # Tìm đơn vị tiền tệ
                        price_unit = ""
                        
                        # Tìm trong cùng cell hoặc gần kề
                        parent_td = price_element_with_data.find_parent('td')
                        if parent_td:
                            unit_element = parent_td.select_one('span.product__price-unit')
                            if unit_element:
                                price_unit = unit_element.text.strip()
                        
                        # Nếu không tìm thấy, tìm element kế tiếp
                        if not price_unit:
                            next_element = price_element_with_data.find_next_sibling()
                            if next_element and 'product__price-unit' in next_element.get('class', []):
                                price_unit = next_element.text.strip()
                        
                        # Nếu vẫn không tìm thấy
                        if not price_unit:
                            unit_element = soup.select_one('span.product__price-unit')
                            if unit_element:
                                price_unit = unit_element.text.strip()
                            else:
                                price_unit = "₫"  # Mặc định
                        
                        # Định dạng giá cuối cùng
                        product_info['Giá'] = formatted_price + price_unit
                        print(f"Giá từ data-root: {product_info['Giá']}")
                    except ValueError as e:
                        print(f"Lỗi khi xử lý giá từ data-root: {str(e)}")
            
            # 2. NẾU KHÔNG TÌM THẤY: Tìm giá từ các vị trí thông thường
            if not product_info['Giá']:
                price_selectors = [
                    'div.product__card--price span.fw-bold.text-danger.text-start',
                    '.product__card--price span',
                    '.product__card--price .fw-bold',
                    '.product-price',
                    '.product__price-print',
                    '.price-box .price',
                    '.special-price .price',
                    '[data-price-type="finalPrice"] .price'
                ]
                
                for selector in price_selectors:
                    price_element = soup.select_one(selector)
                    if price_element:
                        product_price = price_element.text.strip()
                        
                        # Tìm đơn vị tiền tệ
                        price_unit = ""
                        unit_element = soup.select_one('span.product__price-unit')
                        if unit_element:
                            price_unit = unit_element.text.strip()
                        
                        # Định dạng giá
                        if price_unit and price_unit not in product_price:
                            product_info['Giá'] = product_price + price_unit
                        else:
                            product_info['Giá'] = product_price
                        
                        print(f"Giá sản phẩm: {product_info['Giá']}")
                        break
            
            # 3. KIỂM TRA LẠI: Nếu vẫn không tìm thấy, tìm bất kỳ phần tử nào có thuộc tính data-root
            if not product_info['Giá']:
                elements_with_data_root = soup.select('[data-root]')
                if elements_with_data_root:
                    for element in elements_with_data_root:
                        data_root = element.get('data-root')
                        if data_root:
                            try:
                                price_value = int(data_root)
                                product_info['Giá'] = f"{price_value:,}₫".replace(",", ".")
                                print(f"Giá từ data-root (phương pháp 3): {product_info['Giá']}")
                                break
                            except ValueError:
                                continue
            
            # Làm sạch giá trước khi trả về
            if product_info['Giá']:
                product_info['Giá'] = clean_price(product_info['Giá'])
            
            return product_info
            
        except requests.exceptions.RequestException as e:
            current_retry += 1
            print(f"Lỗi tải trang (lần thử {current_retry}): {str(e)}")
            if current_retry < max_retries:
                print(f"Thử lại trong 3 giây...")
                time.sleep(3)
            else:
                print(f"Đã thử {max_retries} lần, bỏ qua URL {url}")
    
    # Trả về dữ liệu tối thiểu nếu không thể tải trang
    return {
        'STT': index,
        'URL': url,
        'Mã sản phẩm': "",
        'Giá': ""
    }
