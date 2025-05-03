# CrawlBot - Công cụ thu thập dữ liệu sản phẩm

CrawlBot là ứng dụng web giúp thu thập thông tin sản phẩm từ các website thương mại điện tử.

## Tính năng

1. **Thu thập liên kết sản phẩm từ trang danh mục**

   - Nhập file txt chứa danh sách URL của các trang danh mục
   - Ứng dụng sẽ duyệt qua từng trang và thu thập tất cả liên kết sản phẩm
   - Kết quả được xuất ra file txt chứa tất cả liên kết sản phẩm

2. **Thu thập thông tin sản phẩm từ liên kết**
   - Nhập file txt chứa danh sách URL của các sản phẩm
   - Nhập file Excel mẫu xác định các thông tin cần thu thập
   - Ứng dụng sẽ thu thập thông tin từ trang chi tiết của mỗi sản phẩm
   - Kết quả được xuất ra file Excel

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
