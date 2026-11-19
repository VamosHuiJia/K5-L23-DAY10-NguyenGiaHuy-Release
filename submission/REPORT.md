# Báo cáo nhóm NguyenGiaHuy

## 1. Số liệu luyện tập
- F1 theo seed (rule / cross_check / overall): seed 6007: ≈ 1.0 / chưa có / ≈ 0.7, báo 11 lỗi luật; seed 6008: ≈ 1.0 / chưa có / ≈ 0.8, báo 12 lỗi luật. Các giá trị F1 được làm tròn theo kết quả tự đo. Nhóm em kiểm tra thêm lô sạch 50 ảnh, validator báo 0 lỗi luật.
- Seed 6007 sau bản review cập nhật của Nguyễn Hải Đăng: rule ≈ 1.0 /
  cross_check ≈ 0.6 / overall ≈ 0.8 (số làm tròn từ scorer chính thức).
  `errors.json` có 19 finding: 11 luật + 8 confirmed. Report gốc được chép vào
  `submission/r6007/report.md`.
- Đo ngày 19/11/2026 với detector phần Đăng: lô sạch `runs/clean` (50 ảnh calib,
  seed 5, `--no-faults`) có 0 lỗi luật, 0 ứng viên. Seed 6007 có 7 ứng viên trên
  7 ảnh: 3 bất đồng lớp, 4 missing_box. Đăng đã review cả 7 đề xuất chính:
  5 confirmed (3 class_swap, 2 missing_box), 2 rejected. Người xem còn bổ sung
  3 category_swap confirmed; tổng log có 10 dòng: 8 confirmed, 2 rejected.
- Ngưỡng khởi đầu: bbox IoU ≥ 0.50, hoặc mask coverage ≥ 0.85 với tỉ lệ diện tích
  bbox detection / bbox segment trong [0.25, 4]. Missing-box segment có ít nhất
  max(128 pixel, 0.0005 × diện tích ảnh), mỗi chiều bbox ≥ 8 pixel. Ánh xạ bằng tên
  lớp, bỏ stuff/crowd. Ngưỡng từ kế hoạch triển khai; chưa tinh chỉnh bằng review,
  không dùng private truth, tham số injection hay dò điểm.
- Kết quả trên chỉ cho hai detector đề xuất của Đăng; chưa bao gồm drift/merge
  của Thái. `review_log.csv` giữ nguyên các type/verdict đã được chọn của Đăng.
  Scorer chấm semantic gồm cả năm loại; 0.6 là kết quả chung hiện tại, không phải F1
  riêng từng detector và không chứng minh nguyên nhân bỏ sót.
- Tự động phân luồng: 1 đề xuất hình học mạnh cần kiểm tra nhanh, 6 ca khó, 0
  candidate bị chặn. Ca khó gồm bất đồng lớp, sát mép, nghi gộp instance và ca
  bị ảnh hưởng bởi lỗi luật. `auto_assessments.json` ghi provenance máy, tách khỏi
  review người thật. Lần chốt dùng `review_progress_dang.json` và xác nhận trực
  tiếp của người dùng rằng đã review xong, bỏ qua cờ UI `submitted=false`.
  Nguồn JSON giữ nguyên, được chép vào bài nộp cùng `review_import.json`.
- Phạm vi hoàn tất: phần Nguyễn Hải Đăng trong P2. Status ghi
  `phase2_team_complete=false`; chưa thực hiện/tích hợp bbox_drift, instance_merge
  hoặc hoàn tất phần được giao cho Đặng Quốc Thái.

## 2. Vì sao báo hết ứng viên làm F1 giảm?
Bất đồng tự nhiên do che khuất/crowd/ghép nhầm có thể tạo false positive; nhận
hết ứng viên làm giảm precision và có thể giảm F1. Trong 7 đề xuất chính, người
xem confirmed 5 và rejected 2; bổ sung 3 confirmed về mask. Tổng 8 confirmed
được merge, 2 rejected giữ trong review log. Đây là verdict người xem,
không phải TP/FP theo đáp án. Không suy TP/FP từ điểm làm tròn hoặc thử chấm các
tập con. Lô sạch có 0 ứng viên ở ngưỡng khởi đầu.

## 3. Quyết định và lineage
<<QC v1 a/b, kết luận, vì sao R-021 phải xem lại hoặc train lại.>>

## 4. Hash chứng minh gì, không chứng minh gì?
<<>>

## 5. Truy vết
<<Tiêu chí tầng a/b, vì sao lấy guideline theo job.>>

## 6. Bug hoặc điểm chưa rõ trong lab (nếu có)
- Validator luật hiện cho box tràn 0.01 chuẩn hóa (1%) theo tolerance bộ handover,
  trong khi GUIDE yêu cầu bắt cả tràn nhẹ. Đã ghi nhận để người phụ trách P1 xem
  lại; triển khai phần Đăng chưa sửa rule này.
- Kiểm thử riêng P2 dùng fixture tự tạo, không đọc `_private` hay chế độ giảng viên.
  Browser preview trong app chặn URL `file://`, nên chưa QA giao diện bằng trình
  duyệt thật trong phiên này. Logic JavaScript được kiểm tra bằng Node với DOM
  giả lập; người dùng sau đó đã mở trang và lưu review thật.
- Lỗi UX của công cụ nhóm: bắt buộc điền category_swap làm chặn hoàn tất bbox
  dù đã rejected đề xuất. Đã sửa: chỉ bắt buộc loại được giao; mask là tùy chọn.
  Dòng mask cũ không có verdict/note được bỏ qua, không tự biến thành rejected.
- Hai rejected tại `000000007278`, `000000007888` không có note
  trong file người xem. Giữ verdict; note CSV ghi rõ đây là metadata import và
  nguồn không có ghi chú quan sát. Không coi metadata là lý do nhìn ảnh; chất
  lượng bằng chứng rejected chưa đầy đủ theo rubric dù đã xuất được file.
- Hai note bổ sung về mask tại `000000007278`, `000000019221` chưa chọn verdict:
  giữ nguyên trong source JSON và `review_import.json`, không tự suy thành lỗi.
- Ba category_swap được người xem chọn confirmed được giữ nguyên, kể cả khi
  note mô tả mask lấn biên/thiếu mask thay vì sai tên lớp. Điều này chưa chắc
  khớp định nghĩa category_swap của lab; không đổi type hoặc verdict qua điểm.
  Có ảnh được báo hai loại lỗi; khi làm P3, người phụ trách cần dùng đúng quy
  tắc đếm target của lab, không coi 19 finding là 19 ảnh khác nhau.
