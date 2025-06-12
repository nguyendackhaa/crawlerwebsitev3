import re
import pandas as pd
import os
from urllib.parse import urlparse
import zipfile
import traceback

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

def standardize_filename(product_code):
    """
    Chuẩn hóa tên file từ mã sản phẩm.
    Thay thế các ký tự không hợp lệ bằng dấu gạch ngang và loại bỏ các dấu gạch ngang trùng lặp.
    
    Args:
        product_code (str): Mã sản phẩm cần chuẩn hóa
        
    Returns:
        str: Tên file đã được chuẩn hóa
    """
    import re
    
    # Thay thế các ký tự không hợp lệ bằng dấu gạch ngang
    invalid_chars = r'[\\/:*?"<>|,=\s]'
    normalized = re.sub(invalid_chars, '-', product_code)
    
    # Loại bỏ các dấu gạch ngang trùng lặp
    normalized = re.sub(r'-+', '-', normalized)
    
    # Chỉ giữ lại chữ cái, số, dấu gạch ngang và gạch dưới
    normalized = re.sub(r'[^a-zA-Z0-9\-_]', '', normalized)
    
    # Loại bỏ dấu gạch ngang ở đầu và cuối chuỗi
    normalized = normalized.strip('-')
    
    return normalized 