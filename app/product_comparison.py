import pandas as pd
import os
from datetime import datetime
import logging
import traceback
import tkinter as tk
from tkinter import filedialog
from typing import List, Dict, Union, Set, Tuple, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def compare_products_multi(base_file: str, comparison_files: List[str], 
                         output_path: str = None, 
                         comparison_column: str = None,
                         colorize: bool = True,
                         export_summary: bool = False) -> str:
    """
    So sánh sản phẩm giữa file cơ sở và nhiều file khác, tạo báo cáo Excel với các sheet riêng biệt.
    
    Args:
        base_file: Đường dẫn đến file Excel cơ sở (File 1)
        comparison_files: Danh sách đường dẫn đến các file Excel cần so sánh (File 2, 3, ...)
        output_path: Đường dẫn đến file kết quả (mặc định: Ket_qua_so_sanh.xlsx)
        comparison_column: Tên cột dùng để so sánh (mặc định: tự động phát hiện)
        colorize: Có định dạng màu sắc cho kết quả hay không
        export_summary: Có xuất file tổng hợp mã trùng hay không
        
    Returns:
        Đường dẫn đến file kết quả
    """
    try:
        logger.info(f"Bắt đầu so sánh sản phẩm từ file cơ sở: {base_file} với {len(comparison_files)} file khác")
        
        # Đặt đường dẫn kết quả mặc định nếu chưa cung cấp
        if not output_path:
            output_dir = os.path.dirname(base_file)
            output_path = os.path.join(output_dir, "Ket_qua_so_sanh.xlsx")
        
        # Giới hạn số file so sánh tối đa 10 file
        if len(comparison_files) > 10:
            logger.warning(f"Vượt quá số lượng file cho phép (10). Chỉ sử dụng 10 file đầu tiên.")
            comparison_files = comparison_files[:10]
            
        # Đọc file cơ sở
        base_df, base_code_col = read_product_file(base_file, comparison_column)
        base_product_codes = set(base_df[base_code_col].astype(str))
        logger.info(f"Đã đọc file cơ sở với {len(base_product_codes)} mã sản phẩm")
            
        # Khởi tạo Excel writer
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            # Chuẩn bị cấu trúc cho dữ liệu tổng hợp
            all_matches = set()
            summary_data = {
                'File so sánh': [],
                'Tổng số mã trong File cơ sở': [],
                'Tổng số mã trong File so sánh': [],
                'Số mã trùng': [],
                'Số mã không trùng': []
            }
            
            # Xử lý từng file so sánh
            for comp_file in comparison_files:
                try:
                    logger.info(f"Đang xử lý file so sánh: {os.path.basename(comp_file)}")
                    
                    # Đọc file so sánh
                    comp_df, comp_code_col = read_product_file(comp_file, comparison_column)
                    comp_product_codes = set(comp_df[comp_code_col].astype(str))
                    
                    # Xác định các mã trùng và không trùng
                    matching_codes = base_product_codes.intersection(comp_product_codes)
                    non_matching_codes = comp_product_codes - base_product_codes
                    
                    # Thêm vào tập hợp mã trùng để làm báo cáo tổng hợp
                    all_matches.update(matching_codes)
                    
                    # Tạo tên sheet từ tên file
                    file_name = os.path.splitext(os.path.basename(comp_file))[0]
                    sheet_name = clean_sheet_name(file_name)
                    
                    # Cập nhật dữ liệu tổng quan
                    summary_data['File so sánh'].append(file_name)
                    summary_data['Tổng số mã trong File cơ sở'].append(len(base_product_codes))
                    summary_data['Tổng số mã trong File so sánh'].append(len(comp_product_codes))
                    summary_data['Số mã trùng'].append(len(matching_codes))
                    summary_data['Số mã không trùng'].append(len(non_matching_codes))
                    
                    # Tạo DataFrame cho mã trùng và không trùng
                    matching_df = comp_df[comp_df[comp_code_col].isin(matching_codes)].copy()
                    non_matching_df = comp_df[comp_df[comp_code_col].isin(non_matching_codes)].copy()
                    
                    # Tạo sheet cho file so sánh hiện tại
                    # Sắp xếp dữ liệu theo cột mã sản phẩm
                    matching_df = matching_df.sort_values(by=comp_code_col)
                    non_matching_df = non_matching_df.sort_values(by=comp_code_col)
                    
                    # Ghi dữ liệu vào sheet
                    row_pos = 0
                    
                    # Tiêu đề cho bảng mã trùng
                    worksheet = writer.sheets[sheet_name] if sheet_name in writer.sheets else writer.book.add_worksheet(sheet_name)
                    
                    # Tiêu đề cho bảng mã trùng
                    worksheet.write(row_pos, 0, "DANH SÁCH MÃ TRÙNG")
                    row_pos += 1
                    
                    # Ghi dữ liệu mã trùng
                    if not matching_df.empty:
                        matching_df.to_excel(writer, sheet_name=sheet_name, startrow=row_pos, index=False)
                        row_pos += len(matching_df) + 2  # +2 cho header và khoảng trống
                    else:
                        worksheet.write(row_pos, 0, "Không có mã trùng")
                        row_pos += 2
                    
                    # Tiêu đề cho bảng mã không trùng
                    worksheet.write(row_pos, 0, "DANH SÁCH MÃ KHÔNG TRÙNG")
                    row_pos += 1
                    
                    # Ghi dữ liệu mã không trùng
                    if not non_matching_df.empty:
                        non_matching_df.to_excel(writer, sheet_name=sheet_name, startrow=row_pos, index=False)
                    else:
                        worksheet.write(row_pos, 0, "Không có mã không trùng")
                    
                    # Định dạng màu sắc nếu được yêu cầu
                    if colorize:
                        format_sheet_colors(writer, worksheet, matching_df, non_matching_df, row_pos)
                    
                    # Tự động điều chỉnh chiều rộng cột
                    for idx, col in enumerate(comp_df.columns):
                        # +2 cho khoảng trống đệm
                        max_len = max(
                            comp_df[col].astype(str).str.len().max() if len(comp_df) > 0 else 0,
                            len(str(col))
                        ) + 2
                        worksheet.set_column(idx, idx, max_len)
                    
                    logger.info(f"Đã tạo sheet {sheet_name} với {len(matching_df)} mã trùng và {len(non_matching_df)} mã không trùng")
                    
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý file {comp_file}: {str(e)}")
                    traceback.print_exc()
            
            # Tạo sheet tổng quan
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name='Tổng quan', index=False)
            
            # Định dạng sheet tổng quan
            worksheet = writer.sheets['Tổng quan']
            for idx, col in enumerate(summary_df.columns):
                max_width = max(
                    summary_df[col].astype(str).str.len().max(),
                    len(col)
                ) + 2
                worksheet.set_column(idx, idx, max_width)
            
            # Tạo sheet tổng hợp mã trùng nếu được yêu cầu
            if export_summary and all_matches:
                # Tạo DataFrame chỉ với các mã trùng từ file cơ sở
                summary_matches_df = base_df[base_df[base_code_col].isin(all_matches)].copy()
                summary_matches_df = summary_matches_df.sort_values(by=base_code_col)
                summary_matches_df.to_excel(writer, sheet_name='Tổng hợp mã trùng', index=False)
                
                # Định dạng sheet
                worksheet = writer.sheets['Tổng hợp mã trùng']
                for idx, col in enumerate(summary_matches_df.columns):
                    max_width = max(
                        summary_matches_df[col].astype(str).str.len().max() if len(summary_matches_df) > 0 else 0,
                        len(col)
                    ) + 2
                    worksheet.set_column(idx, idx, max_width)
                
        logger.info(f"Đã tạo báo cáo so sánh thành công: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Lỗi khi so sánh sản phẩm: {str(e)}")
        logger.error(traceback.format_exc())
        raise Exception(f"Lỗi khi so sánh sản phẩm: {str(e)}")

