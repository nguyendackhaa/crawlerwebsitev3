import os
from app import create_app, socketio

# Đảm bảo thư mục logs tồn tại
logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(logs_dir, exist_ok=True)

app = create_app()

if __name__ == '__main__':
    socketio.run(app, host='127.0.0.1', port=5000, debug=True) 