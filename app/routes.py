import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, send_from_directory, jsonify, session
from werkzeug.utils import secure_filename
import tempfile
import traceback
from app.crawler import extract_category_links, scrape_product_info, is_product_url, get_product_info, download_autonics_images, download_autonics_jpg_images, download_product_documents, extract_product_urls, is_category_url, download_baa_product_images, download_baa_product_images_fixed, extract_product_price
import pandas as pd
from openpyxl.utils import get_column_letter
from datetime import datetime
from flask import current_app
from app import utils, socketio
from app.category_crawler import CategoryCrawler
import time
import openpyxl
import re
import zipfile
import shutil
from urllib.parse import urlparse

main_bp = Blueprint('main', __name__)

ALLOWED_EXTENSIONS_TXT = {'txt'}
ALLOWED_EXTENSIONS_EXCEL = {'xlsx', 'xls'}

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

@main_bp.route('/')
def index():
    return render_template('index.html')

@main_bp.route('/extract-links', methods=['POST'])
def extract_links():
    """
    Trích xuất liên kết sản phẩm từ file txt chứa danh sách URL danh mục
    """
    if 'link_file' not in request.files:
        flash('Không tìm thấy file!', 'error')
        return redirect(url_for('main.index'))
        
    file = request.files['link_file']
    
    if file.filename == '':
        flash('Không có file nào được chọn!', 'error')
        return redirect(url_for('main.index'))
        
    if not allowed_file(file.filename, ALLOWED_EXTENSIONS_TXT):
        flash('Chỉ cho phép file .txt!', 'error')
        return redirect(url_for('main.index'))
    
    try:
        # Đọc nội dung file
        content = file.read().decode('utf-8')
        
        # Tách thành danh sách URL, bỏ qua dòng trống
        raw_urls = content.strip().split('\n')
        urls = [url.strip() for url in raw_urls if url.strip()]
        
        # Lọc các URL hợp lệ
        valid_urls = []
        invalid_urls = []
        
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {'percent': 0, 'message': 'Đang kiểm tra URL...'})
        
        # Kiểm tra các URL
        for url in urls:
            # Kiểm tra URL đặc biệt của đèn tháp LED
            is_led_url = 'den-thap-led-sang-tinh-chop-nhay-d45mm-qlight-st45l-and-st45ml-series_4779' in url
            
            if utils.is_valid_url(url) and (is_led_url or is_category_url(url)):
                valid_urls.append(url)
            else:
                invalid_urls.append(url)
        
        if not valid_urls:
            flash('Không có URL danh mục hợp lệ trong file!', 'error')
            return redirect(url_for('main.index'))
            
        # Gửi thông báo cập nhật
        socketio.emit('progress_update', {'percent': 10, 'message': f'Đã tìm thấy {len(valid_urls)} URL danh mục hợp lệ'})
        
        # Trích xuất liên kết sản phẩm từ các URL danh mục
        product_links = extract_category_links(valid_urls)
        
        if not product_links:
            flash('Không tìm thấy liên kết sản phẩm nào!', 'error')
            return redirect(url_for('main.index'))
            
        # Gửi thông báo hoàn thành trích xuất liên kết
        socketio.emit('progress_update', {'percent': 90, 'message': f'Đã trích xuất xong {len(product_links)} liên kết sản phẩm'})
        
        # Tạo file kết quả
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        result_filename = f"product_links_{timestamp}.txt"
        result_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], result_filename)
        
        # Lưu danh sách liên kết vào file
        with open(result_file_path, 'w', encoding='utf-8') as f:
            for link in product_links:
                f.write(link + '\n')
        
        # Tạo đường dẫn download
        download_url = url_for('main.download_file', filename=result_filename)
        
        # Thông báo thành công
        success_message = f'Đã tìm thấy {len(product_links)} liên kết sản phẩm. '
        if invalid_urls:
            success_message += f'Có {len(invalid_urls)} URL không hợp lệ bị bỏ qua.'
            
        flash(success_message, 'success')
        
        # Gửi thông báo hoàn thành
        socketio.emit('progress_update', {'percent': 100, 'message': 'Hoàn thành!'})
        
        # Hiển thị trang kết quả
        return render_template('index.html', download_url=download_url)
        
    except Exception as e:
        error_message = str(e)
        # In chi tiết lỗi
        traceback.print_exc()
        flash(f'Lỗi: {error_message}', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/scrape-products', methods=['POST'])
def scrape_products():
    try:
        if 'product_link_file' not in request.files or 'excel_template' not in request.files:
            return render_template('index.html', error="Thiếu file")
        
        link_file = request.files['product_link_file']
        excel_file = request.files['excel_template']
        
        if link_file.filename == '' or excel_file.filename == '':
            return render_template('index.html', error="Không có file nào được chọn")
        
        if not allowed_file(link_file.filename, ALLOWED_EXTENSIONS_TXT):
            return render_template('index.html', error="File liên kết phải là file .txt")
        
        if not allowed_file(excel_file.filename, ALLOWED_EXTENSIONS_EXCEL):
            return render_template('index.html', error="File mẫu phải là file .xlsx hoặc .xls")
        
        # Đọc file txt chứa danh sách URL sản phẩm
        product_urls = []
        invalid_urls = []
        content = link_file.read().decode('utf-8')
        
        for line in content.splitlines():
            line = line.strip()
            if line.startswith('http'):
                if is_product_url(line):
                    product_urls.append(line)
                else:
                    invalid_urls.append(line)
        
        if invalid_urls:
            warning_msg = f"Có {len(invalid_urls)} URL không phải là trang sản phẩm và sẽ bị bỏ qua."
            print(warning_msg)
            print(f"Các URL không hợp lệ: {invalid_urls}")
            
        if not product_urls:
            return render_template('index.html', error="Không tìm thấy URL sản phẩm hợp lệ trong file")
        
        # Lưu file Excel mẫu
        excel_path = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx').name
        excel_file.save(excel_path)
        
        # Thu thập thông tin sản phẩm
        result_file = scrape_product_info(product_urls, excel_path)
        
        # Xóa file Excel mẫu sau khi sử dụng
        os.unlink(excel_path)
        
        success_message = f"Đã thu thập thông tin từ {len(product_urls)} sản phẩm."
        if invalid_urls:
            success_message += f" Đã bỏ qua {len(invalid_urls)} URL không hợp lệ."
            
        print(success_message)
        
        return send_file(result_file, as_attachment=True, download_name="product_info.xlsx")
    except Exception as e:
        error_msg = f"Lỗi khi xử lý: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return render_template('index.html', error=error_msg)

@main_bp.route('/process', methods=['POST'])
def process_url():
    """
    Process URL from form submission and scrape product information
    """
    try:
        url = request.form.get('url')
        required_fields = request.form.getlist('required_fields')
        
        if not url:
            flash('URL không được để trống!', 'error')
            return redirect(url_for('index'))
        
        if not utils.is_valid_url(url):
            flash('URL không hợp lệ!', 'error')
            return redirect(url_for('index'))

        if not required_fields:
            flash('Hãy chọn ít nhất một trường dữ liệu!', 'error')
            return redirect(url_for('index'))

        # Convert checkbox values to field names
        field_mapping = {
            'field_id': 'STT',
            'field_code': 'Mã sản phẩm',
            'field_name': 'Tên sản phẩm',
            'field_overview': 'Tổng quan',
            'field_url': 'URL'
        }
        
        selected_fields = [field_mapping[field] for field in required_fields if field in field_mapping]
        
        # Scrape product information
        product_info_list = get_product_info(url, selected_fields)
        
        if not product_info_list:
            flash('Không tìm thấy thông tin sản phẩm!', 'error')
            return redirect(url_for('index'))
            
        # Generate a temporary file name
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        temp_file = f"product_info_{timestamp}.xlsx"
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], temp_file)
        
        # Save to Excel file
        utils.save_to_excel(product_info_list, file_path)
        
        # Generate download URL
        download_url = url_for('main.download_file', filename=temp_file)
        
        return render_template('index.html', download_url=download_url)
    
    except Exception as e:
        current_app.logger.error(f"Error processing URL: {str(e)}")
        flash(f'Đã xảy ra lỗi: {str(e)}', 'error')
        return redirect(url_for('index'))

@main_bp.route('/download/<path:filename>')
def download_file(filename):
    download_dir = os.path.join(current_app.root_path, 'downloads')
    file_path = os.path.join(download_dir, filename)
    
    # Kiểm tra xem file có tồn tại không
    if not os.path.exists(file_path):
        # Trường hợp file ZIP không tồn tại nhưng thư mục tồn tại (chưa được nén)
        if filename.endswith('.zip'):
            folder_name = filename[:-4]  # Bỏ phần .zip để lấy tên thư mục
            folder_path = os.path.join(download_dir, folder_name)
            
            # Kiểm tra xem thư mục có tồn tại không
            if os.path.exists(folder_path) and os.path.isdir(folder_path):
                try:
                    # Tạo file ZIP mới
                    utils.create_zip_from_folder(folder_path, file_path)
                    print(f"Đã tạo file ZIP mới: {file_path}")
                except Exception as e:
                    print(f"Lỗi khi tạo file ZIP: {str(e)}")
                    flash(f'Lỗi khi tạo file ZIP: {str(e)}', 'error')
                    return redirect(url_for('main.index'))
    
    # Tách filename thành thư mục và tên file nếu có '/'
    if '/' in filename:
        # Ví dụ: reports/product_comparison_report_20250429_170113.xlsx
        folder, file = filename.split('/', 1)
        download_path = os.path.join(download_dir, folder)
        return send_from_directory(directory=download_path, path=file, as_attachment=True)
    else:
        # Trường hợp file nằm trực tiếp trong thư mục downloads
        return send_from_directory(directory=download_dir, path=filename, as_attachment=True)

@main_bp.route('/view-images')
def view_images():
    """Hiển thị danh sách các ảnh đã tải xuống"""
    try:
        # Thư mục chứa tất cả các thư mục ảnh
        download_dir = os.path.join(current_app.root_path, 'downloads')
        
        # Tìm tất cả các thư mục images_*
        image_folders = [d for d in os.listdir(download_dir) if os.path.isdir(os.path.join(download_dir, d)) and d.startswith('images_')]
        
        if not image_folders:
            return render_template('view_images.html', error="Không tìm thấy thư mục ảnh nào")
        
        # Sắp xếp theo thời gian tạo (mới nhất trước)
        image_folders.sort(reverse=True)
        
        # Lấy thư mục mới nhất
        latest_folder = image_folders[0]
        latest_folder_path = os.path.join(download_dir, latest_folder)
        
        # Tìm tất cả các file ảnh trong thư mục
        all_images = []
        for root, dirs, files in os.walk(latest_folder_path):
            for file in files:
                if file.endswith('.webp'):
                    # Tạo đường dẫn tương đối từ thư mục tải xuống
                    rel_path = os.path.relpath(os.path.join(root, file), download_dir)
                    # Chuyển dấu gạch ngược thành gạch chéo cho URL
                    rel_path = rel_path.replace('\\', '/')
                    
                    # Lấy mã sản phẩm từ tên file
                    product_code = os.path.splitext(file)[0]
                    
                    all_images.append({
                        'path': rel_path,
                        'code': product_code,
                        'url': url_for('main.view_image', image_path=rel_path)
                    })
        
        return render_template('view_images.html', images=all_images, folder=latest_folder)
        
    except Exception as e:
        error_msg = f"Lỗi khi hiển thị ảnh: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return render_template('view_images.html', error=error_msg)

@main_bp.route('/view-image/<path:image_path>')
def view_image(image_path):
    """Hiển thị một ảnh cụ thể"""
    download_dir = os.path.join(current_app.root_path, 'downloads')
    return send_from_directory(directory=download_dir, path=image_path)

