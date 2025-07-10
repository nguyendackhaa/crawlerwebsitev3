import os
import requests
from PIL import Image
from io import BytesIO
import time

def is_white_background_product_image(url):
    """Kiểm tra xem URL có phải là ảnh sản phẩm có nền trắng không"""
    try:
        if not url or not isinstance(url, str):
            return False
        
        url_lower = url.lower()
        
        # Kiểm tra pattern URL Fotek sản phẩm có nền trắng
        fotek_product_patterns = [
            '/image/catalog/product/item/',
            '/image/catalog/product/',
            '/catalog/product/item/',
            '/catalog/product/'
        ]
        
        has_product_pattern = any(pattern in url_lower for pattern in fotek_product_patterns)
        
        # Kiểm tra extension file ảnh
        valid_extensions = ['.png', '.jpg', '.jpeg', '.webp']
        has_valid_extension = any(url_lower.endswith(ext) or ext + '?' in url_lower for ext in valid_extensions)
        
        # Loại trừ các ảnh không phải sản phẩm chính (CẬP NHẬT: chỉ check trong filename và path, không check toàn bộ URL)
        exclude_keywords = [
            'specification', 'speci',  # Thông số kỹ thuật (bỏ 'spec' vì có thể conflict với 'specs' trong URL)
            'wiring', 'wire', 'wd',    # Sơ đồ đấu nối  
            'dimension', 'dims', 'dms', # Kích thước
            'manual', 'catalog',       # Sách hướng dẫn
            'drawing', 'schematic',    # Bản vẽ
            'connection', 'install',   # Hướng dẫn lắp đặt
            'thumbnail', 'thumb',      # Ảnh nhỏ
            'icon', 'logo'             # Icon, logo
        ]
        
        # Chỉ kiểm tra exclude keywords trong filename (không check toàn bộ URL path)
        filename = url.split('/')[-1].split('?')[0].lower()
        has_exclude_keyword = any(keyword in filename for keyword in exclude_keywords)
        
        is_product_image = (
            has_product_pattern and 
            has_valid_extension and 
            not has_exclude_keyword and
            len(filename) > 5
        )
        
        print(f"URL: {url}")
        print(f"  - Has product pattern: {has_product_pattern}")
        print(f"  - Has valid extension: {has_valid_extension}")
        print(f"  - Filename: {filename}")
        print(f"  - Has exclude keyword in filename: {has_exclude_keyword}")
        if has_exclude_keyword:
            matching_keywords = [k for k in exclude_keywords if k in filename]
            print(f"  - Matching exclude keywords: {matching_keywords}")
        print(f"  - Is product image: {is_product_image}")
        
        return is_product_image
        
    except Exception as e:
        print(f"Lỗi khi kiểm tra ảnh sản phẩm: {str(e)}")
        return False

