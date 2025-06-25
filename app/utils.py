import re
import pandas as pd
import os
from urllib.parse import urlparse
import zipfile
import traceback
import unicodedata

def is_valid_url(url):
    """
    Kiểm tra URL có hợp lệ không
    """
    pattern = r'^https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&\/=]*)$'
    return bool(re.match(pattern, url))

def save_to_excel(data_list, file_path):
    """
    Lưu danh sách dữ liệu vào file Excel
    """
    # Tạo thư mục chứa file nếu chưa tồn tại
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    # Tạo DataFrame từ danh sách dữ liệu
    df = pd.DataFrame(data_list)
    
    # Lưu vào file Excel
    df.to_excel(file_path, index=False)
    
    return file_path 

def create_zip_from_folder(folder_path, zip_path):
    """
    Tạo file ZIP từ thư mục
    
    Args:
        folder_path (str): Đường dẫn tới thư mục cần nén
        zip_path (str): Đường dẫn đầu ra cho file ZIP
        
    Returns:
        bool: True nếu thành công, False nếu thất bại
    """
    try:
        print(f"Bắt đầu tạo file ZIP từ thư mục: {folder_path}")
        print(f"File ZIP sẽ được lưu tại: {zip_path}")
        
        # Đảm bảo thư mục cha của file ZIP tồn tại
        zip_dir = os.path.dirname(zip_path)
        if not os.path.exists(zip_dir):
            os.makedirs(zip_dir)
            print(f"Đã tạo thư mục: {zip_dir}")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Duyệt qua tất cả các file trong thư mục
            for root, dirs, files in os.walk(folder_path):
                print(f"Đang quét thư mục: {root}")
                print(f"Tìm thấy {len(files)} files")
                
                for file in files:
                    # Tạo đường dẫn đầy đủ đến file
                    file_path = os.path.join(root, file)
                    # Tạo tên cho file trong ZIP (đường dẫn tương đối)
                    arcname = os.path.relpath(file_path, folder_path)
                    print(f"Thêm file vào ZIP: {arcname}")
                    # Thêm file vào ZIP
                    zipf.write(file_path, arcname)
        
        # Kiểm tra file ZIP đã được tạo
        if os.path.exists(zip_path):
            zip_size = os.path.getsize(zip_path)
            print(f"File ZIP đã được tạo thành công: {zip_path} (Kích thước: {zip_size} bytes)")
            return True
        else:
            print(f"Không tìm thấy file ZIP sau khi tạo: {zip_path}")
            return False
            
    except Exception as e:
        print(f"Lỗi khi tạo file ZIP: {str(e)}")
        print(f"Chi tiết lỗi: {traceback.format_exc()}")
        return False 

def slug(title: str, separator: str = '-', language: str = 'en', dictionary: dict = {'@': 'at'}) -> str:
    def to_ascii(text: str, lang: str = 'en') -> str:
        # Chuyển đổi chuỗi unicode sang ASCII (loại bỏ dấu tiếng Việt...)
        text = unicodedata.normalize('NFKD', text)
        return text.encode('ascii', 'ignore').decode('ascii')

    def to_lower(text: str) -> str:
        return text.lower()

    if language:
        title = to_ascii(title, language)

    # Convert all dashes/underscores into separator
    flip = '_' if separator == '-' else '-'
    title = re.sub(f"[{re.escape(flip)}]+", separator, title)

    # Replace dictionary words like '@' => 'at'
    for key, val in dictionary.items():
        title = title.replace(key, f"{separator}{val}{separator}")

    # Remove all characters except letters, numbers, separators, and whitespace
    title = to_lower(title)
    title = re.sub(f"[^{re.escape(separator)}\w\s]", '', title, flags=re.UNICODE)

    # Replace all whitespace or repeated separators with single separator
    title = re.sub(f"[{re.escape(separator)}\s]+", separator, title)

    return title.strip(separator)

def standardize_filename(product_code):
    """
    Chuẩn hóa tên file từ mã sản phẩm.
    Sử dụng hàm slug để chuẩn hóa tên file, loại bỏ dấu tiếng Việt và các ký tự đặc biệt.
    Mặc định viết hoa tất cả các chữ cái.
    
    Args:
        product_code (str): Mã sản phẩm cần chuẩn hóa
        
    Returns:
        str: Tên file đã được chuẩn hóa và viết hoa
    """
    # Sử dụng hàm slug để chuẩn hóa tên file
    normalized = slug(product_code, separator='-', language='vi')
    
    # Viết hoa tất cả các chữ cái
    normalized = normalized.upper()
    
    # Đảm bảo tên file không quá dài (giới hạn 255 ký tự)
    if len(normalized) > 255:
        normalized = normalized[:255]
    
    return normalized 