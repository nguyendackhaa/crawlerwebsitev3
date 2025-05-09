import os
import re
import zipfile
import traceback
import pandas as pd
import openpyxl
from datetime import datetime
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
from openpyxl.utils import get_column_letter
from flask import current_app, session
from .utils import is_valid_url
from .crawler import is_category_url, is_product_url, extract_product_info, update_socketio, emit_progress

class CategoryCrawler:
    def __init__(self, socketio):
        """Khởi tạo CategoryCrawler với socketio instance"""
        self.socketio = socketio
        update_socketio(socketio)
        
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
            result_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], f'category_info_{timestamp}')
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
            zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, current_app.config['UPLOAD_FOLDER'])
                        zipf.write(file_path, relative_path)
            
            # Lưu đường dẫn file ZIP vào session
            session['last_download'] = zip_filename
            
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
            'a[href*="san-pham"]'
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
            '.pages a[href]'
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
    
    def _collect_product_info(self, category_dir, category_name, product_links, progress_base):
        """Thu thập thông tin sản phẩm"""
        try:
            # Tạo file Excel template tạm thời
            excel_temp_path = os.path.join(category_dir, f'{category_name}_template.xlsx')
            
            # Tạo template Excel
            wb = openpyxl.Workbook()
            ws = wb.active
            required_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'Tổng quan']
            for col_idx, field in enumerate(required_fields, 1):
                ws.cell(row=1, column=col_idx).value = field
            wb.save(excel_temp_path)
            
            # Thu thập thông tin sản phẩm
            emit_progress(progress_base + 20, f'Đang cào dữ liệu {len(product_links)} sản phẩm từ danh mục: {category_name}')
            
            excel_result = extract_product_info(product_links, excel_temp_path)
            
            # Copy file kết quả vào thư mục danh mục
            if excel_result and os.path.exists(excel_result):
                shutil.copy(excel_result, os.path.join(category_dir, f'{category_name}_products.xlsx'))
                product_info_list = pd.read_excel(excel_result).to_dict('records')
            else:
                product_info_list = []
                
            # Xóa file template tạm thời
            if os.path.exists(excel_temp_path):
                os.remove(excel_temp_path)
                
            return product_info_list
            
        except Exception as e:
            print(f"Lỗi khi thu thập thông tin sản phẩm từ danh mục {category_name}: {str(e)}")
            traceback.print_exc()
            return []
    
    def _create_reports(self, result_dir, category_info, valid_urls):
        """Tạo các báo cáo từ dữ liệu đã thu thập"""
        import re
        report_file = os.path.join(result_dir, 'category_report.xlsx')
        df = pd.DataFrame(category_info)

        if not df.empty:
            all_products_data = []
            products_without_price = []
            category_dfs = {}

            # Thu thập tất cả thông tin sản phẩm từ các danh mục
            for category_url in valid_urls:
                try:
                    category_name = self._extract_category_name(category_url)
                    category_dir = os.path.join(result_dir, category_name)
                    excel_file = os.path.join(category_dir, f'{category_name}_products.xlsx')
                    if os.path.exists(excel_file):
                        df_products = pd.read_excel(excel_file)
                        if 'Danh mục' not in df_products.columns:
                            df_products['Danh mục'] = category_name
                        all_products_data.extend(df_products.to_dict('records'))
                        products_without_price_count = df_products['Giá'].isna().sum() + df_products['Giá'].isin(['', 'N/A', 'Liên hệ']).sum()
                        products_without_price.append({
                            'Tên danh mục': category_name,
                            'Số sản phẩm không có giá': products_without_price_count
                        })
                        # Tạo tên sheet hợp lệ cho Excel
                        sheet_name = re.sub(r'[\\/*?:\[\]]', '_', category_name)[:31]
                        # Nếu trùng tên sheet thì thêm số thứ tự
                        base_sheet_name = sheet_name
                        count = 1
                        while sheet_name in category_dfs:
                            sheet_name = f"{base_sheet_name}_{count}"
                            sheet_name = sheet_name[:31]
                            count += 1
                        category_dfs[sheet_name] = df_products
                except Exception as e:
                    print(f'Lỗi khi xử lý danh mục {category_url} trong báo cáo: {str(e)}')

            # Ghi ra file Excel với nhiều sheet
            with pd.ExcelWriter(report_file, engine='openpyxl') as writer:
                # Sheet tổng quan
                df.to_excel(writer, sheet_name='Tổng quan', index=False)
                # Sheet sản phẩm không có giá
                if products_without_price:
                    df_no_price = pd.DataFrame(products_without_price)
                    df_no_price.to_excel(writer, sheet_name='Sản phẩm không có giá', index=False)
                # Sheet tất cả sản phẩm
                if all_products_data:
                    df_all = pd.DataFrame(all_products_data)
                    df_all.to_excel(writer, sheet_name='Tất cả sản phẩm', index=False)
                # Sheet riêng từng danh mục
                for sheet_name, df_cat in category_dfs.items():
                    df_cat.to_excel(writer, sheet_name=sheet_name, index=False)

        emit_progress(100, f'Đã hoàn tất thu thập dữ liệu từ {len(valid_urls)} danh mục. Xem kết quả trong thư mục: {result_dir}') 