def read_product_file(file_path: str, comparison_column: str = None) -> Tuple[pd.DataFrame, str]:
    """
    Đọc file Excel/CSV chứa dữ liệu sản phẩm và trả về DataFrame và tên cột mã sản phẩm.
    
    Args:
        file_path: Đường dẫn đến file dữ liệu
        comparison_column: Tên cột dùng để so sánh (tùy chọn)
        
    Returns:
        Tuple chứa DataFrame và tên cột mã sản phẩm
    """
    # Đọc file dựa vào phần mở rộng
    file_ext = os.path.splitext(file_path)[1].lower()
    if file_ext == '.csv':
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)
    
    # Chuẩn hóa tên cột
    df.columns = [str(col).strip() for col in df.columns]
    
    # Nếu đã chỉ định cột so sánh và cột đó tồn tại trong DataFrame
    if comparison_column and comparison_column in df.columns:
        product_code_col = comparison_column
    else:
        # Tự động phát hiện cột mã sản phẩm
        product_code_col = None
        potential_columns = ['Mã sản phẩm', 'Mã SP', 'SKU', 'Product Code', 'Product ID']
        
        for col in df.columns:
            if col in potential_columns or ('mã' in col.lower() and ('sản phẩm' in col.lower() or 'sp' in col.lower())):
                product_code_col = col
                break
        
        if product_code_col is None and len(df.columns) > 0:
            # Nếu không tìm thấy cột mã sản phẩm cụ thể, giả định cột đầu tiên là mã sản phẩm
            product_code_col = df.columns[0]
            logger.warning(f"Không tìm thấy cột mã sản phẩm rõ ràng trong file {os.path.basename(file_path)}. Sử dụng cột đầu tiên: {product_code_col}")
    
    # Chuẩn hóa giá trị cột mã sản phẩm
    df[product_code_col] = df[product_code_col].astype(str).str.upper().str.strip()
    
    # Loại bỏ các giá trị NaN hoặc rỗng
    df = df[~df[product_code_col].isin(['NAN', ''])]
    df = df[~df[product_code_col].isna()]
    
    logger.info(f"Đã đọc file {os.path.basename(file_path)} với {len(df)} sản phẩm hợp lệ - cột mã sản phẩm: {product_code_col}")
    return df, product_code_col

