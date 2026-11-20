Release: lab250_5a8f63fb9dd0_v2
Parent: lab250_5a8f63fb9dd0
Số ảnh: 240
Số nhãn: 1688
Guideline: v1.2 (J01-J03), v1.3 (J04-J10)
QC v1: 20/250 = 8.0% (mỗi target trong errors.json tính một lần)
Kết luận v1: Hold — 8.0% vượt ngưỡng 2%; lineage đã ghi HOLD.
Kết luận v2: Release — tách ảnh lỗi detection sang rework; giữ nguyên nhãn và nội dung ảnh còn lại; xử lý guideline/hash/split trong bản v2.
Ghi chú: v1 không bị sửa. Ảnh lỗi detection đưa lại cho annotator/reviewer theo rework_queue.csv: 000000006460 - A04/Q02, 000000006614 - A04/Q02, 000000009769 - A04/Q01, 000000010707 - A02/Q02, 000000011122 - A02/Q02, 000000017379 - A05/Q03, 000000017436 - A05/Q03, 000000018150 - A04/Q01, 000000019221 - A04/Q01, 000000020247 - A04/Q01. Hai ảnh wrong_hash (000000013659, 000000025393) được giữ nguyên byte ảnh; manifest v2 ghi SHA-256 thực tế sau khi kiểm tra ảnh mở được. Sửa guideline theo job cho 000000007795. Gộp split scene_615 về val trong truy vết v2 (4 val, 1 train ở v1). Các lỗi panoptic giữ trong v2, báo nhóm panoptic: category_swap 000000025096; instance_merge 000000007888, 000000024919; invalid_category_id 000000014380; thing_overlap 000000007278, 000000015956. Đây là bàn giao detection, không tuyên bố các lỗi panoptic đã sửa.
