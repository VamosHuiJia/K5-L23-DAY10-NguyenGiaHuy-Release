# API ghép detection–panoptic để nối phần lệch box / gộp instance

Nhánh: `nguyenhaidang_03190`. Không cần merge `main` để dùng API. Code chỉ dùng
thư viện chuẩn và `scripts/pngutil.py`, đọc dữ liệu trong delivery được truyền vào.

## Hàm dùng ngay

```python
from pathlib import Path
from validator.cross_check import DeliveryReader

reader = DeliveryReader(Path("runs/r6007/delivery"))
result = reader.match("000000007888")  # tên ảnh không đuôi, giữ số 0 đầu
if result is None:
    print(reader.diagnostics)
else:
    print(result["file_name"], result["width"], result["height"])
    print(result["boxes"])
    print(result["segments"])
    print(result["matches"])
```

Khởi tạo reader một lần cho cả lô:

```python
for image in sorted(reader.manifest):
    result = reader.match(image)
    if result is None:
        continue  # ghi/đọc reader.diagnostics, không tự coi là không có lỗi
    # Gọi hàm lệch box / gộp instance của em tại đây.
```

`DeliveryReader.match(image, config=None) -> dict | None` không gọi detector
sai lớp/thiếu box và không quyết định confirmed/rejected. Ảnh không có trong
manifest gây `KeyError`; lỗi đọc label/mask/kích thước ghi diagnostic và trả None.
File ảnh bị thiếu vẫn có thể trả metadata/nhãn với `image_available=false`.

## Các trường trả về (schema_version=1)

| Trường | Ý nghĩa |
|---|---|
| `image`, `file_name` | Mã ảnh không đuôi và tên file ảnh có đuôi |
| `width`, `height` | Kích thước pixel từ bản ghi ảnh panoptic; kiểm tra khớp mask |
| `boxes` | Danh sách box: `row`, `class_id`, `class_name`, `bbox_xyxy` |
| `segments` | Danh sách segment hợp lệ: `id`, `category_id`, `class_name`, `iscrowd`, `isthing`, `bbox_xyxy`, `mask_pixels` |
| `pairs` | Mọi cặp box–segment có đo hình học, kể cả cặp overlap bằng 0 |
| `matches` | Các cặp được chọn theo ngưỡng không phụ thuộc lớp; là tập con của `pairs` |
| `unmatched_box_rows` | Box chưa có cặp nào được chọn |
| `unmatched_segment_ids` | Segment thing không crowd chưa có cặp nào được chọn |
| `matching_config` | Giá trị ngưỡng được dùng |

- `row`/`box_row` là **số dòng YOLO từ 1**, không phải index Python từ 0. Nếu có
  dòng trống hoặc dòng nhãn lỗi, số row của các box hợp lệ vẫn theo file gốc.
- `bbox_xyxy` là `[left, top, right, bottom]` theo **pixel, có thể là số thực**.
  Không làm tròn, không tự clip box lỗi vào mép ảnh.
- Detection và panoptic dùng hệ ID lớp khác nhau; `same_class` so sánh **tên**.
- Mỗi phần tử trong `pairs`/`matches` chứa `box_row`, `segment_id`, `bbox_iou`,
  `mask_coverage`, `area_ratio`, `same_class`, `eligible_for_matching`, `matched`.
- `mask_coverage` = số pixel thật của segment nằm trong box / tổng pixel thật
  của segment. `area_ratio` = diện tích bbox detection / diện tích bbox segment.
- `iscrowd` và `isthing` là 0/1. Trả cả crowd/stuff trong `segments` và giữ số đo
  trong `pairs`, nhưng `eligible_for_matching=false`, không chọn vào `matches`.

## Quy tắc chọn cặp

Thing không crowd, có pixel thật được chọn khi IoU ≥ 0.50 **hoặc** mask coverage
≥ 0.85 và area_ratio trong [0.25, 4]. Có thể truyền `Thresholds(...)` làm `config`
nếu phần của em cần ngưỡng khác. `matches` **không ép 1–1**; một segment có thể
ghép với nhiều box và ngược lại. Cặp khác lớp vẫn được giữ để hai phần không
bỏ mất bằng chứng. `same_class` cho phép hàm downstream tự lọc.

Với bbox_drift, đừng chỉ dùng `matches`: box lệch nhiều có thể không đạt ngưỡng;
dùng cả `pairs` và `unmatched_*`. Với instance_merge, gom các cặp theo segment_id
rồi xem nhiều box cùng tên lớp có phủ các phần pixel khác nhau của segment.
API cung cấp bằng chứng, không tự kết luận đó là lỗi drift/merge.

Nếu cần pixel chính xác thay vì JSON, dùng:

```python
context = reader.compare(image, include_all_segments=True)
# context.spans[segment_id]: các run [y, left, right_exclusive]
# context.pairs[segment_id]: mọi số đo với các detection box
```

`compare(image)` mặc định giữ hành vi cũ (chỉ thing không crowd), không đổi định
dạng candidates hoặc làm mất khả năng nhập review đã lưu.

## Ví dụ và test

`examples/matching.sample.json` là output đầy đủ từ **fixture tự tạo**, không
phải kết luận trên ảnh lab. Ví dụ có 2 box `truck` cùng ghép segment 523:

```json
{
  "image": "img_0001",
  "file_name": "img_0001.png",
  "width": 64,
  "height": 64,
  "boxes": [
    {"row": 1, "class_id": 0, "class_name": "truck", "bbox_xyxy": [10, 10, 30, 30]},
    {"row": 2, "class_id": 0, "class_name": "truck", "bbox_xyxy": [30, 10, 50, 30]}
  ],
  "segments": [
    {"id": 523, "class_name": "truck", "iscrowd": 0, "isthing": 1}
  ],
  "matches": [
    {"box_row": 1, "segment_id": 523, "bbox_iou": 0.5, "mask_coverage": 0.5},
    {"box_row": 2, "segment_id": 523, "bbox_iou": 0.5, "mask_coverage": 0.5}
  ]
}
```

Đoạn trên là trích gọn; JSON trong examples có đủ mọi trường. Test contract:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_matching.py -v
```
