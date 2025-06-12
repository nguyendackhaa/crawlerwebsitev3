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

## Cài đặt

1. Đảm bảo máy tính đã cài đặt Python 3.7 trở lên
2. Clone repository về máy:

```
git clone https://github.com/username/CrawlBot.git
cd CrawlBot
```

3. Cài đặt các thư viện cần thiết:

```
pip install -r requirements.txt
```

## Cách sử dụng

1. Khởi động ứng dụng:

```
cd CrawlerWebsitev2; python -m flask run
```

2. Mở trình duyệt và truy cập: `http://localhost:5000`

3. Sử dụng các chức năng:
   - **Chức năng 1**: Thu thập liên kết sản phẩm từ trang danh mục
   - **Chức năng 2**: Thu thập thông tin sản phẩm từ liên kết

## Chuẩn bị file đầu vào

1. **File danh sách URL danh mục** (txt):

   - Mỗi URL trên một dòng, ví dụ:

   ```
   https://baa.vn/vn/Category/cam-bien-quang-dien-autonics_47_378/
   https://baa.vn/vn/Category/cam-bien-anh-sang_47_389/
   ```

2. **File danh sách URL sản phẩm** (txt):

   - Mỗi URL trên một dòng, ví dụ:

   ```
   https://baa.vn/vn/product/cam-bien-quang-autonics-bx5m-mfr_1325639/
   https://baa.vn/vn/product/cam-bien-quang-autonics-bup-30_1325634/
   ```

3. **File Excel mẫu**:
   - File Excel với các cột tương ứng với thông tin cần thu thập
   - Ví dụ: `Mã sản phẩm`, `Tên sản phẩm`, `Giá`, `Khoảng cách phát hiện`, `Nguồn cấp`, v.v.

## Lưu ý

- Tốc độ thu thập phụ thuộc vào số lượng sản phẩm và tốc độ mạng
- Nên sử dụng VPN hoặc proxy nếu thu thập một lượng lớn dữ liệu để tránh bị chặn
- Một số website có biện pháp chống thu thập dữ liệu tự động, hiệu quả có thể bị hạn chế
- LH : 0355108736 để biết thêm chi tiết
