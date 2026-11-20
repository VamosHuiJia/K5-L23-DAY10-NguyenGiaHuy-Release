# Báo cáo nhóm NguyenGiaHuy

## 1. Số liệu luyện tập
- **F1 tự đo (rule / cross_check / overall):** seed 6007 sau gộp review lần lượt ≈ 1.0 / ≈ 1.0 / ≈ 1.0; `errors.json` có 20 lỗi, gồm 11 lỗi luật và 9 lỗi gán nhãn đã được xác nhận. Seed 6008 mới ở P1: rule ≈ 1.0, có 12 lỗi luật; `cross_check` chưa thực hiện. Overall ≈ 0.8 là kết quả ở giai đoạn P1, không cùng giai đoạn với seed 6007 sau review nên không so sánh trực tiếp. F1 được làm tròn một chữ số theo kết quả tự đo.
- **Lô sạch (`--no-faults`, 50 ảnh):** nhóm ghi nhận validator không báo lỗi luật và còn 2 ứng viên `instance_merge` trên 2/50 ảnh; nhóm đã xem và bác bỏ cả hai. Tuy nhiên output và review log riêng của lô sạch hiện chưa có trong hồ sơ nộp nên các số này chưa thể kiểm tra độc lập.
- **Hàng đợi và review seed 6007:** validator sinh 17 ứng viên trên 14 ảnh. `review_log.csv` hiện có 26 dòng: 9 `confirmed`, 17 `rejected`. Có review ngoài hàng đợi validator; chỉ các dòng `confirmed` được gộp vào danh sách lỗi.
- **Ngưỡng và sửa logic ghép:** nhóm dùng `drift_coverage=0.90`, `merge_min_fraction=0.15`, `merge_coverage=0.85`, `merge_max_iou=0.30`, `association_distance=1.0`, `min_segment_pixels=128`. Cấu hình trong `r6007/candidates.json` là của hàng đợi seed 6007, không phải bằng chứng cho lần chạy lô sạch. Khi xem ảnh, nhóm phát hiện một số cặp box–mask thuộc 2 object và sửa logic chọn cặp. Do không còn output lô sạch trước và sau sửa, nhóm không nêu mức giảm số ứng viên. F1 làm tròn không chứng minh đã bắt hết mọi lỗi.

## 2. Vì sao báo hết ứng viên làm F1 giảm?
Ứng viên do validator sinh ra cần review trực tiếp, không mặc định là lỗi. Trong review seed 6007, 17/26 dòng bị `rejected`; ví dụ một số `instance_merge` thực chất là box trùng đã được `duplicate_box` bắt. Nếu báo các ca bị bác bỏ như lỗi, chúng sẽ thành FP, làm precision giảm và có thể kéo F1 xuống. Vì vậy nhóm chỉ gộp 9 dòng `confirmed` cùng 11 lỗi luật vào `errors.json`.

## 3. Quyết định và lineage
Nhóm em chốt QC v1 là 20/250 = 8%; theo quy ước lab, mỗi `scene_leak` tính là một ảnh lỗi. Tỉ lệ vượt ngưỡng 2% nên lô `lab250_5a8f63fb9dd0` được quyết định HOLD, đã ghi trong lineage. Lệnh `python scripts/lineage.py --root submission impact --release-id lab250_5a8f63fb9dd0` trả về `R-021: affected=True action=review_or_retrain`. R-021 đã dùng lô v1, nên cần được xem lại hoặc train lại; sửa dataset không tự sửa model đã học từ dữ liệu cũ.

## 4. Hash chứng minh gì, không chứng minh gì?
SHA-256 giúp kiểm tra file hiện tại có khớp với digest đã ghi trong manifest hay không; không chứng minh nhãn đúng về ngữ nghĩa, ảnh đúng nguồn, hoặc file chưa từng bị sửa trước lúc tính hash. `errors.json` ghi `wrong_hash` cho ảnh `000000013659` và `000000025393`. Nhóm kiểm tra ảnh mở được, giữ nguyên byte ảnh ở v2 và ghi digest vào manifest; hash của hai file trong v2 hiện khớp manifest. Kết quả hash không tự chứng minh nội dung nhãn đúng.

## 5. Truy vết
Theo tiêu chí lab, `tier_a` là ảnh trong scene được ticket nêu, thuộc job dùng guideline cũ và có box thuộc lớp bị đổi quy tắc; `tier_b` là ảnh khác đáp ứng cùng điều kiện nhưng ngoài scene đã báo; `notify` là annotator và reviewer của các job liên quan. Nhóm ghi `000000002157`, `000000002431`, `000000003845`, `000000003934` vào `tier_a`; `000000002592`, `000000002685`, `000000003661` vào `tier_b`; notify `A02`, `Q01`. `truyvet.csv` cho thấy các ảnh này thuộc job `J02`, guideline `v1.2`. Dùng guideline theo job vì đó là phiên bản được giao cho annotator; metadata theo ảnh có thể sai, như lỗi `guideline_mismatch` của `000000007795`.

## 6. Bug hoặc điểm chưa rõ trong lab (nếu có)
Hàng đợi và review log không khớp một-một: có 17 cặp (ảnh, loại lỗi) trong `candidates.json` nhưng 26 cặp trong `review_log.csv`; 16 cặp khớp chính xác, một ứng viên `class_swap` ở `000000025096` được review dưới nhãn `category_swap`, và 10 cặp review nằm ngoài hàng đợi. Vì vậy không nên nói mọi ứng viên đều có verdict tương ứng; `errors.json` chỉ nhận các lỗi đã `confirmed`.