@main_bp.route('/convert-to-webp', methods=['POST'])
def convert_to_webp():
    """Chuyển đổi ảnh từ JPG/PNG sang WebP"""
    try:
        if 'image_files' not in request.files:
            return render_template('index.html', error="Không tìm thấy file")
        
        files = request.files.getlist('image_files')
        
        if not files or files[0].filename == '':
            return render_template('index.html', error="Không có file nào được chọn")
        
        # Kiểm tra các file có đúng định dạng không
        for file in files:
            filename = file.filename.lower()
            if not (filename.endswith('.jpg') or filename.endswith('.jpeg') or filename.endswith('.png')):
                return render_template('index.html', error="Chỉ chấp nhận file .jpg, .jpeg, .png")
        
        # Tạo thư mục đầu ra cho ảnh đã chuyển đổi
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        webp_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], f'webp_converted_{timestamp}')
        os.makedirs(webp_folder, exist_ok=True)
        print(f"Đã tạo thư mục lưu ảnh webp: {webp_folder}")
        
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 5, 
            'message': f'Bắt đầu chuyển đổi {len(files)} ảnh sang định dạng WebP'
        })
        
        # Biến lưu trữ kết quả chuyển đổi
        converted_files = []
        conversion_results = []
        
        # Xử lý từng file
        for i, file in enumerate(files):
            # Tính phần trăm tiến trình
            progress = int(5 + ((i / len(files)) * 90))
            
            try:
                # Tên file gốc và tên file webp
                original_filename = secure_filename(file.filename)
                base_name = os.path.splitext(original_filename)[0]
                webp_filename = f"{base_name}.webp"
                
                # Đường dẫn đầy đủ cho file đầu ra
                webp_path = os.path.join(webp_folder, webp_filename)
                
                # Cập nhật tiến trình
                socketio.emit('progress_update', {
                    'percent': progress,
                    'message': f'Đang xử lý ảnh {i+1}/{len(files)}: {original_filename}'
                })
                
                # Đọc ảnh với Pillow
                from PIL import Image
                img = Image.open(file)
                
                # Chuyển đổi sang mode RGB nếu cần
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                # Lưu dưới dạng WebP với chất lượng cao
                img.save(webp_path, 'WEBP', quality=90)
                
                # Lưu thông tin kết quả
                converted_files.append(webp_path)
                conversion_results.append({
                    'original': original_filename,
                    'converted': webp_filename,
                    'path': webp_path,
                    'status': 'success'
                })
                
                print(f"Đã chuyển đổi thành công: {original_filename} -> {webp_filename}")
                
            except Exception as e:
                error_msg = f"Lỗi khi xử lý {file.filename}: {str(e)}"
                print(error_msg)
                conversion_results.append({
                    'original': file.filename,
                    'status': 'error',
                    'error': str(e)
                })
        
        # Gửi thông báo hoàn thành
        socketio.emit('progress_update', {
            'percent': 95, 
            'message': f'Đã chuyển đổi thành công {len(converted_files)}/{len(files)} ảnh. Đang tạo file ZIP...'
        })
        
        # Tạo file ZIP để tải xuống
        zip_filename = f'webp_images_{timestamp}.zip'
        zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
        
        # Tạo file zip từ thư mục ảnh
        if utils.create_zip_from_folder(webp_folder, zip_path):
            print(f"Đã tạo file ZIP thành công: {zip_path}")
            
            # Cập nhật hoàn thành
            socketio.emit('progress_update', {
                'percent': 100, 
                'message': f'Đã chuyển đổi và nén {len(converted_files)} ảnh thành công!'
            })
            
            # Thông báo thành công
            success_message = f"Đã chuyển đổi thành công {len(converted_files)}/{len(files)} ảnh sang định dạng WebP."
            
            # Tạo URL để tải xuống file zip
            download_url = url_for('main.download_file', filename=zip_filename)
            
            return render_template('index.html', download_url=download_url, success_message=success_message)
        else:
            return render_template('index.html', error="Không thể tạo file ZIP từ ảnh đã chuyển đổi")
    
    except Exception as e:
        error_msg = f"Lỗi khi xử lý: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        socketio.emit('progress_update', {'percent': 0, 'message': f'Đã xảy ra lỗi: {str(e)}'})
        return render_template('index.html', error=error_msg)

@main_bp.route('/download-images', methods=['POST'])
def download_images():
    try:
        # Đặt thời gian bắt đầu
        start_time = time.time()
        
        if 'product_code_file' not in request.files:
            return render_template('index.html', error="Không tìm thấy file")
        
        file = request.files['product_code_file']
        
        if file.filename == '':
            return render_template('index.html', error="Không có file nào được chọn")
        
        if not allowed_file(file.filename, ALLOWED_EXTENSIONS_TXT):
            return render_template('index.html', error="Chỉ chấp nhận file .txt")
        
        # Đọc file txt chứa danh sách mã sản phẩm
        product_codes = []
        content = file.read().decode('utf-8')
        for line in content.splitlines():
            line = line.strip()
            if line:  # Bỏ qua dòng trống
                product_codes.append(line)
        
        if not product_codes:
            return render_template('index.html', error="Không tìm thấy mã sản phẩm hợp lệ trong file")
        
        # Tạo thư mục đầu ra cho hình ảnh
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        images_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], f'images_{timestamp}')
        os.makedirs(images_folder, exist_ok=True)
        print(f"Đã tạo thư mục lưu ảnh: {images_folder}")
        
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 5, 
            'message': f'Chuẩn bị tải {len(product_codes)} hình ảnh sản phẩm Autonics'
        })
        
        # Tải ảnh sản phẩm với giới hạn thời gian
        summary = download_autonics_images(product_codes, images_folder)
        
        if summary['successful'] == 0:
            elapsed_time = time.time() - start_time
            print(f"Quá trình xử lý thất bại sau {elapsed_time:.2f} giây")
            return render_template('index.html', error="Không tải được ảnh nào")
        
        # Tạo đường dẫn cho file zip
        zip_filename = f'product_images_{timestamp}.zip'
        zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
        print(f"Bắt đầu tạo file ZIP: {zip_path}")
        
        # Tạo file zip từ thư mục ảnh
        if utils.create_zip_from_folder(images_folder, zip_path):
            print(f"Đã tạo file ZIP thành công: {zip_path}")
            success_message = f"Đã tải thành công {summary['successful']}/{summary['total']} ảnh sản phẩm."
            
            # Tạo URL để tải xuống file zip
            download_url = url_for('main.download_file', filename=zip_filename)
            
            # Tính toán thời gian xử lý tổng cộng
            elapsed_time = time.time() - start_time
            print(f"Hoàn thành sau {elapsed_time:.2f} giây")
            avg_time = elapsed_time / len(product_codes) if product_codes else 0
            print(f"Thời gian xử lý trung bình: {avg_time:.2f} giây/sản phẩm")
            
            # Bổ sung thông tin hiệu năng vào thông báo thành công
            if avg_time <= 10:
                success_message += f" Tốc độ xử lý: {avg_time:.2f} giây/sản phẩm (tốt)."
            else:
                success_message += f" Tốc độ xử lý: {avg_time:.2f} giây/sản phẩm (cần cải thiện)."
            
            return render_template('index.html', download_url=download_url, success_message=success_message)
        else:
            return render_template('index.html', error="Không thể tạo file ZIP từ ảnh đã tải")
    
    except Exception as e:
        error_msg = f"Lỗi khi xử lý: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        socketio.emit('progress_update', {'percent': 0, 'message': f'Đã xảy ra lỗi: {str(e)}'})
        return render_template('index.html', error=error_msg)

@main_bp.route('/download-jpg-images', methods=['POST'])
def download_jpg_images():
    try:
        # Đặt thời gian bắt đầu
        start_time = time.time()
        
        if 'product_code_file' not in request.files:
            return render_template('index.html', error="Không tìm thấy file")
        
        file = request.files['product_code_file']
        
        if file.filename == '':
            return render_template('index.html', error="Không có file nào được chọn")
        
        if not allowed_file(file.filename, ALLOWED_EXTENSIONS_TXT):
            return render_template('index.html', error="Chỉ chấp nhận file .txt")
        
        # Đọc file txt chứa danh sách mã sản phẩm
        product_codes = []
        content = file.read().decode('utf-8')
        for line in content.splitlines():
            line = line.strip()
            if line:  # Bỏ qua dòng trống
                product_codes.append(line)
        
        if not product_codes:
            return render_template('index.html', error="Không tìm thấy mã sản phẩm hợp lệ trong file")
        
        # Tạo thư mục đầu ra cho hình ảnh
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        images_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], f'jpg_images_{timestamp}')
        os.makedirs(images_folder, exist_ok=True)
        print(f"Đã tạo thư mục lưu ảnh JPG: {images_folder}")
        
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 5, 
            'message': f'Chuẩn bị tải {len(product_codes)} hình ảnh JPG chất lượng cao'
        })
        
        # Tải ảnh sản phẩm với chất lượng cao
        summary = download_autonics_jpg_images(product_codes, images_folder)
        
        if summary['successful'] == 0:
            elapsed_time = time.time() - start_time
            print(f"Quá trình xử lý thất bại sau {elapsed_time:.2f} giây")
            return render_template('index.html', error="Không tải được ảnh nào")
        
        # Tạo đường dẫn cho file zip
        zip_filename = f'jpg_product_images_{timestamp}.zip'
        zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
        print(f"Bắt đầu tạo file ZIP: {zip_path}")
        
        # Tạo file zip từ thư mục ảnh
        if utils.create_zip_from_folder(images_folder, zip_path):
            print(f"Đã tạo file ZIP thành công: {zip_path}")
            success_message = f"Đã tải thành công {summary['successful']}/{summary['total']} ảnh JPG chất lượng cao."
            
            # Tạo URL để tải xuống file zip
            download_url = url_for('main.download_file', filename=zip_filename)
            
            # Tính toán thời gian xử lý tổng cộng
            elapsed_time = time.time() - start_time
            print(f"Hoàn thành sau {elapsed_time:.2f} giây")
            avg_time = elapsed_time / len(product_codes) if product_codes else 0
            print(f"Thời gian xử lý trung bình: {avg_time:.2f} giây/sản phẩm")
            
            # Bổ sung thông tin hiệu năng vào thông báo thành công
            if avg_time <= 10:
                success_message += f" Tốc độ xử lý: {avg_time:.2f} giây/sản phẩm (tốt)."
            else:
                success_message += f" Tốc độ xử lý: {avg_time:.2f} giây/sản phẩm (cần cải thiện)."
            
            return render_template('index.html', download_url=download_url, success_message=success_message)
        else:
            return render_template('index.html', error="Không thể tạo file ZIP từ ảnh đã tải")
    
    except Exception as e:
        error_msg = f"Lỗi khi xử lý: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        socketio.emit('progress_update', {'percent': 0, 'message': f'Đã xảy ra lỗi: {str(e)}'})
        return render_template('index.html', error=error_msg)