def process_fotek_product_image(image, image_url):
    """Xử lý ảnh sản phẩm Fotek: chuyển nền trong suốt thành nền trắng"""
    try:
        # Kiểm tra xem có phải ảnh sản phẩm Fotek không
        if not is_white_background_product_image(image_url):
            print(f"⚠️ Không phải ảnh sản phẩm Fotek, bỏ qua xử lý nền trắng")
            if image.mode in ("RGBA", "P"):
                return image.convert("RGB")
            return image
        
        print(f"🎯 Đang xử lý ảnh sản phẩm Fotek với nền trắng...")
        
        # Kiểm tra mode của ảnh
        original_mode = image.mode
        print(f"📋 Mode ảnh gốc: {original_mode}")
        
        if original_mode == "RGBA":
            print(f"🔍 Phát hiện ảnh RGBA (có alpha channel), đang xử lý nền trắng...")
            
            # Tạo ảnh nền trắng cùng kích thước
            white_background = Image.new("RGB", image.size, (255, 255, 255))
            print(f"⚪ Đã tạo nền trắng kích thước: {image.size}")
            
            # Composite ảnh gốc lên nền trắng
            composite_image = Image.alpha_composite(
                white_background.convert("RGBA"), 
                image
            ).convert("RGB")
            
            print(f"✅ Đã composite ảnh lên nền trắng thành công")
            return composite_image
            
        elif original_mode == "P":
            print(f"🔍 Phát hiện ảnh Palette mode, kiểm tra transparency...")
            
            if "transparency" in image.info:
                print(f"🎭 Ảnh có transparency, chuyển đổi sang RGBA rồi xử lý nền trắng...")
                rgba_image = image.convert("RGBA")
                
                white_background = Image.new("RGB", rgba_image.size, (255, 255, 255))
                composite_image = Image.alpha_composite(
                    white_background.convert("RGBA"), 
                    rgba_image
                ).convert("RGB")
                
                print(f"✅ Đã xử lý ảnh Palette với transparency")
                return composite_image
            else:
                print(f"📝 Ảnh Palette không có transparency, chuyển sang RGB")
                return image.convert("RGB")
                
        elif original_mode in ("RGB", "L"):
            print(f"📷 Ảnh đã có nền ({original_mode}), không cần xử lý thêm")
            if original_mode == "L":
                return image.convert("RGB")
            return image
            
        else:
            print(f"⚠️ Mode ảnh không được hỗ trợ: {original_mode}, chuyển sang RGB")
            return image.convert("RGB")
            
    except Exception as e:
        print(f"❌ Lỗi khi xử lý ảnh sản phẩm Fotek: {str(e)}")
        if image.mode in ("RGBA", "P"):
            return image.convert("RGB")
        return image

def test_fotek_image():
    """Test tải và xử lý ảnh Fotek"""
    # URL ảnh test
    image_url = "https://www.fotek.com.tw/image/catalog/product/Item/TC-NT-10R.png?v=1592418834"
    
    print("🚀 Bắt đầu test ảnh Fotek...")
    print(f"📎 URL: {image_url}")
    print("=" * 80)
    
    try:
        # Tải ảnh
        print("🔽 Đang tải ảnh từ Fotek...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.fotek.com.tw'
        }
        
        response = requests.get(image_url, headers=headers, timeout=30)
        print(f"📊 HTTP Status: {response.status_code}")
        print(f"📦 Content-Type: {response.headers.get('content-type', 'N/A')}")
        print(f"📏 Content-Length: {len(response.content)} bytes")
        
        if response.status_code == 200:
            # Mở ảnh bằng PIL
            original_image = Image.open(BytesIO(response.content))
            print(f"📄 Ảnh gốc - Mode: {original_image.mode}, Size: {original_image.size}")
            
            # Lưu ảnh gốc để so sánh
            original_image.save("TC-NT-10R_original.png", "PNG")
            print("💾 Đã lưu ảnh gốc: TC-NT-10R_original.png")
            
            # Xử lý ảnh với logic nền trắng
            print("\n" + "=" * 50)
            processed_image = process_fotek_product_image(original_image, image_url)
            
            # Lưu ảnh đã xử lý
            processed_image.save("TC-NT-10R_processed.webp", "WEBP", quality=95, method=6)
            print("💾 Đã lưu ảnh đã xử lý: TC-NT-10R_processed.webp")
            
            # Lưu thêm bản PNG để dễ so sánh
            processed_image.save("TC-NT-10R_processed.png", "PNG")
            print("💾 Đã lưu ảnh đã xử lý (PNG): TC-NT-10R_processed.png")
            
            print("\n🎉 TEST HOÀN THÀNH!")
            print("📁 Kiểm tra các file sau:")
            print("   - TC-NT-10R_original.png (ảnh gốc)")
            print("   - TC-NT-10R_processed.webp (ảnh đã xử lý - WebP)")
            print("   - TC-NT-10R_processed.png (ảnh đã xử lý - PNG)")
            
        else:
            print(f"❌ Lỗi HTTP {response.status_code}")
            
    except Exception as e:
        print(f"❌ Lỗi khi test: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_fotek_image() 