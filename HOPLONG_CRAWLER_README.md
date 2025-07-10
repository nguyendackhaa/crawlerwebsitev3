# 🤖 HoplongCrawler - Cào dữ liệu HoplongTech.com

## 📋 Tổng quan

**HoplongCrawler** là một class chuyên dụng để cào dữ liệu từ website **HoplongTech.com**, chuyên về cảm biến và thiết bị tự động hóa. Crawler này được tích hợp đầy đủ vào hệ thống CrawlerWebsite với giao diện web thân thiện.

## ✨ Tính năng chính

### 🎯 **Tính năng cào dữ liệu:**
- ✅ **Cào danh mục cảm biến** từ `/category/cam-bien`
- ✅ **Giao diện chọn danh mục** cho người dùng
- ✅ **Cào thông tin sản phẩm chi tiết:**
  - Tên sản phẩm
  - Mã sản phẩm  
  - Giá sản phẩm (có ghi chú VAT)
  - Thương hiệu
  - Thông số kỹ thuật đầy đủ
- ✅ **Phân chia theo thương hiệu** (tự động tạo folder)
- ✅ **Xuất Excel và JSON**
- ✅ **Progress tracking và logging real-time**

### 🏗️ **Tính năng kỹ thuật:**
- ✅ **Multi-threading** với giới hạn số thread an toàn
- ✅ **Retry strategy** cho các request thất bại
- ✅ **Session management** với headers mô phỏng trình duyệt
- ✅ **Error handling** toàn diện
- ✅ **Rate limiting** để không làm quá tải server
- ✅ **Socket.IO integration** cho real-time updates

## 🚀 Cách sử dụng

### 1️⃣ **Qua giao diện Web (Khuyên dùng):**

```bash
# Khởi động server Flask
python app.py

# Truy cập giao diện HoplongCrawler
http://localhost:5000/hoplong
```

### 2️⃣ **Sử dụng trực tiếp trong code:**

```python
from app.crawlhoplong import HoplongCrawler

# Khởi tạo crawler
crawler = HoplongCrawler()

# Lấy danh sách danh mục
categories = crawler.get_sensor_categories()
print(f"Tìm thấy {len(categories)} danh mục")

# Chọn danh mục để cào
selected_categories = categories[:3]  # 3 danh mục đầu tiên

# Bắt đầu cào dữ liệu
results = crawler.crawl_category_products(
    selected_categories=selected_categories,
    max_products_per_category=100  # Giới hạn 100 sản phẩm/danh mục
)

print(f"Kết quả: {results['successful_products']}/{results['total_products']} sản phẩm")
```

## 📁 Cấu trúc dữ liệu đầu ra

### **Thư mục kết quả:**
```
hoplongtech_products_20250103_141530/
├── brands/                          # Thư mục các thương hiệu
│   ├── Hanyoung/                   # Thương hiệu Hanyoung
│   │   ├── Hanyoung_products.json  # Dữ liệu JSON
│   │   └── Hanyoung_products.xlsx  # Báo cáo Excel
│   ├── Omron/                      # Thương hiệu Omron
│   │   ├── Omron_products.json
│   │   └── Omron_products.xlsx
│   └── ...                         # Các thương hiệu khác
└── crawling_summary.json           # Tổng kết crawling
```

### **Định dạng dữ liệu sản phẩm:**
```json
{
  "url": "https://hoplongtech.com/products/up18s-8na",
  "name": "Cảm biến tiệm cận UP18S-8NA Hanyoung",
  "code": "UP18S-8NA",
  "brand": "Hanyoung",
  "price": {
    "price": 173880,
    "price_text": "173.880đ",
    "vat_note": "(Giá chưa bao gồm VAT)"
  },
  "specifications": {
    "Đường kính": "18x18",
    "Khoảng cách phát hiện": "8mm",
    "Kết nối": "Loại cáp",
    "Điện áp cung cấp": "12-24VDC",
    "Đầu ra điều khiển": "NPN NO"
  },
  "crawled_at": "2025-01-03 14:15:30"
}
```

## 🎛️ Cấu hình crawler

