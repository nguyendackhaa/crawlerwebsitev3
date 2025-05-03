import pandas as pd
import os
from datetime import datetime
import logging
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def compare_product_codes(file1_path, file2_path, output_dir=None):
    """
    So sánh mã sản phẩm từ hai file Excel và trả về phân tích về sự trùng lặp
    
    Args:
        file1_path (str): Đường dẫn đến file Excel thứ nhất
        file2_path (str): Đường dẫn đến file Excel thứ hai
        output_dir (str, optional): Thư mục để lưu báo cáo Excel
        
    Returns:
        str hoặc dict: Đường dẫn đến file báo cáo Excel nếu thành công, dictionary với thông tin lỗi nếu thất bại
    """
    try:
        logger.info(f"Bắt đầu so sánh mã sản phẩm từ hai file: {file1_path} và {file2_path}")
        
        # Xác định loại file và đọc dữ liệu
        file1_ext = os.path.splitext(file1_path)[1].lower()
        file2_ext = os.path.splitext(file2_path)[1].lower()
        
        logger.info(f"Định dạng file 1: {file1_ext}, định dạng file 2: {file2_ext}")
        
        # Đọc file Excel 1
        if file1_ext == '.csv':
            df1 = pd.read_csv(file1_path)
            logger.info(f"Đã đọc file CSV 1 với {len(df1)} dòng")
        else:
            df1 = pd.read_excel(file1_path)
            logger.info(f"Đã đọc file Excel 1 với {len(df1)} dòng")
            
        # Đọc file Excel 2
        if file2_ext == '.csv':
            df2 = pd.read_csv(file2_path)
            logger.info(f"Đã đọc file CSV 2 với {len(df2)} dòng")
        else:
            df2 = pd.read_excel(file2_path)
            logger.info(f"Đã đọc file Excel 2 với {len(df2)} dòng")
        
        # Lấy mã sản phẩm từ cột B (cột thứ hai, index 1) của mỗi file
        # Kiểm tra số lượng cột trong mỗi DataFrame
        if df1.shape[1] < 2:
            logger.warning(f"File 1 không có cột B (chỉ có {df1.shape[1]} cột)")
            return {'error': 'File 1 không có cột B', 'status': 'failed'}
        
        if df2.shape[1] < 2:
            logger.warning(f"File 2 không có cột B (chỉ có {df2.shape[1]} cột)")
            return {'error': 'File 2 không có cột B', 'status': 'failed'}
        
        # Lấy cột B (index 1) từ mỗi DataFrame
        product_codes1 = df1.iloc[:, 1].astype(str).str.strip()
        product_codes2 = df2.iloc[:, 1].astype(str).str.strip()
        
        # Loại bỏ các giá trị rỗng hoặc 'nan'
        product_codes1 = product_codes1[product_codes1.str.lower() != 'nan']
        product_codes1 = product_codes1[product_codes1 != '']
        
        product_codes2 = product_codes2[product_codes2.str.lower() != 'nan']
        product_codes2 = product_codes2[product_codes2 != '']
        
        # Tính toán các mã trùng lặp
        duplicate_codes = set(product_codes1).intersection(set(product_codes2))
        
        # Lấy các mã duy nhất cho mỗi file
        unique_to_file1 = set(product_codes1) - set(product_codes2)
        unique_to_file2 = set(product_codes2) - set(product_codes1)
        
        logger.info(f"Số lượng mã sản phẩm trùng lặp: {len(duplicate_codes)}")
        logger.info(f"Số lượng mã sản phẩm chỉ có trong file 1: {len(unique_to_file1)}")
        logger.info(f"Số lượng mã sản phẩm chỉ có trong file 2: {len(unique_to_file2)}")
        
        # Tạo kết quả phân tích
        result = {
            'total_products_file1': len(product_codes1),
            'total_products_file2': len(product_codes2),
            'duplicate_count': len(duplicate_codes),
            'unique_to_file1_count': len(unique_to_file1),
            'unique_to_file2_count': len(unique_to_file2),
            'duplicate_codes': sorted(list(duplicate_codes)),
            'unique_to_file1': sorted(list(unique_to_file1)),
            'unique_to_file2': sorted(list(unique_to_file2))
        }
        
        # Tạo báo cáo Excel nếu được yêu cầu
        if output_dir is not None:
            report_path = export_comparison_report(result, file1_path, file2_path, output_dir)
            return report_path
        
        return result
        
    except Exception as e:
        logger.error(f"Lỗi khi so sánh mã sản phẩm: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            'error': str(e),
            'status': 'failed'
        }

