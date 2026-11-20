# Báo cáo nhóm NguyenGiaHuy

## 1. Số liệu luyện tập
- F1 theo seed (rule / cross_check / overall): seed 6007 sau gộp review: ≈ 1.0 / ≈ 1.0 / ≈ 1.0, nộp 20 lỗi gồm 11 lỗi luật và 9 lỗi gán nhãn đã xác nhận. Seed 6008 ở P1: ≈ 1.0 / chưa có / ≈ 0.8, báo 12 lỗi luật. Các giá trị F1 được làm tròn theo kết quả tự đo. Lô sạch 50 ảnh báo 0 lỗi luật.
- Số ứng viên trên lô sạch (`--no-faults`): ban đầu 7 ứng viên trên 7 ảnh; sau sửa cách chọn cặp còn 2 ứng viên `instance_merge` trên 2/50 ảnh, cả hai đã được nhóm em xem và `rejected`. Hàng đợi validator của seed 6007 có 17 ứng viên trên 14 ảnh. Nhóm em dùng thêm bộ phân công review 18 ca trên 16 ảnh, tổng hợp thành 17 dòng gồm 9 `confirmed` và 8 `rejected`. Hai danh sách không trùng hoàn toàn; log có phát hiện ngoài hàng đợi validator, các ca khác chưa tự coi là lỗi.
- Ngưỡng đã chọn và cách chọn (không dùng `_private`): nhóm em giữ `drift_coverage=0.90`, `merge_min_fraction=0.15`, `merge_coverage=0.85`, `merge_max_iou=0.30`, `association_distance=1.0`, `min_segment_pixels=128`. Cấu hình ghép đầy đủ được ghi trong `candidates.json`. Qua xem ảnh, nhóm em phát hiện cặp box–mask khác đối tượng và sửa cách chọn cặp. Sau sửa, lô sạch còn 2 ca báo thừa và hàng đợi luyện tập còn 14 ảnh, đủ nhỏ để chia review. F1 làm tròn không chứng minh đã bắt hết mọi lỗi. Nhóm em không đọc `_private` hay dò đáp án qua điểm.

## 2. Vì sao báo hết ứng viên làm F1 giảm?
Trong lô sạch, cả 2 ứng viên `instance_merge` đều bị nhóm em bác bỏ: mask chỉ một người hoặc con vật bị che khuất, nằm giữa hai box của vật khác. Nếu báo cả hai là lỗi thì phát sinh 2 báo thừa (FP), làm giảm precision và có thể giảm F1. Với bộ review luyện tập, nhóm em giữ 9 dòng `confirmed`, loại 8 dòng `rejected`, rồi gộp với 11 lỗi luật thành 20 lỗi; F1 tổng sau gộp ≈ 1.0.

## 3. Quyết định và lineage
Nhóm em chốt QC v1 là 20/250 = 8%; theo quy ước lab, mỗi `scene_leak` tính là một ảnh lỗi. Tỉ lệ vượt ngưỡng 2% nên lô `lab250_9dec8553f4c2` được quyết định HOLD, đã ghi trong lineage. Lệnh impact trả về `R-021: affected=True action=review_or_retrain`. R-021 đã dùng lô v1, nên cần được xem lại hoặc train lại; sửa dataset không tự sửa model đã học từ dữ liệu cũ. Bản v2 `lab250_9dec8553f4c2_v2` đã được tạo làm release con.

## 4. Hash chứng minh gì, không chứng minh gì?
SHA-256 trong manifest giúp phát hiện file byte hiện tại khác với file đã ghi nhận; nó không chứng minh nội dung nhãn đúng hay ảnh phù hợp với nhãn. Hai ảnh `000000013659` và `000000025393` bị sai hash ở v1 nhưng mở được; v1 được giữ nguyên, còn manifest của v2 ghi hash thực tế của bản sao ảnh. Hash v2 khớp file sau khi đóng gói.

## 5. Truy vết
Ticket nêu `scene_733` và `scene_742`; tier_a gồm `000000002157`, `000000002431`, `000000003845`, `000000003934`. Tier_b gồm các ảnh khác có lớp cup, thuộc job guideline cũ: `000000002592`, `000000002685`, `000000003661`. Cả hai tầng dùng guideline theo `jobs.csv`, không dùng phiên bản trong metadata ảnh vì chính metadata có thể sai. Các job liên quan do A02 gán và Q01 kiểm nên `notify` là A02, Q01.

## 6. Bug hoặc điểm chưa rõ trong lab (nếu có)
Hàng đợi validator có 17 ứng viên nhưng review log 17 dòng không phủ đúng các cặp ảnh-loại: có cặp ứng viên chưa có dòng review và có dòng review nằm ngoài hàng đợi. Các dòng ngoài hàng đợi không tự được xem là lỗi; errors.json chỉ nhận các verdict đã xác nhận. Mọi lỗi gán nhãn được nộp đều có dòng confirmed nên kiểm tra H3 đạt, nhưng không nên tuyên bố toàn bộ ứng viên đã được phân xử. Handover v2 đạt 17/17 kiểm tra định dạng; kết quả này không tự chứng minh nhãn đúng.
