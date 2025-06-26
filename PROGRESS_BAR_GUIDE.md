# 🚀 Hệ thống Terminal Progress Bar

Hệ thống thanh tiến trình terminal chi tiết cho ứng dụng CrawlerWebsite với đầy đủ tính năng theo yêu cầu.

## ✨ Tính năng chính

### 1. **Hiển thị chi tiết**
- ✅ Phần trăm hoàn thành (0-100%)
- ⏱️ Thời gian đã trôi qua và ước tính còn lại (ETA)
- 📝 Tên function đang thực thi
- 📊 Thống kê chi tiết cuối mỗi operation

### 2. **Nested Operations**
- 🌳 Hỗ trợ operations lồng nhau với indentation levels
- 👶 Progress bars con cho sub-tasks
- 🔗 Quản lý mối quan hệ parent-child

### 3. **Color Coding**
- 🟢 **Xanh lá**: Thành công
- 🟡 **Vàng**: Cảnh báo
- 🔴 **Đỏ**: Lỗi
- 🔵 **Xanh dương**: Thông tin
- 🔷 **Cyan**: Tiến trình

### 4. **Tóm tắt hệ thống**
- 📋 Báo cáo tổng quan cuối session
- 📈 Thống kê tỷ lệ thành công/thất bại
- ⏰ Tổng thời gian xử lý

### 5. **Reusable Module**
- 🎯 Decorator `@progress_tracker` để wrap bất kỳ function nào
- 🔧 Utility functions: `simple_progress`, `batch_progress`
- 🧩 Dễ dàng tích hợp vào code hiện có

### 6. **Clean Output**
- 🧹 Xử lý output sạch sẽ ngay cả với concurrent operations
- 🔄 Thread-safe với multiple operations
- 🎨 Format đẹp với ANSI colors

### 7. **Chế độ hiển thị**
- 📖 **Verbose**: Hiển thị đầy đủ chi tiết
- 📄 **Minimal**: Chỉ hiển thị thông tin cần thiết

### 8. **Edge Cases**
- ⚡ Xử lý functions chạy rất nhanh (< 1s)
- 🐌 Xử lý functions chạy rất lâu (> 1h)
- 🛡️ Error handling an toàn

## 🎯 Cách sử dụng

### 1. **Decorator đơn giản**

```python
from app.progress_bar import progress_tracker, TerminalProgressBar

@progress_tracker(name="Tên công việc", total_steps=100, verbose=True)
def my_function(progress: TerminalProgressBar):
    progress.update(25, "Đang xử lý bước 1", "Chi tiết công việc")
    # ... làm việc gì đó ...
    
    progress.update(75, "Đang xử lý bước 2")
    # ... làm việc gì đó ...
    
    # Progress sẽ tự complete khi function kết thúc
```

### 2. **Progress bar thủ công**

```python
from app.progress_bar import simple_progress

progress = simple_progress("Tên công việc", 100, verbose=True)
progress.update(50, "Đang xử lý...", "Chi tiết thêm")
progress.complete("success", "Hoàn thành!", {"Items": 100, "Errors": 0})
```

### 3. **Nested operations**

```python
from app.progress_bar import simple_progress, create_child_progress

main_progress = simple_progress("Công việc chính", 100)
main_progress.update(20, "Bắt đầu sub-task")

# Tạo progress bar con
sub_progress = create_child_progress(main_progress, "Sub-task", 50)
for i in range(10):
    sub_progress.update((i+1)*5, f"Item {i+1}/10")

sub_progress.complete("success", "Sub-task hoàn tất")
main_progress.complete("success", "Tất cả hoàn tất")
```

### 4. **Batch processing**

```python
from app.progress_bar import batch_progress

def process_item(item):
    # Xử lý một item
    return f"processed_{item}"

items = ["item1", "item2", "item3"]
results = batch_progress(items, "Xử lý batch", process_item)
```

### 5. **Error handling**

```python
progress = simple_progress("Công việc có lỗi", 100)
try:
    progress.update(50, "Đang xử lý...")
    # ... code có thể lỗi ...
    progress.complete("success", "Thành công")
except Exception as e:
    progress.error(f"Lỗi: {str(e)}", "Chi tiết lỗi...")
```

## 🎨 Ví dụ output

