# Báo cáo nhóm NguyenGiaHuy

## 1. Số liệu luyện tập
- F1 theo seed (rule / cross_check / overall): kết quả đo ở P1 của seed 6007: ≈ 1.0 / chưa có / ≈ 0.7, báo 11 lỗi luật; seed 6008: ≈ 1.0 / chưa có / ≈ 0.8, báo 12 lỗi luật. Các giá trị F1 được làm tròn theo kết quả tự đo. Nhóm em kiểm tra thêm lô sạch 50 ảnh, validator báo 0 lỗi luật. Chưa đo F1 lỗi gán nhãn sau review luyện tập.
- Số ứng viên trên lô sạch (`--no-faults`): ban đầu 7 ứng viên trên 7 ảnh; sau sửa cách chọn cặp còn 2 ứng viên `instance_merge` trên 2/50 ảnh. Nhóm em xem và `rejected` cả hai vì mask chỉ một vật bị che khuất nằm giữa hai box. Hàng đợi seed 6007: 17 ứng viên trên 14 ảnh, gồm 3 `bbox_drift`, 3 `class_swap`, 7 `instance_merge`, 4 `missing_box`; chưa review xong.
- Ngưỡng đã chọn và cách chọn (không dùng `_private`): nhóm em đang giữ `drift_coverage=0.90`, `merge_min_fraction=0.15`, `merge_coverage=0.85`, `merge_max_iou=0.30`, `association_distance=1.0`, `min_segment_pixels=128`. Cấu hình ghép đầy đủ được ghi trong `candidates.json`. Qua xem ảnh, nhóm em phát hiện cặp box–mask khác đối tượng và sửa cách chọn cặp. Sau sửa, lô sạch còn 2 ca báo thừa và hàng đợi luyện tập còn 14 ảnh để chia cho cả nhóm review. Nhóm em chưa kết luận về độ bỏ sót lỗi gán nhãn, không đọc `_private` hay dò đáp án qua điểm.

## 2. Vì sao báo hết ứng viên làm F1 giảm?
Trong lô sạch, cả 2 ứng viên `instance_merge` đều bị nhóm em bác bỏ sau khi xem ảnh: mask chỉ một người hoặc con vật bị che khuất, nằm giữa hai box của vật khác. Nếu báo cả hai là lỗi thì phát sinh 2 báo thừa (FP), làm giảm precision và có thể giảm F1. Seed 6007 hiện có 17 ứng viên chưa review hết, nên nhóm em chưa coi đó là 17 lỗi thật.

## 3. Quyết định và lineage
<<QC v1 a/b, kết luận, vì sao R-021 phải xem lại hoặc train lại.>>

## 4. Hash chứng minh gì, không chứng minh gì?
<<>>

## 5. Truy vết
<<Tiêu chí tầng a/b, vì sao lấy guideline theo job.>>

## 6. Bug hoặc điểm chưa rõ trong lab (nếu có)
<<>>
