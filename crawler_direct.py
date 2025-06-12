#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script để chạy crawler trực tiếp mà không cần Flask application context
"""

import os
import sys
import time
import argparse
from threading import Timer
from app.category_crawler import CategoryCrawler, safe_print
import traceback

def print_progress(percent, message):
    """In tiến trình theo định dạng chuẩn"""
    print(f"Tiến trình: {percent}% - {message}")

class DummySocketIO:
    """Một lớp giả để thay thế socketio khi chạy trực tiếp"""
    def emit(self, event, data):
        if event == 'progress_update':
            print_progress(data['percent'], data['message'])

def clear_screen():
    """Xóa màn hình terminal"""
    os.system('cls' if os.name == 'nt' else 'clear')

def main():
    # Phân tích tham số dòng lệnh
    parser = argparse.ArgumentParser(description='Cào dữ liệu từ trang web codienhaiau.com')
    parser.add_argument('--url', help='URL danh mục cần cào dữ liệu (có thể nhập nhiều URL, mỗi URL trên một dòng)')
    parser.add_argument('--file', help='Đường dẫn đến file chứa danh sách URL danh mục (mỗi URL trên một dòng)')
    parser.add_argument('--output', default='output', help='Thư mục đầu ra để lưu dữ liệu (mặc định: "output")')
    parser.add_argument('--threads', type=int, default=8, help='Số luồng xử lý tối đa (mặc định: 8)')
    
    args = parser.parse_args()
    
    # Đảm bảo có ít nhất một nguồn URL
    if not args.url and not args.file:
        print("Lỗi: Cần cung cấp URL danh mục hoặc file chứa danh sách URL!")
        parser.print_help()
        return 1
    
    # Đọc URLs từ nguồn
    urls = []
    if args.url:
        urls.append(args.url)
    
    if args.file:
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                file_urls = [line.strip() for line in f if line.strip()]
                urls.extend(file_urls)
        except Exception as e:
            print(f"Lỗi khi đọc file {args.file}: {str(e)}")
            return 1
    
    # Nối các URL thành một chuỗi
    urls_text = '\n'.join(urls)
    
    # Đảm bảo thư mục đầu ra tồn tại
    os.makedirs(args.output, exist_ok=True)
    
    # Khởi tạo crawler
    dummy_socketio = DummySocketIO()
    crawler = CategoryCrawler(dummy_socketio, upload_folder=args.output)
    crawler.max_workers = args.threads
    
    # Xóa màn hình và hiển thị thông tin
    clear_screen()
    print("=== CRAWLER TRỰC TIẾP ===")
    print(f"Số URL danh mục: {len(urls)}")
    print(f"Thư mục đầu ra: {args.output}")
    print(f"Số luồng: {args.threads}")
    print("=========================")
    print("Bắt đầu cào dữ liệu...")
    
    # Chạy crawler
    start_time = time.time()
    print(f"Bắt đầu cào dữ liệu với {args.threads} luồng...")
    print(f"URL(s) đang xử lý: {urls}")
    
    try:
        success, message = crawler.process_codienhaiau_categories(urls_text)
    except Exception as e:
        print(f"Lỗi nghiêm trọng khi chạy crawler: {str(e)}")
        traceback.print_exc()
        success = False
        message = f"Lỗi: {str(e)}"
    
    # Hiển thị kết quả
    end_time = time.time()
    duration = end_time - start_time
    
    print("\n=== KẾT QUẢ ===")
    if success:
        print(f"Thành công: {message}")
    else:
        print(f"Thất bại: {message}")
    
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)
    print(f"Thời gian thực thi: {int(hours)} giờ, {int(minutes)} phút, {seconds:.2f} giây")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main()) 