def clean_sheet_name(name: str) -> str:
    """
    Làm sạch tên sheet, đảm bảo tên hợp lệ cho Excel.
    
    Args:
        name: Tên sheet gốc
        
    Returns:
        Tên sheet đã được làm sạch
    """
    # Loại bỏ ký tự không hợp lệ trong tên sheet Excel
    invalid_chars = ['/', '\\', '*', '?', ':', '[', ']']
    clean_name = name
    for char in invalid_chars:
        clean_name = clean_name.replace(char, '_')
    
    # Giới hạn độ dài tên sheet (Excel giới hạn 31 ký tự)
    if len(clean_name) > 31:
        clean_name = clean_name[:31]
    
    return clean_name

def format_sheet_colors(writer, worksheet, matching_df, non_matching_df, non_matching_start_row):
    """
    Định dạng màu sắc cho các dòng trong sheet Excel.
    
    Args:
        writer: Excel writer
        worksheet: Excel worksheet
        matching_df: DataFrame chứa dữ liệu mã trùng
        non_matching_df: DataFrame chứa dữ liệu mã không trùng
        non_matching_start_row: Dòng bắt đầu của bảng mã không trùng
    """
    # Định nghĩa các định dạng màu sắc
    green_format = writer.book.add_format({'bg_color': '#E6FFEC'})  # Màu xanh nhạt
    red_format = writer.book.add_format({'bg_color': '#FFECEC'})    # Màu đỏ nhạt
    header_format = writer.book.add_format({'bold': True, 'bg_color': '#DDDDDD'})  # Header màu xám
    
    # Định dạng tiêu đề
    worksheet.set_row(0, None, header_format)  # Tiêu đề bảng mã trùng
    worksheet.set_row(non_matching_start_row, None, header_format)  # Tiêu đề bảng mã không trùng
    
    # Định dạng các dòng dữ liệu mã trùng
    if not matching_df.empty:
        for i in range(len(matching_df)):
            worksheet.set_row(i + 2, None, green_format)  # +2 cho tiêu đề và header
    
    # Định dạng các dòng dữ liệu mã không trùng
    if not non_matching_df.empty:
        for i in range(len(non_matching_df)):
            worksheet.set_row(non_matching_start_row + i + 2, None, red_format)  # +2 cho tiêu đề và header

