import os
import cv2
import numpy as np
from PIL import Image, ImageEnhance
import logging
from pathlib import Path
import subprocess
import sys
import shutil
import time

class ImageResizer:
    """
    Class xử lý việc tăng kích thước ảnh sử dụng các phương pháp nâng cao chất lượng ảnh.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        # Thiết lập logger
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        
        # Kiểm tra xem Real-ESRGAN executable có tồn tại không
        self.realesrgan_available = self._check_realesrgan_available()
    
    def _check_realesrgan_available(self):
        """Kiểm tra xem Real-ESRGAN executable có khả dụng không"""
        # Kiểm tra các vị trí có thể có file thực thi
        possible_paths = [
            # Thư mục gốc của ứng dụng
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'realesrgan-ncnn-vulkan.exe'),
            # Thư mục hiện tại
            os.path.join(os.path.dirname(__file__), 'realesrgan-ncnn-vulkan.exe'),
            # Thư mục bin
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bin', 'realesrgan-ncnn-vulkan.exe'),
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                self.logger.info(f"Tìm thấy Real-ESRGAN executable tại: {path}")
                self.realesrgan_path = path
                return True
                
        self.logger.warning("Không tìm thấy Real-ESRGAN executable. Sẽ sử dụng phương pháp thay thế.")
        return False
    
    def upscale_image(self, input_path, output_path=None, scale=2, target_size=(600, 600)):
        """
        Sử dụng các phương pháp nâng cao chất lượng để tăng kích thước ảnh.
        
        Args:
            input_path: Đường dẫn đến ảnh đầu vào
            output_path: Đường dẫn đến ảnh đầu ra. Nếu None, sẽ tự động tạo tên file
            scale: Tỷ lệ phóng to (2, 4, 8) - chỉ được sử dụng khi target_size là None
            target_size: Kích thước đích (width, height) - nếu đặt, sẽ bỏ qua tham số scale
            
        Returns:
            Đường dẫn đến ảnh đã được xử lý
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Không tìm thấy ảnh: {input_path}")
        
        # Nếu không có đường dẫn đầu ra, tạo một với định dạng .webp
        if output_path is None:
            input_filename = os.path.basename(input_path)
            input_name = os.path.splitext(input_filename)[0]
            if target_size:
                output_path = os.path.join(os.path.dirname(input_path), f"{input_name}_enhanced_{target_size[0]}x{target_size[1]}.webp")
            else:
                output_path = os.path.join(os.path.dirname(input_path), f"{input_name}_upscaled_{scale}x.webp")
        
        # Kiểm tra và đổi định dạng đầu ra thành .webp nếu cần
        output_ext = os.path.splitext(output_path)[1].lower()
        if output_ext != '.webp':
            output_path = os.path.splitext(output_path)[0] + '.webp'
        
        # Đường dẫn đầu ra tuyệt đối
        output_path = os.path.abspath(output_path)
        
        self.logger.info(f"Đang xử lý ảnh: {input_path}")
        
        # Thử upscale với Real-ESRGAN nếu khả dụng
        if self.realesrgan_available:
            try:
                # Đầu tiên upscale ảnh với Real-ESRGAN để nâng cao chất lượng
                temp_dir = os.path.join(os.path.dirname(output_path), "temp_realesrgan")
                os.makedirs(temp_dir, exist_ok=True)
                
                # Tạo đường dẫn tạm cho ảnh đã upscale
                temp_output = os.path.join(temp_dir, f"upscaled_{os.path.basename(input_path).replace('.webp', '.png')}")
                
                # Upscale ảnh với Real-ESRGAN
                self.logger.info("Đang upscale ảnh với Real-ESRGAN...")
                self._upscale_with_realesrgan(input_path, temp_output, scale=4)
                
                # Sau đó resize ảnh đã upscale về kích thước mong muốn (600x600) và giữ tỷ lệ
                if target_size:
                    self.logger.info(f"Đang resize ảnh đã upscale thành kích thước {target_size}...")
                    result = self._resize_to_target_size(temp_output, output_path, target_size)
                    
                    # Xóa thư mục tạm
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception as e:
                        self.logger.warning(f"Không thể xóa thư mục tạm: {str(e)}")
                    
                    return result
                
            except Exception as e:
                self.logger.error(f"Lỗi khi sử dụng Real-ESRGAN: {str(e)}")
                self.logger.info("Chuyển sang phương pháp thay thế...")
        
        # Nếu không thể sử dụng Real-ESRGAN hoặc có lỗi xảy ra, sử dụng phương pháp thay thế
        if target_size:
            self.logger.info(f"Kích thước đích: {target_size[0]}x{target_size[1]} pixel")
            return self._resize_to_target_size(input_path, output_path, target_size)
        else:
            self.logger.info(f"Tỷ lệ phóng to: {scale}x")
            return self._enhanced_resize(input_path, output_path, scale)
    
    def _upscale_with_realesrgan(self, input_path, output_path, scale=4, model="realesrgan-x4plus"):
        """
        Sử dụng Real-ESRGAN executable để nâng cao chất lượng ảnh
        
        Args:
            input_path: Đường dẫn đến ảnh đầu vào
            output_path: Đường dẫn đến ảnh đầu ra
            scale: Tỷ lệ phóng to (2, 3, 4)
            model: Mô hình Real-ESRGAN sử dụng (realesrgan-x4plus, realesrgan-x4plus-anime, v.v.)
            
        Returns:
            Đường dẫn đến ảnh đã được xử lý
        """
        if not self.realesrgan_available:
            raise FileNotFoundError("Real-ESRGAN executable không khả dụng")
        
        # Đảm bảo input_path và output_path là đường dẫn tuyệt đối
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)
        
        # Đảm bảo thư mục đầu ra tồn tại
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Tạo một thư mục tạm để lưu các file trung gian
        temp_dir = os.path.join(os.path.dirname(output_path), "temp_esrgan_" + str(int(time.time())))
        os.makedirs(temp_dir, exist_ok=True)
        
        # Kiểm tra extension của đầu vào để đảm bảo hỗ trợ
        input_ext = os.path.splitext(input_path)[1].lower()
        
        # Chuyển đổi sang định dạng PNG với chất lượng cao nếu cần
        temp_input = input_path
        if input_ext in ['.webp', '.jpg', '.jpeg']:
            # Chuyển sang PNG chất lượng cao
            temp_input = os.path.join(temp_dir, os.path.basename(input_path).replace(input_ext, '.png'))
            img = Image.open(input_path)
            
            # Đảm bảo ảnh ở định dạng RGB
            if img.mode != 'RGB':
                img = img.convert('RGB')
                
            # Lưu với chất lượng cao không nén
            img.save(temp_input, format='PNG')
            self.logger.info(f"Đã chuyển đổi ảnh từ {input_ext} sang PNG: {temp_input}")
        
        # Tạo đường dẫn tạm cho output
        temp_output = os.path.join(temp_dir, f"upscaled_{os.path.basename(temp_input)}")
        
        # Tạo lệnh để gọi Real-ESRGAN với các tham số tối ưu
        cmd = [
            self.realesrgan_path,
            "-i", temp_input,
            "-o", temp_output,
            "-s", str(scale),  # Phóng to gấp 4 lần
            "-n", model,       # Sử dụng mô hình tiêu chuẩn
            "-v",              # Hiển thị chi tiết để debug
            "-f", "png"        # Xuất dưới dạng PNG để giữ nguyên chất lượng
        ]
        
        # Thêm tham số giảm nhiễu nếu ảnh gốc có kích thước nhỏ
        img_size = Image.open(input_path).size
        if img_size[0] < 300 or img_size[1] < 300:
            cmd.extend(["-j", "3"])  # Tăng chất lượng xử lý
        
        # Chạy lệnh
        try:
            self.logger.info(f"Thực hiện lệnh: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                self.logger.error(f"Lỗi khi chạy Real-ESRGAN: {result.stderr}")
                raise RuntimeError(f"Real-ESRGAN thất bại với mã lỗi {result.returncode}: {result.stderr}")
            else:
                self.logger.info(f"Real-ESRGAN đã xử lý thành công: {result.stdout}")
            
            # Kiểm tra xem ảnh đầu ra có tồn tại không
            if not os.path.exists(temp_output):
                self.logger.warning("Không tìm thấy ảnh đầu ra từ Real-ESRGAN, có thể RealESRGAN đã tạo ra tên file khác")
                # Tìm file được tạo trong thư mục tạm
                potential_outputs = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.startswith('upscaled_')]
                if potential_outputs:
                    temp_output = potential_outputs[0]
                    self.logger.info(f"Tìm thấy file đầu ra thay thế: {temp_output}")
                else:
                    raise FileNotFoundError("Không tìm thấy ảnh đầu ra từ Real-ESRGAN")
            
            # Đọc ảnh đã upscale để chuẩn bị xử lý tiếp
            upscaled_img = Image.open(temp_output)
            
            # Sau khi đã upscale, lưu ảnh cuối cùng với định dạng yêu cầu 
            # nhưng đảm bảo chất lượng cao
            if output_path.lower().endswith('.webp'):
                upscaled_img.save(output_path, format='WEBP', quality=100, method=6)
            else:
                upscaled_img.save(output_path, quality=100)
                
            # Xóa các file tạm
            try:
                shutil.rmtree(temp_dir)
                self.logger.info(f"Đã xóa thư mục tạm: {temp_dir}")
            except Exception as e:
                self.logger.warning(f"Không thể xóa thư mục tạm: {str(e)}")
                
            return output_path
            
        except Exception as e:
            self.logger.error(f"Lỗi khi chạy Real-ESRGAN: {str(e)}")
            
            # Xóa thư mục tạm ngay cả khi có lỗi
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
                
            raise
            
    def _resize_to_target_size(self, input_path, output_path, target_size=(600, 600)):
        """
        Thay đổi kích thước ảnh thành kích thước đích cụ thể, giữ tỷ lệ khung hình và 
        đảm bảo chất lượng tối ưu, không bị vỡ ảnh.
        """
        try:
            # Đọc ảnh với PIL để hỗ trợ đa dạng định dạng (kể cả WebP)
            img_pil = Image.open(input_path)
            
            # Đảm bảo ảnh ở chế độ RGB
            if img_pil.mode != 'RGB':
                img_pil = img_pil.convert('RGB')
            
            # Lấy kích thước gốc
            orig_width, orig_height = img_pil.size
            self.logger.info(f"Kích thước gốc: {orig_width}x{orig_height}")
            
            # Tính toán tỷ lệ khung hình
            target_width, target_height = target_size
            
            # Tính toán kích thước mới giữ nguyên tỷ lệ
            if orig_width / orig_height > target_width / target_height:
                # Ảnh rộng hơn so với tỷ lệ mục tiêu
                new_width = target_width
                new_height = int((target_width / orig_width) * orig_height)
            else:
                # Ảnh cao hơn hoặc đúng tỷ lệ với mục tiêu
                new_height = target_height
                new_width = int((target_height / orig_height) * orig_width)
            
            # Đảm bảo kích thước mới không vượt quá kích thước đích
            new_width = min(new_width, target_width)
            new_height = min(new_height, target_height)
            
            self.logger.info(f"Kích thước mới (giữ tỷ lệ): {new_width}x{new_height}")
            
            # Khởi tạo ảnh nền trắng
            final_img = Image.new("RGB", target_size, (255, 255, 255))
            
            # Nếu ảnh đã qua upscale bằng AI, sử dụng thuật toán nội suy chất lượng cao
            if "realesrgan" in str(input_path).lower() or orig_width > target_width or orig_height > target_height:
                # Đối với ảnh đã được nâng cao chất lượng bằng AI hoặc ảnh lớn hơn kích thước mục tiêu
                # Sử dụng LANCZOS để giảm kích thước có chất lượng tốt nhất
                resized_img = img_pil.resize((new_width, new_height), Image.LANCZOS)
            else:
                # Đối với ảnh nhỏ hơn kích thước đích và chưa qua xử lý AI
                # Sử dụng thuật toán BICUBIC hoặc LANCZOS cho chất lượng cao khi phóng to
                resized_img = img_pil.resize((new_width, new_height), Image.BICUBIC)
            
            # Chèn ảnh đã resize vào giữa ảnh nền
            paste_x = (target_width - new_width) // 2
            paste_y = (target_height - new_height) // 2
            final_img.paste(resized_img, (paste_x, paste_y))
            
            # Áp dụng các bộ lọc nâng cao chất lượng
            # Tăng độ sắc nét vừa phải để tránh nhiễu ảnh
            enhancer = ImageEnhance.Sharpness(final_img)
            final_img = enhancer.enhance(1.1)  # Giảm xuống 1.1 để tránh vỡ ảnh
            
            # Tinh chỉnh độ tương phản nhẹ
            enhancer = ImageEnhance.Contrast(final_img)
            final_img = enhancer.enhance(1.05)
            
            # Đảm bảo thư mục đầu ra tồn tại
            output_dir = os.path.dirname(output_path)
            os.makedirs(output_dir, exist_ok=True)
            
            # Lưu ảnh với định dạng và chất lượng phù hợp
            if output_path.lower().endswith('.webp'):
                final_img.save(output_path, format="WEBP", quality=95, method=6, lossless=False)
            else:
                final_img.save(output_path, quality=95)
            
            self.logger.info(f"Đã tạo ảnh với kích thước {target_width}x{target_height} và lưu tại: {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"Lỗi khi resize ảnh: {e}")
            raise
    
    def _enhanced_resize(self, input_path, output_path, scale):
        """Phương pháp resize nâng cao sử dụng kết hợp OpenCV và PIL."""
        try:
            # Kiểm tra xem file có phải là WebP không
            input_is_webp = input_path.lower().endswith('.webp')
            
            if input_is_webp:
                # Nếu là WebP, sử dụng PIL để đọc ảnh
                from PIL import Image
                img_pil = Image.open(input_path)
                img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
            else:
                # Đọc ảnh với OpenCV cho các định dạng khác
                img = cv2.imread(input_path)
                
            if img is None:
                raise ValueError(f"Không thể đọc ảnh: {input_path}")
            
            # Resize với thuật toán nội suy CUBIC (chất lượng cao hơn LINEAR)
            height, width = img.shape[:2]
            new_height, new_width = int(height * scale), int(width * scale)
            self.logger.info(f"Thay đổi kích thước từ {width}x{height} thành {new_width}x{new_height}")
            
            # Áp dụng thuật toán CUBIC để có kết quả tốt hơn
            resized = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_CUBIC)
            
            # Chuyển BGR sang RGB
            rgb_img = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            
            # Chuyển sang PIL để áp dụng các bộ lọc nâng cao
            pil_img = Image.fromarray(rgb_img)
            
            # Nâng cao độ sắc nét
            enhancer = ImageEnhance.Sharpness(pil_img)
            enhanced_img = enhancer.enhance(1.3)  # Tăng độ sắc nét lên 30%
            
            # Nâng cao độ tương phản
            enhancer = ImageEnhance.Contrast(enhanced_img)
            enhanced_img = enhancer.enhance(1.1)  # Tăng độ tương phản lên 10%
            
            # Tăng nhẹ độ bão hòa màu sắc để ảnh sống động hơn
            enhancer = ImageEnhance.Color(enhanced_img)
            enhanced_img = enhancer.enhance(1.1)  # Tăng độ bão hòa lên 10%
            
            # Lưu ảnh định dạng WebP với chất lượng cao
            enhanced_img.save(output_path, format='WEBP', quality=95)
            
            self.logger.info(f"Đã xử lý và lưu ảnh thành công: {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"Lỗi khi nâng cao chất lượng ảnh: {e}")
            raise

# Hàm tiện ích để sử dụng nhanh chóng
def upscale_image(input_path, output_path=None, scale=2, target_size=(600, 600)):
    """
    Hàm tiện ích để upscale ảnh với các tham số mặc định.
    """
    resizer = ImageResizer()
    return resizer.upscale_image(input_path, output_path, scale, target_size) 