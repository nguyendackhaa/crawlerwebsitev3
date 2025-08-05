"""
Test thực tế để đếm chính xác số lượng sản phẩm trong Photoelectric category
Không đoán mò - crawl thực tế từ website
"""

from app.crawlerAutonics import AutonicsCrawler
import time

def test_photoelectric_category_analysis():
    """Test thực tế category Photoelectric để đếm exact số sản phẩm"""
    print("🔍 PHÂN TÍCH THỰC TẾ PHOTOELECTRIC CATEGORY")
    print("=" * 80)
    
    crawler = AutonicsCrawler()
    category_url = "https://www.autonics.com/vn/product/category/Photoelectric"
    
    try:
        # Step 1: Extract all series từ category
        print("📋 BƯỚC 1: Lấy danh sách series từ category...")
        series_urls = crawler.extract_series_from_category(category_url)
        total_series = len(series_urls)
        
        print(f"✅ Tìm thấy {total_series} series trong Photoelectric category")
        
        if total_series == 0:
            print("❌ Không tìm thấy series nào, dừng test")
            return
        
        # Step 2: Analyze từng series để đếm products
        print(f"\n📊 BƯỚC 2: Phân tích {total_series} series để đếm sản phẩm...")
        print("-" * 80)
        
        total_products = 0
        series_analysis = []
        
        for i, series_url in enumerate(series_urls, 1):
            try:
                print(f"\n🔄 [{i}/{total_series}] Analyzing: {series_url}")
                
                # Extract series name từ URL
                series_name = series_url.split('/')[-1]
                
                # Count products trong series này
                products = crawler.extract_products_from_series(series_url)
                product_count = len(products)
                
                total_products += product_count
                series_analysis.append({
                    'series_name': series_name,
                    'url': series_url,
                    'product_count': product_count
                })
                
                print(f"   📦 {series_name}: {product_count} sản phẩm")
                
                # Small delay để tránh rate limiting
                time.sleep(2)
                
            except Exception as e:
                print(f"   ❌ Lỗi khi analyze {series_url}: {str(e)}")
                continue
        
        # Step 3: Tổng hợp kết quả
        print("\n" + "=" * 80)
        print("📈 KẾT QUẢ PHÂN TÍCH THỰC TẾ")
        print("=" * 80)
        
        print(f"🎯 Total Series: {total_series}")
        print(f"🎯 Total Products: {total_products}")
        print(f"🎯 Average Products/Series: {total_products/total_series:.1f}")
        
        # Top 10 series có nhiều sản phẩm nhất
        print(f"\n📊 TOP 10 SERIES CÓ NHIỀU SẢN PHẨM NHẤT:")
        top_series = sorted(series_analysis, key=lambda x: x['product_count'], reverse=True)[:10]
        
        for i, series in enumerate(top_series, 1):
            print(f"   {i:2d}. {series['series_name']:20s}: {series['product_count']:3d} sản phẩm")
        
        # Series có ít sản phẩm nhất
        print(f"\n📊 TOP 5 SERIES CÓ ÍT SẢN PHẨM NHẤT:")
        bottom_series = sorted(series_analysis, key=lambda x: x['product_count'])[:5]
        
        for i, series in enumerate(bottom_series, 1):
            print(f"   {i:2d}. {series['series_name']:20s}: {series['product_count']:3d} sản phẩm")
        
        # Distribution analysis
        print(f"\n📊 PHÂN PHỐI SỐ LƯỢNG SẢN PHẨM:")
        ranges = [
            (0, 10, "1-10 sản phẩm"),
            (11, 50, "11-50 sản phẩm"), 
            (51, 100, "51-100 sản phẩm"),
            (101, 999, "100+ sản phẩm")
        ]
        
        for min_count, max_count, label in ranges:
            count = len([s for s in series_analysis if min_count <= s['product_count'] <= max_count])
            percentage = (count / total_series * 100) if total_series > 0 else 0
            print(f"   {label:20s}: {count:2d} series ({percentage:5.1f}%)")
        
        # Save detailed results
        print(f"\n💾 CHI TIẾT TẤT CẢ SERIES:")
        for series in sorted(series_analysis, key=lambda x: x['series_name']):
            print(f"   {series['series_name']:25s}: {series['product_count']:3d} sản phẩm")
            
    except Exception as e:
        print(f"❌ Lỗi khi test category: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_photoelectric_category_analysis()