### **Các tham số có thể điều chỉnh:**

```python
class HoplongCrawler:
    def __init__(self, socketio=None):
        # Cấu hình crawling
        self.max_workers = 3                    # Số thread đồng thời
        self.delay_between_requests = 1.0       # Delay giữa các request (giây)
        
        # URL cơ bản
        self.base_url = "https://hoplongtech.com"
        self.category_base_url = "https://hoplongtech.com/category/cam-bien"
```

### **Tham số crawling:**
- `max_products_per_category`: Giới hạn số sản phẩm mỗi danh mục (None = không giới hạn)
- `max_pages`: Giới hạn số trang cào (None = tất cả trang)
- `output_dir`: Thư mục lưu kết quả (auto-generate nếu không chỉ định)

## 🧪 Test crawler

### **Chạy test cơ bản:**
```bash
python test_hoplong_crawler.py
```

### **Test từng bước:**
```python
from app.crawlhoplong import test_hoplong_crawler

# Chạy test có sẵn
result = test_hoplong_crawler()
```

## 🔧 Xử lý lỗi thường gặp

### **1. Lỗi kết nối:**
```
❌ Lỗi lấy danh sách danh mục: HTTPConnectionPool
```
**Giải pháp:** Kiểm tra kết nối internet và tường lửa

### **2. Lỗi import:**
```
❌ No module named 'crawlhoplong'
```
**Giải pháp:** Đảm bảo file `app/crawlhoplong.py` tồn tại và đường dẫn đúng

### **3. Lỗi rate limiting:**
```
❌ 429 Too Many Requests
```
**Giải pháp:** Tăng `delay_between_requests` và giảm `max_workers`

### **4. Lỗi parsing HTML:**
```
❌ Không tìm thấy selector
```
**Giải pháp:** Website có thể thay đổi cấu trúc, cần cập nhật selector

## 📊 Thống kê hiệu suất

### **Tốc độ crawling:**
- **Danh mục**: ~5-10 giây/danh mục
- **Sản phẩm**: ~2-3 giây/sản phẩm
- **Thông số kỹ thuật**: ~1-2 giây/bảng thông số

### **Tài nguyên sử dụng:**
- **RAM**: ~50-100MB
- **CPU**: ~10-20% (3 threads)
- **Network**: ~1-2MB/100 sản phẩm

## 🌐 API Endpoints

### **1. Lấy danh sách danh mục:**
```http
GET /api/hoplong/categories
```

**Response:**
```json
{
  "success": true,
  "categories": [...],
  "total_categories": 15,
  "total_products": 25000,
  "base_url": "https://hoplongtech.com"
}
```

### **2. Bắt đầu crawling:**
```http
POST /api/hoplong/crawl
Content-Type: application/json

{
  "categories": [...],
  "max_products_per_category": 100
}
```

## 🚨 Lưu ý quan trọng

### **⚖️ Sử dụng có trách nhiệm:**
- 🕒 **Respect rate limits**: Không crawl quá nhanh
- 📋 **Tuân thủ robots.txt**: Kiểm tra quy định của website
- 🎯 **Crawl có mục đích**: Chỉ lấy dữ liệu cần thiết
- 💼 **Sử dụng thương mại**: Cần xin phép chủ website

### **🔧 Bảo trì:**
- 🔄 **Cập nhật selectors** khi website thay đổi
- 📈 **Monitor hiệu suất** và điều chỉnh tham số
- 🛡️ **Backup dữ liệu** thường xuyên
- 🧪 **Test định kỳ** để đảm bảo hoạt động

## 📞 Hỗ trợ

Nếu gặp vấn đề hoặc cần hỗ trợ:

1. 📝 **Kiểm tra log** trong giao diện web
2. 🧪 **Chạy test script** để chẩn đoán
3. 🔧 **Xem phần xử lý lỗi** ở trên
4. 📧 **Liên hệ team phát triển** với thông tin chi tiết

---

**© 2025 Haiphongtech.vn** - Hệ thống crawler chuyên nghiệp cho cảm biến và thiết bị tự động hóa 