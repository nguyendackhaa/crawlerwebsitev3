# Crawler Website v2

Đây là ứng dụng web crawler được phát triển bằng Flask, cho phép trích xuất thông tin sản phẩm, hình ảnh và tài liệu từ các trang web thương mại điện tử.

## Tính năng chính

- Trích xuất liên kết sản phẩm từ trang danh mục
- Trích xuất thông tin sản phẩm chi tiết
- Tải xuống hình ảnh sản phẩm
- Tải xuống tài liệu sản phẩm
- So sánh mã sản phẩm giữa các file
- Lọc sản phẩm theo tiêu chí
- Trích xuất giá sản phẩm
- Tương tác thời gian thực với SocketIO

## Yêu cầu hệ thống

- Python 3.x
- IIS (để triển khai trên Windows Server)
- Các thư viện Python được liệt kê trong `app/requirements.txt`

## Cài đặt và triển khai

Xem chi tiết hướng dẫn cài đặt trong tệp [INSTALLATION_GUIDE.md](INSTALLATION_GUIDE.md).

## Cấu trúc dự án

```
CrawlerWebsitev2/
│
├── app/
│   ├── __init__.py           # Khởi tạo ứng dụng Flask
│   ├── crawler.py            # Logic crawler chính
│   ├── product_comparison.py # So sánh sản phẩm
│   ├── routes.py             # Định nghĩa các route
│   ├── templates/            # Templates HTML
│   │   ├── base.html         # Template cơ sở
│   │   ├── index.html        # Trang chính
│   │   ├── view_images.html  # Xem hình ảnh
│   │   └── view_baa_images.html # Xem hình ảnh BAA
│   ├── utils.py              # Các hàm tiện ích
│   └── requirements.txt      # Các phụ thuộc
│
├── logs/                     # Thư mục chứa log
├── venv/                     # Môi trường ảo Python
├── README.md                 # Tài liệu tổng quan
├── INSTALLATION_GUIDE.md     # Hướng dẫn cài đặt chi tiết
├── run.py                    # Điểm khởi chạy ứng dụng
└── web.config                # Cấu hình IIS
```

## Chạy ứng dụng trong môi trường phát triển

```
python run.py
```

Ứng dụng sẽ chạy tại địa chỉ: http://127.0.0.1:5000

## Giấy phép

© 2023-2024. Mọi quyền được bảo lưu.