```
[15:30:25] 🚀 Cào dữ liệu BAA.vn - Bắt đầu...
[15:30:26] [████████████████████] 100.0% (100/100) ⏱ 1.2s | Đã cào 150 sản phẩm
  [15:30:25] 🚀 BAA Product Crawler - Bắt đầu...
  [15:30:26] [████████████████████] 100.0% (70/70) ⏱ 0.8s | Cào dữ liệu hoàn tất
  [15:30:26] ✅ BAA Product Crawler - Cào dữ liệu hoàn tất (⏱ 0.8s)
    📊 Sản phẩm đã cào: 150
    📊 Thư mục kết quả: /path/to/result
[15:30:26] ✅ Cào dữ liệu BAA.vn - Cào dữ liệu BAA.vn hoàn tất (⏱ 1.2s)
  📊 URLs đầu vào: 5
  📊 Sản phẩm cào được: 150
  📊 Kích thước ZIP: 15.2 MB
```

## 🔧 Tích hợp với routes hiện có

Các routes đã được tích hợp progress tracking:

### ✅ Đã tích hợp
- `extract_links` - Trích xuất liên kết sản phẩm
- `crawl_baa` - Cào dữ liệu BAA.vn  
- `download_images` - Tải ảnh sản phẩm
- `filter_products` - Lọc sản phẩm

### 🔄 Cần tích hợp thêm
- `convert_to_webp` - Chuyển đổi ảnh WebP
- `compare_categories` - So sánh danh mục
- `download_documents` - Tải tài liệu
- `categorize_products` - Phân loại sản phẩm

## 🧪 Test Demo

Chạy file demo để xem hệ thống hoạt động:

```bash
python test_progress_demo.py
```

Demo sẽ hiển thị:
- ✅ Function với decorator
- 🌳 Nested operations  
- ❌ Error handling
- 📦 Batch processing
- 🔄 Concurrent operations
- 🎨 Different status types

## 📊 Tóm tắt tổng quan

Cuối mỗi session, hệ thống sẽ hiển thị:

```
============================================================
📋 TÓM TẮT TỔNG QUAN HỆ THỐNG
============================================================
⏱️  Tổng thời gian: 2m15s
🎯 Tổng số operations: 5
✅ Thành công: 4
❌ Thất bại: 1
📊 Tỷ lệ thành công: 80.0%
============================================================
```

## 🔗 Kết hợp với SocketIO

Hệ thống terminal progress được thiết kế để hoạt động song song với SocketIO:

- **Terminal Progress**: Cho admin/developer theo dõi
- **SocketIO**: Cho end users trên web interface

```python
# Cả hai sẽ được cập nhật đồng thời
progress.update(50, "Đang xử lý...")
socketio.emit('progress_update', {'percent': 50, 'message': 'Đang xử lý...'})
```

## 🎯 Best Practices

### 1. **Tên progress rõ ràng**
```python
# ✅ Tốt
@progress_tracker(name="Cào dữ liệu BAA.vn", total_steps=100)

# ❌ Không tốt  
@progress_tracker(name="Process", total_steps=100)
```

### 2. **Update tiến trình đều đặn**
```python
# ✅ Tốt - Update đều đặn
for i, item in enumerate(items):
    progress.update((i+1)/len(items)*100, f"Xử lý {i+1}/{len(items)}")

# ❌ Không tốt - Không update
for item in items:
    process(item)  # Không có progress update
```

### 3. **Nested operations hợp lý**
```python
# ✅ Tốt - Nested logic rõ ràng
main_progress = simple_progress("Công việc chính", 100)
sub_progress = create_child_progress(main_progress, "Sub-task", 50)

# ❌ Không tốt - Quá nhiều levels
# level 1 -> level 2 -> level 3 -> level 4 (quá sâu)
```

### 4. **Error handling đầy đủ**
```python
# ✅ Tốt
try:
    # ... code ...
    progress.complete("success", "Hoàn thành")
except Exception as e:
    progress.error(f"Lỗi: {str(e)}", traceback.format_exc())
```

## 🚀 Performance

- ⚡ **Fast**: Chỉ update terminal khi cần thiết (> 0.5s interval)
- 🧵 **Thread-safe**: An toàn với multiple threads
- 💾 **Memory efficient**: Tự động cleanup instances
- 🎨 **Clean**: Không ảnh hưởng performance của main tasks

---

**Hệ thống Terminal Progress Bar đã sẵn sàng sử dụng! 🎉** 