@main_bp.route('/download-documents', methods=['POST'])
def download_documents():
    try:
        # Đặt thời gian bắt đầu
        start_time = time.time()
        
        if 'product_code_file' not in request.files:
            return render_template('index.html', error="Không tìm thấy file")
        
        file = request.files['product_code_file']
        
        if file.filename == '':
            return render_template('index.html', error="Không có file nào được chọn")
        
        if not allowed_file(file.filename, ALLOWED_EXTENSIONS_TXT):
            return render_template('index.html', error="Chỉ chấp nhận file .txt")
        
        # Đọc file txt chứa danh sách URL sản phẩm thay vì mã sản phẩm
        product_urls = []
        content = file.read().decode('utf-8')
        for line in content.splitlines():
            line = line.strip()
            if line and line.startswith('http'):  # Chỉ nhận các dòng có chứa URL
                product_urls.append(line)
        
        if not product_urls:
            return render_template('index.html', error="Không tìm thấy URL sản phẩm hợp lệ trong file")
        
        # Tạo thư mục đầu ra cho tài liệu PDF
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        documents_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], f'pdf_documents_{timestamp}')
        os.makedirs(documents_folder, exist_ok=True)
        print(f"Đã tạo thư mục lưu tài liệu PDF: {documents_folder}")
        
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 5, 
            'message': f'Chuẩn bị tải tài liệu PDF cho {len(product_urls)} sản phẩm'
        })
        
        # Tải tài liệu PDF cho các sản phẩm
        summary = download_product_documents(product_urls, documents_folder)
        
        # Tính tổng số tài liệu đã tải thành công
        total_documents = 0
        for product_code, result in summary['product_results'].items():
            total_documents += result.get('successful_documents', 0)
        
        if total_documents == 0:
            elapsed_time = time.time() - start_time
            print(f"Quá trình xử lý thất bại sau {elapsed_time:.2f} giây")
            return render_template('index.html', error="Không tải được tài liệu PDF nào")
        
        # Tạo đường dẫn cho file zip
        zip_filename = f'product_documents_{timestamp}.zip'
        zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
        print(f"Bắt đầu tạo file ZIP: {zip_path}")
        
        # Tạo file zip từ thư mục tài liệu
        if utils.create_zip_from_folder(documents_folder, zip_path):
            print(f"Đã tạo file ZIP thành công: {zip_path}")
            success_message = f"Đã tải thành công {total_documents} tài liệu PDF từ {summary['successful_products']}/{summary['total_products']} sản phẩm."
            
            # Tạo URL để tải xuống file zip
            download_url = url_for('main.download_file', filename=zip_filename)
            
            # Tính toán thời gian xử lý tổng cộng
            elapsed_time = time.time() - start_time
            print(f"Hoàn thành sau {elapsed_time:.2f} giây")
            avg_time = elapsed_time / len(product_urls) if product_urls else 0
            print(f"Thời gian xử lý trung bình: {avg_time:.2f} giây/sản phẩm")
            
            # Bổ sung thông tin hiệu năng vào thông báo thành công
            if avg_time <= 15:
                success_message += f" Tốc độ xử lý: {avg_time:.2f} giây/sản phẩm (tốt)."
            else:
                success_message += f" Tốc độ xử lý: {avg_time:.2f} giây/sản phẩm (cần cải thiện)."
            
            return render_template('index.html', download_url=download_url, success_message=success_message)
        else:
            return render_template('index.html', error="Không thể tạo file ZIP từ tài liệu đã tải")
    
    except Exception as e:
        error_msg = f"Lỗi khi xử lý: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        socketio.emit('progress_update', {'percent': 0, 'message': f'Đã xảy ra lỗi: {str(e)}'})
        return render_template('index.html', error=error_msg)

@main_bp.route('/compare-categories', methods=['POST'])
def compare_categories():
    try:
        # Kiểm tra các file đã tải lên
        if 'urls_file_1' not in request.files or 'urls_file_2' not in request.files:
            return render_template('index.html', error="Vui lòng tải lên cả hai file TXT chứa danh sách URL")
        
        urls_file_1 = request.files['urls_file_1']
        urls_file_2 = request.files['urls_file_2']
        
        if urls_file_1.filename == '' or urls_file_2.filename == '':
            return render_template('index.html', error="Không có file nào được chọn")
        
        if not allowed_file(urls_file_1.filename, ALLOWED_EXTENSIONS_TXT) or not allowed_file(urls_file_2.filename, ALLOWED_EXTENSIONS_TXT):
            return render_template('index.html', error="Chỉ chấp nhận file .txt cho danh sách URL")
        
        # Thông báo tiến trình
        socketio.emit('progress_update', {
            'percent': 5, 
            'message': 'Đang đọc file URLs...'
        })
        
        # Đọc danh sách URL từ file
        urls_1 = []  # URLs từ BAA.vn
        content = urls_file_1.read().decode('utf-8')
        for line in content.splitlines():
            line = line.strip()
            if line and line.startswith('http'):
                urls_1.append(line)
        
        urls_2 = []  # URLs từ HaiphongTech
        content = urls_file_2.read().decode('utf-8')
        for line in content.splitlines():
            line = line.strip()
            if line and line.startswith('http'):
                urls_2.append(line)
        
        if not urls_1:
            return render_template('index.html', error="Không tìm thấy URL hợp lệ trong file thứ nhất")
        
        if not urls_2:
            return render_template('index.html', error="Không tìm thấy URL hợp lệ trong file thứ hai")
        
        # Thông báo trước khi bắt đầu thu thập
        socketio.emit('progress_update', {
            'percent': 10, 
            'message': f'Đã tìm thấy {len(urls_1)} URL từ file BAA.vn. Đang xử lý...'
        })
        
        # Thu thập sản phẩm từ BAA.vn
        from app.crawler import is_product_url, extract_product_urls
        
        # Phân loại URL thành URL danh mục và URL sản phẩm
        product_urls_1 = []
        category_urls_1 = []
        
        for url in urls_1:
            if is_product_url(url):
                product_urls_1.append(url)
            else:
                category_urls_1.append(url)
        
        # Thu thập thêm từ các URL danh mục
        for i, category_url in enumerate(category_urls_1):
            progress = 10 + int((i / len(category_urls_1)) * 30)
            socketio.emit('progress_update', {
                'percent': progress, 
                'message': f'Đang xử lý URL danh mục BAA.vn ({i+1}/{len(category_urls_1)}): {category_url}'
            })
            
            # Lấy sản phẩm từ danh mục
            category_products = extract_product_urls(category_url)
            for product_url in category_products:
                if product_url not in product_urls_1:
                    product_urls_1.append(product_url)
        
        if not product_urls_1:
            return render_template('index.html', error="Không tìm thấy sản phẩm nào từ URLs BAA.vn")
        
        socketio.emit('progress_update', {
            'percent': 40, 
            'message': f'Đã tìm thấy {len(product_urls_1)} sản phẩm từ BAA.vn. Đang xử lý HaiphongTech...'
        })
        
        # Thu thập sản phẩm từ HaiphongTech
        product_urls_2 = []
        category_urls_2 = []
        
        for url in urls_2:
            if is_product_url(url):
                product_urls_2.append(url)
            else:
                category_urls_2.append(url)
        
        # Thu thập thêm từ các URL danh mục
        for i, category_url in enumerate(category_urls_2):
            progress = 40 + int((i / len(category_urls_2)) * 30)
            socketio.emit('progress_update', {
                'percent': progress, 
                'message': f'Đang xử lý URL danh mục HaiphongTech ({i+1}/{len(category_urls_2)}): {category_url}'
            })
            
            # Lấy sản phẩm từ danh mục
            category_products = extract_product_urls(category_url)
            for product_url in category_products:
                if product_url not in product_urls_2:
                    product_urls_2.append(product_url)
        
        if not product_urls_2:
            return render_template('index.html', error="Không tìm thấy sản phẩm nào từ URLs HaiphongTech")
        
        socketio.emit('progress_update', {
            'percent': 70, 
            'message': f'Đã tìm thấy {len(product_urls_2)} sản phẩm từ HaiphongTech. Đang trích xuất và chuẩn hóa mã sản phẩm...'
        })
        
        # Trích xuất và chuẩn hóa mã sản phẩm từ các URL của BAA.vn
        product_codes_1 = []  # Chuẩn hóa mã sản phẩm
        product_urls_by_code_1 = {}  # Map mã sản phẩm -> URL
        original_codes_by_std_1 = {}  # Map mã chuẩn hóa -> mã gốc
        
        for i, url in enumerate(product_urls_1):
            if i % 10 == 0:
                progress = 70 + int((i / len(product_urls_1)) * 5)
                socketio.emit('progress_update', {
                    'percent': progress, 
                    'message': f'Đang trích xuất mã sản phẩm từ BAA.vn ({i}/{len(product_urls_1)})...'
                })
            
            # Trích xuất mã sản phẩm từ URL hoặc nội dung trang
            original_code = extract_product_code_from_url(url)
            if original_code:
                # Function extract_product_code_from_url đã gọi standardize_product_code
                product_codes_1.append(original_code)
                product_urls_by_code_1[original_code] = url
                original_codes_by_std_1[original_code] = original_code
                print(f"Sản phẩm BAA.vn: {original_code} - {url}")
        
        # Trích xuất và chuẩn hóa mã sản phẩm từ các URL của HaiphongTech
        product_codes_2 = []  # Chuẩn hóa mã sản phẩm
        product_urls_by_code_2 = {}  # Map mã sản phẩm -> URL
        original_codes_by_std_2 = {}  # Map mã chuẩn hóa -> mã gốc
        
        for i, url in enumerate(product_urls_2):
            if i % 10 == 0:
                progress = 75 + int((i / len(product_urls_2)) * 5)
                socketio.emit('progress_update', {
                    'percent': progress, 
                    'message': f'Đang trích xuất mã sản phẩm từ HaiphongTech ({i}/{len(product_urls_2)})...'
                })
            
            # Trích xuất mã sản phẩm từ URL hoặc nội dung trang
            original_code = extract_product_code_from_url(url)
            if original_code:
                # Function extract_product_code_from_url đã gọi standardize_product_code
                product_codes_2.append(original_code)
                product_urls_by_code_2[original_code] = url
                original_codes_by_std_2[original_code] = original_code
                print(f"Sản phẩm HaiphongTech: {original_code} - {url}")
        
        socketio.emit('progress_update', {
            'percent': 80, 
            'message': f'Đã tìm thấy {len(product_codes_1)} mã sản phẩm từ BAA.vn và {len(product_codes_2)} mã sản phẩm từ HaiphongTech. Đang so sánh...'
        })
        
        # So sánh các mã sản phẩm (đã được chuẩn hóa)
        socketio.emit('progress_update', {
            'percent': 80, 
            'message': f'Đã tìm thấy {len(product_codes_1)} mã sản phẩm từ BAA.vn và {len(product_codes_2)} mã sản phẩm từ HaiphongTech. Đang so sánh...'
        })
        
        # Tạo tập hợp (set) của tất cả mã sản phẩm
        all_product_codes = set(product_codes_1).union(set(product_codes_2))
        all_product_codes = sorted(list(all_product_codes))
        
        # Tạo các tập hợp để phân loại
        # 1. Các mã sản phẩm có trên cả hai website
        common_codes = list(set(product_codes_1).intersection(set(product_codes_2)))
        common_codes.sort()
        
        # 2. Các mã sản phẩm chỉ có trên BAA.vn
        unique_codes_baa = list(set(product_codes_1) - set(product_codes_2))
        unique_codes_baa.sort()
        
        # 3. Các mã sản phẩm chỉ có trên HaiphongTech
        unique_codes_hpt = list(set(product_codes_2) - set(product_codes_1))
        unique_codes_hpt.sort()
        
        # Tạo danh sách URLs tương ứng
        unique_urls_baa = [product_urls_by_code_1.get(code, '') for code in unique_codes_baa]
        unique_urls_hpt = [product_urls_by_code_2.get(code, '') for code in unique_codes_hpt]
        
        socketio.emit('progress_update', {
            'percent': 85, 
            'message': f'Phân tích hoàn tất. Tìm thấy {len(all_product_codes)} mã sản phẩm khác nhau. Đang tạo báo cáo...'
        })
        
        # Tạo báo cáo Excel
        # 1. Tạo một DataFrame chứa tất cả mã sản phẩm và URLs tương ứng từ hai website
        print(f"Tổng số mã sản phẩm: {len(all_product_codes)}")
        print(f"Số mã sản phẩm chung: {len(common_codes)}")
        print(f"Số mã sản phẩm chỉ có trên BAA.vn: {len(unique_codes_baa)}")
        print(f"Số mã sản phẩm chỉ có trên HaiphongTech: {len(unique_codes_hpt)}")
        
        comparison_data = []
        for code in all_product_codes:
            # URL từ BAA.vn
            url_baa = product_urls_by_code_1.get(code, '')
            # URL từ HaiphongTech
            url_hpt = product_urls_by_code_2.get(code, '')
            # Trạng thái: Chung, Chỉ BAA.vn, Chỉ HaiphongTech
            if code in common_codes:
                status = "Chung"
            elif code in unique_codes_baa:
                status = "Chỉ BAA.vn"
            else:
                status = "Chỉ HaiphongTech"
            
            comparison_data.append({
                'Mã sản phẩm': code,
                'URL BAA.vn': url_baa,
                'URL HaiphongTech': url_hpt,
                'Trạng thái': status
            })
        
        # Tạo output directory
        output_dir = os.path.join(current_app.static_folder, 'output_compare')
        os.makedirs(output_dir, exist_ok=True)
        
        # Lưu báo cáo Excel
        excel_path = os.path.join(output_dir, 'product_comparison.xlsx')
        df = pd.DataFrame(comparison_data)
        df.to_excel(excel_path, index=False)
        
        # Lưu danh sách URLs sản phẩm độc nhất vào file TXT
        # 1. URLs từ BAA.vn không có trên HaiphongTech
        unique_baa_txt_path = os.path.join(output_dir, 'unique_baa_urls.txt')
        with open(unique_baa_txt_path, 'w', encoding='utf-8') as f:
            for i, url in enumerate(unique_urls_baa):
                if url:
                    f.write(f"{unique_codes_baa[i]}\t{url}\n")
        
        # 2. URLs từ HaiphongTech không có trên BAA.vn
        unique_hpt_txt_path = os.path.join(output_dir, 'unique_hpt_urls.txt')
        with open(unique_hpt_txt_path, 'w', encoding='utf-8') as f:
            for i, url in enumerate(unique_urls_hpt):
                if url:
                    f.write(f"{unique_codes_hpt[i]}\t{url}\n")
        
        # Nén các file kết quả lại thành ZIP
        zip_path = os.path.join(current_app.static_folder, 'downloads', 'product_comparison.zip')
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            # Thêm file Excel
            zipf.write(excel_path, os.path.basename(excel_path))
            # Thêm các file TXT
            zipf.write(unique_baa_txt_path, os.path.basename(unique_baa_txt_path))
            zipf.write(unique_hpt_txt_path, os.path.basename(unique_hpt_txt_path))
            
            socketio.emit('progress_update', {
                'percent': 100, 
            'message': 'So sánh danh mục sản phẩm hoàn tất!'
        })
        
        # Trả về URL để tải xuống file ZIP
        download_url = url_for('main.download_file', filename='product_comparison.zip')
        return render_template('index.html', download_url=download_url, success_message="So sánh danh mục sản phẩm thành công!")
            
    except Exception as e:
        error_msg = f"Lỗi: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        socketio.emit('progress_update', {'percent': 0, 'message': f'Đã xảy ra lỗi: {str(e)}'})
        return render_template('index.html', error=error_msg)

