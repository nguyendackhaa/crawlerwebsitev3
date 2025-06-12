import pandas as pd
import os
from datetime import datetime
import logging
import traceback

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ProductCategorizer:
    """
    Class xử lý phân loại sản phẩm theo danh mục và xuất ra file Excel với các sheet riêng biệt.
    """

    def __init__(self):
        """
        Khởi tạo class với các danh mục mặc định và thứ tự cột mặc định.
        """
        # Danh sách các danh mục mục tiêu
        self.target_categories = [
            # Danh mục Autonics
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
            
            # Danh mục Keyence
            'bo-dem-keyence',
            'bo-nguon-keyence',
            'cam-bien-keyence',
            'camera-cong-nghiep-keyence',
            'hmi-keyence',
            'i-o-keyence',
            'plc-keyence',
            'servo-keyence',
            
            # Luôn để danh mục Khác ở cuối cùng
            'Khác'  # Thêm danh mục 'Khác' để gom nhóm các loại còn lại
        ]

        # Các cột mong muốn trong file xuất theo thứ tự
        self.output_columns_order = [
            'Mã sản phẩm',
            'Tên sản phẩm',
            'Giá',
            'Danh mục',
            'URL',
            'Tổng quan'
        ]

        # Tên cột chứa thông tin danh mục và mã sản phẩm
        self.category_column = 'Danh mục'
        self.product_code_column = 'Mã sản phẩm'

    def categorize_and_export(self, file_path, output_dir):
        """
        Đọc file Excel/CSV, phân loại sản phẩm theo danh mục và xuất ra các sheet riêng biệt.

        Args:
            file_path (str): Đường dẫn đến file Excel/CSV đầu vào.
            output_dir (str): Thư mục để lưu file Excel kết quả.

        Returns:
            str hoặc dict: Đường dẫn đến file Excel kết quả nếu thành công, 
                           dictionary với thông tin lỗi nếu thất bại.
        """
        try:
            logger.info(f"Bắt đầu phân loại sản phẩm theo danh mục từ file: {file_path}")

            # Đọc dữ liệu đầu vào
            df = self._read_input_file(file_path)
            if isinstance(df, dict) and 'error' in df:
                return df  # Trả về lỗi nếu đọc file thất bại

            # Kiểm tra và chuẩn hóa dữ liệu
            if not self._validate_dataframe(df):
                return {
                    'error': f"File không có cột '{self.category_column}' cần thiết để phân loại.",
                    'status': 'failed'
                }

            # Tạo thư mục output nếu chưa tồn tại
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
                logger.info(f"Đã tạo thư mục output: {output_dir}")

            # Tạo file Excel đầu ra
            output_path = self._create_output_file_name(output_dir)
            
            # Tiến hành phân loại và xuất file Excel
            result = self._export_to_excel(df, output_path)
            return result

        except Exception as e:
            logger.error(f"Lỗi không xác định khi phân loại sản phẩm: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                'error': str(e),
                'status': 'failed'
            }

    def _read_input_file(self, file_path):
        """
        Đọc file đầu vào dựa trên định dạng (Excel hoặc CSV).
        
        Args:
            file_path (str): Đường dẫn đến file cần đọc.
            
        Returns:
            pd.DataFrame hoặc dict: DataFrame chứa dữ liệu hoặc dict chứa thông tin lỗi.
        """
        try:
            file_ext = os.path.splitext(file_path)[1].lower()
            
            if file_ext == '.csv':
                df = pd.read_csv(file_path)
                logger.info(f"Đã đọc file CSV với {len(df)} dòng")
            elif file_ext in ['.xlsx', '.xls']:
                df = pd.read_excel(file_path)
                logger.info(f"Đã đọc file Excel với {len(df)} dòng")
            else:
                return {
                    'error': f"Định dạng file không được hỗ trợ: {file_ext}. Chỉ hỗ trợ .xlsx, .xls hoặc .csv",
                    'status': 'failed'
                }
                
            return df
            
        except FileNotFoundError:
            error_msg = f"Không tìm thấy file: {file_path}"
            logger.error(error_msg)
            return {'error': error_msg, 'status': 'failed'}
        except Exception as e:
            error_msg = f"Lỗi khi đọc file {file_path}: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return {'error': error_msg, 'status': 'failed'}

    def _validate_dataframe(self, df):
        """
        Kiểm tra DataFrame có chứa các cột cần thiết và chuẩn hóa dữ liệu.
        
        Args:
            df (pd.DataFrame): DataFrame cần kiểm tra.
            
        Returns:
            bool: True nếu dữ liệu hợp lệ, False nếu không.
        """
        # Kiểm tra cột danh mục tồn tại
        if self.category_column not in df.columns:
            logger.error(f"File không có cột '{self.category_column}'. Không thể phân loại.")
            return False
            
        # Kiểm tra cột mã sản phẩm tồn tại
        if self.product_code_column not in df.columns:
             logger.warning(f"File không có cột '{self.product_code_column}'. Báo cáo vẫn được tạo nhưng thiếu cột mã sản phẩm.")

        # Chuẩn hóa cột Danh mục: điền giá trị NaN bằng chuỗi rỗng, strip whitespace
        df[self.category_column] = df[self.category_column].fillna('').astype(str).str.strip()
        
        # Ghi log các danh mục duy nhất có trong dữ liệu
        unique_categories = df[self.category_column].unique()
        logger.info(f"Các danh mục duy nhất trong dữ liệu: {unique_categories}")
        
        return True

    def _create_output_file_name(self, output_dir):
        """
        Tạo tên file đầu ra với timestamp.
        
        Args:
            output_dir (str): Thư mục đầu ra.
            
        Returns:
            str: Đường dẫn đầy đủ của file đầu ra.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"products_by_category_{timestamp}.xlsx"
        output_path = os.path.join(output_dir, output_filename)
        
        logger.info(f"Tạo file kết quả: {output_path}")
        return output_path

    def _normalize_category(self, category):
        """
        Chuẩn hóa tên danh mục để so sánh (bỏ dấu, viết thường, chuyển khoảng trắng thành dấu gạch ngang).
        
        Args:
            category (str): Tên danh mục cần chuẩn hóa.
            
        Returns:
            str: Tên danh mục đã chuẩn hóa.
        """
        return category.lower().strip()
        
    def _export_to_excel(self, df, output_path):
        """
        Phân loại sản phẩm theo danh mục và xuất ra file Excel.
        
        Args:
            df (pd.DataFrame): DataFrame chứa dữ liệu cần phân loại.
            output_path (str): Đường dẫn đến file Excel đầu ra.
            
        Returns:
            str hoặc dict: Đường dẫn đến file Excel nếu thành công, dictionary với thông tin lỗi nếu thất bại.
        """
        try:
            # Thử sử dụng xlsxwriter engine trước
            with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
                processed_indices = set()  # Theo dõi các dòng đã được xử lý
                
                # Trước khi xử lý, in ra danh sách các danh mục trong dữ liệu
                unique_categories = df[self.category_column].unique()
                logger.info(f"Bắt đầu xử lý với các danh mục: {unique_categories}")
                
                # Chuẩn hóa cột danh mục trong DataFrame
                df['normalized_category'] = df[self.category_column].apply(self._normalize_category)
                
                # Xử lý lần lượt các danh mục
                for category in self.target_categories:
                    if category == 'Khác':
                        continue  # Xử lý danh mục Khác sau cùng
                    
                    # Chuẩn hóa danh mục mục tiêu
                    normalized_category = self._normalize_category(category)
                    
                    # Lọc dữ liệu theo danh mục chuẩn hóa
                    category_df = df.loc[df['normalized_category'] == normalized_category].copy()
                    
                    if not category_df.empty:
                        logger.info(f"Tìm thấy {len(category_df)} sản phẩm cho danh mục: {category}")
                        
                        # Lọc và sắp xếp cột theo thứ tự mong muốn
                        available_columns = [col for col in self.output_columns_order if col in df.columns]
                        category_df = category_df[available_columns]
                        
                        # Ghi ra sheet tương ứng, thay thế ký tự không hợp lệ trong tên sheet
                        sheet_name = self._sanitize_sheet_name(category)
                        category_df.to_excel(writer, sheet_name=sheet_name, index=False)
                        
                        # Lưu lại index của các dòng đã xử lý
                        processed_indices.update(category_df.index)
                    else:
                        logger.info(f"Không tìm thấy sản phẩm nào cho danh mục: {category}")
                
                # Xử lý danh mục "Khác" - các dòng không thuộc danh mục nào ở trên
                self._process_other_category(df, processed_indices, writer)
                
                # Thử format các sheet nếu có thể
                self._format_excel_sheets(writer, df)
                
            logger.info(f"Đã tạo file Excel phân loại theo danh mục thành công: {output_path}")
            return output_path
            
        except ImportError:
            # Nếu không có xlsxwriter, thử dùng openpyxl
            logger.warning("xlsxwriter không khả dụng, đang thử lại với engine openpyxl...")
            return self._export_to_excel_with_openpyxl(df, output_path)
        except Exception as e:
            error_msg = f"Lỗi khi xuất file Excel với xlsxwriter: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            # Thử phương án dự phòng với openpyxl
            return self._export_to_excel_with_openpyxl(df, output_path)

    def _export_to_excel_with_openpyxl(self, df, output_path):
        """
        Phân loại sản phẩm và xuất ra Excel sử dụng engine openpyxl (phương án dự phòng).
        
        Args:
            df (pd.DataFrame): DataFrame chứa dữ liệu cần phân loại.
            output_path (str): Đường dẫn đến file Excel đầu ra.
            
        Returns:
            str hoặc dict: Đường dẫn đến file Excel nếu thành công, dictionary với thông tin lỗi nếu thất bại.
        """
        try:
            with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                processed_indices = set()  # Theo dõi các dòng đã được xử lý
                
                # Trước khi xử lý, in ra danh sách các danh mục trong dữ liệu
                unique_categories = df[self.category_column].unique()
                logger.info(f"Bắt đầu xử lý với openpyxl với các danh mục: {unique_categories}")
                
                # Chuẩn hóa cột danh mục trong DataFrame nếu chưa có
                if 'normalized_category' not in df.columns:
                    df['normalized_category'] = df[self.category_column].apply(self._normalize_category)
                
                # Xử lý lần lượt các danh mục
                for category in self.target_categories:
                    if category == 'Khác':
                        continue  # Xử lý danh mục Khác sau cùng
                    
                    # Chuẩn hóa danh mục mục tiêu
                    normalized_category = self._normalize_category(category)
                    
                    # Lọc dữ liệu theo danh mục chuẩn hóa
                    category_df = df.loc[df['normalized_category'] == normalized_category].copy()
                    
                    if not category_df.empty:
                        logger.info(f"Tìm thấy {len(category_df)} sản phẩm cho danh mục: {category}")
                        
                        # Lọc và sắp xếp cột theo thứ tự mong muốn
                        available_columns = [col for col in self.output_columns_order if col in df.columns]
                        category_df = category_df[available_columns]
                        
                        # Ghi ra sheet tương ứng, thay thế ký tự không hợp lệ trong tên sheet
                        sheet_name = self._sanitize_sheet_name(category)
                        category_df.to_excel(writer, sheet_name=sheet_name, index=False)
                        
                        # Lưu lại index của các dòng đã xử lý
                        processed_indices.update(category_df.index)
                    else:
                        logger.info(f"Không tìm thấy sản phẩm nào cho danh mục: {category}")
                
                # Xử lý danh mục "Khác" - các dòng không thuộc danh mục nào ở trên
                self._process_other_category(df, processed_indices, writer)
                
            logger.info(f"Đã tạo file Excel phân loại theo danh mục thành công với openpyxl: {output_path}")
            return output_path
            
        except Exception as e:
            error_msg = f"Lỗi khi xuất file Excel với openpyxl: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return {'error': error_msg, 'status': 'failed'}

    def _process_other_category(self, df, processed_indices, writer):
        """
        Xử lý các sản phẩm không thuộc danh mục nào đã định nghĩa.
        
        Args:
            df (pd.DataFrame): DataFrame chứa toàn bộ dữ liệu.
            processed_indices (set): Tập hợp các index đã được xử lý.
            writer (pd.ExcelWriter): Excel writer đang mở.
        """
        # Lọc các dòng chưa được xử lý
        remaining_df = df.loc[~df.index.isin(processed_indices)].copy()
        
        # Loại bỏ các dòng có cột Danh mục rỗng
        remaining_df = remaining_df[remaining_df[self.category_column] != '']
        
        if not remaining_df.empty:
            # Lấy danh sách các danh mục còn lại chưa được xử lý
            remaining_categories = remaining_df[self.category_column].unique()
            logger.info(f"Các danh mục chưa xử lý sẽ đưa vào sheet 'Khác': {remaining_categories}")
            logger.info(f"Tìm thấy {len(remaining_df)} sản phẩm cho danh mục: Khác")
            
            # Lọc và sắp xếp cột theo thứ tự mong muốn, loại bỏ cột normalized_category
            available_columns = [col for col in self.output_columns_order if col in df.columns]
            remaining_df = remaining_df[available_columns]
            
            # Ghi ra sheet "Khác"
            remaining_df.to_excel(writer, sheet_name='Khác', index=False)
        else:
            logger.info("Không tìm thấy sản phẩm nào cho danh mục: Khác")

    def _sanitize_sheet_name(self, name):
        """
        Loại bỏ các ký tự không hợp lệ trong tên sheet Excel.
        
        Args:
            name (str): Tên sheet cần chuẩn hóa.
            
        Returns:
            str: Tên sheet đã chuẩn hóa.
        """
        # Thay thế các ký tự không hợp lệ và giới hạn độ dài
        invalid_chars = ['/', '\\', '*', '?', ':', '[', ']']
        result = name
        for char in invalid_chars:
            result = result.replace(char, '_')
        
        # Excel giới hạn tên sheet tối đa 31 ký tự
        return result[:31]

    def _format_excel_sheets(self, writer, df):
        """
        Định dạng các sheet Excel nếu engine là xlsxwriter.
        
        Args:
            writer (pd.ExcelWriter): Excel writer đang mở.
            df (pd.DataFrame): DataFrame gốc.
        """
        # Chỉ thực hiện nếu writer là xlsxwriter
        try:
            workbook = writer.book
            for sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                
                # Tạo định dạng cho header
                header_format = workbook.add_format({
                    'bold': True,
                    'text_wrap': True,
                    'valign': 'top',
                    'bg_color': '#D8E4BC',
                    'border': 1
                })
                
                # Auto-fit cột
                for i, col in enumerate(df.columns):
                    col_width = max(df[col].astype(str).str.len().max(), len(col)) + 2
                    worksheet.set_column(i, i, col_width)
                
                # Định dạng header
                for i, col in enumerate(df.columns):
                    worksheet.write(0, i, col, header_format)
                
        except Exception as e:
            logger.warning(f"Không thể định dạng Excel: {str(e)}")
            # Không làm gì nếu có lỗi. Định dạng không ảnh hưởng đến dữ liệu 