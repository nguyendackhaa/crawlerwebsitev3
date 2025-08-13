# Keyence Crawler Interface Documentation

## Tổng quan

Giao diện web cho **Keyence Product Crawler** đã được hoàn thiện với đầy đủ tính năng cào dữ liệu sản phẩm từ website Keyence Việt Nam (keyence.com.vn).

## Cấu trúc giao diện

### 1. Template HTML
- **File**: `crawlerwebsitev3/app/templates/keyence_crawler.html`
- **Design**: Sử dụng Bootstrap 5.3.0 với thiết kế responsive
- **Color scheme**: Gradient đỏ-cam (Keyence brand colors)
- **Features**: Real-time progress tracking, logs container, stats display

### 2. Routes Backend
- **File**: `crawlerwebsitev3/app/routes.py`
- **Routes được thêm**:
  - `/keyence` - Trang chính Keyence Crawler
  - `/crawl-keyence` - API endpoint để bắt đầu crawling
  - `/download-keyence-result/<folder_name>` - Tải xuống kết quả ZIP
  - `/list-keyence-results` - Lấy danh sách kết quả crawler

### 3. Navigation
- **File**: `crawlerwebsitev3/app/templates/index.html`
- **Thêm tab**: "Cào dữ liệu Keyence.com.vn" với icon CPU

## Tính năng giao diện

### Header Section
```html
<h1><i class="fas fa-industry"></i> Keyence Product Crawler</h1>
<p class="subtitle">Cào dữ liệu sản phẩm từ website Keyence Việt Nam với xử lý đa luồng và white background processing</p>
<div class="website-info">
    <i class="fas fa-globe"></i> <strong>Target:</strong> keyence.com.vn
    <span class="ms-3"><i class="fas fa-language"></i> Vietnamese interface</span>
    <span class="ms-3 keyence-badge">NO AI REQUIRED</span>
</div>
```

### Control Panel
- **Trang Chủ**: Quay về trang chính
- **Xem Kết Quả**: Load danh sách kết quả crawler
- **Xóa Logs**: Clear logs container

### URL Input Section
- **Textarea**: Nhập multiple category URLs (một URL mỗi dòng)
- **Validation**: Chỉ chấp nhận URLs chứa `keyence.com.vn`
- **Examples**: Hiển thị ví dụ URLs mẫu
- **Features list**: 9 tính năng chính của crawler

### Progress Section
- **Progress bar**: Real-time progress tracking
- **Stats**: 6 thống kê (Categories, Series, Products, Images, Errors, Time)
- **Logs container**: Terminal-style logs với timestamp

### Results Section
- **Dynamic loading**: Tự động load kết quả sau khi crawler hoàn thành
- **Download**: Nút tải xuống ZIP cho từng kết quả
- **Metadata**: Hiển thị category count, product count, image count, created time

## API Endpoints

### 1. GET /keyence
**Mục đích**: Hiển thị trang Keyence Crawler  
**Response**: Template HTML `keyence_crawler.html`

### 2. POST /crawl-keyence
**Mục đích**: Bắt đầu quá trình cào dữ liệu  
**Input**: 
```json
{
    "category_urls": [
        "https://www.keyence.com.vn/products/sensor/photoelectric/",
        "https://www.keyence.com.vn/products/sensor/proximity/"
    ]
}
```
**Response**:
```json
{
    "success": true,
    "message": "Đã bắt đầu cào dữ liệu Keyence",
    "category_count": 2
}
```

### 3. GET /download-keyence-result/<folder_name>
**Mục đích**: Tải xuống kết quả dưới dạng ZIP  
**Response**: File ZIP chứa toàn bộ kết quả crawler

### 4. GET /list-keyence-results
**Mục đích**: Lấy danh sách kết quả crawler  
**Response**:
```json
{
    "success": true,
    "results": [
        {
            "folder_name": "KeyenceProduct_04082025_143022",
            "created_time": "04/08/2025 14:30",
            "category_count": 2,
            "product_count": 157,
            "image_count": 143
        }
    ]
}
```

## JavaScript Functionality

### Socket.IO Integration
```javascript
socket.on('progress_update', function (data) {
    updateProgress(data.percent, data.message, data.detail || '');
});

socket.on('crawler_completed', function (data) {
    showSuccess(data.message);
    loadResults();
    resetUI();
});

socket.on('crawler_error', function (data) {
    showError(data.message);
    resetUI();
});
```

### URL Validation
```javascript
const invalidUrls = urls.filter(url => !url.includes('keyence.com.vn'));
if (invalidUrls.length > 0) {
    showError(`Các URL không hợp lệ (phải chứa keyence.com.vn): ${invalidUrls.join(', ')}`);
    return;
}
```

### Real-time Progress
- Progress bar animation
- Real-time logs với timestamp
- Stats counter updates
- Timer tracking

## Khác biệt với Omron Crawler

### 1. Branding
- **Colors**: Đỏ-cam (Keyence) vs Xanh lam (Omron)
- **Icons**: Industry (`fas fa-industry`) vs Gear (`fas fa-gear`)
- **Badge**: "NO AI REQUIRED" thay vì Gemini AI features

### 2. Technical Differences
- **Không cần Gemini API**: Keyence crawler không dịch text, giữ nguyên tiếng Việt
- **Output folder**: `output_keyence` thay vì `output_omron`
- **URL validation**: `keyence.com.vn` thay vì `omron.co.uk`

### 3. Features Highlighting
- **White background processing**: Đặc biệt nhấn mạnh cho WordPress
- **WebP conversion**: 90% compression
- **Complex specs parsing**: 32+ specifications per product
- **HTML table format**: Match Keyence website structure

## File Structure
```
crawlerwebsitev3/
├── app/
│   ├── templates/
│   │   ├── keyence_crawler.html    # Giao diện chính
│   │   └── index.html              # Updated với Keyence tab
│   ├── routes.py                   # Updated với Keyence routes
│   └── crawlerKeyence.py          # Crawler backend
└── docs/
    └── KeyenceCrawler_Interface.md # Tài liệu này
```

## Usage Instructions

### 1. Truy cập Keyence Crawler
- Vào trang chủ: `http://localhost:5000/`
- Click tab "Cào dữ liệu Keyence.com.vn" hoặc
- Truy cập trực tiếp: `http://localhost:5000/keyence`

### 2. Nhập Category URLs
```
https://www.keyence.com.vn/products/sensor/photoelectric/
https://www.keyence.com.vn/products/sensor/proximity/
https://www.keyence.com.vn/products/sensor/measurement/
```

### 3. Bắt đầu Crawling
- Click "Bắt Đầu Cào Dữ Liệu"
- Theo dõi progress real-time
- Xem logs chi tiết

### 4. Tải Kết Quả
- Sau khi hoàn thành, click "Xem Kết Quả"
- Download ZIP files chứa Excel + Images

## Status
✅ **HOÀN THÀNH** - Giao diện Keyence Crawler đã sẵn sàng sử dụng

### Checklist
- [x] Template HTML với full responsive design
- [x] Backend routes integration
- [x] Socket.IO real-time progress
- [x] URL validation và error handling
- [x] Results listing và download
- [x] Navigation integration
- [x] Documentation hoàn chỉnh

## Next Steps
1. **Testing**: Test crawler với actual Keyence URLs
2. **Performance**: Monitor memory usage với large datasets  
3. **Enhancement**: Thêm filters cho results listing
4. **Deployment**: Cấu hình production environment
