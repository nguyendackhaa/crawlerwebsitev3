#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Routes cho HoplongCrawler
"""

from flask import Blueprint, render_template, request, jsonify, send_file
from datetime import datetime
import threading
import os
import time

# Tạo blueprint cho hoplong routes
hoplong_bp = Blueprint('hoplong', __name__)

def register_hoplong_routes(app, socketio):
    """
    Đăng ký các routes cho HoplongCrawler
    
    Args:
        app: Flask app instance
        socketio: SocketIO instance
    """
    
    @app.route('/hoplong')
    def hoplong_crawler():
        """Giao diện crawler cho HoplongTech.com"""
        return render_template('hoplong_crawler.html')

    @app.route('/api/hoplong/categories', methods=['GET'])
    def get_hoplong_categories():
        """API lấy danh sách danh mục cảm biến từ HoplongTech"""
        try:
            from .crawlhoplong import HoplongCrawler
            
            crawler = HoplongCrawler(socketio)
            result = crawler.get_category_selection_interface()
            return jsonify(result)
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @app.route('/api/hoplong/crawl', methods=['POST'])
    def start_hoplong_crawl():
        """API bắt đầu crawl dữ liệu từ HoplongTech"""
        try:
            from .crawlhoplong import HoplongCrawler
            
            data = request.get_json()
            print(f"[DEBUG] Received crawl request: {data}")
            
            selected_categories = data.get('categories', [])
            max_products_per_category = data.get('max_products_per_category')
            
            print(f"[DEBUG] Selected categories: {len(selected_categories)}")
            print(f"[DEBUG] Max products per category: {max_products_per_category}")
            
            if not selected_categories:
                return jsonify({
                    'success': False,
                    'error': 'Vui lòng chọn ít nhất một danh mục'
                }), 400
            
            # Tạo thư mục output
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_dir = f"hoplongtech_crawl_{timestamp}"
            
            def crawl_thread():
                """Thread function để crawl dữ liệu"""
                try:
                    print(f"[DEBUG] Starting crawl thread...")
                    crawler = HoplongCrawler(socketio)
                    
                    # Emit tiến trình bắt đầu
                    socketio.emit('progress_update', {
                        'percent': 0,
                        'message': 'Bắt đầu cào dữ liệu...'
                    })
                    socketio.emit('log_message', {
                        'message': f'Khởi tạo crawler cho {len(selected_categories)} danh mục',
                        'type': 'info'
                    })
                    
                    results = crawler.crawl_category_products(
                        selected_categories=selected_categories,
                        output_dir=output_dir,
                        max_products_per_category=max_products_per_category
                    )
                    
                    print(f"[DEBUG] Crawl completed. Results: {results.get('successful_products', 0)}/{results.get('total_products', 0)}")
                    
                    # Tạo download link nếu có file ZIP
                    download_link = None
                    if results.get('download_file'):
                        filename = os.path.basename(results['download_file'])
                        download_link = f"/download/hoplong/{filename}"
                    
                    # Emit kết quả cuối cùng với thống kê chi tiết
                    socketio.emit('crawl_complete', {
                        'success': True,
                        'results': results,
                        'download_link': download_link,
                        'statistics': {
                            'total_products': results.get('total_products', 0),
                            'successful_products': results.get('successful_products', 0),
                            'failed_products': results.get('failed_products', 0),
                            'success_rate': round(results.get('successful_products', 0) / results.get('total_products', 1) * 100, 1),
                            'brands_count': len(results.get('brands_data', {})),
                            'categories_count': results.get('total_categories', 0),
                            'crawl_duration': f"{time.time() - results.get('start_time', time.time()):.1f}s"
                        },
                        'message': f'🎉 Crawling hoàn thành! {results["successful_products"]}/{results["total_products"]} sản phẩm thành công!'
                    })
                    
                except Exception as e:
                    print(f"[ERROR] Crawl thread error: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    
                    socketio.emit('crawl_complete', {
                        'success': False,
                        'error': str(e),
                        'message': f'Lỗi crawling: {str(e)}'
                    })
                    socketio.emit('log_message', {
                        'message': f'Lỗi nghiêm trọng: {str(e)}',
                        'type': 'error'
                    })
            
            # Bắt đầu crawl trong background thread
            thread = threading.Thread(target=crawl_thread)
            thread.daemon = True
            thread.start()
            
            print(f"[DEBUG] Crawl thread started successfully")
            
            return jsonify({
                'success': True,
                'message': 'Đã bắt đầu crawl dữ liệu HoplongTech',
                'output_dir': output_dir
            })
            
        except Exception as e:
            print(f"[ERROR] API error: {str(e)}")
            import traceback
            traceback.print_exc()
            
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @app.route('/download/hoplong/<filename>')
    def download_hoplong_file(filename):
        """Download file kết quả crawling HoplongTech"""
        try:
            # Đảm bảo file tồn tại và an toàn
            if not filename.endswith('.zip'):
                return jsonify({'error': 'Chỉ hỗ trợ download file ZIP'}), 400
            
            file_path = os.path.join(os.getcwd(), filename)
            
            if not os.path.exists(file_path):
                return jsonify({'error': 'File không tồn tại'}), 404
            
            return send_file(file_path, as_attachment=True, download_name=filename)
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500 