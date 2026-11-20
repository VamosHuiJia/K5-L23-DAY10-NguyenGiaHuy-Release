# Review 11 lỗi luật bằng UI

Từ gốc repo:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/review_rules.py --run runs/r6007
Invoke-Item .\runs\r6007\review_rules.html
```

Trang tự chứa ảnh, mở offline bằng Chrome/Edge. Không cần chạy lại server P2.
Trong seed 6007 có 11 finding luật thuộc 8 loại, liên quan 15 ảnh; một finding
scene_leak đại diện cả scene, không phải chỉ một ảnh. Invalid_class là loại
luật thứ chín được hỗ trợ nhưng không xuất hiện trong danh sách hiện tại.

1. Nhập tên người đang kiểm tra. Chọn mục lỗi bằng dropdown hoặc Trước/Sau.
2. Xem ảnh và bằng chứng số liệu bên phải, đọc trích dẫn từ docs/DELIVERY.md và
   GUIDE.md có tên file/số dòng. Link trỏ đến đúng tài liệu lab trên GitHub;
   quote được lấy nguyên văn từ file local, không tự sinh quy tắc mới.
3. Với scene_leak: chọn từng ảnh trong scene và so bảng split cả cảnh. Với hash:
   xem SHA-256 kỳ vọng/thực tính và mở ảnh để nhận biết nguyên vẹn hay hỏng.
   Với guideline_mismatch: lấy jobs.csv làm chuẩn, không lấy phiên bản ảnh.
4. Box liên quan được tô nổi, có thể chọn từng số dòng box để xem trùng/lệch
   biên. Với panoptic, mask của segment có vấn đề được bật sẵn; bảng hiện ID,
   category, diện tích khai và pixel thật. Ảnh thiếu có placeholder và bằng
   chứng manifest/file; vẫn có thể xác nhận luật missing_image.
5. Đánh dấu đã kiểm tra, chọn confirmed hoặc rejected. **Cả hai đều tính đã
   review** khi có tên người xem. Ghi chú luật là tùy chọn, không yêu cầu thêm
   dòng category_swap hoặc ghi đủ 20 ký tự như review semantic.
6. Có lưu tạm trong localStorage nếu trình duyệt hỗ trợ. Bấm **Lưu progress
   JSON** để sao lưu/bàn giao chắc chắn. **Nạp progress luật** chỉ nhận đúng lô
   và bằng chứng, không nhận file review semantic cũ.
7. Khi đủ 11/11, bấm **Xuất CSV review luật**. File tải về Downloads; chép JSON
   vào `runs/r6007/rule_review_progress.json`, CSV vào `runs/r6007/rule_review_log.csv`.

Các file này là audit bổ sung cho lỗi luật. Chúng không sửa `errors.json`,
review semantic đã chốt, bản nộp hoặc delivery; không dùng `merge_review.py`
để gộp CSV luật. Cần yêu cầu riêng nếu muốn điều chỉnh finding sau review.

## Chốt khi scene/hash để máy kiểm tra

Không cần chọn verdict bằng mắt cho scene_leak/wrong_hash. Tái chạy luật vào
file riêng, rồi đối chiếu với progress người xem:

```powershell
.\.venv\Scripts\python.exe -X utf8 validator/validate.py runs/r6007/delivery --out runs/r6007/rule_review_audit/machine_rules.json
.\.venv\Scripts\python.exe -X utf8 tools/review_rules.py --run runs/r6007 --audit-progress runs/r6007/rule_review_progress.json --machine-rules runs/r6007/rule_review_audit/machine_rules.json
```

Audit bỏ qua state UI của hai loại này, tính lại SHA-256 và split theo scene.
Các verdict người xem được giữ nguyên; không ký kết quả máy dưới tên người.
`rule_review_audit/result.json` và CSV đặt hai cột human_verdict/machine_result
riêng, ghi rõ bất đồng. Hai loại máy vẫn phải có bằng chứng tái tính phù hợp,
không chỉ dựa vào việc một finding có sẵn trong file JSON.

Với thing_overlap, định nghĩa lab là **area khai báo > số pixel thực tế**, không
phải kết luận đơn thuần từ cảm giác mask phủ nhiều/ít khi xem ảnh. Hash khác
manifest chứng minh checksum không khớp; chưa tự chứng minh ảnh đã hỏng. Việc
ảnh nguyên vẹn hay hỏng để quyết định sửa manifest/rework thuộc P4.

Ngày 20/11/2026, người dùng đã đồng ý kết quả máy và yêu cầu sửa hai verdict
thing_overlap. Bản evaluation mới ở `submission/r6007/rule_review/`: 8 human
confirmed, 0 rejected, 3 mục giao máy. `corrections.json` và bản before_correction
giữ rõ nguồn trước/sau, số liệu và yêu cầu chấp thuận; không đổi semantic review.

Nguồn các luật là docs/DELIVERY.md §2 và GUIDE.md P1. Changelog trong delivery
chỉ nói về thay đổi cup v1.2/v1.3; không dùng nó để suy ra mọi quy tắc box/mask.
Với bbox tràn biên, UI hiện cả tràn nhẹ theo GUIDE và nói rõ validator P1 hiện
có tolerance 0.01; với duplicate box, UI ghi rõ tolerance code dùng để đối chiếu.

Test offline, không truy cập private truth/mạng:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_rule_review.py -v
node tests/test_rule_review_ui.cjs
```
