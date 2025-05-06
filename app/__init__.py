from flask import Flask
import os
from flask_socketio import SocketIO

# Tạo đối tượng SocketIO
socketio = SocketIO()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'crawlbot_secret_key'
    
    # Tạo thư mục uploads/downloads nếu chưa tồn tại
    downloads_dir = os.path.join(app.root_path, 'downloads')
    os.makedirs(downloads_dir, exist_ok=True)
    app.config['UPLOAD_FOLDER'] = downloads_dir
    
    # Tạo thư mục logs nếu chưa tồn tại
    logs_dir = os.path.join(os.path.dirname(app.root_path), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    
    # Đăng ký blueprint
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    # Khởi tạo SocketIO với ứng dụng Flask
    socketio.init_app(app, cors_allowed_origins="*")
    
    return app 