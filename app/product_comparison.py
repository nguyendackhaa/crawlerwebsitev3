import pandas as pd
import os
from datetime import datetime
import logging
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def compare_product_codes(file1_path, file2_path, output_dir=None):
    """
    So sánh mã sản phẩm từ hai file Excel/CSV và trả về phân tích về sự trùng lặp.
    
    Args:
        file1_path (str): Đường dẫn đến file Excel/CSV thứ nhất
        file2_path (str): Đường dẫn đến file Excel/CSV thứ hai
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
        
        # Đọc file 1
        if file1_ext == '.csv':
            df1 = pd.read_csv(file1_path)
            logger.info(f"Đã đọc file CSV 1 với {len(df1)} dòng")
        else:
            # Mặc định đọc sheet đầu tiên nếu không chỉ định
            df1 = pd.read_excel(file1_path)
            logger.info(f"Đã đọc file Excel 1 với {len(df1)} dòng")
            
        # Đọc file 2
        if file2_ext == '.csv':
            df2 = pd.read_csv(file2_path)
            logger.info(f"Đã đọc file CSV 2 với {len(df2)} dòng")
        else:
            # Mặc định đọc sheet đầu tiên nếu không chỉ định
            df2 = pd.read_excel(file2_path)
            logger.info(f"Đã đọc file Excel 2 với {len(df2)} dòng")
        
        # Lấy mã sản phẩm từ cột B (index 1) và bắt đầu từ hàng thứ 2 (bỏ qua tiêu đề)
        product_codes1 = df1.iloc[1:, 1].astype(str).str.strip()
        product_codes2 = df2.iloc[1:, 1].astype(str).str.strip()
        
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
            # Truyền cả df1 và df2 sang hàm xuất báo cáo
            report_path = export_comparison_report(result, df1, df2, output_dir)
            return report_path
        
        return result
        
    except Exception as e:
        logger.error(f"Lỗi khi so sánh mã sản phẩm: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            'error': str(e),
            'status': 'failed'
        }

def export_comparison_report(result, df1, df2, output_dir):
    """
    Xuất báo cáo so sánh ra file Excel bao gồm thông tin chi tiết.
    
    Args:
        result (dict): Kết quả phân tích
        df1 (pd.DataFrame): DataFrame của file thứ nhất
        df2 (pd.DataFrame): DataFrame của file thứ hai
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
        
        # Các cột thông tin muốn hiển thị trong báo cáo
        info_columns = ['Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'Danh mục', 'Tổng quan']
        
        # Đảm bảo các cột tồn tại trong DataFrame, chỉ lấy các cột chung
        available_info_columns_1 = [col for col in info_columns if col in df1.columns]
        available_info_columns_2 = [col for col in info_columns if col in df2.columns]

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
                # Lọc chi tiết từ cả hai file và gộp lại
                df1_trung = df1[df1['Mã sản phẩm'].isin(result['duplicate_codes'])][available_info_columns_1].copy()
                df2_trung = df2[df2['Mã sản phẩm'].isin(result['duplicate_codes'])][available_info_columns_2].copy()
                
                # Đổi tên cột để phân biệt nguồn gốc
                df1_trung = df1_trung.rename(columns={col: f'{col}_File1' for col in df1_trung.columns if col != 'Mã sản phẩm'})
                df2_trung = df2_trung.rename(columns={col: f'{col}_File2' for col in df2_trung.columns if col != 'Mã sản phẩm'})
                
                # Gộp hai DataFrame dựa trên Mã sản phẩm
                # Sử dụng outer join để đảm bảo tất cả mã trùng lặp đều có mặt, ngay cả khi thiếu ở 1 file
                merged_duplicates_df = pd.merge(df1_trung, df2_trung, on='Mã sản phẩm', how='outer')

                # Sắp xếp theo Mã sản phẩm
                merged_duplicates_df = merged_duplicates_df.sort_values(by='Mã sản phẩm')
                
                merged_duplicates_df.to_excel(writer, sheet_name='Mã trùng lặp', index=False)
                
                # Sheet mã chỉ có trong File 1
                df1_only = df1[df1['Mã sản phẩm'].isin(result['unique_to_file1'])][available_info_columns_1].copy()
                df1_only = df1_only.sort_values(by='Mã sản phẩm')
                df1_only.to_excel(writer, sheet_name='Chỉ có trong File 1', index=False)
                
                # Sheet mã chỉ có trong File 2
                df2_only = df2[df2['Mã sản phẩm'].isin(result['unique_to_file2'])][available_info_columns_2].copy()
                df2_only = df2_only.sort_values(by='Mã sản phẩm')
                df2_only.to_excel(writer, sheet_name='Chỉ có trong File 2', index=False)
                
                # Format workbook (tùy chọn, có thể gây lỗi nếu engine không hỗ trợ)
                try:
                    workbook = writer.book
                    for sheet_name in writer.sheets:
                        sheet = writer.sheets[sheet_name]
                        # Chỉ định cột cần tự động điều chỉnh chiều rộng
                        if sheet_name == 'Mã trùng lặp':
                            columns_to_autofit = merged_duplicates_df.columns
                        elif sheet_name == 'Chỉ có trong File 1':
                            columns_to_autofit = df1_only.columns
                        elif sheet_name == 'Chỉ có trong File 2':
                            columns_to_autofit = df2_only.columns
                        elif sheet_name == 'Tổng quan':
                             columns_to_autofit = summary_df.columns
                        else:
                            columns_to_autofit = []
                            
                        for i, col in enumerate(columns_to_autofit):
                             # Tính toán chiều rộng tối đa cho cột (có thể cần tinh chỉnh)
                            max_width = max(merged_duplicates_df[col].astype(str).apply(len).max() if sheet_name == 'Mã trùng lặp' else 
                                            df1_only[col].astype(str).apply(len).max() if sheet_name == 'Chỉ có trong File 1' else 
                                            df2_only[col].astype(str).apply(len).max() if sheet_name == 'Chỉ có trong File 2' else 
                                            summary_df[col].astype(str).apply(len).max(), len(col))
                            sheet.set_column(i, i, max_width + 2) # +2 cho khoảng trống đệm

                except Exception as format_error:
                    logger.warning(f"Lỗi khi định dạng Excel với xlsxwriter: {format_error}. Tiếp tục mà không định dạng.")

            logger.info(f"Đã tạo báo cáo Excel thành công: {report_path}")
            return report_path
        
        except ImportError:
             logger.warning("xlsxwriter không khả dụng, đang thử lại với engine openpyxl...")
             # Thử sử dụng engine openpyxl thay thế
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
                
                # Sheet mã trùng lặp (sử dụng logic tương tự như trên)
                df1_trung = df1[df1['Mã sản phẩm'].isin(result['duplicate_codes'])][available_info_columns_1].copy()
                df2_trung = df2[df2['Mã sản phẩm'].isin(result['duplicate_codes'])][available_info_columns_2].copy()
                
                df1_trung = df1_trung.rename(columns={col: f'{col}_File1' for col in df1_trung.columns if col != 'Mã sản phẩm'})
                df2_trung = df2_trung.rename(columns={col: f'{col}_File2' for col in df2_trung.columns if col != 'Mã sản phẩm'})
                
                merged_duplicates_df = pd.merge(df1_trung, df2_trung, on='Mã sản phẩm', how='outer')
                merged_duplicates_df = merged_duplicates_df.sort_values(by='Mã sản phẩm')

                merged_duplicates_df.to_excel(writer, sheet_name='Mã trùng lặp', index=False)
                
                # Sheet mã chỉ có trong File 1
                df1_only = df1[df1['Mã sản phẩm'].isin(result['unique_to_file1'])][available_info_columns_1].copy()
                df1_only = df1_only.sort_values(by='Mã sản phẩm')
                df1_only.to_excel(writer, sheet_name='Chỉ có trong File 1', index=False)
                
                # Sheet mã chỉ có trong File 2
                df2_only = df2[df2['Mã sản phẩm'].isin(result['unique_to_file2'])][available_info_columns_2].copy()
                df2_only = df2_only.sort_values(by='Mã sản phẩm')
                df2_only.to_excel(writer, sheet_name='Chỉ có trong File 2', index=False)
                
             logger.info(f"Đã tạo báo cáo Excel thành công với engine openpyxl: {report_path}")
             return report_path
        
    except Exception as e:
        logger.error(f"Lỗi khi tạo báo cáo Excel: {str(e)}")
        logger.error(traceback.format_exc())
        return None 

def compare_product_details(file1_path, file2_path, output_path):
    """
    So sánh chi tiết sản phẩm giữa file Excel (sheet 'Tổng hợp sản phẩm') và file CSV.
    Xuất ra file Excel với 3 sheet: Trùng mã, Chỉ file 1, Chỉ file 2.
    (Giữ lại hàm này cho mục đích ban đầu nếu cần)
    """
    try:
        logger.info(f"Bắt đầu so sánh chi tiết sản phẩm từ {file1_path} (sheet 'Tổng hợp sản phẩm') và {file2_path}")

        # Đọc dữ liệu
        # Kiểm tra sheet 'Tổng hợp sản phẩm' tồn tại trong file1
        excel_file = pd.ExcelFile(file1_path)
        if 'Tổng hợp sản phẩm' not in excel_file.sheet_names:
             logger.error(f"File Excel {file1_path} không có sheet 'Tổng hợp sản phẩm'")
             return {'error': f"File Excel {file1_path} không có sheet 'Tổng hợp sản phẩm'", 'status': 'failed'}

        df1 = excel_file.parse('Tổng hợp sản phẩm')
        df2 = pd.read_csv(file2_path)

        logger.info(f"Đã đọc file 1 (sheet 'Tổng hợp sản phẩm') với {len(df1)} dòng")
        logger.info(f"Đã đọc file 2 (CSV) với {len(df2)} dòng")

        # Chuẩn hóa mã sản phẩm về str và strip
        # Sử dụng cột đầu tiên (index 0) cho Mã sản phẩm
        if df1.shape[1] < 1 or df2.shape[1] < 1:
             logger.error("Một trong hai file không có đủ cột cho Mã sản phẩm (cột A)")
             return {'error': "Một trong hai file không có đủ cột cho Mã sản phẩm (cột A)", 'status': 'failed'}
             
        df1['Mã sản phẩm'] = df1.iloc[:, 0].astype(str).str.strip()
        df2['Mã sản phẩm'] = df2.iloc[:, 0].astype(str).str.strip()

        # Tìm các mã trùng
        codes1 = set(df1['Mã sản phẩm'])
        codes2 = set(df2['Mã sản phẩm'])

        # Loại bỏ giá trị rỗng/nan trước khi so sánh tập hợp
        codes1 = {code for code in codes1 if code.lower() != 'nan' and code != ''}
        codes2 = {code for code in codes2 if code.lower() != 'nan' and code != ''}

        duplicate_codes = codes1 & codes2
        only_file1_codes = codes1 - codes2
        only_file2_codes = codes2 - codes1

        logger.info(f"Số lượng mã trùng lặp (chi tiết): {len(duplicate_codes)}")
        logger.info(f"Số lượng mã chỉ có trong file 1 (chi tiết): {len(only_file1_codes)}")
        logger.info(f"Số lượng mã chỉ có trong file 2 (chi tiết): {len(only_file2_codes)}")

        # Các cột thông tin muốn hiển thị trong báo cáo chi tiết
        info_columns_detail = ['Mã sản phẩm', 'Tên sản phẩm', 'Giá', 'Danh mục', 'URL', 'Tổng quan']

        # Đảm bảo các cột tồn tại trong DataFrame
        available_info_columns_detail_1 = [col for col in info_columns_detail if col in df1.columns]
        available_info_columns_detail_2 = [col for col in info_columns_detail if col in df2.columns]

        # Lọc dữ liệu
        # Chỉ lấy các cột thông tin khả dụng
        df1_trung = df1[df1['Mã sản phẩm'].isin(duplicate_codes)][available_info_columns_detail_1].copy()
        df2_trung = df2[df2['Mã sản phẩm'].isin(duplicate_codes)][available_info_columns_detail_2].copy()
        df1_only = df1[df1['Mã sản phẩm'].isin(only_file1_codes)][available_info_columns_detail_1].copy()
        df2_only = df2[df2['Mã sản phẩm'].isin(only_file2_codes)][available_info_columns_detail_2].copy()

        # Gộp thông tin trùng mã (giữ cả 2 nguồn, thêm tiền tố để phân biệt)
        # Đổi tên cột để phân biệt nguồn gốc
        df1_trung_renamed = df1_trung.rename(columns={col: f'{col}_File1' for col in df1_trung.columns if col != 'Mã sản phẩm'})
        df2_trung_renamed = df2_trung.rename(columns={col: f'{col}_File2' for col in df2_trung.columns if col != 'Mã sản phẩm'})

        # Gộp hai DataFrame dựa trên Mã sản phẩm
        # Sử dụng outer join để đảm bảo tất cả mã trùng lặp đều có mặt, ngay cả khi thiếu ở 1 file
        df_trung = pd.merge(df1_trung_renamed, df2_trung_renamed, on='Mã sản phẩm', how='outer')

        # Sắp xếp dữ liệu
        df_trung = df_trung.sort_values(by='Mã sản phẩm')
        df1_only = df1_only.sort_values(by='Mã sản phẩm')
        df2_only = df2_only.sort_values(by='Mã sản phẩm')
        
        # Xuất ra file Excel
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df_trung.to_excel(writer, sheet_name='Trùng mã', index=False)
            df1_only.to_excel(writer, sheet_name='Chỉ file 1', index=False)
            df2_only.to_excel(writer, sheet_name='Chỉ file 2', index=False)

        logger.info(f"Đã tạo báo cáo chi tiết thành công: {output_path}")
        return output_path
        
    except FileNotFoundError as fnf_error:
        logger.error(f"Lỗi File not found: {fnf_error}")
        return {'error': f"File không tìm thấy: {fnf_error}", 'status': 'failed'}
    except KeyError as ke:
        logger.error(f"Lỗi KeyError (Có thể do thiếu cột): {ke}")
        return {'error': f"Lỗi định dạng file hoặc thiếu cột cần thiết: {ke}", 'status': 'failed'}
    except Exception as e:
        logger.error(f"Lỗi khi so sánh chi tiết sản phẩm: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            'error': str(e),
            'status': 'failed'
        } 

def split_products_by_category(file_path, output_dir):
    """
    Đọc file Excel/CSV, phân loại sản phẩm theo danh mục và xuất ra các sheet riêng biệt.

    Args:
        file_path (str): Đường dẫn đến file Excel/CSV đầu vào.
        output_dir (str): Thư mục để lưu file Excel kết quả.

    Returns:
        str hoặc dict: Đường dẫn đến file Excel kết quả nếu thành công, dictionary với thông tin lỗi nếu thất bại.
    """
    try:
        logger.info(f"Bắt đầu phân loại sản phẩm theo danh mục từ file: {file_path}")

        # Xác định loại file và đọc dữ liệu
        file_ext = os.path.splitext(file_path)[1].lower()

        if file_ext == '.csv':
            df = pd.read_csv(file_path)
            logger.info(f"Đã đọc file CSV với {len(df)} dòng")
        else:
            # Thử đọc tất cả các sheet nếu là file Excel, sau đó gộp lại nếu cần
            # Tuy nhiên, dựa trên yêu cầu và ảnh gốc, có vẻ như chỉ cần đọc sheet đầu tiên.
            # Nếu file có nhiều sheet cần xử lý, logic này cần điều chỉnh.
            # Giả sử file đầu vào chỉ có 1 sheet hoặc cần xử lý sheet đầu tiên.
            df = pd.read_excel(file_path)
            logger.info(f"Đã đọc file Excel với {len(df)} dòng")

        # Danh sách các danh mục mục tiêu
        target_categories = [
            'Cảm biến quang',
            'Cảm biến áp suất',
            'Cảm biến lưu lượng',
            'Cảm biến tiệm cận',
            'Cảm biến mức',
            'Cảm biến nhiệt độ',
            'Cảm biến đọc mã vạch',
            'Cảm biến an toàn',
            'Cảm biến độ dịch chuyển',
            'Bộ khuếch đại',
            'Thiết bị sợi quang',
            'Bộ điều khiển',
            'Khối giao tiếp',
            'Màn hình điều khiển',
            'Đầu cảm biến',
            'Khác' # Thêm danh mục 'Khác' để gom nhóm các loại còn lại
        ]

        # Cột chứa thông tin danh mục (Giả định tên cột là 'Danh mục')
        category_column = 'Danh mục'
        product_code_column = 'Mã sản phẩm'
        
        # Các cột mong muốn trong file xuất
        output_columns_order = [
            'Mã sản phẩm',
            'Tên sản phẩm',
            'Giá',
            'Danh mục',
            'URL',
            'Tổng quan'
        ]

        # Kiểm tra cột danh mục tồn tại
        if category_column not in df.columns:
            error_msg = f"File không có cột '{category_column}'. Không thể phân loại."
            logger.error(error_msg)
            return {'error': error_msg, 'status': 'failed'}
            
        # Kiểm tra cột mã sản phẩm tồn tại
        if product_code_column not in df.columns:
             logger.warning(f"File không có cột '{product_code_column}'. Báo cáo vẫn được tạo nhưng thiếu cột mã sản phẩm.")

        # Chuẩn hóa cột Danh mục: điền giá trị NaN bằng chuỗi rỗng, strip whitespace
        df[category_column] = df[category_column].fillna('').astype(str).str.strip()
        
        # Tạo thư mục output nếu chưa tồn tại
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            logger.info(f"Đã tạo thư mục output: {output_dir}")

        # Tạo tên file báo cáo với timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"products_by_category_{timestamp}.xlsx"
        output_path = os.path.join(output_dir, output_filename)

        logger.info(f"Tạo file kết quả: {output_path}")

        # Cố gắng ghi file Excel với xlsxwriter trước
        try:
            with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
                processed_indices = set() # Theo dõi các dòng đã được xử lý

                for category in target_categories:
                    if category == 'Khác':
                        continue # Xử lý mục Khác sau cùng
                        
                    # Lọc các dòng thuộc danh mục hiện tại (không phân biệt chữ hoa/thường)
                    # Sử dụng .loc để tránh SettingWithCopyWarning
                    df_category = df.loc[df[category_column].str.lower() == category.lower()].copy()
                    
                    if not df_category.empty:
                        logger.info(f"Tìm thấy {len(df_category)} sản phẩm cho danh mục: {category}")
                        # Lọc các cột mong muốn và sắp xếp lại thứ tự
                        # Chỉ lấy các cột có trong DataFrame gốc
                        available_output_columns = [col for col in output_columns_order if col in df_category.columns]
                        df_category = df_category[available_output_columns]
                        
                        # Ghi ra sheet tương ứng
                        # Thay thế ký tự không hợp lệ trong tên sheet nếu cần (ví dụ: / \ * ? : [ ] )
                        sheet_name = category.replace('/','_').replace('\\','_').replace('*','_').replace('?','_').replace(':','_').replace('[','_').replace(']','_')[:31] # Tên sheet tối đa 31 ký tự
                        df_category.to_excel(writer, sheet_name=sheet_name, index=False)
                        # Lưu lại index của các dòng đã xử lý
                        processed_indices.update(df_category.index)

                # Xử lý danh mục 'Khác': các dòng còn lại chưa được xử lý
                remaining_df = df.loc[~df.index.isin(processed_indices)].copy()
                
                # Loại bỏ các dòng có cột Danh mục rỗng hoặc NaN (đã điền thành chuỗi rỗng)
                remaining_df = remaining_df[remaining_df[category_column] != '' ]

                if not remaining_df.empty:
                    logger.info(f"Tìm thấy {len(remaining_df)} sản phẩm cho danh mục: Khác")
                    # Lọc các cột mong muốn và sắp xếp lại thứ tự
                    available_output_columns = [col for col in output_columns_order if col in remaining_df.columns]
                    remaining_df = remaining_df[available_output_columns]

                    # Ghi ra sheet 'Khác'
                    remaining_df.to_excel(writer, sheet_name='Khác', index=False)
                else:
                     logger.info("Không tìm thấy sản phẩm nào cho danh mục: Khác")

            logger.info(f"Đã tạo file Excel phân loại theo danh mục thành công: {output_path}")
            return output_path

        except ImportError:
            logger.warning("xlsxwriter không khả dụng, đang thử lại với engine openpyxl...")
            # Nếu xlsxwriter không khả dụng, thử sử dụng engine openpyxl thay thế
            try:
                with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                    processed_indices = set() # Theo dõi các dòng đã được xử lý (đặt lại)
                    # Có thể cần đọc lại df nếu nó bị thay đổi, nhưng trong trường hợp này không cần thiết

                    for category in target_categories:
                        if category == 'Khác':
                            continue # Xử lý mục Khác sau cùng
                            
                        # Lọc các dòng thuộc danh mục hiện tại (không phân biệt chữ hoa/thường)
                        df_category = df.loc[df[category_column].str.lower() == category.lower()].copy()
                        
                        if not df_category.empty:
                            logger.info(f"Tìm thấy {len(df_category)} sản phẩm cho danh mục: {category}")
                            # Lọc các cột mong muốn và sắp xếp lại thứ tự
                            available_output_columns = [col for col in output_columns_order if col in df_category.columns]
                            df_category = df_category[available_output_columns]

                            # Ghi ra sheet tương ứng
                            sheet_name = category.replace('/','_').replace('\\','_').replace('*','_').replace('?','_').replace(':','_').replace('[','_').replace(']','_')[:31] # Tên sheet tối đa 31 ký tự
                            df_category.to_excel(writer, sheet_name=sheet_name, index=False)
                            # Lưu lại index của các dòng đã xử lý
                            processed_indices.update(df_category.index)

                    # Xử lý danh mục 'Khác': các dòng còn lại chưa được xử lý
                    remaining_df = df.loc[~df.index.isin(processed_indices)].copy()
                    remaining_df = remaining_df[remaining_df[category_column] != '' ]

                    if not remaining_df.empty:
                        logger.info(f"Tìm thấy {len(remaining_df)} sản phẩm cho danh mục: Khác")
                        # Lọc các cột mong muốn và sắp xếp lại thứ tự
                        available_output_columns = [col for col in output_columns_order if col in remaining_df.columns]
                        remaining_df = remaining_df[available_output_columns]

                        # Ghi ra sheet 'Khác'
                        remaining_df.to_excel(writer, sheet_name='Khác', index=False)
                    else:
                         logger.info("Không tìm thấy sản phẩm nào cho danh mục: Khác")

                logger.info(f"Đã tạo file Excel phân loại theo danh mục thành công với engine openpyxl: {output_path}")
                return output_path

            except Exception as e: # Bắt các lỗi khác xảy ra trong quá trình ghi bằng openpyxl
                 logger.error(f"Lỗi khi ghi file Excel với openpyxl: {str(e)}")
                 # Không trả về ở đây, để lỗi được bắt bởi khối except tổng quát bên ngoài
                 raise e # Re-raise lỗi để khối except tổng quát xử lý

    except FileNotFoundError as fnf_error:
        logger.error(f"Lỗi File not found: {fnf_error}")
        return {'error': f"File không tìm thấy: {fnf_error}", 'status': 'failed'}
    except KeyError as ke:
        logger.error(f"Lỗi KeyError (Có thể do thiếu cột): {ke}")
        return {'error': f"Lỗi định dạng file hoặc thiếu cột cần thiết: {ke}", 'status': 'failed'}
    except Exception as e:
        logger.error(f"Lỗi tổng quát khi phân loại sản phẩm: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            'error': str(e),
            'status': 'failed'
        } 