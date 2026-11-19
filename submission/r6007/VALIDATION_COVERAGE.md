# Kiểm tra phần Đăng trên toàn bộ ảnh đã chuẩn bị

Ngày 19/11/2026, chạy lại validator trên hai delivery bao phủ đủ **300 mã ảnh**
đã chuẩn bị trong data_source: 250 practice và 50 calib; hai tập không trùng nhau.
Validator chỉ đọc delivery, không đọc annotation gốc hoặc cache trong data_source.
Kiểm kê tên file nguồn chỉ để xác minh độ bao phủ, không dùng làm đáp án.

| Lô | Mục ảnh trong manifest | File ảnh hiện có | Lỗi luật | Ứng viên phần Đăng |
|---|---:|---:|---:|---:|
| practice | 250 | 249 | 11 | 7 |
| calib | 50 | 50 | 0 | 0 |

Practice: 3 bất đồng lớp và 4 missing_box, tổng 7 ảnh có ứng viên. Một ảnh thiếu
trong delivery là lỗi missing_image đã phát hiện; không lấy ảnh gốc chép bù vào
v1. Calib: 0 lỗi luật, 0 ứng viên. Practice có 2 diagnostic đã biết: category
không hợp lệ và ảnh bị thiếu.

Đầu ra lần chạy lại khớp hoàn toàn đầu ra đã lưu. File review gốc, review_log,
errors đã merge, trạng thái hoàn tất phần Đăng và bài nộp đã có đều giữ nguyên.
Không chạy lại scorer hoặc tự thay verdict. Đây là kiểm tra bao phủ và khả năng
chạy, không xác nhận rằng mọi semantic label đều đúng. Phần Thái chưa tích hợp.

Lệnh chạy (output riêng, không ghi đè errors đã review):

```powershell
.\.venv\Scripts\python.exe -X utf8 validator/validate.py runs/r6007/delivery --out runs/phase2_validation_all/r6007/rules.json --candidates runs/phase2_validation_all/r6007/candidates.json
.\.venv\Scripts\python.exe -X utf8 validator/validate.py runs/clean/delivery --out runs/phase2_validation_all/clean/rules.json --candidates runs/phase2_validation_all/clean/candidates.json
```

Chi tiết lượt kiểm tra nằm trong runs/phase2_validation_all/summary.json (local,
không commit runs). Tài liệu này có thể commit cùng kết quả P2 cho nhóm trưởng.