def select_files_gui() -> Tuple[str, List[str], str, str]:
    """
    Mở hộp thoại chọn file gốc, các file so sánh và thư mục kết quả.
    
    Returns:
        Tuple gồm (đường dẫn file gốc, danh sách đường dẫn file so sánh, thư mục kết quả, cột so sánh)
    """
    try:
        root = tk.Tk()
        root.withdraw()  # Ẩn cửa sổ gốc
        
        # Chọn file gốc (File 1)
        base_file = filedialog.askopenfilename(
            title="Chọn file gốc (File 1)",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if not base_file:
            return None, None, None, None
        
        # Chọn các file so sánh (File 2, 3, ...)
        comparison_files = filedialog.askopenfilenames(
            title="Chọn các file để so sánh (tối đa 10 file)",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if not comparison_files:
            return None, None, None, None
        
        # Giới hạn số file so sánh
        comparison_files = list(comparison_files)
        if len(comparison_files) > 10:
            comparison_files = comparison_files[:10]
        
        # Tạo cửa sổ nhập cột so sánh
        comp_column_window = tk.Toplevel(root)
        comp_column_window.title("Chọn cột so sánh")
        comp_column_window.geometry("400x150")
        
        tk.Label(comp_column_window, text="Nhập tên cột dùng để so sánh (để trống để tự động phát hiện):").pack(pady=10)
        
        column_entry = tk.Entry(comp_column_window, width=30)
        column_entry.pack(pady=10)
        column_entry.insert(0, "Mã sản phẩm")  # Giá trị mặc định
        
        result_column = [None]  # Sử dụng list để lưu kết quả từ callback
        
        def on_ok():
            result_column[0] = column_entry.get().strip()
            comp_column_window.destroy()
        
        tk.Button(comp_column_window, text="OK", command=on_ok).pack(pady=10)
        
        comp_column_window.wait_window()  # Đợi cửa sổ đóng
        
        comparison_column = result_column[0] if result_column[0] else None
        
        # Chọn thư mục lưu kết quả
        output_dir = filedialog.askdirectory(title="Chọn thư mục lưu kết quả")
        
        if not output_dir:
            # Sử dụng thư mục của file gốc nếu không chọn
            output_dir = os.path.dirname(base_file)
        
        output_path = os.path.join(output_dir, "Ket_qua_so_sanh.xlsx")
        
        return base_file, comparison_files, output_path, comparison_column
    
    except Exception as e:
        logger.error(f"Lỗi khi mở hộp thoại chọn file: {str(e)}")
        return None, None, None, None

def main():
    """
    Hàm chính để chạy công cụ so sánh sản phẩm từ giao diện dòng lệnh.
    """
    try:
        print("Công cụ so sánh sản phẩm")
        print("========================")
        print("1. Chọn file bằng hộp thoại đồ họa")
        print("2. Nhập đường dẫn file từ dòng lệnh")
        print("0. Thoát")
        
        choice = input("Lựa chọn của bạn: ")
        
        if choice == "0":
            print("Thoát chương trình.")
            return
        
        base_file = None
        comparison_files = []
        output_path = None
        comparison_column = None
        colorize = True
        export_summary = False
        
        if choice == "1":
            # Sử dụng giao diện đồ họa
            base_file, comparison_files, output_path, comparison_column = select_files_gui()
            
            if not base_file or not comparison_files:
                print("Không có đủ thông tin để thực hiện so sánh.")
                return
                
            # Hỏi thêm về màu sắc và xuất tổng hợp
            colorize = input("Bạn có muốn định dạng màu sắc cho kết quả? (y/n, mặc định: y): ").lower() != 'n'
            export_summary = input("Bạn có muốn xuất file tổng hợp mã trùng? (y/n, mặc định: n): ").lower() == 'y'
            
        else:
            # Nhập thông tin từ dòng lệnh
            base_file = input("Nhập đường dẫn đến file gốc (File 1): ")
            if not os.path.exists(base_file):
                print(f"File không tồn tại: {base_file}")
                return
            
            print("Nhập đường dẫn đến các file so sánh (tối đa 10 file, nhập 'done' để kết thúc):")
            while len(comparison_files) < 10:
                file_path = input(f"File so sánh {len(comparison_files) + 1} (hoặc 'done'): ")
                if file_path.lower() == 'done':
                    break
                if not os.path.exists(file_path):
                    print(f"File không tồn tại: {file_path}")
                    continue
                comparison_files.append(file_path)
            
            if not comparison_files:
                print("Không có file nào để so sánh.")
                return
            
            comparison_column = input("Nhập tên cột dùng để so sánh (để trống để tự động phát hiện): ")
            if not comparison_column:
                comparison_column = None
            
            output_dir = input("Nhập thư mục lưu kết quả (để trống để sử dụng thư mục hiện tại): ")
            if not output_dir:
                output_dir = os.path.dirname(base_file)
            elif not os.path.exists(output_dir):
                os.makedirs(output_dir)
            
            output_path = os.path.join(output_dir, "Ket_qua_so_sanh.xlsx")
            
            colorize_input = input("Bạn có muốn định dạng màu sắc cho kết quả? (y/n, mặc định: y): ")
            colorize = colorize_input.lower() != 'n'
            
            export_summary_input = input("Bạn có muốn xuất file tổng hợp mã trùng? (y/n, mặc định: n): ")
            export_summary = export_summary_input.lower() == 'y'
        
        # Thực hiện so sánh
        print("\nĐang tiến hành so sánh sản phẩm...")
        result_path = compare_products_multi(
            base_file=base_file,
            comparison_files=comparison_files,
            output_path=output_path,
            comparison_column=comparison_column,
            colorize=colorize,
            export_summary=export_summary
        )
        
        print(f"So sánh sản phẩm hoàn thành. Báo cáo đã được lưu tại:\n{result_path}")
    
    except Exception as e:
        print(f"Lỗi: {str(e)}")
        logger.error(f"Lỗi trong hàm main(): {str(e)}")
        logger.error(traceback.format_exc())

def compare_product_codes(file1_path, file2_paths, output_dir=None, categorize_results=False, *args, **kwargs):
    """
    So sánh mã sản phẩm từ file HPT (file1) với nhiều file khác (file2_paths) và trả về phân tích về sự trùng lặp.
    
    Args:
        file1_path (str): Đường dẫn đến file Excel/CSV của HPT
        file2_paths (list): Danh sách đường dẫn đến các file Excel/CSV cần so sánh
        output_dir (str, optional): Thư mục để lưu báo cáo Excel
        categorize_results (bool, optional): Có phân loại kết quả theo danh mục hay không
        *args: Các tham số bổ sung
        **kwargs: Các tham số bổ sung dạng key-value
        
    Returns:
        str hoặc dict: Đường dẫn đến file báo cáo Excel nếu thành công, dictionary với thông tin lỗi nếu thất bại
    """
    try:
        logger.info(f"Đang chuyển tiếp yêu cầu so sánh sang hàm mới")
        # Nếu cột mã sản phẩm được chỉ định là B2
        column_name = 'B'  # Cột B
        
        # Sử dụng hàm mới với các tham số tương thích
        if output_dir:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"product_comparison_report_{timestamp}.xlsx")
        else:
            output_path = None
            
        result = compare_products_multi(
            base_file=file1_path,
            comparison_files=file2_paths,
            output_path=output_path,
            comparison_column=column_name,  # Sử dụng cột B
            colorize=True,
            export_summary=True
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Lỗi khi so sánh mã sản phẩm: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            'error': str(e),
            'status': 'failed'
        }

if __name__ == "__main__":
    main() 