@main_bp.route('/compare-product-codes', methods=['POST'])
def compare_product_codes():
    try:
        # Kiểm tra các file đã tải lên
        if 'excel_file_1' not in request.files or 'excel_file_2' not in request.files:
            return render_template('index.html', error="Vui lòng tải lên cả hai file Excel chứa danh sách mã sản phẩm")
        
        excel_file_1 = request.files['excel_file_1']
        excel_file_2 = request.files['excel_file_2']
        
        if excel_file_1.filename == '' or excel_file_2.filename == '':
            return render_template('index.html', error="Không có file nào được chọn")
        
        allowed_extensions = set(['xlsx', 'xls', 'csv'])
        if not (excel_file_1.filename.rsplit('.', 1)[1].lower() in allowed_extensions and 
                excel_file_2.filename.rsplit('.', 1)[1].lower() in allowed_extensions):
            return render_template('index.html', error="Chỉ chấp nhận file .xlsx, .xls hoặc .csv")
        
        # Thông báo tiến trình
        socketio.emit('progress_update', {
            'percent': 10, 
            'message': 'Đang đọc dữ liệu từ các file Excel...'
        })
        
        # Lưu các file tạm thời
        temp_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        file1_path = os.path.join(temp_dir, secure_filename(excel_file_1.filename))
        file2_path = os.path.join(temp_dir, secure_filename(excel_file_2.filename))
        
        excel_file_1.save(file1_path)
        excel_file_2.save(file2_path)
        
        socketio.emit('progress_update', {
            'percent': 30, 
            'message': 'Đang so sánh mã sản phẩm giữa hai file...'
        })
        
        # Gọi hàm so sánh từ module product_comparison
        from app.product_comparison import compare_product_codes
        output_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'reports')
        os.makedirs(output_dir, exist_ok=True)
        
        report_path = compare_product_codes(file1_path, file2_path, output_dir)
        
        if report_path and isinstance(report_path, str) and os.path.exists(report_path):
            socketio.emit('progress_update', {
                'percent': 100, 
                'message': 'Đã hoàn tất so sánh mã sản phẩm!'
            })
            
            # Tạo đường dẫn tải xuống
            report_filename = os.path.basename(report_path)
            download_url = url_for('main.download_file', filename=f'reports/{report_filename}')
            
            return render_template('index.html', 
                                 success_message=f"Đã so sánh xong mã sản phẩm từ hai file Excel!",
                                 download_url=download_url)
        else:
            error_msg = "Có lỗi xảy ra khi so sánh mã sản phẩm"
            if isinstance(report_path, dict) and 'error' in report_path:
                error_msg = f"Lỗi: {report_path.get('error', '')}"
            return render_template('index.html', error=error_msg)
            
    except Exception as e:
        print(f"Lỗi: {str(e)}")
        print(traceback.format_exc())
        socketio.emit('progress_update', {'percent': 0, 'message': f'Đã xảy ra lỗi: {str(e)}'})
        return render_template('index.html', error=f"Lỗi khi so sánh mã sản phẩm: {str(e)}")

# Hàm trích xuất mã sản phẩm từ URL hoặc nội dung trang
def extract_product_code_from_url(url):
    try:
        # Pattern để trích xuất mã sản phẩm từ URL
        # 1. Pattern cho URL BAA.vn: ... /bo-chuyen-doi-nhiet-do-autonics-kt-502h0_60329
        # 2. Pattern cho URL HaiphongTech: ... /bo-dieu-khien-nhiet-do-e5cc-rx2asm-800

        # Cố gắng trích xuất mã từ URL BAA.vn dạng URL sản phẩm
        baa_pattern = r'(?:san-pham\/.*?)(?:autonics|omron|ls|mitsubishi|fuji|idec|keyence|optex-fa|optex|panasonic|hanyoung|honeywell|koino|ckd|cikachi|chint|mean-well|weintek|eaton|lc|lsis|azbil|riko|coel|power|siemens|schneider|delta|hager|tele|taian|tend|socomec|leuze|sick|takex|heyi|trans|fotek|anly|winstar|nikkon|apator|bulgin|finder|benedict|protek|sanyu|nowox|anly|abb|ginice|wattstopper|bristoleye|hubbell|contrinex|elco|turck|banner|wenglor|rockwell|omron-movisens|st|oez|hfr|himel|yaskawa|futek|metz-connect|contactor|relay|circuit-protector|autonics-counter-timer|autonics-digital-panel-meter|panasonic-measurement).*?[_-]([a-zA-Z0-9\-]+?)(?:_|\-|$)'
        match = re.search(baa_pattern, url, re.IGNORECASE)
        if match:
            product_code = match.group(1)
            # Chuẩn hóa mã sản phẩm
            return standardize_product_code(product_code)
        
        # Cố gắng trích xuất mã từ URL HaiphongTech dạng URL sản phẩm
        hpt_pattern = r'(?:.*?\/)((?:[a-zA-Z0-9]+)(?:-[a-zA-Z0-9]+)+)(?:-(?:gia)|$)'
        match = re.search(hpt_pattern, url, re.IGNORECASE)
        if match:
            product_code = match.group(1)
            # Chuẩn hóa mã sản phẩm
            return standardize_product_code(product_code)
        
        # Nếu không thể trích xuất từ URL, thử tải nội dung trang và trích xuất từ HTML
        from app.crawler import get_html_content
        
        html_content = get_html_content(url)
        if not html_content:
            return None
        
        # Tạo đối tượng BeautifulSoup
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Tìm kiếm mã sản phẩm trong các thẻ meta, h1, div, span, etc.
        # 1. Thử tìm trong thẻ có class "product__symbol__value" (BAA.vn)
        product_symbol = soup.select_one('.product__symbol__value, .product-code, .sku, [itemprop="sku"], .product_code')
        if product_symbol and product_symbol.text.strip():
            product_code = product_symbol.text.strip()
            return standardize_product_code(product_code)
        
        # 2. Thử tìm trong tiêu đề sản phẩm
        product_title = soup.select_one('h1.product__name, h1.product-title, h1.product-name, h1')
        if product_title:
            title_text = product_title.text.strip()
            # Tìm mã sản phẩm trong tiêu đề - giả định mã có dạng XX-XXXX hoặc XXXXX
            code_pattern = r'([A-Z0-9\-]{3,}(?:-[A-Z0-9\-]+)+)'
            code_matches = re.findall(code_pattern, title_text)
            if code_matches:
                for code in code_matches:
                    # Kiểm tra xem code có hợp lệ không (ví dụ: không phải là các từ thông thường)
                    if not re.search(r'^(THE|AND|FOR|WITH|FROM)$', code, re.IGNORECASE):
                        return standardize_product_code(code)
        
        return None
        
    except Exception as e:
        print(f"Lỗi khi trích xuất mã sản phẩm từ URL {url}: {str(e)}")
        return None

# Hàm chuẩn hóa mã sản phẩm
def standardize_product_code(code):
    if not code:
        return None
    
    # Chuyển về chữ hoa
    std_code = code.upper()
    
    # Xóa các ký tự đặc biệt ở đầu và cuối
    std_code = std_code.strip('-_.,;:\'"\t ')
    
    # Xóa các text không cần thiết
    unwanted_texts = ['AUTONICS', 'OMRON', 'MITSUBISHI', 'PANASONIC', 'LS', 'GIÁ', 'PRICE', 'BÁO GIÁ', 'MUA', 'MUA HÀNG']
    for text in unwanted_texts:
        std_code = std_code.replace(text, '')
    
    # Xóa khoảng trắng
    std_code = std_code.strip()
    
    # Nếu code quá ngắn, trả về None
    if len(std_code) < 3:
        return None
    
    return std_code

