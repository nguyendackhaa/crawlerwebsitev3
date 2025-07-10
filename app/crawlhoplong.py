#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crawler cho website HoplongTech.com
Chuyên cào dữ liệu cảm biến và thiết bị tự động hóa
Copyright © 2025 Haiphongtech.vn
"""

import os
import re
import time
import json
import requests
import traceback
from datetime import datetime
from urllib.parse import urljoin, urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
import threading

# Import các hàm utility - sử dụng try-except để tránh lỗi import
try:
    from . import utils
except ImportError:
    # Fallback cho standalone execution
    try:
        import utils
    except ImportError:
        utils = None

# Import các hàm utility từ module chính
try:
    from .category_crawler import log_and_emit, update_progress, safe_print
except ImportError:
    # Fallback nếu không import được
    def log_and_emit(message):
        print(f"[LOG] {message}")
    
    def update_progress(percent, message):
        print(f"[{percent}%] {message}")
    
    def safe_print(message):
        print(message)

class HoplongCrawler:
    """
    Crawler chuyên dụng cho website HoplongTech.com
    Cào dữ liệu cảm biến và thiết bị tự động hóa với phân chia theo thương hiệu
    """
    
    def __init__(self, socketio=None):
        """
        Khởi tạo HoplongCrawler
        
        Args:
            socketio: Socket.IO instance để emit tiến trình (optional)
        """
        self.socketio = socketio
        self.base_url = "https://hoplongtech.com"
        self.category_base_url = "https://hoplongtech.com/category/cam-bien"
        
        # Cấu hình session với retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
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
        
        # Cấu hình crawling tối ưu cho tốc độ
        self.max_workers = 8  # Tăng số thread để crawl nhanh hơn
        self.delay_between_requests = 0.3  # Giảm delay để tăng tốc
        self.batch_size = 50  # Xử lý theo batch để hiệu quả hơn
        
        log_and_emit("✅ Đã khởi tạo HoplongCrawler với cấu hình tối ưu tốc độ")
        log_and_emit(f"🚀 Cấu hình: {self.max_workers} workers, delay {self.delay_between_requests}s")
    
    def get_sensor_categories(self):
        """
        Lấy danh sách các loại cảm biến từ trang danh mục
        
        Returns:
            list: Danh sách các danh mục cảm biến với thông tin chi tiết
        """
        log_and_emit(f"🔍 Đang lấy danh sách danh mục cảm biến từ {self.category_base_url}")
        
        try:
            response = self.session.get(self.category_base_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            categories = []
            
            # Tìm tất cả div.cate-name
            cate_divs = soup.find_all('div', class_='cate-name')
            
            for div in cate_divs:
                try:
                    # Lấy link và tên danh mục
                    link_tag = div.find('a')
                    if not link_tag:
                        continue
                    
                    category_url = link_tag.get('href')
                    if not category_url:
                        continue
                    
                    # Đảm bảo URL đầy đủ
                    if category_url.startswith('/'):
                        category_url = urljoin(self.base_url, category_url)
                    
                    category_name = link_tag.get_text(strip=True)
                    
                    # Lấy số lượng sản phẩm
                    span_tag = div.find('span')
                    product_count_text = span_tag.get_text(strip=True) if span_tag else "0 Sản phẩm"
                    
                    # Trích xuất số lượng sản phẩm
                    count_match = re.search(r'(\d+)', product_count_text)
                    product_count = int(count_match.group(1)) if count_match else 0
                    
                    category_info = {
                        'name': category_name,
                        'url': category_url,
                        'product_count': product_count,
                        'display_text': f"{category_name} ({product_count} sản phẩm)"
                    }
                    
                    categories.append(category_info)
                    log_and_emit(f"📂 Tìm thấy danh mục: {category_name} - {product_count} sản phẩm")
                    
                except Exception as e:
                    log_and_emit(f"⚠️ Lỗi xử lý danh mục: {str(e)}")
                    continue
            
            log_and_emit(f"✅ Đã lấy {len(categories)} danh mục cảm biến")
            return categories
            
        except Exception as e:
            log_and_emit(f"❌ Lỗi lấy danh sách danh mục: {str(e)}")
            return []
    
    def get_products_from_category(self, category_url, max_pages=None):
        """
        Lấy danh sách sản phẩm từ một danh mục
        
        Args:
            category_url (str): URL của danh mục
            max_pages (int, optional): Số trang tối đa để cào
            
        Returns:
            list: Danh sách URL sản phẩm
        """
        log_and_emit(f"🔍 Đang lấy danh sách sản phẩm từ: {category_url}")
        
        product_urls = []
        current_page = 1
        
        while True:
            if max_pages and current_page > max_pages:
                break
            
            # Tạo URL cho trang hiện tại
            if current_page == 1:
                page_url = category_url
            else:
                # Kiểm tra format pagination của website
                if '?' in category_url:
                    page_url = f"{category_url}&page={current_page}"
                else:
                    page_url = f"{category_url}?page={current_page}"
            
            try:
                log_and_emit(f"📄 Đang cào trang {current_page}: {page_url}")
                
                response = self.session.get(page_url, timeout=30)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Tìm các link sản phẩm
                page_products = self._extract_product_links(soup)
                
                if not page_products:
                    log_and_emit(f"📄 Trang {current_page} không có sản phẩm, kết thúc")
                    break
                
                product_urls.extend(page_products)
                log_and_emit(f"📦 Trang {current_page}: Tìm thấy {len(page_products)} sản phẩm")
                
                # Kiểm tra xem có trang tiếp theo không
                if not self._has_next_page(soup):
                    log_and_emit(f"📄 Đã đến trang cuối, kết thúc")
                    break
                
                current_page += 1
                time.sleep(self.delay_between_requests)
                
            except Exception as e:
                log_and_emit(f"❌ Lỗi cào trang {current_page}: {str(e)}")
                break
        
        log_and_emit(f"✅ Đã lấy {len(product_urls)} sản phẩm từ {current_page-1} trang")
        return product_urls
    
    def _extract_product_links(self, soup):
        """
        Trích xuất link sản phẩm từ soup của trang danh mục
        
        Args:
            soup: BeautifulSoup object của trang
            
        Returns:
            list: Danh sách URL sản phẩm
        """
        product_links = []
        
        # Tìm các link sản phẩm - có thể có nhiều pattern khác nhau
        selectors = [
            'a[href*="/products/"]',  # Link chứa /products/
            'a[wire\\:navigate][title]',  # Link có wire:navigate và title
            '.product-item a',  # Link trong item sản phẩm
            '.product-card a',  # Link trong card sản phẩm
        ]
        
        for selector in selectors:
            links = soup.select(selector)
            for link in links:
                href = link.get('href')
                if href and '/products/' in href:
                    # Đảm bảo URL đầy đủ
                    if href.startswith('/'):
                        full_url = urljoin(self.base_url, href)
                    else:
                        full_url = href
                    
                    if full_url not in product_links:
                        product_links.append(full_url)
        
        return product_links
    
    def _has_next_page(self, soup):
        """
        Kiểm tra xem có trang tiếp theo không
        
        Args:
            soup: BeautifulSoup object của trang hiện tại
            
        Returns:
            bool: True nếu có trang tiếp theo
        """
        # Các selector có thể để tìm nút "Next" hoặc pagination
        next_selectors = [
            '.pagination .next:not(.disabled)',
            '.pagination a[rel="next"]',
            '.page-nav .next',
        ]
        
        for selector in next_selectors:
            if soup.select(selector):
                return True
        
        # Tìm link chứa text "Trang sau", "Next", ">"
        links = soup.find_all('a')
        for link in links:
            link_text = link.get_text(strip=True)
            if link_text in ['Trang sau', 'Next', '>', '›', '»']:
                return True
        
        return False
    
    def crawl_product_details(self, product_url):
        """
        Cào thông tin chi tiết một sản phẩm
        
        Args:
            product_url (str): URL của sản phẩm
            
        Returns:
            dict: Thông tin chi tiết sản phẩm
        """
        try:
            log_and_emit(f"📦 Đang cào sản phẩm: {product_url}")
            
            response = self.session.get(product_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Lấy thông tin cơ bản
            product_info = {
                'url': product_url,
                'name': self._extract_product_name(soup),
                'code': self._extract_product_code(soup),
                'price': self._extract_product_price(soup),
                'brand': self._extract_product_brand(soup),
                'specifications': self._extract_specifications(soup),
                'crawled_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            log_and_emit(f"✅ Đã cào thành công: {product_info['name']}")
            return product_info
            
        except Exception as e:
            log_and_emit(f"❌ Lỗi cào sản phẩm {product_url}: {str(e)}")
            return None
    
    def _extract_product_name(self, soup):
        """Trích xuất tên sản phẩm"""
        selectors = [
            'h1.content-title',
            'h1',
            '.product-title',
            '.content-title'
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                return element.get_text(strip=True)
        
        return "Không có tên sản phẩm"
    
    def _extract_product_code(self, soup):
        """Trích xuất mã sản phẩm"""
        selectors = [
            'p.content-meta__sku',
            '.product-sku',
            '.sku'
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                text = element.get_text(strip=True)
                # Trích xuất mã từ text "Mã sản phẩm: ABC123"
                match = re.search(r'Mã sản phẩm:\s*(.+)', text)
                if match:
                    return match.group(1).strip()
                return text
        
        return "Không có mã sản phẩm"
    
    def _extract_product_price(self, soup):
        """Trích xuất giá sản phẩm"""
        price_info = {
            'price': None,
            'price_text': None,
            'vat_note': None
        }
        
        # Tìm div chứa giá
        price_container = soup.select_one('.left')
        if price_container:
            # Lấy giá chính
            price_elements = price_container.select('p')
            for p in price_elements:
                text = p.get_text(strip=True)
                if 'đ' in text and 'VAT' not in text.upper():
                    price_info['price_text'] = text
                    # Trích xuất số tiền
                    price_match = re.search(r'([\d,.]+)đ', text)
                    if price_match:
                        price_str = price_match.group(1).replace(',', '').replace('.', '')
                        try:
                            price_info['price'] = int(price_str)
                        except:
                            pass
                elif 'VAT' in text.upper():
                    price_info['vat_note'] = text
        
        return price_info
    
    def _extract_product_brand(self, soup):
        """Trích xuất thương hiệu sản phẩm"""
        selectors = [
            'a.content-meta__brand',
            '.brand-name',
            '.product-brand a',
            '.brand a'
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                return element.get_text(strip=True)
        
        return "Không có thương hiệu"
    
    def _extract_specifications(self, soup):
        """Trích xuất thông số kỹ thuật"""
        specifications = {}
        
        # Tìm div chứa thông số kỹ thuật
        tech_div = soup.select_one('#technical')
        if not tech_div:
            tech_div = soup.select_one('[id="technical"]')
        if not tech_div:
            tech_div = soup.select_one('.content-tab__detail')
        
        if tech_div:
            # Tìm tất cả các ul trong div thông số
            ul_elements = tech_div.find_all('ul')
            
            for ul in ul_elements:
                li_elements = ul.find_all('li')
                
                for li in li_elements:
                    title_span = li.find('span', class_='title')
                    content_span = li.find('span', class_='content')
                    
                    if title_span and content_span:
                        title = title_span.get_text(strip=True)
                        content = content_span.get_text(strip=True)
                        specifications[title] = content
        
        return specifications
    
    def crawl_category_products(self, selected_categories, output_dir=None, max_products_per_category=None):
        """
        Cào sản phẩm từ các danh mục đã chọn
        
        Args:
            selected_categories (list): Danh sách các danh mục đã chọn
            output_dir (str, optional): Thư mục output
            max_products_per_category (int, optional): Số sản phẩm tối đa mỗi danh mục
            
        Returns:
            dict: Kết quả crawling
        """
        if not output_dir:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_dir = f"hoplongtech_products_{timestamp}"
        
        # Tạo thư mục output
        os.makedirs(output_dir, exist_ok=True)
        
        results = {
            'total_categories': len(selected_categories),
            'total_products': 0,
            'successful_products': 0,
            'failed_products': 0,
            'categories_data': {},
            'brands_data': {},
            'output_dir': output_dir,
            'errors': [],
            'start_time': time.time()  # Track thời gian bắt đầu
        }
        
        log_and_emit(f"🚀 Bắt đầu cào {len(selected_categories)} danh mục")
        
        try:
            for idx, category in enumerate(selected_categories, 1):
                category_name = category['name']
                category_url = category['url']
                
                log_and_emit(f"📂 [{idx}/{len(selected_categories)}] Đang xử lý danh mục: {category_name}")
                update_progress((idx-1) * 100 // len(selected_categories), 
                              f"Đang cào danh mục: {category_name}")
                
                try:
                    # Lấy danh sách sản phẩm
                    product_urls = self.get_products_from_category(category_url)
                    
                    if max_products_per_category:
                        product_urls = product_urls[:max_products_per_category]
                    
                    results['total_products'] += len(product_urls)
                    log_and_emit(f"📦 Tìm thấy {len(product_urls)} sản phẩm trong danh mục {category_name}")
                    
                    # Cào thông tin từng sản phẩm
                    category_products = []
                    
                    if product_urls:
                        log_and_emit(f"🔥 Bắt đầu crawl {len(product_urls)} sản phẩm với {self.max_workers} workers")
                        
                        # Xử lý theo batch để tối ưu
                        total_products = len(product_urls)
                        completed_products = 0
                        
                        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                            # Submit tất cả tasks cùng lúc
                            future_to_url = {
                                executor.submit(self.crawl_product_details, url): url 
                                for url in product_urls
                            }
                            
                            # Process results as they complete
                            for future in as_completed(future_to_url):
                                url = future_to_url[future]
                                try:
                                    product_info = future.result()
                                    completed_products += 1
                                    
                                    # Update progress chi tiết
                                    progress_percent = (completed_products * 100) // total_products
                                    update_progress(
                                        (idx-1) * 100 // len(selected_categories) + progress_percent // len(selected_categories), 
                                        f"[{category_name}] Đã cào {completed_products}/{total_products} sản phẩm"
                                    )
                                    
                                    if product_info:
                                        category_products.append(product_info)
                                        results['successful_products'] += 1
                                        
                                        # Phân loại theo thương hiệu
                                        brand = product_info['brand']
                                        if brand not in results['brands_data']:
                                            results['brands_data'][brand] = []
                                        results['brands_data'][brand].append(product_info)
                                        
                                        # Log tiến trình chi tiết mỗi 10 sản phẩm
                                        if completed_products % 10 == 0:
                                            log_and_emit(f"⚡ Đã hoàn thành {completed_products}/{total_products} sản phẩm ({progress_percent}%)")
                                        
                                    else:
                                        results['failed_products'] += 1
                                        results['errors'].append(f"Không cào được sản phẩm: {url}")
                                        
                                except Exception as e:
                                    completed_products += 1
                                    log_and_emit(f"❌ Lỗi xử lý sản phẩm {url}: {str(e)}")
                                    results['failed_products'] += 1
                                    results['errors'].append(f"Lỗi cào sản phẩm {url}: {str(e)}")
                                
                                # Giảm delay vì đã có rate limiting trong session
                                time.sleep(self.delay_between_requests / self.max_workers)
                    
                    results['categories_data'][category_name] = category_products
                    log_and_emit(f"✅ Hoàn thành danh mục {category_name}: {len(category_products)} sản phẩm thành công")
                    
                except Exception as e:
                    error_msg = f"Lỗi xử lý danh mục {category_name}: {str(e)}"
                    log_and_emit(f"❌ {error_msg}")
                    results['errors'].append(error_msg)
                    continue
            
            # Lưu kết quả
            self._save_results(results, output_dir)
            
            update_progress(100, "Hoàn thành crawling!")
            log_and_emit(f"🎉 Crawling hoàn thành! Kết quả: {results['successful_products']}/{results['total_products']} sản phẩm")
            
            if results['errors']:
                log_and_emit(f"⚠️ Có {len(results['errors'])} lỗi trong quá trình crawling")
            
            return results
            
        except Exception as e:
            error_msg = f"Lỗi nghiêm trọng trong quá trình crawling: {str(e)}"
            log_and_emit(f"❌ {error_msg}")
            results['errors'].append(error_msg)
            import traceback
            traceback.print_exc()
            return results
    
    def _save_results(self, results, output_dir):
        """
        Lưu kết quả crawling
        
        Args:
            results (dict): Dữ liệu kết quả
            output_dir (str): Thư mục output
        """
        log_and_emit("💾 Đang lưu kết quả...")
        
        # Lưu theo thương hiệu (như yêu cầu)
        brands_dir = os.path.join(output_dir, "brands")
        os.makedirs(brands_dir, exist_ok=True)
        
        for brand, products in results['brands_data'].items():
            if products:
                # Tạo tên file an toàn
                safe_brand_name = re.sub(r'[^\w\s-]', '', brand).strip()
                safe_brand_name = re.sub(r'[-\s]+', '_', safe_brand_name)
                
                brand_dir = os.path.join(brands_dir, safe_brand_name)
                os.makedirs(brand_dir, exist_ok=True)
                
                # Lưu JSON
                json_file = os.path.join(brand_dir, f"{safe_brand_name}_products.json")
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(products, f, ensure_ascii=False, indent=2)
                
                # Tạo bảng Excel
                self._create_excel_report(products, brand_dir, safe_brand_name)
                
                log_and_emit(f"📁 Đã lưu {len(products)} sản phẩm thương hiệu {brand}")
        
        # Lưu tổng hợp với thống kê chi tiết
        summary_data = {
            'summary': {
                'total_categories': results['total_categories'],
                'total_products': results['total_products'],
                'successful_products': results['successful_products'],
                'failed_products': results['failed_products'],
                'success_rate': round(results['successful_products'] / results['total_products'] * 100, 2) if results['total_products'] > 0 else 0,
                'brands_count': len(results['brands_data']),
                'crawled_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'crawl_duration': f"{time.time() - results['start_time']:.1f}s"
            },
            'brands_summary': {
                brand: len(products) 
                for brand, products in results['brands_data'].items()
            },
            'categories_summary': {
                cat_name: len(products)
                for cat_name, products in results['categories_data'].items()
            },
            'errors': results.get('errors', [])
        }
        
        summary_file = os.path.join(output_dir, "crawling_summary.json")
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        
        # Tạo file ZIP để download
        zip_file = self._create_download_zip(output_dir)
        results['download_file'] = zip_file
        
        log_and_emit(f"✅ Đã lưu tất cả kết quả vào: {output_dir}")
        log_and_emit(f"📦 File tải xuống: {zip_file}")
    
    def _create_excel_report(self, products, output_dir, brand_name):
        """
        Tạo báo cáo Excel cho sản phẩm
        
        Args:
            products (list): Danh sách sản phẩm
            output_dir (str): Thư mục output
            brand_name (str): Tên thương hiệu
        """
        try:
            import pandas as pd
            
            # Chuẩn bị dữ liệu cho Excel
            excel_data = []
            
            for product in products:
                row = {
                    'Tên sản phẩm': product.get('name', ''),
                    'Mã sản phẩm': product.get('code', ''),
                    'Thương hiệu': product.get('brand', ''),
                    'Giá (VNĐ)': product.get('price', {}).get('price', ''),
                    'Giá (Text)': product.get('price', {}).get('price_text', ''),
                    'Ghi chú VAT': product.get('price', {}).get('vat_note', ''),
                    'URL': product.get('url', ''),
                    'Thời gian cào': product.get('crawled_at', '')
                }
                
                # Thêm thông số kỹ thuật
                specs = product.get('specifications', {})
                for spec_name, spec_value in specs.items():
                    row[f"Thông số: {spec_name}"] = spec_value
                
                excel_data.append(row)
            
            # Tạo DataFrame và lưu Excel
            df = pd.DataFrame(excel_data)
            excel_file = os.path.join(output_dir, f"{brand_name}_products.xlsx")
            
            with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Sản phẩm', index=False)
                
                # Định dạng worksheet
                worksheet = writer.sheets['Sản phẩm']
                
                # Tự động điều chỉnh độ rộng cột
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
            
            log_and_emit(f"📊 Đã tạo báo cáo Excel: {excel_file}")
            
        except ImportError:
            log_and_emit("⚠️ Không có pandas, bỏ qua tạo file Excel")
        except Exception as e:
            log_and_emit(f"❌ Lỗi tạo Excel: {str(e)}")
    
    def _create_download_zip(self, output_dir):
        """
        Tạo file ZIP để download
        
        Args:
            output_dir (str): Thư mục chứa kết quả
            
        Returns:
            str: Đường dẫn file ZIP
        """
        import zipfile
        
        zip_filename = f"{output_dir}.zip"
        
        try:
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Thêm tất cả files trong output_dir
                for root, dirs, files in os.walk(output_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arc_name = os.path.relpath(file_path, os.path.dirname(output_dir))
                        zipf.write(file_path, arc_name)
            
            log_and_emit(f"📦 Đã tạo file ZIP: {zip_filename}")
            return zip_filename
            
        except Exception as e:
            log_and_emit(f"❌ Lỗi tạo ZIP: {str(e)}")
            return None
    
    def get_category_selection_interface(self):
        """
        Tạo giao diện cho người dùng chọn danh mục
        
        Returns:
            dict: Thông tin cho giao diện web
        """
        categories = self.get_sensor_categories()
        
        return {
            'success': True,
            'categories': categories,
            'total_categories': len(categories),
            'total_products': sum(cat['product_count'] for cat in categories),
            'base_url': self.base_url,
            'category_base_url': self.category_base_url
        }

# Các hàm utility bổ sung
def create_hoplong_crawler_session():
    """Tạo session mới cho HoplongCrawler"""
    return HoplongCrawler()

def test_hoplong_crawler():
    """Hàm test cơ bản cho HoplongCrawler"""
    crawler = HoplongCrawler()
    
    print("🧪 Testing HoplongCrawler...")
    
    # Test lấy danh mục
    categories = crawler.get_sensor_categories()
    print(f"✅ Tìm thấy {len(categories)} danh mục")
    
    if categories:
        # Test cào 1 sản phẩm từ danh mục đầu tiên
        first_category = categories[0]
        print(f"🔍 Test danh mục: {first_category['name']}")
        
        products = crawler.get_products_from_category(first_category['url'], max_pages=1)
        print(f"📦 Tìm thấy {len(products)} sản phẩm")
        
        if products:
            # Test crawl 1 sản phẩm
            product_info = crawler.crawl_product_details(products[0])
            if product_info:
                print(f"✅ Test thành công: {product_info['name']}")
                return True
    
    print("❌ Test thất bại")
    return False

if __name__ == "__main__":
    test_hoplong_crawler() 