# Phạm vi chốt Phase 2 — phần Nguyễn Hải Đăng

Đã chốt từ bản `review_progress_dang.json` được người dùng xác nhận review xong
ngày 19/11/2026. Bỏ qua cờ `submitted=false` của UI; giữ nguyên file nguồn.

- Hoàn tất đối chiếu sai lớp/thiếu box và review 7 ảnh: 5 confirmed chính,
  2 rejected chính. Thêm 3 category_swap confirmed do người xem bổ sung.
- `review_log.csv`: 10 dòng, 8 confirmed và 2 rejected. Các dòng không có
  verdict không được biến thành kết luận; hai ghi chú mask được lưu trong
  `review_import.json` và source JSON.
- `errors.json`: 19 finding = 11 lỗi luật + 8 semantic confirmed.
- F1 chính thức làm tròn: overall 0.8, rule 1.0, cross_check_review 0.6.
- Lô sạch: 50 ảnh, 0 lỗi luật, 0 ứng viên từ detector hiện tại.
- Hai rejected không có note quan sát trong nguồn. CSV ghi rõ metadata này;
  không coi metadata là lý do nhìn ảnh. Hạn chế về ý nghĩa các type mask được
  ghi trong REPORT; không tự sửa verdict dựa vào điểm.

Chưa làm/chưa tích hợp phần **Đặng Quốc Thái**: bbox_drift, instance_merge và
phần được phân công cho Thái. Status ghi `phase2_team_complete=false`.
Đây là hoàn tất phần Đăng, không phải tuyên bố hoàn tất P2 của cả nhóm hoặc
các pha release/lineage/v2/trace/security/đề ẩn.

Đối chiếu nguồn: `review_progress_dang.json`, `review_import.json`,
`phase2_status.json`, `report.md`. Không kèm dữ liệu gốc, private truth hay ZIP.