@main_bp.route('/download-baa-images', methods=['POST'])
def download_baa_images():
    """
    Tải ảnh sản phẩm từ BAA.vn và chuyển sang WebP kích thước gốc
    """
    try:
        if 'product_links_file' not in request.files:
            flash('Không tìm thấy file!', 'error')
            return redirect(url_for('main.index'))
        
        file = request.files['product_links_file']
        
        if file.filename == '':
            flash('Không có file nào được chọn!', 'error')
            return redirect(url_for('main.index'))
            
        if not allowed_file(file.filename, ALLOWED_EXTENSIONS_TXT):
            flash('Chỉ cho phép file .txt!', 'error')
            return redirect(url_for('main.index'))
        
        # Đọc nội dung file
        content = file.read().decode('utf-8')
        
        # Lấy danh sách URL sản phẩm
        product_urls = [url.strip() for url in content.strip().split('\n') if url.strip()]
        
        if not product_urls:
            flash('File không chứa URL sản phẩm nào!', 'error')
            return redirect(url_for('main.index'))
            
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 0, 
            'message': f'Bắt đầu xử lý {len(product_urls)} URL sản phẩm...'
        })
        
        # Tạo thư mục đầu ra
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], f'baa_images_{timestamp}')
        
        # Tải ảnh sản phẩm - Sử dụng phiên bản đã sửa lỗi để xử lý đúng đường dẫn ảnh
        results = download_baa_product_images_fixed(product_urls, output_folder)
        
        # Nén thư mục ảnh thành file ZIP
        zip_filename = f'baa_images_{timestamp}.zip'
        zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
        
        if utils.create_zip_from_folder(output_folder, zip_path):
            # Tạo URL tải xuống
            download_url = url_for('main.download_file', filename=zip_filename)
            
            # Tạo URL xem ảnh
            view_images_url = url_for('main.view_baa_images', folder=f'baa_images_{timestamp}')
            
            # Thông báo thành công
            success_message = f"Đã tải {results['success']}/{results['total']} ảnh sản phẩm"
            if results['failed'] > 0:
                success_message += f", {results['failed']} thất bại"
            success_message += f" (Tổng số ảnh: {len(results['image_paths'])})"
            
            flash(success_message, 'success')
            
            # Gửi thông báo hoàn thành
            socketio.emit('progress_update', {
                'percent': 100,
                'message': 'Hoàn thành!'
            })
            
            return render_template('index.html', download_url=download_url, view_images_url=view_images_url)
        else:
            flash('Lỗi khi tạo file ZIP!', 'error')
            return redirect(url_for('main.index'))
        
    except Exception as e:
        error_message = str(e)
        print(f"Lỗi khi tải ảnh BAA: {error_message}")
        traceback.print_exc()
        flash(f'Lỗi: {error_message}', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/view-baa-images/<folder>')
def view_baa_images(folder):
    """Hiển thị danh sách các ảnh BAA đã tải xuống"""
    try:
        # Đường dẫn đến thư mục chứa ảnh
        images_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], folder)
        
        if not os.path.exists(images_folder) or not os.path.isdir(images_folder):
            flash('Thư mục ảnh không tồn tại!', 'error')
            return redirect(url_for('main.index'))
        
        # Tìm tất cả các file ảnh trong thư mục
        all_images = []
        for file in os.listdir(images_folder):
            if file.endswith('.webp'):
                # Lấy mã sản phẩm từ tên file
                product_code = os.path.splitext(file)[0]
                
                # Đường dẫn tương đối đến file ảnh
                rel_path = os.path.join(folder, file)
                
                all_images.append({
                    'path': rel_path,
                    'code': product_code,
                    'url': url_for('main.view_image', image_path=rel_path)
                })
        
        # Sắp xếp ảnh theo mã sản phẩm
        all_images.sort(key=lambda x: x['code'])
        
        return render_template('view_baa_images.html', images=all_images, folder=folder)
        
    except Exception as e:
        error_message = str(e)
        flash(f'Lỗi khi xem ảnh: {error_message}', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/filter-products', methods=['POST'])
def filter_products():
    """
    Lọc danh sách mã sản phẩm từ file Excel dựa trên danh sách mã cần xóa
    """
    try:
        # Kiểm tra nếu có cả danh sách mã và file Excel
        if 'product_codes' not in request.form or 'excel_file' not in request.files:
            flash('Vui lòng nhập danh sách mã sản phẩm và tải lên file Excel!', 'error')
            return redirect(url_for('main.index'))
        
        # Lấy danh sách mã sản phẩm từ form
        product_codes_text = request.form['product_codes']
        if not product_codes_text.strip():
            flash('Danh sách mã sản phẩm không được để trống!', 'error')
            return redirect(url_for('main.index'))
        
        # Xử lý danh sách mã sản phẩm (mỗi mã trên một dòng)
        product_codes = [code.strip() for code in product_codes_text.strip().split('\n') if code.strip()]
        
        # Kiểm tra file Excel
        excel_file = request.files['excel_file']
        if excel_file.filename == '':
            flash('Không có file Excel nào được chọn!', 'error')
            return redirect(url_for('main.index'))
        
        if not allowed_file(excel_file.filename, ALLOWED_EXTENSIONS_EXCEL):
            flash('Chỉ chấp nhận file .xlsx hoặc .xls!', 'error')
            return redirect(url_for('main.index'))
        
        # Thông báo tiến trình
        socketio.emit('progress_update', {
            'percent': 10, 
            'message': f'Đang xử lý {len(product_codes)} mã sản phẩm cần lọc...'
        })
        
        # Lưu file tạm thời
        temp_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        input_excel_path = os.path.join(temp_dir, secure_filename(excel_file.filename))
        excel_file.save(input_excel_path)
        
        # Đọc file Excel với pandas
        df = None
        try:
            df = pd.read_excel(input_excel_path)
            socketio.emit('progress_update', {
                'percent': 30, 
                'message': f'Đã đọc file Excel với {len(df)} dòng. Đang lọc dữ liệu...'
            })
        except Exception as e:
            flash(f'Lỗi khi đọc file Excel: {str(e)}', 'error')
            return redirect(url_for('main.index'))
        
        # Kiểm tra nếu DataFrame trống hoặc không có dữ liệu
        if df is None or df.empty:
            flash('File Excel không chứa dữ liệu!', 'error')
            return redirect(url_for('main.index'))
        
        # Kiểm tra nếu DataFrame không có ít nhất 2 cột (cần cột B)
        if df.shape[1] < 2:
            flash('File Excel phải có ít nhất 2 cột (cột B chứa mã sản phẩm)!', 'error')
            return redirect(url_for('main.index'))
        
        # Lấy cột B (chứa mã sản phẩm)
        df_product_codes = df.iloc[:, 1].astype(str).str.strip()
        
        # Tạo mask để lọc các hàng có mã sản phẩm nằm trong danh sách cần xóa
        rows_to_remove = df_product_codes.isin([str(code).strip() for code in product_codes])
        
        # Tạo DataFrame chứa các dòng sẽ bị xóa
        removed_df = df[rows_to_remove].copy()
        
        # Đếm số dòng sẽ bị xóa
        removed_count = rows_to_remove.sum()
        
        socketio.emit('progress_update', {
            'percent': 60, 
            'message': f'Đã tìm thấy {removed_count} mã sản phẩm cần xóa. Đang tạo báo cáo...'
        })
        
        # Tạo DataFrame mới không chứa các hàng đã lọc
        filtered_df = df[~rows_to_remove]
        
        # Tạo tên file output
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_filename = f"filtered_products_{timestamp}.xlsx"
        output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], output_filename)
        
        # Lưu kết quả vào file Excel mới với các sheet riêng
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            # Sheet chính chứa dữ liệu đã lọc
            filtered_df.to_excel(writer, sheet_name='Dữ liệu đã lọc', index=False)
            
            # Sheet thứ hai chứa dữ liệu đã xóa
            if not removed_df.empty:
                removed_df.to_excel(writer, sheet_name='Mã đã xóa', index=False)
            
            # Tạo sheet tổng hợp
            summary_data = {
                'Thông tin': [
                    'Tổng số dòng ban đầu', 
                    'Số dòng đã xóa', 
                    'Số dòng còn lại',
                ],
                'Số lượng': [
                    len(df),
                    removed_count,
                    len(filtered_df)
                ]
            }
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name='Tổng hợp', index=False)
            
            # Format các sheet
            workbook = writer.book
            for sheet in writer.sheets.values():
                sheet.set_column('A:Z', 18)
        
        socketio.emit('progress_update', {
            'percent': 100, 
            'message': 'Hoàn thành lọc dữ liệu!'
        })
        
        # Tạo URL để tải xuống file kết quả
        download_url = url_for('main.download_file', filename=output_filename)
        
        # Thông báo kết quả
        success_message = f"Đã lọc thành công! Đã xóa {removed_count}/{len(df)} dòng dữ liệu."
        return render_template('index.html', download_url=download_url, success_message=success_message)
        
    except Exception as e:
        error_message = str(e)
        print(f"Lỗi khi lọc sản phẩm: {error_message}")
        traceback.print_exc()
        flash(f'Lỗi: {error_message}', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/extract-prices', methods=['POST'])
def extract_prices():
    """
    Trích xuất giá sản phẩm từ danh sách các URL sản phẩm
    """
    try:
        if 'product_links_file' not in request.files:
            flash('Không tìm thấy file!', 'error')
            return redirect(url_for('main.index'))
        
        file = request.files['product_links_file']
        
        if file.filename == '':
            flash('Không có file nào được chọn!', 'error')
            return redirect(url_for('main.index'))
            
        if not allowed_file(file.filename, ALLOWED_EXTENSIONS_TXT):
            flash('Chỉ cho phép file .txt!', 'error')
            return redirect(url_for('main.index'))
        
        # Đọc nội dung file
        content = file.read().decode('utf-8')
        
        # Lấy danh sách URL sản phẩm
        product_urls = [url.strip() for url in content.strip().split('\n') if url.strip()]
        
        if not product_urls:
            flash('File không chứa URL sản phẩm nào!', 'error')
            return redirect(url_for('main.index'))
            
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 0, 
            'message': f'Bắt đầu trích xuất giá cho {len(product_urls)} URL sản phẩm...'
        })
        
        # Trích xuất thông tin sản phẩm, bao gồm giá
        product_data = []
        required_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'URL']
        
        for index, url in enumerate(product_urls, 1):
            socketio.emit('progress_update', {
                'percent': int((index / len(product_urls)) * 70), 
                'message': f'Đang trích xuất giá từ URL {index}/{len(product_urls)}...'
            })
            
            try:
                product_info = extract_product_info(url, required_fields=required_fields, index=index)
                product_data.append(product_info)
            except Exception as e:
                print(f"Lỗi khi trích xuất URL {url}: {str(e)}")
                # Thêm dữ liệu tối thiểu nếu có lỗi
                product_data.append({
                    'STT': index,
                    'Mã sản phẩm': '',
                    'Tên sản phẩm': '',
                    'Giá': 'Lỗi: ' + str(e),
                    'URL': url
                })
        
        # Tạo DataFrame từ dữ liệu đã thu thập
        df = pd.DataFrame(product_data)
        
        # Tạo tên file output
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_filename = f"product_prices_{timestamp}.xlsx"
        output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], output_filename)
        
        # Lưu kết quả vào file Excel
        socketio.emit('progress_update', {
            'percent': 80, 
            'message': 'Đang tạo file Excel...'
        })
        
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Giá sản phẩm', index=False)
            
            # Format sheet
            workbook = writer.book
            worksheet = writer.sheets['Giá sản phẩm']
            
            # Định dạng cột
            worksheet.set_column('A:A', 5)   # STT
            worksheet.set_column('B:B', 20)  # Mã sản phẩm
            worksheet.set_column('C:C', 40)  # Tên sản phẩm
            worksheet.set_column('D:D', 20)  # Giá
            worksheet.set_column('E:E', 50)  # URL
            
            # Tạo định dạng
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'align': 'center',
                'border': 1,
                'bg_color': '#D7E4BC'
            })
            
            # Áp dụng định dạng cho tiêu đề
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
        
        socketio.emit('progress_update', {
            'percent': 100, 
            'message': 'Hoàn thành trích xuất giá sản phẩm!'
        })
        
        # Tạo URL để tải xuống file kết quả
        download_url = url_for('main.download_file', filename=output_filename)
        
        # Thông báo kết quả
        success_message = f"Đã trích xuất giá cho {len(product_data)} sản phẩm thành công!"
        return render_template('index.html', download_url=download_url, success_message=success_message)
        
    except Exception as e:
        error_message = str(e)
        print(f"Lỗi khi trích xuất giá sản phẩm: {error_message}")
        traceback.print_exc()
        flash(f'Lỗi: {error_message}', 'error')
        return redirect(url_for('main.index')) 

