#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script để kiểm tra tính năng load danh mục từ file category.txt
"""

import sys
import os
sys.path.append('.')

from app.misumicrawler import MisumiCrawler
import json

class MockSocketIO:
    """Mock SocketIO để test"""
    def emit(self, event, data, namespace=None):
        print(f"[{event}] {data['percent']}% - {data['message']}")

def test_load_categories_from_file():
    """Test load danh mục từ file category.txt"""
    print("=" * 60)
    print("TEST: Load danh mục từ file category.txt")
    print("=" * 60)
    
    # Tạo crawler instance
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    # Test load từ file
    result = crawler.load_categories_from_file("category.txt")
    
    if result and result.get('categories'):
        categories = result['categories']
        print(f"\n✅ Thành công! Đã load {len(categories)} danh mục chính")
        print(f"📄 Nguồn: {result.get('source', 'unknown')}")
        
        # Hiển thị danh sách danh mục
        print("\n📋 DANH SÁCH DANH MỤC CHÍNH:")
        for i, cat in enumerate(categories, 1):
            subcat_count = len(cat.get('subcategories', []))
            print(f"{i:2d}. {cat['name']} ({subcat_count} danh mục con)")
            print(f"    🔗 {cat['url']}")
            print(f"    🆔 {cat['id']}")
        
        # Hiển thị chi tiết 1 danh mục
        if categories:
            print(f"\n📊 CHI TIẾT DANH MỤC ĐẦU TIÊN: {categories[0]['name']}")
            subcategories = categories[0].get('subcategories', [])
            for j, subcat in enumerate(subcategories[:5], 1):  # Hiển thị 5 đầu
                sub_subcat_count = len(subcat.get('sub_subcategories', []))
                print(f"  {j}. {subcat['name']} ({sub_subcat_count} items)")
                print(f"     🔗 {subcat['url']}")
                
                # Hiển thị sub-subcategories
                for k, sub_subcat in enumerate(subcat.get('sub_subcategories', [])[:3], 1):
                    print(f"     {k}. {sub_subcat['name']}")
            
            if len(subcategories) > 5:
                print(f"  ... và {len(subcategories) - 5} danh mục con khác")
    
    else:
        print("❌ Không thể load danh mục từ file!")
        return False
    
    return True

def test_category_statistics():
    """Test thống kê danh mục"""
    print("\n" + "=" * 60)
    print("TEST: Thống kê danh mục")
    print("=" * 60)
    
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    # Load danh mục trước
    result = crawler.load_categories_from_file("category.txt")
    
    if result and result.get('categories'):
        # Lấy thống kê
        stats = crawler.get_category_statistics()
        
        print("📊 THỐNG KÊ DANH MỤC:")
        print(f"📁 Tổng danh mục chính: {stats['total_main_categories']}")
        print(f"📂 Tổng danh mục con: {stats['total_subcategories']}")
        print(f"📄 Tổng sub-subcategories: {stats['total_sub_subcategories']}")
        print(f"🌐 Nguồn dữ liệu: {stats['source']}")
        
        print("\n📋 CHI TIẾT TỪNG DANH MỤC:")
        for detail in stats['categories_details'][:10]:  # Top 10
            print(f"• {detail['name']}: {detail['subcategories']} sub, {detail['sub_subcategories']} sub-sub")
        
        return True
    
    return False

def test_save_categories():
    """Test lưu danh mục vào file JSON"""
    print("\n" + "=" * 60)
    print("TEST: Lưu danh mục vào file JSON")
    print("=" * 60)
    
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    # Load danh mục
    result = crawler.load_categories_from_file("category.txt")
    
    if result and result.get('categories'):
        categories = result['categories']
        
        # Lưu vào file JSON
        backup_file = crawler.save_categories_to_file(categories, "misumi_categories_backup.json")
        
        if backup_file:
            print(f"✅ Đã lưu danh mục vào: {backup_file}")
            
            # Kiểm tra file đã được tạo
            if os.path.exists(backup_file):
                file_size = os.path.getsize(backup_file)
                print(f"📁 Kích thước file: {file_size:,} bytes")
                
                # Đọc lại để kiểm tra
                with open(backup_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                print(f"✅ Kiểm tra file: {len(data['categories'])} danh mục được lưu")
                return True
        
    return False

def test_load_category_structure():
    """Test method load_category_structure (ưu tiên file, fallback website)"""
    print("\n" + "=" * 60)
    print("TEST: Load category structure (file -> website)")
    print("=" * 60)
    
    socketio = MockSocketIO()
    crawler = MisumiCrawler(socketio)
    
    # Test method chính
    result = crawler.load_category_structure()
    
    if result and result.get('categories'):
        categories = result['categories']
        source = result.get('source', 'unknown')
        
        print(f"✅ Thành công load {len(categories)} danh mục")
        print(f"🌐 Nguồn: {source}")
        
        # Hiển thị vài danh mục đầu
        print("\n📋 DANH MỤC ĐẦU TIÊN:")
        for cat in categories[:3]:
            print(f"• {cat['name']} - {len(cat.get('subcategories', []))} subcategories")
        
        return True
    
    return False

def main():
    """Chạy tất cả test"""
    print("🚀 BẮT ĐẦU TEST MISUMI CRAWLER")
    print("🕐 " + "=" * 50)
    
    tests = [
        ("Load từ file category.txt", test_load_categories_from_file),
        ("Thống kê danh mục", test_category_statistics),
        ("Lưu vào file JSON", test_save_categories),
        ("Load category structure", test_load_category_structure),
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
    print("📊 KẾT QUẢ TỔNG KẾT:")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {test_name}")
    
    print(f"\n🎯 Tổng kết: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 TẤT CẢ TEST ĐỀU THÀNH CÔNG!")
    else:
        print("⚠️  Một số test chưa thành công, cần kiểm tra lại.")

if __name__ == "__main__":
    main() 