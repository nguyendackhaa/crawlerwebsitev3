"""
Module chuyển đổi ảnh sang WebP thực sự
Đảm bảo file output là WebP chuẩn, không bị nhầm lẫn với JPG
"""

import os
import struct
import hashlib
from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)

class WebPConverter:
    """Chuyển đổi ảnh sang WebP với các kiểm tra nghiêm ngặt"""
    
    @staticmethod
    def verify_webp_signature(file_path):
        """
        Kiểm tra file có phải là WebP thực sự không bằng cách kiểm tra file signature
        WebP signature: RIFF....WEBP
        """
        try:
            with open(file_path, 'rb') as f:
                # Đọc 12 bytes đầu tiên
                header = f.read(12)
                
                # WebP header structure:
                # Bytes 0-3: "RIFF"
                # Bytes 4-7: File size (little-endian)
                # Bytes 8-11: "WEBP"
                
                if len(header) < 12:
                    return False
                
                # Kiểm tra RIFF signature
                if header[0:4] != b'RIFF':
                    logger.error(f"File {file_path} không có RIFF signature")
                    return False
                
                # Kiểm tra WEBP signature
                if header[8:12] != b'WEBP':
                    logger.error(f"File {file_path} không có WEBP signature")
                    return False
                
                return True
                
        except Exception as e:
            logger.error(f"Lỗi khi kiểm tra WebP signature: {str(e)}")
            return False
    
    @staticmethod
    def get_webp_info(file_path):
        """
        Lấy thông tin chi tiết về file WebP
        """
        try:
            with Image.open(file_path) as img:
                info = {
                    'format': img.format,
                    'mode': img.mode,
                    'size': img.size,
                    'width': img.width,
                    'height': img.height,
                    'info': img.info
                }
                
                # Kiểm tra định dạng
                if img.format != 'WEBP':
                    logger.warning(f"File {file_path} được PIL nhận diện là {img.format}, không phải WEBP")
                
                return info
                
        except Exception as e:
            logger.error(f"Lỗi khi đọc thông tin WebP: {str(e)}")
            return None
    
    @staticmethod
    def convert_to_webp(input_path, output_path, quality=90, lossless=False, method=6):
        """
        Chuyển đổi ảnh sang WebP với các tùy chọn tối ưu
        
        Args:
            input_path: Đường dẫn file ảnh đầu vào
            output_path: Đường dẫn file WebP đầu ra
            quality: Chất lượng (1-100), mặc định 90
            lossless: True để nén không mất dữ liệu
            method: Phương pháp nén (0-6), càng cao càng chậm nhưng nén tốt hơn
        
        Returns:
            dict: Thông tin về quá trình chuyển đổi
        """
        result = {
            'success': False,
            'input_file': input_path,
            'output_file': output_path,
            'input_size': 0,
            'output_size': 0,
            'compression_ratio': 0,
            'error': None,
            'webp_verified': False,
            'webp_info': None
        }
        
        try:
            # Kiểm tra file đầu vào
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"File không tồn tại: {input_path}")
            
            # Lấy kích thước file gốc
            result['input_size'] = os.path.getsize(input_path)
            
            # Mở và xử lý ảnh
            with Image.open(input_path) as img:
                # Log thông tin ảnh gốc
                logger.info(f"Ảnh gốc: {img.format}, {img.mode}, {img.size}")
                
                # Chuyển đổi sang RGB nếu cần (WebP không hỗ trợ một số mode)
                if img.mode == 'RGBA':
                    # Giữ nguyên RGBA cho WebP
                    converted_img = img
                elif img.mode == 'LA' or img.mode == 'L':
                    # Chuyển grayscale sang RGB
                    converted_img = img.convert('RGB')
                elif img.mode == 'P':
                    # Chuyển palette sang RGBA nếu có transparency, RGB nếu không
                    if 'transparency' in img.info:
                        converted_img = img.convert('RGBA')
                    else:
                        converted_img = img.convert('RGB')
                elif img.mode == 'CMYK':
                    # Chuyển CMYK sang RGB
                    converted_img = img.convert('RGB')
                else:
                    # Các mode khác chuyển sang RGB
                    if img.mode != 'RGB':
                        converted_img = img.convert('RGB')
                    else:
                        converted_img = img
                
                # Tạo thư mục đầu ra nếu chưa tồn tại
                os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
                
                # Tùy chọn lưu WebP
                save_kwargs = {
                    'format': 'WEBP',
                    'quality': quality,
                    'method': method,
                    'lossless': lossless
                }
                
                # Lưu file WebP
                converted_img.save(output_path, **save_kwargs)
                
                logger.info(f"Đã lưu WebP: {output_path}")
            
            # Kiểm tra file đã được tạo
            if not os.path.exists(output_path):
                raise Exception("File WebP không được tạo")
            
            # Lấy kích thước file WebP
            result['output_size'] = os.path.getsize(output_path)
            
            # Tính tỷ lệ nén
            if result['input_size'] > 0:
                result['compression_ratio'] = round((1 - result['output_size'] / result['input_size']) * 100, 2)
            
            # QUAN TRỌNG: Kiểm tra file có phải WebP thực sự không
            result['webp_verified'] = WebPConverter.verify_webp_signature(output_path)
            
            if not result['webp_verified']:
                # Xóa file không hợp lệ
                os.remove(output_path)
                raise Exception("File output không phải là WebP thực sự!")
            
            # Lấy thông tin WebP
            result['webp_info'] = WebPConverter.get_webp_info(output_path)
            
            # Kiểm tra lại format từ PIL
            if result['webp_info'] and result['webp_info']['format'] != 'WEBP':
                # Xóa file không hợp lệ
                os.remove(output_path)
                raise Exception(f"PIL nhận diện file là {result['webp_info']['format']}, không phải WEBP!")
            
            result['success'] = True
            logger.info(f"Chuyển đổi thành công: {input_path} -> {output_path} (giảm {result['compression_ratio']}%)")
            
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Lỗi chuyển đổi WebP: {str(e)}")
            
            # Xóa file lỗi nếu có
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except:
                    pass
        
        return result
    
    @staticmethod
    def batch_convert(input_files, output_dir, quality=90, lossless=False, method=6):
        """
        Chuyển đổi nhiều file sang WebP
        
        Args:
            input_files: Danh sách đường dẫn file đầu vào
            output_dir: Thư mục đầu ra
            quality: Chất lượng WebP
            lossless: Nén không mất dữ liệu
            method: Phương pháp nén
            
        Returns:
            list: Danh sách kết quả chuyển đổi
        """
        results = []
        
        # Tạo thư mục đầu ra
        os.makedirs(output_dir, exist_ok=True)
        
        for input_file in input_files:
            # Tạo tên file đầu ra
            base_name = os.path.splitext(os.path.basename(input_file))[0]
            output_file = os.path.join(output_dir, f"{base_name}.webp")
            
            # Chuyển đổi
            result = WebPConverter.convert_to_webp(
                input_file, 
                output_file, 
                quality=quality,
                lossless=lossless,
                method=method
            )
            
            results.append(result)
        
        return results
    
    @staticmethod
    def get_mime_type(file_path):
        """
        Lấy MIME type thực sự của file
        """
        try:
            # Kiểm tra bằng file signature
            with open(file_path, 'rb') as f:
                header = f.read(12)
                
                # WebP
                if header[0:4] == b'RIFF' and header[8:12] == b'WEBP':
                    return 'image/webp'
                
                # JPEG
                if header[0:3] == b'\xff\xd8\xff':
                    return 'image/jpeg'
                
                # PNG
                if header[0:8] == b'\x89PNG\r\n\x1a\n':
                    return 'image/png'
                
                # GIF
                if header[0:6] in (b'GIF87a', b'GIF89a'):
                    return 'image/gif'
                
                # BMP
                if header[0:2] == b'BM':
                    return 'image/bmp'
                
            return 'application/octet-stream'
            
        except Exception as e:
            logger.error(f"Lỗi khi xác định MIME type: {str(e)}")
            return 'application/octet-stream' 