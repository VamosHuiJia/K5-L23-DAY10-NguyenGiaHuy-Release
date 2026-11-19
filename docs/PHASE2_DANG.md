# Phase 2 — Nguyễn Hải Đăng cần làm gì

Phần mềm đã chuẩn bị đối chiếu **sai lớp / thiếu box** và một trang review offline.
Việc còn cần Đăng làm là **trực tiếp xem ảnh, ghi kết luận thật, phối hợp với Thái,
và bàn giao bằng chứng**. Không cần mở CVAT hoặc tạo task mới cho quy trình này.
Không gán lại box hay sửa nhãn v1 trong Phase 2.

## Đã chốt phần Đăng từ file review người dùng lưu

Ngày 19/11/2026, người dùng xác nhận đã review đủ 7 ảnh và yêu cầu chốt phần Đăng,
trừ phần Thái. Đã nhập `runs/r6007/review_progress_dang.json`, giữ nguyên file
nguồn cập nhật: 7 đề xuất chính có 5 confirmed, 2 rejected; thêm 3 confirmed
category_swap người xem đã chọn. Log có 10 dòng, 8 confirmed và 2 rejected.
Hai ghi chú mask chưa chọn verdict chỉ được lưu làm ghi chú, không tự kết luận.
Đã merge thành 19 finding và export sang `submission/r6007/`. F1 làm tròn:
overall 0.8 / rule 1.0 / semantic 0.6. Hạn chế note được ghi trong REPORT.

Lệnh tái lập khi người dùng xác nhận file đã review xong:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/phase2.py finalize --run runs/r6007 --review-progress runs/r6007/review_progress_dang.json --export
```

Lệnh nhập này bỏ qua cờ submitted của UI cũ, vẫn kiểm tra release, fingerprint,
đủ candidate, đã xem ảnh và verdict. Rejected thiếu note được giữ và đánh dấu
thiếu bằng chứng quan sát; không viết lý do thay người xem. Các phần dưới mô tả
quy trình chung/số liệu ban đầu, không yêu cầu Đăng review lại. Toàn P2 của nhóm
chưa hoàn tất vì phần Thái được loại khỏi scope.

## 0. Cách tự động mới — chỉ cần một lệnh

Đứng ở thư mục repo, chạy:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/phase2.py serve --run runs/r6007
```

Giữ cửa sổ PowerShell này chạy, mở **http://127.0.0.1:8765/** trong Chrome/Edge.
Không cần CVAT, không cần upload hay tải/copy JSON sau mỗi phiên. Chỉ một tiến
trình server cho run này; mở nhiều tab sửa cùng phần sẽ bị chặn nếu bản cũ định
ghi đè bản mới. Nhấn Ctrl+C trong PowerShell để dừng server sau khi xong.

Tự động thực hiện:

- Kiểm tra hash delivery và tái sử dụng cache nếu dữ liệu/code/output không đổi.
- Sinh rules, candidates; đo lại lô sạch nếu `runs/clean/delivery` đang có.
- Ghi `auto_assessments.json`, chia trang **Ca khó**, **Kiểm tra nhanh**, **Bị chặn**.
- Đề xuất máy có `source=automated_geometry`, `human_reviewed=false`; không phải
  verdict của Đăng và không được tự đưa vào review log.
- Tự lưu thao tác người xem vào `runs/r6007/reviews/<luồng>.json`, tự nạp lại khi
  mở trang. Có xung đột thì giữ bản mới trên đĩa và yêu cầu người xem đối chiếu.
- Sau khi các phần có ứng viên đều được bấm **Hoàn tất phần này**, kiểm tra đủ
  review, tạo `review_log.csv`, merge `errors.json`, chạy scorer một lần cho kết
  quả hoàn tất. Không chạy scorer khi đang gõ note hoặc còn bản nháp.

Việc Đăng thực hiện:

1. Mở **Ca khó** và **Kiểm tra nhanh**, nhập tên thật của người đang xem.
2. Xem ảnh, đánh dấu đã xem, điền verdict và note như mục 3 bên dưới. Không có
   verdict/ghi chú được điền sẵn dưới tên Đăng. Ca khó phù hợp chuyên môn Thái
   thì cùng Thái xử lý; đổi tên người review trước khi người đó nhập kết luận.
3. Bấm **Hoàn tất phần này** khi xem xong mỗi phần. Nút này kiểm tra đủ thông tin
   rồi mới cho tổng hợp. Đợi thông báo đã tự lưu trước khi đóng tab.
