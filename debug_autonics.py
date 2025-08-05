"""
Debug script để test Autonics crawler và inspect HTML structure
"""

import requests
from bs4 import BeautifulSoup
import time

def debug_category_page(url):
    """Debug category page để hiểu cấu trúc HTML"""
    print(f"\n=== DEBUGGING CATEGORY PAGE: {url} ===")
    
    # Setup session với headers
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    session.headers.update(headers)
    
    try:
        # Get page
        print("🔍 Đang tải trang...")
        response = session.get(url, timeout=30)
        response.raise_for_status()
        print(f"✅ Response status: {response.status_code}")
        print(f"📏 Content length: {len(response.text)} characters")
        
        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Save HTML for inspection
        with open('debug_category.html', 'w', encoding='utf-8') as f:
            f.write(response.text)
        print("📝 HTML saved to debug_category.html")
        
        # Inspect various selectors
        print("\n🔍 INSPECTING HTML STRUCTURE:")
        
        # Try to find series links
        print("\n1. Looking for series links with '/vn/series/' pattern:")
        series_links = soup.find_all('a', href=lambda x: x and '/vn/series/' in x)
        print(f"   Found {len(series_links)} series links")
        for i, link in enumerate(series_links[:5]):  # Show first 5
            print(f"   - {link.get('href')}")
            if i >= 4:
                print(f"   ... and {len(series_links) - 5} more")
                break
        
        # Try different selectors
        print("\n2. Looking for list items with links:")
        li_with_links = soup.find_all('li')
        series_in_li = []
        for li in li_with_links:
            a_tag = li.find('a')
            if a_tag and a_tag.get('href') and '/vn/series/' in a_tag.get('href'):
                series_in_li.append(a_tag.get('href'))
        print(f"   Found {len(series_in_li)} series in <li> tags")
        for href in series_in_li[:5]:
            print(f"   - {href}")
        
        # Look for specific class patterns
        print("\n3. Looking for common class patterns:")
        potential_containers = [
            'list-wrap', 'series-list', 'product-list', 'category-list',
            'grid', 'series-grid', 'items'
        ]
        
        for class_name in potential_containers:
            elements = soup.find_all(class_=lambda x: x and class_name in str(x).lower())
            if elements:
                print(f"   Found elements with class containing '{class_name}': {len(elements)}")
                for elem in elements[:2]:
                    print(f"     - {elem.name} class='{elem.get('class')}'")
        
        # Look for pagination
        print("\n4. Looking for pagination:")
        pagination_selectors = [
            'paging-wrap', 'pagination', 'page-nav', 'pager'
        ]
        for selector in pagination_selectors:
            elements = soup.find_all(class_=lambda x: x and selector in str(x).lower())
            if elements:
                print(f"   Found pagination with class containing '{selector}': {len(elements)}")
        
        # Look for main content area
        print("\n5. Looking for main content areas:")
        main_areas = soup.find_all(['main', 'section', 'div'], class_=lambda x: x and any(keyword in str(x).lower() for keyword in ['content', 'main', 'body', 'container']))
        print(f"   Found {len(main_areas)} potential main content areas")
        
        # Get page title for context
        title = soup.find('title')
        if title:
            print(f"\n📄 Page title: {title.get_text(strip=True)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_specific_selectors(url):
    """Test __INIT_DATA__ extraction method"""
    print(f"\n=== TESTING __INIT_DATA__ EXTRACTION FOR: {url} ===")
    
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    session.headers.update(headers)
    
    try:
        response = session.get(url, timeout=30)
        html = response.text
        
        print("\n🎯 Testing __INIT_DATA__ extraction:")
        
        # Extract from window.__INIT_DATA__
        start_marker = 'window.__INIT_DATA__ = '
        start_idx = html.find(start_marker)
        
        if start_idx == -1:
            print("❌ Không tìm thấy window.__INIT_DATA__")
            return []
        
        json_start = start_idx + len(start_marker)
        json_end = html.find(';\n', json_start)
        if json_end == -1:
            json_end = html.find(';', json_start)
        
        if json_end == -1:
            print("❌ Không tìm thấy điểm kết thúc JSON")
            return []
        
        json_str = html[json_start:json_end].strip()
        
        import json
        data = json.loads(json_str)
        
        # Get resultList
        result_list = data.get('resultList', [])
        
        print(f"✅ Found {len(result_list)} series trong __INIT_DATA__")
        
        series_items = []
        for item in result_list:
            url_name = item.get('urlNm', '')
            series_name = item.get('seriesNm', '')
            if url_name:
                series_items.append({
                    'urlNm': url_name,
                    'seriesNm': series_name,
                    'full_url': f"https://www.autonics.com/vn/series/{url_name}",
                    'imageUrl': item.get('imageUrl', '')
                })
        
        print(f"\nSeries found:")
        for item in series_items[:5]:
            print(f"  - {item['full_url']} -> {item['seriesNm']}")
        
        if len(series_items) > 5:
            print(f"  ... and {len(series_items) - 5} more")
        
        # Check pagination
        pagination_info = data.get('paginationInfo', {})
        if pagination_info:
            current_page = pagination_info.get('currentPageNo', 1)
            total_pages = pagination_info.get('totalPageCount', 1)
            print(f"\n📄 Pagination: {current_page}/{total_pages}")
        
        return series_items
        
    except Exception as e:
        print(f"❌ Error testing __INIT_DATA__: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

if __name__ == "__main__":
    test_urls = [
        "https://www.autonics.com/vn/product/category/Photoelectric",
        "https://www.autonics.com/vn/product/category/Proximity"
    ]
    
    for url in test_urls:
        print("="*80)
        debug_category_page(url)
        time.sleep(2)  # Be nice to the server
        
        series_found = test_specific_selectors(url)
        print(f"\n📊 Summary for {url}:")
        print(f"   Series found: {len(series_found)}")
        
        time.sleep(3)  # Wait between requests
    
    print("\n" + "="*80)
    print("🏁 Debug completed. Check debug_category.html for full HTML content.")