@main_bp.route('/extract-only-prices', methods=['POST'])
def extract_only_prices():
    """
    Chỉ trích xuất giá sản phẩm từ danh sách các URL sản phẩm
    """
    try:
        if 'product_links_file' not in request.files:
            flash('Không tìm thấy file!', 'error')
            return redirect(url_for('main.index'))
        
        file = request.files['product_links_file']
        
        if file.filename == '':
            flash('Không có file nào được chọn!', 'error')
            return redirect(url_for('main.index'))
            
        if not allowed_file(file.filename, ALLOWED_EXTENSIONS_TXT):
            flash('Chỉ cho phép file .txt!', 'error')
            return redirect(url_for('main.index'))
        
        # Đọc nội dung file
        content = file.read().decode('utf-8')
        
        # Lấy danh sách URL sản phẩm
        product_urls = [url.strip() for url in content.strip().split('\n') if url.strip()]
        
        if not product_urls:
            flash('File không chứa URL sản phẩm nào!', 'error')
            return redirect(url_for('main.index'))
            
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 0, 
            'message': f'Bắt đầu trích xuất giá cho {len(product_urls)} URL sản phẩm...'
        })
        
        # Chỉ trích xuất giá và mã sản phẩm
        product_data = []
        
        for index, url in enumerate(product_urls, 1):
            socketio.emit('progress_update', {
                'percent': int((index / len(product_urls)) * 70), 
                'message': f'Đang trích xuất giá từ URL {index}/{len(product_urls)}...'
            })
            
            try:
                # Sử dụng hàm chuyên biệt để chỉ lấy mã và giá
                product_info = extract_product_price(url, index=index)
                product_data.append(product_info)
            except Exception as e:
                print(f"Lỗi khi trích xuất URL {url}: {str(e)}")
                # Thêm dữ liệu tối thiểu nếu có lỗi
                product_data.append({
                    'STT': index,
                    'URL': url,
                    'Mã sản phẩm': '',
                    'Giá': 'Lỗi: ' + str(e)
                })
        
        # Tạo DataFrame từ dữ liệu đã thu thập
        df = pd.DataFrame(product_data)
        
        # Tạo tên file output
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_filename = f"product_only_prices_{timestamp}.xlsx"
        output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], output_filename)
        
        # Lưu kết quả vào file Excel
        socketio.emit('progress_update', {
            'percent': 80, 
            'message': 'Đang tạo file Excel...'
        })
        
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Giá sản phẩm', index=False)
            
            # Format sheet
            workbook = writer.book
            worksheet = writer.sheets['Giá sản phẩm']
            
            # Định dạng cột
            worksheet.set_column('A:A', 5)   # STT
            worksheet.set_column('B:B', 50)  # URL
            worksheet.set_column('C:C', 20)  # Mã sản phẩm
            worksheet.set_column('D:D', 20)  # Giá
            
            # Tạo định dạng
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'align': 'center',
                'border': 1,
                'bg_color': '#D7E4BC'
            })
            
            # Áp dụng định dạng cho tiêu đề
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
        
        socketio.emit('progress_update', {
            'percent': 100, 
            'message': 'Hoàn thành trích xuất giá sản phẩm!'
        })
        
        # Tạo URL để tải xuống file kết quả
        download_url = url_for('main.download_file', filename=output_filename)
        
        # Thông báo kết quả
        success_message = f"Đã trích xuất giá cho {len(product_data)} sản phẩm thành công!"
        return render_template('index.html', download_url=download_url, success_message=success_message)
        
    except Exception as e:
        error_message = str(e)
        print(f"Lỗi khi trích xuất giá sản phẩm: {error_message}")
        traceback.print_exc()
        flash(f'Lỗi: {error_message}', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/extract-category-links', methods=['POST'])
def extract_category_links_separate():
    """
    Trích xuất liên kết sản phẩm từ nhiều danh mục và sắp xếp thành thư mục riêng
    """
    try:
        # Kiểm tra xem có dữ liệu danh mục không
        if 'category_urls' not in request.form:
            flash('Vui lòng nhập danh sách URL danh mục!', 'error')
            return redirect(url_for('main.index'))
            
        # Lấy danh sách URL danh mục từ form
        category_urls_text = request.form['category_urls']
        if not category_urls_text.strip():
            flash('Danh sách URL danh mục không được để trống!', 'error')
            return redirect(url_for('main.index'))
        
        # Tách thành danh sách URL, bỏ qua dòng trống
        raw_urls = category_urls_text.strip().split('\n')
        urls = [url.strip() for url in raw_urls if url.strip()]
        
        # Lọc các URL hợp lệ
        valid_urls = []
        invalid_urls = []
        
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {'percent': 0, 'message': 'Đang kiểm tra URL danh mục...'})
        
        # Kiểm tra các URL
        for url in urls:
            if utils.is_valid_url(url) and is_category_url(url):
                valid_urls.append(url)
            else:
                invalid_urls.append(url)
        
        if not valid_urls:
            flash('Không có URL danh mục hợp lệ!', 'error')
            return redirect(url_for('main.index'))
            
        # Gửi thông báo cập nhật
        socketio.emit('progress_update', {'percent': 5, 'message': f'Đã tìm thấy {len(valid_urls)} URL danh mục hợp lệ'})
        
        # Tạo thư mục chính để lưu kết quả
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        result_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], f'category_products_{timestamp}')
        os.makedirs(result_dir, exist_ok=True)
        
        # Tạo tệp txt để lưu tất cả các liên kết
        all_products_file = os.path.join(result_dir, 'all_product_links.txt')
        all_product_links = []
        
        # Xử lý từng URL danh mục riêng biệt
        category_info = []  # Lưu thông tin về mỗi danh mục
        
        for i, category_url in enumerate(valid_urls):
            try:
                progress = 5 + int((i / len(valid_urls)) * 85)
                socketio.emit('progress_update', {
                    'percent': progress, 
                    'message': f'Đang xử lý danh mục {i+1}/{len(valid_urls)}: {category_url}'
                })
                
                # Trích xuất tên danh mục từ URL
                parsed_url = urlparse(category_url)
                url_path = parsed_url.path
                
                # Lấy phần cuối của URL làm tên danh mục
                category_name = url_path.strip('/').split('/')[-1]
                # Loại bỏ phần ID số từ tên danh mục nếu có
                category_name = re.sub(r'_\d+$', '', category_name)
                
                # Tạo thư mục cho danh mục này
                category_dir = os.path.join(result_dir, category_name)
                os.makedirs(category_dir, exist_ok=True)
                
                # Thu thập liên kết sản phẩm từ danh mục này
                category_products = extract_category_links([category_url])
                
                if category_products:
                    # Lưu các liên kết sản phẩm vào file txt riêng của danh mục
                    category_file = os.path.join(category_dir, f'{category_name}_links.txt')
                    with open(category_file, 'w', encoding='utf-8') as f:
                        for link in category_products:
                            f.write(link + '\n')
                            
                    # Thêm vào danh sách tất cả sản phẩm
                    all_product_links.extend(category_products)
                    
                    # Thêm thông tin danh mục vào danh sách
                    category_info.append({
                        'Tên danh mục': category_name,
                        'URL danh mục': category_url,
                        'Số sản phẩm': len(category_products)
                    })
                else:
                    print(f"Không tìm thấy sản phẩm nào trong danh mục: {category_url}")
                    
            except Exception as e:
                error_message = str(e)
                print(f"Lỗi khi xử lý danh mục {category_url}: {error_message}")
                traceback.print_exc()
        
        # Lưu tất cả liên kết vào file chung
        with open(all_products_file, 'w', encoding='utf-8') as f:
            for link in all_product_links:
                f.write(link + '\n')
        
        # Tạo file Excel báo cáo về các danh mục
        report_file = os.path.join(result_dir, 'category_report.xlsx')
        df = pd.DataFrame(category_info)
        
        if not df.empty:
            df.to_excel(report_file, index=False)
        
        # Nén thư mục kết quả thành file ZIP
        zip_filename = f'category_products_{timestamp}.zip'
        zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
        
        # Tạo file ZIP từ thư mục
        if utils.create_zip_from_folder(result_dir, zip_path):
            # Gửi thông báo hoàn thành
            socketio.emit('progress_update', {
                'percent': 100, 
                'message': f'Đã hoàn thành thu thập {len(all_product_links)} liên kết sản phẩm từ {len(valid_urls)} danh mục!'
            })
            
            # Tạo URL để tải xuống file zip
            download_url = url_for('main.download_file', filename=zip_filename)
            
            # Thông báo thành công
            success_message = f"Đã thu thập thành công {len(all_product_links)} liên kết sản phẩm từ {len(valid_urls)} danh mục."
            
            return render_template('index.html', download_url=download_url, success_message=success_message)
        else:
            flash('Lỗi khi tạo file ZIP!', 'error')
            return redirect(url_for('main.index'))
            
    except Exception as e:
        error_message = str(e)
        # In chi tiết lỗi
        traceback.print_exc()
        flash(f'Lỗi: {error_message}', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/scrape-category-products', methods=['POST'])
def scrape_category_products():
    """
    Thu thập thông tin sản phẩm từ nhiều danh mục và sắp xếp thành thư mục riêng
    """
    try:
        # Kiểm tra xem có dữ liệu danh mục không
        if 'category_urls' not in request.form:
            flash('Vui lòng nhập danh sách URL danh mục!', 'error')
            return redirect(url_for('main.index'))
            
        # Lấy danh sách URL danh mục từ form
        category_urls_text = request.form['category_urls']
        if not category_urls_text.strip():
            flash('Danh sách URL danh mục không được để trống!', 'error')
            return redirect(url_for('main.index'))
        
        # Tách thành danh sách URL, bỏ qua dòng trống
        raw_urls = category_urls_text.strip().split('\n')
        urls = [url.strip() for url in raw_urls if url.strip()]
        
        # Lọc các URL hợp lệ
        valid_urls = []
        invalid_urls = []
        
        # Gửi thông báo bắt đầu
        socketio.emit('progress_update', {'percent': 0, 'message': 'Đang kiểm tra URL danh mục...'})
        
        # Kiểm tra các URL
        for url in urls:
            if utils.is_valid_url(url) and is_category_url(url):
                valid_urls.append(url)
            else:
                invalid_urls.append(url)
        
        if not valid_urls:
            flash('Không có URL danh mục hợp lệ!', 'error')
            return redirect(url_for('main.index'))
            
        # Gửi thông báo cập nhật
        socketio.emit('progress_update', {'percent': 5, 'message': f'Đã tìm thấy {len(valid_urls)} URL danh mục hợp lệ'})
        
        # Tạo thư mục chính để lưu kết quả
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        result_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], f'category_info_{timestamp}')
        os.makedirs(result_dir, exist_ok=True)
        
        # Xử lý từng URL danh mục riêng biệt
        category_info = []  # Lưu thông tin về mỗi danh mục
        
        for i, category_url in enumerate(valid_urls):
            try:
                category_progress_base = 5 + int((i / len(valid_urls)) * 85)
                socketio.emit('progress_update', {
                    'percent': category_progress_base, 
                    'message': f'Đang xử lý danh mục {i+1}/{len(valid_urls)}: {category_url}'
                })
                
                # Trích xuất tên danh mục từ URL
                parsed_url = urlparse(category_url)
                url_path = parsed_url.path
                
                # Lấy phần cuối của URL làm tên danh mục
                category_name = url_path.strip('/').split('/')[-1]
                # Loại bỏ phần ID số từ tên danh mục nếu có
                category_name = re.sub(r'_\d+$', '', category_name)
                
                # Tạo thư mục cho danh mục này
                category_dir = os.path.join(result_dir, category_name)
                os.makedirs(category_dir, exist_ok=True)
                
                # Thu thập liên kết sản phẩm từ danh mục này
                socketio.emit('progress_update', {
                    'percent': category_progress_base + 5, 
                    'message': f'Đang thu thập liên kết sản phẩm từ danh mục: {category_name}'
                })
                
                category_products = extract_category_links([category_url])
                
                if category_products:
                    # Lưu các liên kết sản phẩm vào file txt riêng của danh mục
                    category_file = os.path.join(category_dir, f'{category_name}_links.txt')
                    with open(category_file, 'w', encoding='utf-8') as f:
                        for link in category_products:
                            f.write(link + '\n')
                    
                    # Thu thập thông tin từ các sản phẩm trong danh mục
                    socketio.emit('progress_update', {
                        'percent': category_progress_base + 10, 
                        'message': f'Đang thu thập thông tin {len(category_products)} sản phẩm từ danh mục: {category_name}'
                    })
                    
                    # Các trường cần thu thập
                    required_fields = ['STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'Tổng quan', 'URL']
                    
                    # Thu thập thông tin sản phẩm sử dụng scrape_product_info thay vì extract_product_info
                    try:
                        # Tạo file Excel template tạm thời
                        excel_temp_path = os.path.join(category_dir, f'{category_name}_template.xlsx')
                        
                        # Tạo template Excel đơn giản
                        wb = openpyxl.Workbook()
                        ws = wb.active
                        for col_idx, field in enumerate(required_fields, 1):
                            ws.cell(row=1, column=col_idx).value = field
                        wb.save(excel_temp_path)
                        
                        # Sử dụng scrape_product_info để thu thập thông tin sản phẩm
                        socketio.emit('progress_update', {
                            'percent': category_progress_base + 20, 
                            'message': f'Đang cào dữ liệu {len(category_products)} sản phẩm từ danh mục: {category_name}'
                        })
                        
                        excel_result = scrape_product_info(category_products, excel_temp_path)
                        
                        # Copy file kết quả vào thư mục danh mục
                        if excel_result and os.path.exists(excel_result):
                            shutil.copy(excel_result, os.path.join(category_dir, f'{category_name}_products.xlsx'))
                            product_info_list = pd.read_excel(excel_result).to_dict('records')
                        else:
                            product_info_list = []
                            
                        # Xóa file template tạm thời
                        if os.path.exists(excel_temp_path):
                            os.remove(excel_temp_path)
                            
                    except Exception as e:
                        product_info_list = []
                        print(f"Lỗi khi thu thập thông tin sản phẩm từ danh mục {category_name}: {str(e)}")
                        traceback.print_exc()
                    
                    # Tạo file Excel chứa thông tin sản phẩm (nếu chưa tạo)
                    if product_info_list and not os.path.exists(os.path.join(category_dir, f'{category_name}_products.xlsx')):
                        excel_file = os.path.join(category_dir, f'{category_name}_products.xlsx')
                        
                        # Tạo DataFrame từ danh sách thông tin sản phẩm
                        df_products = pd.DataFrame(product_info_list)
                        
                        # Đảm bảo có đủ các cột cần thiết
                        for field in required_fields:
                            if field not in df_products.columns:
                                df_products[field] = ""
                        
                        # Chỉ giữ lại các cột theo thứ tự
                        available_fields = [field for field in required_fields if field in df_products.columns]
                        df_products = df_products[available_fields]
                        
                        # Lưu vào file Excel
                        with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                            df_products.to_excel(writer, index=False, sheet_name='Sản phẩm')
                            
                            # Định dạng các cột
                            worksheet = writer.sheets['Sản phẩm']
                            column_widths = {'STT': 5, 'Mã sản phẩm': 20, 'Tên sản phẩm': 40, 'Giá': 15, 'Tổng quan': 80, 'URL': 50}
                            
                            for field, width in column_widths.items():
                                if field in available_fields:
                                    col_idx = available_fields.index(field) + 1  # +1 vì Excel đánh số cột từ 1
                                    column_letter = get_column_letter(col_idx)
                                    worksheet.column_dimensions[column_letter].width = width
                    
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
                error_message = str(e)
                print(f"Lỗi khi xử lý danh mục {category_url}: {error_message}")
                traceback.print_exc()
        
        # Tạo file Excel báo cáo về các danh mục
        report_file = os.path.join(result_dir, 'category_report.xlsx')
        df = pd.DataFrame(category_info)
        
        if not df.empty:
            # Chuẩn bị dữ liệu cho các báo cáo chi tiết
            all_products_data = []
            failed_products = []
            products_without_price = []
            
            # Thu thập tất cả thông tin sản phẩm từ các danh mục để phân tích
            for i, category_url in enumerate(valid_urls):
                try:
                    # Trích xuất tên danh mục từ URL
                    parsed_url = urlparse(category_url)
                    url_path = parsed_url.path
                    category_name = url_path.strip('/').split('/')[-1]
                    category_name = re.sub(r'_\d+$', '', category_name)
                    
                    # Đường dẫn đến file Excel của danh mục
                    category_excel = os.path.join(result_dir, category_name, f'{category_name}_products.xlsx')
                    
                    # Đường dẫn đến file txt chứa liên kết sản phẩm
                    category_links_file = os.path.join(result_dir, category_name, f'{category_name}_links.txt')
                    
                    # Lấy danh sách các URL sản phẩm từ file txt
                    all_product_urls = []
                    if os.path.exists(category_links_file):
                        with open(category_links_file, 'r', encoding='utf-8') as f:
                            all_product_urls = [line.strip() for line in f.readlines() if line.strip()]
                    
                    # Đọc dữ liệu sản phẩm đã thu thập được
                    if os.path.exists(category_excel):
                        # Đọc dữ liệu từ file Excel
                        df_category = pd.read_excel(category_excel)
                        
                        # Thêm cột Danh mục và Trạng thái
                        df_category['Danh mục'] = category_name
                        df_category['Trạng thái'] = 'Thành công'
                        
                        # Thêm vào danh sách tổng hợp
                        all_products_data.append(df_category)
                        
                        # URL sản phẩm đã thu thập được
                        collected_urls = set()
                        if 'URL' in df_category.columns:
                            collected_urls = set(df_category['URL'].tolist())
                        
                        # Danh sách URL sản phẩm không thu thập được
                        failed_urls = set(all_product_urls) - collected_urls
                        
                        # Thêm vào danh sách thất bại
                        for url in failed_urls:
                            failed_products.append({
                                'URL': url,
                                'Danh mục': category_name,
                                'Nguyên nhân': 'Không thu thập được dữ liệu'
                            })
                        
                        # Phân loại sản phẩm không có giá
                        for _, row in df_category.iterrows():
                            product_data = row.to_dict()
                            
                            # Kiểm tra nếu không có giá
                            if 'Giá' not in product_data or not product_data['Giá'] or str(product_data['Giá']).strip() == '':
                                products_without_price.append({
                                    'STT': product_data.get('STT', ''),
                                    'Mã sản phẩm': product_data.get('Mã sản phẩm', ''),
                                    'Tên sản phẩm': product_data.get('Tên sản phẩm', ''),
                                    'URL': product_data.get('URL', ''),
                                    'Danh mục': category_name,
                                    'Ghi chú': 'Không có thông tin giá'
                                })
                    else:
                        # Nếu không có file Excel, tất cả các URL sản phẩm đều thất bại
                        for url in all_product_urls:
                            failed_products.append({
                                'URL': url,
                                'Danh mục': category_name,
                                'Nguyên nhân': 'Không tạo được file Excel'
                            })
                except Exception as e:
                    print(f"Lỗi khi đọc dữ liệu từ danh mục {category_name}: {str(e)}")
                    # Thêm thông tin lỗi
                    if 'category_name' in locals():
                        failed_products.append({
                            'URL': category_url,
                            'Danh mục': category_name,
                            'Nguyên nhân': f'Lỗi: {str(e)}'
                        })
            
            # Tạo DataFrame cho tất cả sản phẩm
            if all_products_data:
                df_all_products = pd.concat(all_products_data, ignore_index=True)
            else:
                df_all_products = pd.DataFrame()
            
            # Tạo DataFrame cho sản phẩm thất bại
            df_failed = pd.DataFrame(failed_products)
            
            # Tạo DataFrame cho sản phẩm không có giá
            df_without_price = pd.DataFrame(products_without_price)
            
            # Tạo thống kê tổng hợp về trạng thái thu thập
            collection_stats = []
            for cat in df['Tên danh mục'].unique():
                # Tổng số URL sản phẩm từ danh mục
                category_links_file = os.path.join(result_dir, cat, f'{cat}_links.txt')
                total_urls = 0
                if os.path.exists(category_links_file):
                    with open(category_links_file, 'r', encoding='utf-8') as f:
                        total_urls = sum(1 for line in f if line.strip())
                
                # Số sản phẩm thu thập thành công
                successful_products = len(df_all_products[df_all_products['Danh mục'] == cat]) if not df_all_products.empty else 0
                
                # Số sản phẩm thu thập thất bại
                failed_count = len(df_failed[df_failed['Danh mục'] == cat]) if not df_failed.empty else 0
                
                # Số sản phẩm không có giá
                no_price_count = len(df_without_price[df_without_price['Danh mục'] == cat]) if not df_without_price.empty else 0
                
                # Tính tỷ lệ thành công
                success_rate = (successful_products / total_urls * 100) if total_urls > 0 else 0
                
                collection_stats.append({
                    'Danh mục': cat,
                    'Tổng số URL': total_urls,
                    'Thành công': successful_products,
                    'Thất bại': failed_count,
                    'Không có giá': no_price_count,
                    'Tỷ lệ thành công (%)': round(success_rate, 2)
                })
            
            df_collection_stats = pd.DataFrame(collection_stats)
            
            # Lưu vào file Excel với các sheet
            with pd.ExcelWriter(report_file, engine='openpyxl') as writer:
                # Sheet tổng quan
                df.to_excel(writer, sheet_name='Tổng quan', index=False)
                
                # Sheet thống kê trạng thái thu thập
                if not df_collection_stats.empty:
                    df_collection_stats.to_excel(writer, sheet_name='Thống kê thu thập', index=False)
                
                # Sheet sản phẩm thất bại
                if not df_failed.empty:
                    df_failed.to_excel(writer, sheet_name='Sản phẩm thất bại', index=False)
                
                # Sheet sản phẩm không có giá
                if not df_without_price.empty:
                    df_without_price.to_excel(writer, sheet_name='Sản phẩm không giá', index=False)
                
                # Sheet tất cả sản phẩm
                if not df_all_products.empty:
                    df_all_products.to_excel(writer, sheet_name='Tất cả sản phẩm', index=False)
                
                # Định dạng các sheet
                for sheet_name in writer.sheets:
                    worksheet = writer.sheets[sheet_name]
                    for column in worksheet.columns:
                        max_length = 0
                        column_letter = get_column_letter(column[0].column)
                        for cell in column:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(str(cell.value))
                            except:
                                pass
                        adjusted_width = (max_length + 2)
                        if adjusted_width > 100:  # Giới hạn độ rộng cột
                            adjusted_width = 100
                        worksheet.column_dimensions[column_letter].width = adjusted_width
                
                # Thêm định dạng màu cho sheet thống kê thu thập
                if not df_collection_stats.empty and 'Thống kê thu thập' in writer.sheets:
                    worksheet = writer.sheets['Thống kê thu thập']
                    
                    # Thêm định dạng có điều kiện cho cột Tỷ lệ thành công
                    from openpyxl.styles import PatternFill
                    
                    # Màu sắc
                    red_fill = PatternFill(start_color='FFFF0000', end_color='FFFF0000', fill_type='solid')  # Đỏ
                    yellow_fill = PatternFill(start_color='FFFFFF00', end_color='FFFFFF00', fill_type='solid')  # Vàng
                    green_fill = PatternFill(start_color='FF00FF00', end_color='FF00FF00', fill_type='solid')  # Xanh lá
                    
                    # Tìm cột Tỷ lệ thành công
                    success_rate_col = None
                    for col_idx, col in enumerate(df_collection_stats.columns):
                        if 'Tỷ lệ thành công' in col:
                            success_rate_col = col_idx
                            break
                    
                    if success_rate_col is not None:
                        # Áp dụng định dạng có điều kiện (bắt đầu từ dòng 2 vì dòng 1 là tiêu đề)
                        for row_idx in range(2, len(df_collection_stats) + 2):
                            cell = worksheet.cell(row=row_idx, column=success_rate_col + 1)  # +1 vì openpyxl đánh số cột từ 1
                            value = cell.value
                            
                            if value < 50:  # Dưới 50% - màu đỏ
                                cell.fill = red_fill
                            elif value < 80:  # 50-80% - màu vàng
                                cell.fill = yellow_fill
                            else:  # Trên 80% - màu xanh
                                cell.fill = green_fill
        
        # Nén thư mục kết quả thành file ZIP
        zip_filename = f'category_info_{timestamp}.zip'
        zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
        
        # Tạo file ZIP từ thư mục
        if utils.create_zip_from_folder(result_dir, zip_path):
            # Gửi thông báo hoàn thành
            socketio.emit('progress_update', {
                'percent': 100, 
                'message': f'Đã hoàn thành thu thập thông tin sản phẩm từ {len(valid_urls)} danh mục!'
            })
            
            # Tạo URL để tải xuống file zip
            download_url = url_for('main.download_file', filename=zip_filename)
            
            # Thông báo thành công
            success_message = f"Đã thu thập thành công thông tin sản phẩm từ {len(valid_urls)} danh mục."
            
            return render_template('index.html', download_url=download_url, success_message=success_message)
        else:
            flash('Lỗi khi tạo file ZIP!', 'error')
            return redirect(url_for('main.index'))
            
    except Exception as e:
        error_message = str(e)
        # In chi tiết lỗi
        traceback.print_exc()
        flash(f'Lỗi: {error_message}', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/download-category-baa-images', methods=['POST'])
def download_category_baa_images():
    """Tải ảnh sản phẩm từ nhiều danh mục BAA.vn"""
    try:
        # Lấy danh sách URL danh mục từ form
        category_urls_text = request.form.get('category_urls', '')
        if not category_urls_text.strip():
            flash('Vui lòng nhập ít nhất một URL danh mục', 'error')
            return redirect(url_for('main.index'))
        
        # Tách danh sách URL thành các dòng riêng biệt
        category_urls = [url.strip() for url in category_urls_text.splitlines() if url.strip()]
        
        # Kiểm tra tính hợp lệ của URL
        valid_urls = []
        for url in category_urls:
            if url.startswith(('http://', 'https://')):
                valid_urls.append(url)
            else:
                flash(f'URL không hợp lệ: {url}', 'warning')
        
        if not valid_urls:
            flash('Không có URL danh mục hợp lệ nào', 'error')
            return redirect(url_for('main.index'))
        
        # Tạo thư mục lưu ảnh với timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], f'baa_category_images_{timestamp}')
        os.makedirs(output_dir, exist_ok=True)
        
        # Tạo file báo cáo Excel để theo dõi tiến trình
        report_file = os.path.join(output_dir, 'tong_hop.xlsx')
        
        # Khởi tạo biến lưu trữ kết quả
        all_results = {
            'total_categories': len(valid_urls),
            'total_products': 0,
            'total_success': 0,
            'total_failed': 0,
            'categories': []
        }
        
        # Import các module
        from app.crawler import extract_product_urls, download_baa_product_images_fixed
        from urllib.parse import urlparse
        import pandas as pd
        import openpyxl
        import traceback
        
        # Xử lý từng danh mục
        for index, category_url in enumerate(valid_urls):
            try:
                # Cập nhật tiến trình
                progress = int((index / len(valid_urls)) * 90)
                socketio.emit('progress_update', {
                    'percent': progress,
                    'message': f'Đang xử lý danh mục {index+1}/{len(valid_urls)}: {category_url}'
                })
                
                # Trích xuất tên danh mục từ URL
                category_name = extract_category_name(category_url)
                category_dir = os.path.join(output_dir, category_name)
                os.makedirs(category_dir, exist_ok=True)
                
                # Trích xuất URL sản phẩm từ danh mục
                product_urls = extract_product_urls(category_url)
                
                # Lưu danh sách URL sản phẩm
                urls_file = os.path.join(category_dir, 'product_urls.txt')
                with open(urls_file, 'w', encoding='utf-8') as f:
                    for url in product_urls:
                        f.write(f"{url}\n")
                
                # Tải ảnh sản phẩm
                results = download_baa_product_images_fixed(product_urls, category_dir)
                
                # Cập nhật kết quả
                category_result = {
                    'name': category_name,
                    'url': category_url,
                    'total_products': len(product_urls),
                    'success': results['success'],
                    'failed': results['failed'],
                    'image_paths': results['image_paths']
                }
                
                all_results['categories'].append(category_result)
                all_results['total_products'] += len(product_urls)
                all_results['total_success'] += results['success']
                all_results['total_failed'] += results['failed']
                
                # Lưu báo cáo cho danh mục
                category_report_file = os.path.join(category_dir, 'bao_cao.xlsx')
                create_image_report(results['report_data'], category_report_file)
                
                socketio.emit('progress_update', {
                    'percent': progress + 3,
                    'message': f'Đã tải {results["success"]}/{len(product_urls)} ảnh từ danh mục {category_name}'
                })
                
            except Exception as e:
                print(f"Lỗi khi xử lý danh mục {category_url}: {str(e)}")
                traceback.print_exc()
                all_results['categories'].append({
                    'name': extract_category_name(category_url),
                    'url': category_url,
                    'error': str(e),
                    'total_products': 0,
                    'success': 0,
                    'failed': 0,
                    'image_paths': []
                })
        
        # Tạo báo cáo tổng hợp
        create_category_images_report(all_results, report_file)
        
        # Nén kết quả
        zip_file = output_dir + '.zip'
        utils.create_zip_from_folder(output_dir, zip_file)
        
        # Xóa thư mục tạm sau khi nén
        # shutil.rmtree(output_dir)
        
        socketio.emit('progress_update', {
            'percent': 100,
            'message': 'Hoàn tất tải ảnh từ các danh mục. Đang chuẩn bị tải xuống...'
        })
        
        # Chuyển hướng để tải xuống
        return redirect(url_for('main.download_file', filename=os.path.basename(zip_file)))
        
    except Exception as e:
        error_message = str(e)
        traceback.print_exc()
        flash(f'Lỗi khi tải ảnh từ danh mục: {error_message}', 'error')
        return redirect(url_for('main.index'))

def extract_category_name(url):
    """Trích xuất tên danh mục từ URL"""
    try:
        # Loại bỏ protocol và tên miền
        path = urlparse(url).path
        
        # Loại bỏ các phần không cần thiết
        parts = [p for p in path.split('/') if p and p not in ['vn', 'Category', 'tag']]
        
        if parts:
            # Lấy phần cuối cùng của đường dẫn
            last_part = parts[-1]
            
            # Xử lý trường hợp có tham số ở cuối URL
            if '?' in last_part:
                last_part = last_part.split('?')[0]
            
            # Loại bỏ ID ở cuối URL nếu có
            if '_' in last_part and last_part.split('_')[-1].isdigit():
                last_part = '_'.join(last_part.split('_')[:-1])
            
            # Chuyển đổi dấu gạch ngang thành dấu gạch dưới
            cleaned_name = last_part.replace('-', '_')
            
            return cleaned_name
        
        # Nếu không trích xuất được, sử dụng timestamp
        return datetime.now().strftime('category_%Y%m%d_%H%M%S')
        
    except Exception:
        # Nếu có lỗi, sử dụng timestamp
        return datetime.now().strftime('category_%Y%m%d_%H%M%S')

def create_image_report(report_data, output_file):
    """Tạo báo cáo Excel về việc tải ảnh"""
    try:
        # Tạo DataFrame từ dữ liệu báo cáo
        df = pd.DataFrame(report_data)
        
        # Lưu vào file Excel
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Báo cáo tải ảnh', index=False)
            
            # Tự động điều chỉnh chiều rộng các cột
            worksheet = writer.sheets['Báo cáo tải ảnh']
            for i, col in enumerate(df.columns):
                max_length = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.column_dimensions[worksheet.cell(row=1, column=i+1).column_letter].width = max_length
                
        return True
    except Exception as e:
        print(f"Lỗi khi tạo báo cáo: {str(e)}")
        return False

def create_category_images_report(results, output_file):
    """Tạo báo cáo tổng hợp về việc tải ảnh từ các danh mục"""
    try:
        # Tạo workbook
        wb = openpyxl.Workbook()
        
        # Tạo sheet tổng quan
        ws_overview = wb.active
        ws_overview.title = "Tổng quan"
        
        # Thêm thông tin tổng quan
        ws_overview.append(["Báo cáo tải ảnh từ nhiều danh mục"])
        ws_overview.append(["Thời gian tạo:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        ws_overview.append([])
        ws_overview.append(["Tổng số danh mục:", results['total_categories']])
        ws_overview.append(["Tổng số sản phẩm:", results['total_products']])
        ws_overview.append(["Số ảnh tải thành công:", results['total_success']])
        ws_overview.append(["Số ảnh tải thất bại:", results['total_failed']])
        
        # Tạo sheet chi tiết danh mục
        ws_details = wb.create_sheet("Chi tiết danh mục")
        ws_details.append(["STT", "Tên danh mục", "URL danh mục", "Số sản phẩm", "Thành công", "Thất bại", "Tỷ lệ thành công"])
        
        # Thêm thông tin chi tiết từng danh mục
        for i, category in enumerate(results['categories']):
            success_rate = 0
            if category.get('total_products', 0) > 0:
                success_rate = (category.get('success', 0) / category.get('total_products', 0)) * 100
                
            ws_details.append([
                i + 1,
                category.get('name', 'N/A'),
                category.get('url', 'N/A'),
                category.get('total_products', 0),
                category.get('success', 0),
                category.get('failed', 0),
                f"{success_rate:.2f}%"
            ])
        
        # Điều chỉnh độ rộng cột
        for worksheet in [ws_overview, ws_details]:
            for col in worksheet.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                worksheet.column_dimensions[column].width = max_length + 2
        
        # Lưu workbook
        wb.save(output_file)
        return True
        
    except Exception as e:
        print(f"Lỗi khi tạo báo cáo tổng hợp: {str(e)}")
        return False

@main_bp.route('/crawl-codienhaiau', methods=['POST'])
def crawl_codienhaiau():
    try:
        category_urls = request.form.get('category_urls', '').strip()
        
        if not category_urls:
            flash('Vui lòng nhập ít nhất một URL danh mục.', 'error')
            return redirect(url_for('main.index'))
        
        # Tạo crawler và xử lý danh mục
        crawler = CategoryCrawler(socketio, upload_folder=current_app.config['UPLOAD_FOLDER'])
        success, message, zip_path = crawler.process_codienhaiau_categories(category_urls)
        
        if success:
            filename = os.path.basename(zip_path) if zip_path else session.get('last_download', '')
            if filename:
                # Lưu thông báo thành công và URL tải xuống
                flash(message, 'success')
                return redirect(url_for('main.download_file', filename=filename))
            else:
                flash('Đã xử lý thành công nhưng không tìm thấy file để tải xuống.', 'warning')
                return redirect(url_for('main.index'))
        else:
            flash(f'Lỗi: {message}', 'error')
            return redirect(url_for('main.index'))
        
    except Exception as e:
        traceback.print_exc()
        flash(f'Lỗi không xác định: {str(e)}', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/scrap-category-products', methods=['POST'])
def scrap_category_products():
    try:
        category_urls = request.form.get('category_urls', '').strip()
        
        if not category_urls:
            return jsonify({'status': 'error', 'message': 'Vui lòng nhập ít nhất một URL danh mục.'})
        
        # Tạo crawler và xử lý danh mục
        crawler = CategoryCrawler(socketio, upload_folder=current_app.config['UPLOAD_FOLDER'])
        success, message = crawler.process_category_urls(category_urls)
        
        if success:
            filename = session.get('last_download', '')
            if filename:
                return jsonify({
                    'status': 'success',
                    'message': message,
                    'download': filename
                })
        
        return jsonify({'status': 'error', 'message': message})
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': f'Lỗi không xác định: {str(e)}'})