4. Nếu server/lưu thất bại, **Lưu progress JSON** làm bản dự phòng. Không bỏ bản
   đang có trên đĩa để ép qua lỗi xung đột.
5. Sau khi hệ thống báo complete, đọc report thật, bổ sung nhận xét của nhóm,
   rồi export nếu cần:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/phase2.py finalize --run runs/r6007 --export
```

Kết quả phân luồng hiện tại: **1 kiểm tra nhanh**, **6 ca khó**, **0 candidate bị
chặn**. Ca nhanh là đề xuất missing_box ở `000000019221`; vẫn cần người thật xem
và xác nhận theo RULES. Ca khó: ba bất đồng lớp, một vật sát mép ảnh, một nghi
gộp instance, một so sánh bị ảnh hưởng bởi lỗi luật đã phát hiện. Diagnostic
thiếu ảnh/category không hợp lệ vẫn nằm trong candidate JSON, không bị bỏ đi.

Không có giám định hình ảnh tự động được ghi thành bằng chứng review của con
người. RULES yêu cầu mọi confirmed/rejected là kết luận người thật; vì vậy phần
dễ được giảm xuống một lượt kiểm tra có sẵn bằng chứng, không tự ký tên hộ.

Chạy `prepare` thay `serve` nếu chỉ muốn tạo file, không mở server. Các mục tiếp
theo mô tả số liệu ban đầu và **quy trình offline dự phòng**; với server không
cần tự thực hiện lại các bước tải/copy JSON và merge bằng lệnh thủ công.

## 1. Trạng thái sau triển khai, ngày 19/11/2026

| Lô | Lỗi luật | Ứng viên phần Đăng | Số ảnh có ứng viên |
|---|---:|---:|---:|
| `runs/r6007` — 250 ảnh theo manifest | 11 | 7: 3 bất đồng lớp + 4 thiếu box | 7 |
| `runs/clean` — 50 ảnh calib | 0 | 0 | 0 |

Đây là số đề xuất, **chưa có verdict người xem**. `errors.json` hiện vẫn là
11 lỗi luật đã có, chưa merge lỗi gán nhãn. Không có `review_log.csv` giả lập.
F1 lưu từ P1: rule ≈ 1.0, overall ≈ 0.7; chưa đo lại F1 sau review Phase 2.

Hàng đợi ban đầu:

| Ảnh | Đề xuất cần kiểm tra |
|---|---|
| `000000006460` | Bất đồng lớp: class_swap hoặc category_swap |
| `000000007278` | missing_box |
| `000000007888` | missing_box |
| `000000009769` | Bất đồng lớp: class_swap hoặc category_swap |
| `000000019221` | missing_box |
| `000000020247` | missing_box |
| `000000025096` | Bất đồng lớp: class_swap hoặc category_swap |

Đây chỉ là danh sách sinh từ delivery, không phải đáp án. Danh sách không được
nhúng vào validator. Diagnostic hiện tại: một segment có category không hợp lệ
ở `000000014380`, và ảnh `000000018150` bị thiếu. Cả hai đã thuộc kiểm tra luật;
diagnostic không phải verdict và không được bỏ qua khi tổng hợp chất lượng.

## 2. Mở công cụ đã chuẩn bị

Mở PowerShell tại thư mục repo (hoặc chạy lệnh đầu để vào đúng thư mục):

```powershell
Set-Location -LiteralPath 'I:\downloads\vinuni AA\K5-L23-DAY10-NguyenGiaHuy-Release'
Invoke-Item .\runs\r6007\review.html
```

Nếu Windows mở sai ứng dụng, vào File Explorer → `runs` → `r6007` → chuột phải
`review.html` → **Open with → Google Chrome** hoặc Microsoft Edge. Trang là file
offline tự chứa ảnh; không cần đăng nhập, upload dữ liệu hay mở web CVAT.

Nhập tên **Nguyễn Hải Đăng** vào ô **Người review** trên trang. Đây là tên
người đang xem thật; nếu đổi người thì người đó nhập tên của họ trước khi sửa.

## 3. Review một ảnh

1. Chọn ứng viên ở ô **Ứng viên**. Một ứng viên có thể có nhiều segment: xem hết
   các mục trong **Bằng chứng đối chiếu**. Đọc mã segment, tên lớp và dòng D của
   box detection để tìm đúng vật.
2. Tắt cả hai overlay để xem ảnh gốc. Sau đó bật **Box detection (xanh)** và
   **Mask panoptic (cam)**. Dùng **Vừa khung**, thanh **Zoom**, và cuộn trong ảnh
   để xem vật nhỏ. Mask cam là pixel thật của segment đang bị nghi ngờ.
3. Đánh dấu **Tôi đã mở và trực tiếp xem vùng vật thể** chỉ sau khi đã xem thật.
4. Với bất đồng lớp, chỉ cần đánh giá đề xuất bbox được giao:
   - Detection sai: `class_swap = confirmed`, ghi quan sát.
   - Không xác nhận lỗi detection: `class_swap = rejected`, ghi lý do.
   - Đánh giá mask/category_swap là tùy chọn qua **Thêm loại khác**, không phải
     điều kiện hoàn tất bbox. Ô mask trống từ progress cũ được bỏ qua.
   - Chưa phân biệt được: để verdict trống, lưu progress, nhờ người khác xem.
5. Với `missing_box`, kiểm tra toàn bộ box đang có: có thể box gần đó chính là
   vật này, hoặc vật bị che/crowd làm hai bộ nhãn khác nhau tự nhiên. Chỉ
   `confirmed` khi ảnh thực sự cho thấy vật đáng kể bị thiếu box cần gán.
6. Nếu thực tế là `bbox_drift` hoặc `instance_merge`, trao đổi với Thái bằng mã
   ảnh/segment. Sau khi người xem xác định được, `rejected` đề xuất không đúng,
   dùng **Thêm loại khác → chọn type → Thêm kết luận** và ghi verdict/note riêng.
7. Mỗi dòng ghi chú phải ít nhất 20 ký tự, mô tả vật, vị trí, nhãn đang ghi gì,
   và vì sao kết luận như vậy. Không ghi “OK”, “có lỗi” hay copy cùng câu cho
   mọi ảnh. Trang tự thêm `Reviewer: <tên>` khi xuất CSV.
8. Bấm **Sau →** và lặp lại. Số đếm chỉ tăng khi đủ checkbox, verdict, tên và note.

Khung viết ghi chú, **không phải quan sát được phép chép nguyên**:
`Vật [mô tả] ở [vị trí]; detection ghi [A], panoptic ghi [B]; căn cứ [chi tiết nhìn thấy].`
AI không thay người quyết định. Nếu ảnh không mở được thì dừng ca đó; không
đánh rejected chỉ để xuất được file. Không chỉnh ảnh hoặc label trong delivery.

## 4. Lưu, nghỉ giữa chừng, tiếp tục

- Bấm **Lưu progress JSON** sau mỗi vài ảnh và trước khi đóng tab.
- Chrome lưu vào Downloads theo cài đặt của bạn. Kiểm tra file đã tải về; trang
  HTML **không tự ghi vào repo** và không tự lưu lâu dài.
- Dùng File Explorer chép file vào `runs/r6007/`, đổi tên thành
  `review_progress_dang.json`. Nếu có nhiều bản, giữ bản mới nhất ở tên này,
  lưu bản cũ bằng tên khác; không gộp các bản cũ như các phần độc lập.
- Lần sau mở lại `review.html`, bấm **Nạp progress JSON** và chọn đúng bản đã lưu.
  Trang chỉ nhận cùng release, fingerprint và phần được giao. Có xung đột thì
  giữ hai bản và trao đổi với người xem; không tự chọn một verdict cho người khác.
- Xem hết phần được giao rồi bấm **Xuất CSV hoàn tất** để tải `review_log.csv`.
  Xuất CSV sẽ bị chặn nếu còn ca chưa xong, ảnh lỗi hoặc note thiếu. Vẫn phải
  lưu progress JSON cuối cùng để người tổng hợp kiểm tra toàn bộ hàng đợi.

## 5. Nếu chia hàng đợi cho nhiều người

Mỗi người phải trực tiếp xem ảnh theo RULES. Huy điều phối phân công; Đăng không
được điền tên người khác vào review do mình làm. Không chỉ dùng bộ lọc để chia:
bộ lọc không thay đổi phạm vi phải hoàn tất của trang.

Tạo HTML phần riêng bằng `--images`, truyền **mã ảnh đầy đủ không đuôi** đã được
giao. Ví dụ dưới chỉ minh họa cách giao một phần, không thay quyết định của nhóm:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/review_phase2.py runs/r6007/delivery --candidates runs/r6007/candidates.json --images 000000006460 000000007278 --out runs/r6007/review_dang.html
```

