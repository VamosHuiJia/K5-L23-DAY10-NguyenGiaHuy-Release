# Validator của nhóm

Giao diện cố định:

```
python validator/validate.py <thư_mục_delivery> --out <errors.json> [--candidates <candidates.json>]
```

Chỉ đọc thư mục delivery được truyền vào; chỉ thư viện chuẩn; không mạng. Xem [GUIDE.md](../GUIDE.md) Pha 1–2 và [RULES.md](../RULES.md).

## Phase 2 — phần Nguyễn Hải Đăng

Tự động sinh/cache, phân luồng ca khó, tự lưu review local và tổng hợp sau khi
người xem hoàn tất:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/phase2.py serve --run runs/r6007
```

Mở `http://127.0.0.1:8765/` bằng Chrome/Edge. Server chỉ bind loopback; chỉ phục vụ
các trang review được tạo, không phục vụ delivery hoặc private files. `prepare`
chỉ tạo file; `finalize --export` kiểm tra/tổng hợp và chép bài đã review sang
submission. Code không tự tạo kết luận mang tên người xem.

`cross_check.py` sinh đề xuất `class_swap` (có thể thực tế là `category_swap`)
và `missing_box`. `--out` vẫn chỉ chứa lỗi luật; ứng viên không tự trở thành lỗi.
Hướng dẫn thao tác: [docs/PHASE2_DANG.md](../docs/PHASE2_DANG.md).

```powershell
.\.venv\Scripts\python.exe -X utf8 validator/validate.py runs/r6007/delivery --out runs/r6007/rules.json --candidates runs/r6007/candidates.json
.\.venv\Scripts\python.exe -X utf8 tools/review_phase2.py runs/r6007/delivery --candidates runs/r6007/candidates.json --out runs/r6007/review.html
```

### Dữ liệu ứng viên v1

Top-level: `schema_version`, `release_id`, `config`, `candidates`, `diagnostics`,
`summary`. Không có verdict tự động. Mỗi candidate có:

- `id`: `<image stem>:<proposed type>`; `image`: tên không đuôi, giữ số 0 đầu.
- `type`, `possible_types`, `reason`, `image_available`.
- `evidence`: các quan sát cùng ảnh/loại; mỗi quan sát chứa `segment`,
  `detection` (có thể null), `bbox_iou`, `mask_coverage`, `nearest_boxes`.
- Detection: `row` (dòng YOLO từ 1), `class_id`, `class_name`, `bbox_xyxy`.
- Segment: `id`, `category_id`, `class_name`, `bbox_xyxy`, `mask_pixels` thực đếm.
- Bbox là pixel `[left, top, right, bottom]`, không phải YOLO chuẩn hóa.

`summary.by_type.class_swap` đếm **bất đồng lớp chưa phân xử**, không phải số
lỗi detection đã xác nhận. Một candidate có thể gom nhiều segment cần xem.

### Ghép và ngưỡng khởi đầu

`Thresholds` tập trung ở đầu `cross_check.py`: IoU ≥ 0.50, hoặc mask coverage
≥ 0.85 với diện tích bbox detection / bbox segment trong [0.25, 4]. Segment
thiếu box phải có ít nhất max(128 pixel, 0.0005 × diện tích ảnh), mỗi chiều bbox
≥ 8 pixel. Không xét stuff/crowd; ánh xạ lớp bằng tên. Mọi ngưỡng đều được
ghi vào JSON. Nếu điều chỉnh sau review lô sạch, ghi thay đổi và lý do vào báo
cáo, sinh lại ứng viên và review HTML; progress cũ sẽ bị từ chối nếu fingerprint
thay đổi. Không chọn ngưỡng bằng cách dò điểm từng ảnh.

### Tích hợp phần Thái

`DeliveryReader.compare(image)` trả `Comparison` gồm boxes, segments, mask
`spans` dạng `[y, left, right_exclusive]`, và mọi cặp trong `pairs[segment_id]`.
Không ép ghép 1–1. Thái có thể dùng lại để phát hiện drift/merge; thêm record
`bbox_drift` hoặc `instance_merge` theo cùng contract và giữ ID duy nhất. Viewer
hỗ trợ cả năm type, nhưng hiện chỉ có hai detector đề xuất của Đăng. Ghép xong
phải sinh lại viewer và phân công lại hàng đợi, không tự chép verdict giữa hai
fingerprint khác nhau.

Output nằm trong delivery bị từ chối để bảo vệ v1. Label/mask thiếu hoặc không
đọc được sinh diagnostic; người phụ trách phải xem diagnostic trước khi tuyên bố
đã kiểm hết lô.

### Kiểm thử độc lập

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_phase2.py -v
node tests/test_review_ui.cjs
```

Python kiểm tra bằng delivery tổng hợp, không đọc truth. Node chỉ cần cho test
logic trang; vận hành validator/viewer không cần Node, Pillow, CVAT hay server.
