#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script để kiểm tra tính năng load danh mục từ web thực bằng Selenium và requests
"""

import sys
import os
sys.path.append('.')

from app.misumicrawler import MisumiCrawler
import json
import time

class MockSocketIO:
    """Mock SocketIO để test"""
    def emit(self, event, data, namespace=None):
        print(f"[{event}] {data['percent']}% - {data['message']}")

def test_selenium_web_load():
    """Test load danh mục từ web bằng Selenium"""
    print("=" * 60)
    print("TEST: Load danh mục từ web thực bằng SELENIUM")
    print("=" * 60)
    
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    start_time = time.time()
    result = crawler.load_categories_with_selenium()
    end_time = time.time()
    
    if result and result.get('categories'):
        categories = result['categories']
        print(f"\n🎉 THÀNH CÔNG! (Thời gian: {end_time - start_time:.2f}s)")
        print(f"📊 Đã load {len(categories)} danh mục chính")
        print(f"🌐 Nguồn: {result.get('source', 'unknown')}")
        
        # Hiển thị danh sách danh mục
        print("\n📋 DANH SÁCH DANH MỤC CHÍNH:")
        total_subcats = 0
        total_sub_subcats = 0
        
        for i, cat in enumerate(categories, 1):
            subcat_count = len(cat.get('subcategories', []))
            total_subcats += subcat_count
            
            # Đếm sub-subcategories
            for subcat in cat.get('subcategories', []):
                total_sub_subcats += len(subcat.get('sub_subcategories', []))
            
            print(f"{i:2d}. {cat['name']} ({subcat_count} subcategories)")
        
        print(f"\n📊 TỔNG KẾT:")
        print(f"📁 Danh mục chính: {len(categories)}")
        print(f"📂 Danh mục con: {total_subcats}")
        print(f"📄 Sub-subcategories: {total_sub_subcats}")
        
        return True
    else:
        print("❌ THẤT BẠI!")
        return False

def test_requests_web_load():
    """Test load danh mục từ web bằng HTTP requests"""
    print("\n" + "=" * 60)
    print("TEST: Load danh mục từ web thực bằng HTTP REQUESTS")
    print("=" * 60)
    
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    start_time = time.time()
    result = crawler.load_categories_with_requests()
    end_time = time.time()
    
    if result and result.get('categories'):
        categories = result['categories']
        print(f"\n🎉 THÀNH CÔNG! (Thời gian: {end_time - start_time:.2f}s)")
        print(f"📊 Đã load {len(categories)} danh mục chính")
        print(f"🌐 Nguồn: {result.get('source', 'unknown')}")
        
        # Hiển thị danh sách danh mục
        print("\n📋 DANH SÁCH DANH MỤC CHÍNH:")
        total_subcats = 0
        
        for i, cat in enumerate(categories, 1):
            subcat_count = len(cat.get('subcategories', []))
            total_subcats += subcat_count
            print(f"{i:2d}. {cat['name']} ({subcat_count} subcategories)")
        
        print(f"\n📊 TỔNG KẾT:")
        print(f"📁 Danh mục chính: {len(categories)}")
        print(f"📂 Danh mục con: {total_subcats}")
        
        return True
    else:
        print("❌ THẤT BẠI!")
        return False

def test_combined_web_load():
    """Test load danh mục kết hợp (Selenium + requests fallback)"""
    print("\n" + "=" * 60)
    print("TEST: Load danh mục kết hợp (Selenium + requests)")
    print("=" * 60)
    
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    start_time = time.time()
    result = crawler.load_categories_from_web(prefer_selenium=True)
    end_time = time.time()
    
    if result and result.get('categories'):
        categories = result['categories']
        print(f"\n🎉 THÀNH CÔNG! (Thời gian: {end_time - start_time:.2f}s)")
        print(f"📊 Đã load {len(categories)} danh mục chính")
        print(f"🌐 Nguồn: {result.get('source', 'unknown')}")
        
        # Lưu kết quả vào file JSON
        backup_file = crawler.save_categories_to_file(categories, "web_categories_backup.json")
        if backup_file:
            print(f"💾 Đã lưu backup: {backup_file}")
        
        return True
    else:
        print("❌ THẤT BẠI!")
        return False

def test_main_load_structure():
    """Test method chính load_category_structure (ưu tiên web)"""
    print("\n" + "=" * 60)
    print("TEST: Method chính load_category_structure")
    print("=" * 60)
    
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    start_time = time.time()
    result = crawler.load_category_structure()
    end_time = time.time()
    
    if result and result.get('categories'):
        categories = result['categories']
        source = result.get('source', 'unknown')
        is_mock = result.get('is_mock', False)
        
        print(f"\n🎉 THÀNH CÔNG! (Thời gian: {end_time - start_time:.2f}s)")
        print(f"📊 Đã load {len(categories)} danh mục")
        print(f"🌐 Nguồn: {source}")
        print(f"🎭 Mock data: {'Có' if is_mock else 'Không'}")
        
        # Hiển thị thống kê chi tiết
        stats = crawler.get_category_statistics()
        if stats and 'error' not in stats:
            print(f"\n📊 THỐNG KÊ CHI TIẾT:")
            print(f"📁 Danh mục chính: {stats['total_main_categories']}")
            print(f"📂 Danh mục con: {stats['total_subcategories']}")
            print(f"📄 Sub-subcategories: {stats['total_sub_subcategories']}")
        
        return True
    else:
        print("❌ THẤT BẠI!")
        return False

def test_compare_methods():
    """So sánh hiệu suất các method"""
    print("\n" + "=" * 60)
    print("TEST: So sánh hiệu suất các method")
    print("=" * 60)
    
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    methods = [
        ("HTTP Requests", lambda: crawler.load_categories_with_requests()),
        ("Selenium", lambda: crawler.load_categories_with_selenium()),
        ("Combined (Selenium first)", lambda: crawler.load_categories_from_web(prefer_selenium=True)),
        ("Combined (Requests first)", lambda: crawler.load_categories_from_web(prefer_selenium=False)),
    ]
    
    results = []
    
    for method_name, method_func in methods:
        print(f"\n🧪 Đang test: {method_name}")
        try:
            start_time = time.time()
            result = method_func()
            end_time = time.time()
            
            duration = end_time - start_time
            success = bool(result and result.get('categories'))
            category_count = len(result.get('categories', [])) if success else 0
            source = result.get('source', 'failed') if success else 'failed'
            
            results.append({
                'method': method_name,
                'success': success,
                'duration': duration,
                'categories': category_count,
                'source': source
            })
            
            status = "✅" if success else "❌"
            print(f"{status} {method_name}: {duration:.2f}s - {category_count} danh mục")
            
        except Exception as e:
            results.append({
                'method': method_name,
                'success': False,
                'duration': 0,
                'categories': 0,
                'source': f'error: {str(e)}'
            })
            print(f"❌ {method_name}: LỖI - {str(e)}")
    
    # Tổng kết so sánh
    print(f"\n📊 BẢNG SO SÁNH:")
    print(f"{'Method':<25} {'Status':<8} {'Time':<8} {'Categories':<12} {'Source'}")
    print("-" * 70)
    
    for r in results:
        status = "✅ PASS" if r['success'] else "❌ FAIL"
        print(f"{r['method']:<25} {status:<8} {r['duration']:<8.2f} {r['categories']:<12} {r['source']}")
    
    return results

def main():
    """Chạy tất cả test"""
    print("🚀 BẮT ĐẦU TEST LOAD DANH MỤC TỪ WEB THỰC")
    print("🌐 " + "=" * 50)
    
    tests = [
        ("Load bằng Selenium", test_selenium_web_load),
        ("Load bằng HTTP Requests", test_requests_web_load),
        ("Load kết hợp", test_combined_web_load),
        ("Method chính", test_main_load_structure),
        ("So sánh hiệu suất", test_compare_methods),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            print(f"\n🧪 ĐANG CHẠY: {test_name}")
            result = test_func()
            results.append((test_name, result))
            
            if result:
                print(f"✅ {test_name}: THÀNH CÔNG")
            else:
                print(f"❌ {test_name}: THẤT BẠI")
                
        except Exception as e:
            print(f"💥 {test_name}: LỖI - {str(e)}")
            results.append((test_name, False))
    
    # Tổng kết
    print("\n" + "🏁 " + "=" * 50)
    print("📊 KẾT QUẢ TỔNG KẾT WEB LOAD:")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {test_name}")
    
    print(f"\n🎯 Tổng kết: {passed}/{total} tests passed")
    
    if passed >= total - 1:  # Cho phép 1 test fail
        print("🎉 LOAD TỪ WEB THỰC HOẠT ĐỘNG TỐT!")
    else:
        print("⚠️  Cần kiểm tra lại một số tính năng.")

if __name__ == "__main__":
    main() 