Tạo các phần còn lại tương tự, sao cho mọi ảnh có người xem và không chồng phần
nếu không cần review lần hai. Mỗi người lưu JSON tên riêng. Tất cả cùng dựa trên
một `candidates.json`. Sau khi Thái ghép detector drift/merge, sinh lại toàn bộ
HTML theo hàng đợi mới; progress từ fingerprint cũ sẽ không tự được nhận.

## 6. Kiểm tra và tạo review_log.csv cuối

Nếu Đăng đã review toàn bộ hàng đợi 7 ứng viên hiện tại, chạy:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/review_phase2.py runs/r6007/delivery --candidates runs/r6007/candidates.json --merge-progress runs/r6007/review_progress_dang.json --review-out runs/r6007/review_log.csv
```

Nếu chia việc, thêm **bản JSON cuối của từng phần** sau `--merge-progress` trước
`--review-out`. Công cụ kiểm tra đủ hàng đợi, cùng fingerprint, tên người xem,
note, verdict và xung đột trước khi ghi. File đang có được giữ nguyên nếu kiểm
tra thất bại. Lệnh không tự xác minh người đó đã quan sát đúng; đó là trách
nhiệm của người ký tên trong review.

Sau khi log đúng, merge và tự đo bằng công cụ được cấp:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/merge_review.py --rules runs/r6007/rules.json --review runs/r6007/review_log.csv --out runs/r6007/errors.json
.\.venv\Scripts\python.exe -X utf8 scripts/score_findings.py --run runs/r6007
```

