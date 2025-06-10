from flask import Blueprint

# Tạo các blueprint cho từng module
main_bp = Blueprint('main', __name__)

# Import các route để đăng ký với blueprint tương ứng
from app.routes import main, product_links, product_info, images
from app.routes import documents, compare, category, filter, upscale

# Khi cần thêm module mới, hãy thêm vào đây 