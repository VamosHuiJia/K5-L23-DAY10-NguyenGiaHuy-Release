# Đánh giá 11 lỗi luật — kết quả đã đồng thuận

Phạm vi: release lab250_5a8f63fb9dd0. Người dùng đã review và giao scene_leak /
wrong_hash cho máy. Ngày 20/11/2026, Nguyễn Hải Đăng xác nhận đồng ý với kết quả
máy ở hai thing_overlap và yêu cầu đổi hai verdict rejected thành confirmed.
Không yêu cầu quay lại UI để điền state của các mục giao máy.

- 8 mục confirmed bởi người review, 0 rejected; 3 mục giao máy.
- Máy tái lập đủ 11 finding luật; hiện không còn bất đồng human/machine.
- Hai chỉnh sửa có ủy quyền rõ ràng, kèm số liệu tính được và trích dẫn định
  nghĩa. Không tự mô tả lại quan sát hình ảnh thay người xem.
- Các verdict khác giữ nguyên, kể cả 3 verdict UI còn trống cho scene/hash.

## Hai sửa đổi được người dùng chấp thuận

| Ảnh | Segment | Area khai | Pixel thật | Chênh lệch | Trước | Sau |
|---|---:|---:|---:|---:|---|---|
| 000000007278 | 6973051 | 5792 | 4638 | 1154 | rejected | confirmed |
| 000000015956 | 7634552 | 357 | 261 | 96 | rejected | confirmed |

Theo **docs/DELIVERY.md:46**, thing_overlap là area khai nhiều hơn pixel thật;
**GUIDE.md:69** yêu cầu đếm pixel mask so với area. Việc mask có ít pixel hơn
không bác bỏ luật này. Người dùng đã chấp thuận giải thích và hai verdict trên.

## Các kiểm tra được giao máy

- scene_615 có 4 ảnh val và 1 ảnh train (000000025139), lấy từ metadata.json.
- Hai wrong_hash đều có SHA-256 thực tế khác manifest.
- Guideline mismatch riêng: ảnh 000000007795 ghi v1.2, job J04 quy định v1.3.

- 000000013659:
  - Manifest: `ab798f0ee5d1bed52376a5ee8bfbd973cb4c4fface481a9bc7a3ff5d9c57b4c7`
  - Thực tế: `b357d19dee467587a8788ac4e5b45677f4fde1b62726c690795e188ae75d822b`
- 000000025393:
  - Manifest: `f3a2974ce3686332609124c70e3e6a2e3aca43fccf1cd1bd7c5c03820977f57d`
  - Thực tế: `677e993476aed8ceca9bd9b8edbeb3c8874ed19488c9050cb8b55057a5f1547a`

Nguồn checksum: docs/DELIVERY.md:39, GUIDE.md:66. Khác checksum chưa chứng minh
ảnh hỏng; GUIDE.md:131 yêu cầu kiểm tra ảnh trước quyết định sửa hash/rework ở P4.

## Truy vết

- `rule_review_progress.before_correction.json`: bản review nguyên vẹn trước sửa.
- `corrections.json`: yêu cầu đồng ý của người dùng, hai thay đổi, note trước/sau
  và SHA-256 nguồn trước/sau.
- `rule_review_progress.json`: bản review đã sửa theo yêu cầu.
- `machine_rules.json`: đầu ra validator độc lập.
- `result.json`: bằng chứng số liệu, trích dẫn, verdict người/máy tách biệt.
- `rule_review_log.csv`: bảng đánh giá có cột human_verdict và machine_result.

Audit không tự thay verdict trong nguồn đang nhận; lịch sử sửa theo yêu cầu của
người dùng nằm ở corrections.json. Không đổi findings đã chốt, không sửa delivery
v1 và không đổi review semantic hoặc phần của Thái. Đây là evaluation bổ sung
cho luật, không phải hoàn thành v2/lineage/trace/security hay đề ẩn.
