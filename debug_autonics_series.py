"""
Debug script để test Autonics series page với Selenium WebDriver
"""

import requests
from bs4 import BeautifulSoup
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

def test_series_page_with_requests(url):
    """Test series page với requests thông thường"""
    print(f"\n=== TESTING SERIES PAGE WITH REQUESTS: {url} ===")
    
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    session.headers.update(headers)
    
    try:
        response = session.get(url, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        print(f"✅ Response status: {response.status_code}")
        print(f"📏 Content length: {len(response.text)} characters")
        
        # Tìm __INIT_DATA__
        start_marker = 'window.__INIT_DATA__ = '
        start_idx = response.text.find(start_marker)
        
        if start_idx != -1:
            print("✅ Found window.__INIT_DATA__ với requests")
            
            json_start = start_idx + len(start_marker)
            json_end = response.text.find(';\n', json_start)
            if json_end == -1:
                json_end = response.text.find(';', json_start)
            
            if json_end != -1:
                json_str = response.text[json_start:json_end].strip()
                
                import json
                data = json.loads(json_str)
                
                models = data.get('modelList', data.get('resultList', []))
                print(f"📦 Found {len(models)} models trong __INIT_DATA__")
                
                if models:
                    for i, model in enumerate(models[:3]):
                        model_code = model.get('modlCode', model.get('modelCode', ''))
                        model_name = model.get('modlNm', model.get('modelName', ''))
                        print(f"  {i+1}. {model_code} - {model_name}")
                        
                    if len(models) > 3:
                        print(f"  ... and {len(models) - 3} more")
        else:
            print("❌ Không tìm thấy window.__INIT_DATA__ với requests")
            
            # Fallback: tìm product links trong HTML
            print("\n🔍 Looking for product links in HTML:")
            
            # Tìm các link model
            model_links = soup.find_all('a', href=lambda x: x and '/vn/model/' in x)
            print(f"Found {len(model_links)} model links")
            
            for i, link in enumerate(model_links[:5]):
                print(f"  {i+1}. {link.get('href')} - {link.get_text(strip=True)[:50]}")
            
            # Tìm section series-model
            series_model = soup.find('section', id='series-model')
            if series_model:
                print("✅ Found section#series-model")
                model_items = series_model.find_all('li')
                print(f"Found {len(model_items)} <li> items trong series-model")
            else:
                print("❌ Không tìm thấy section#series-model")
        
        return response.text
        
    except Exception as e:
        print(f"❌ Error with requests: {str(e)}")
        return None

def test_series_page_with_selenium(url):
    """Test series page với Selenium WebDriver"""
    print(f"\n=== TESTING SERIES PAGE WITH SELENIUM: {url} ===")
    
    # Cấu hình Chrome options
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.implicitly_wait(10)
        
        print("🚗 Starting Chrome WebDriver...")
        driver.get(url)
        
        # Đợi page load
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        print("✅ Page loaded with Selenium")
        
        # Đợi thêm một chút cho Vue.js render
        time.sleep(3)
        
        # Lấy HTML sau khi JavaScript render
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        print(f"📏 Selenium content length: {len(html)} characters")
        
        # Tìm __INIT_DATA__
        start_marker = 'window.__INIT_DATA__ = '
        start_idx = html.find(start_marker)
        
        if start_idx != -1:
            print("✅ Found window.__INIT_DATA__ với Selenium")
            
            json_start = start_idx + len(start_marker)
            json_end = html.find(';\n', json_start)
            if json_end == -1:
                json_end = html.find(';', json_start)
            
            if json_end != -1:
                json_str = html[json_start:json_end].strip()
                
                import json
                data = json.loads(json_str)
                
                models = data.get('modelList', data.get('resultList', []))
                print(f"📦 Found {len(models)} models trong __INIT_DATA__")
                
                if models:
                    for i, model in enumerate(models[:3]):
                        model_code = model.get('modlCode', model.get('modelCode', ''))
                        model_name = model.get('modlNm', model.get('modelName', ''))
                        print(f"  {i+1}. {model_code} - {model_name}")
                        
                    if len(models) > 3:
                        print(f"  ... and {len(models) - 3} more")
        else:
            print("❌ Không tìm thấy window.__INIT_DATA__ với Selenium")
            
            # Fallback: tìm các elements đã render
            print("\n🔍 Looking for rendered elements:")
            
            # Tìm section series-model
            try:
                series_model = driver.find_element(By.ID, 'series-model')
                print("✅ Found section#series-model với Selenium")
                
                model_items = series_model.find_elements(By.TAG_NAME, 'li')
                print(f"Found {len(model_items)} <li> items trong series-model")
                
                if model_items:
                    for i, item in enumerate(model_items[:3]):
                        try:
                            link = item.find_element(By.TAG_NAME, 'a')
                            title = item.find_element(By.CLASS_NAME, 'title')
                            print(f"  {i+1}. {link.get_attribute('href')} - {title.text}")
                        except:
                            print(f"  {i+1}. (couldn't extract details)")
                            
            except NoSuchElementException:
                print("❌ Không tìm thấy section#series-model với Selenium")
        
        # Save HTML for inspection
        with open('debug_series_selenium.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print("📝 Selenium HTML saved to debug_series_selenium.html")
        
        return html
        
    except Exception as e:
        print(f"❌ Error with Selenium: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
        
    finally:
        if driver:
            driver.quit()
            print("🚗 Chrome WebDriver closed")

if __name__ == "__main__":
    # Test với một series URL thực tế
    test_urls = [
        "https://www.autonics.com/vn/series/BY",
        "https://www.autonics.com/vn/series/PRFD-K"
    ]
    
    for url in test_urls:
        print("="*80)
        
        # Test với requests trước
        requests_html = test_series_page_with_requests(url)
        
        time.sleep(2)
        
        # Test với Selenium
        selenium_html = test_series_page_with_selenium(url)
        
        # So sánh
        if requests_html and selenium_html:
            print(f"\n📊 Comparison for {url}:")
            print(f"  Requests content length: {len(requests_html)}")
            print(f"  Selenium content length: {len(selenium_html)}")
            print(f"  Difference: {len(selenium_html) - len(requests_html)} characters")
        
        time.sleep(3)  # Wait between URLs
    
    print("\n" + "="*80)
    print("🏁 Debug completed. Check debug_series_selenium.html for Selenium content.")