Chỉ confirmed đi vào errors; rejected vẫn ở review log. Không chạy validator
`--out runs/r6007/errors.json` sau khi merge vì sẽ mất phần confirmed. Ghi F1 thật
trong `runs/r6007/report.md` vào `submission/REPORT.md`. Không dùng điểm để dò
từng ảnh hay nộp thử từng phần danh sách.

## 7. Đo lô sạch và tái sinh nếu cần

`runs/clean` và `review.html` của lô sạch đã được tạo: hiện 0 ứng viên. Ghi số
0 này cùng ngưỡng vào báo cáo; không tạo dòng rejected giả để có minh chứng.
Lô sạch là 50 ảnh calib khác lô luyện tập, không phải nhãn đúng để diff với seed.
Đây chỉ là kết quả của detector Đăng; Thái đo lại sau khi tích hợp đủ detector.

Nếu code/threshold thay đổi hoặc dùng delivery mới, sinh lại từ đầu:

```powershell
.\.venv\Scripts\python.exe -X utf8 validator/validate.py runs/r6007/delivery --out runs/r6007/rules.json --candidates runs/r6007/candidates.json
.\.venv\Scripts\python.exe -X utf8 tools/review_phase2.py runs/r6007/delivery --candidates runs/r6007/candidates.json --out runs/r6007/review.html
.\.venv\Scripts\python.exe -X utf8 validator/validate.py runs/clean/delivery --out runs/clean/rules.json --candidates runs/clean/candidates.json
```

Không cần tải lại dữ liệu hay chạy lại `run_lab.py`. Ngưỡng nằm trong
`validator/cross_check.py`, được ghi vào candidate JSON. Thay ngưỡng phải dựa
vào xem lô sạch/ảnh công khai, ghi rõ lý do và kiểm tra các ca bị thêm/bỏ.

## 8. Bàn giao và commit bằng tài khoản của Đăng

Bàn giao cho Huy và Thái: candidates, review log thật, progress cuối, errors đã
merge, số liệu lô sạch, F1 sau review và các ca chưa rõ. Giải thích được một cặp
box/segment, một confirmed/rejected và lý do ghép theo tên lớp.

Trước commit, kiểm tra đúng danh tính Git của Đăng; không dùng email bịa hoặc
commit thay người khác:

```powershell
git config user.name
git config user.email
git status --short
```

Đăng tự tạo ít nhất 2 commit có nội dung, ví dụ:

```powershell
git add validator tools tests/test_phase2.py tests/test_review_ui.cjs docs/PHASE2_DANG.md submission/prompts.md submission/REPORT.md
git diff --cached --stat
git commit -m "P2: add class and missing-box candidates with offline review"
```

Sau khi **review thật đã xong**, cập nhật report/prompts và export bằng script
chuẩn (nó chỉ chép các phần hiện có; Huy sẽ export lại khi hoàn thiện v2):

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/export_submission.py --run runs/r6007
git add submission
git diff --cached --stat
git commit -m "P2: record human review and measured practice results"
```

Không commit `runs/`, data_source, HTML chứa ảnh hoặc ZIP. `personal/` đang bị
ignore. Không sửa supplied scripts để qua kiểm tra. Push theo nhánh/PR nhóm đã
thống nhất. Triển khai công cụ xong không thay thế bước review của người thật;
toàn Phase 2 còn cần detector và hàng đợi hoàn chỉnh của Thái.
