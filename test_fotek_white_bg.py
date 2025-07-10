#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test tính năng lấy ảnh sản phẩm có nền trắng từ Fotek.com.tw
"""

import sys
import os
sys.path.append('.')

from app.crawlfotek import CrawlFotek

def test_validation():
    """Test validation URL ảnh"""
    print("🧪 TEST: Validation URL ảnh sản phẩm có nền trắng")
    print("=" * 60)
    
    crawler = CrawlFotek()
    
    # Test cases
    test_cases = [
        ("https://www.fotek.com.tw/image/catalog/product/Item/TC-NT-10R.png?v=1592418834", True, "Ảnh sản phẩm chính"),
        ("https://www.fotek.com.tw/image/catalog/product/spec/TC-NT-10R-SPE.png", False, "Ảnh thông số"),
        ("https://www.fotek.com.tw/image/catalog/product/wiring/TC-NT-10R-WD.png", False, "Ảnh sơ đồ"),
        ("https://www.fotek.com.tw/image/banner/logo.png", False, "Ảnh logo"),
    ]
    
    passed = 0
    
    for url, expected, desc in test_cases:
        result = crawler._is_white_background_product_image(url)
        status = "✅ PASS" if result == expected else "❌ FAIL"
        print(f"{desc}: {status}")
        if result == expected:
            passed += 1
    
    print(f"\n📊 Kết quả: {passed}/{len(test_cases)} test cases passed")
    return passed == len(test_cases)

if __name__ == "__main__":
    test_validation() 