def export_comparison_report(result, file1_path, file2_path, output_dir):
    """
    Xuất báo cáo so sánh ra file Excel
    
    Args:
        result (dict): Kết quả phân tích
        file1_path (str): Đường dẫn đến file Excel thứ nhất
        file2_path (str): Đường dẫn đến file Excel thứ hai
        output_dir (str): Thư mục để lưu báo cáo Excel
    
    Returns:
        str: Đường dẫn đến file báo cáo
    """
    try:
        logger.info(f"Bắt đầu tạo báo cáo Excel trong thư mục: {output_dir}")
        
        # Tạo thư mục output nếu chưa tồn tại
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            logger.info(f"Đã tạo thư mục output: {output_dir}")
        
        # Tạo tên file báo cáo với timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"product_comparison_report_{timestamp}.xlsx"
        report_path = os.path.join(output_dir, report_filename)
        
        logger.info(f"Tạo file báo cáo: {report_path}")
        
        try:
            # Tạo Excel writer
            with pd.ExcelWriter(report_path, engine='xlsxwriter') as writer:
                # Sheet tổng quan
                summary_data = {
                    'Thông tin': [
                        'Tổng số mã trong File 1', 
                        'Tổng số mã trong File 2',
                        'Số mã trùng lặp', 
                        'Số mã chỉ có trong File 1',
                        'Số mã chỉ có trong File 2'
                    ],
                    'Số lượng': [
                        result['total_products_file1'],
                        result['total_products_file2'],
                        result['duplicate_count'],
                        result['unique_to_file1_count'],
                        result['unique_to_file2_count']
                    ]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Tổng quan', index=False)
                
                # Sheet mã trùng lặp
                duplicate_df = pd.DataFrame({'Mã trùng lặp': result['duplicate_codes']})
                duplicate_df.to_excel(writer, sheet_name='Mã trùng lặp', index=False)
                
                # Sheet mã chỉ có trong File 1
                unique1_df = pd.DataFrame({'Mã chỉ có trong File 1': result['unique_to_file1']})
                unique1_df.to_excel(writer, sheet_name='Chỉ có trong File 1', index=False)
                
                # Sheet mã chỉ có trong File 2
                unique2_df = pd.DataFrame({'Mã chỉ có trong File 2': result['unique_to_file2']})
                unique2_df.to_excel(writer, sheet_name='Chỉ có trong File 2', index=False)
                
                # Format workbook
                workbook = writer.book
                for sheet in writer.sheets.values():
                    sheet.set_column('A:Z', 18)
                    
            logger.info(f"Đã tạo báo cáo Excel thành công: {report_path}")
            return report_path
        except ImportError as ie:
            logger.error(f"Thiếu module cần thiết: {str(ie)}")
            # Thử sử dụng engine openpyxl thay thế
            logger.info("Đang thử lại với engine openpyxl...")
            with pd.ExcelWriter(report_path, engine='openpyxl') as writer:
                # Sheet tổng quan
                summary_data = {
                    'Thông tin': [
                        'Tổng số mã trong File 1', 
                        'Tổng số mã trong File 2',
                        'Số mã trùng lặp', 
                        'Số mã chỉ có trong File 1',
                        'Số mã chỉ có trong File 2'
                    ],
                    'Số lượng': [
                        result['total_products_file1'],
                        result['total_products_file2'],
                        result['duplicate_count'],
                        result['unique_to_file1_count'],
                        result['unique_to_file2_count']
                    ]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Tổng quan', index=False)
                
                # Sheet mã trùng lặp
                duplicate_df = pd.DataFrame({'Mã trùng lặp': result['duplicate_codes']})
                duplicate_df.to_excel(writer, sheet_name='Mã trùng lặp', index=False)
                
                # Sheet mã chỉ có trong File 1
                unique1_df = pd.DataFrame({'Mã chỉ có trong File 1': result['unique_to_file1']})
                unique1_df.to_excel(writer, sheet_name='Chỉ có trong File 1', index=False)
                
                # Sheet mã chỉ có trong File 2
                unique2_df = pd.DataFrame({'Mã chỉ có trong File 2': result['unique_to_file2']})
                unique2_df.to_excel(writer, sheet_name='Chỉ có trong File 2', index=False)
                
            logger.info(f"Đã tạo báo cáo Excel thành công với engine openpyxl: {report_path}")
            return report_path
        
    except Exception as e:
        logger.error(f"Lỗi khi tạo báo cáo Excel: {str(e)}")
        logger.error(traceback.format_exc())
        return None 