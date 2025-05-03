from crawler import download_baa_product_images
import os

def main():
    # Đọc danh sách URL từ file test.txt
    try:
        with open('../test.txt', 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f.readlines() if line.strip()]
        
        print(f"Đọc được {len(urls)} URL từ file test.txt")
        
        # Tạo thư mục output nếu chưa tồn tại
        output_folder = 'downloads/test_baa_images'
        
        # Gọi hàm download_baa_product_images
        results = download_baa_product_images(urls, output_folder)
        
        # Hiển thị kết quả
        print("\nKết quả tải ảnh:")
        print(f"- Tổng số URL: {results['total']}")
        print(f"- Thành công: {results['success']}")
        print(f"- Thất bại: {results['failed']}")
        print(f"- Số lượng ảnh đã tải: {len(results['image_paths'])}")
        
        # Hiển thị đường dẫn file báo cáo
        if 'report_file' in results:
            print(f"- File báo cáo: {results['report_file']}")
            
        print("\nHoàn thành!")
        
    except Exception as e:
        print(f"Lỗi: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main() 