#!/usr/bin/env python3
"""Build a self-contained rule review UI with delivery evidence and lab citations.

Writes a separate rule audit, never edits v1 or semantic review/errors files.
"""
from __future__ import annotations

import argparse
import base64
import csv
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validator import cross_check as cc
from tools.review_phase2 import svg_runs

TITLES = {
    "missing_image": "Thiếu file ảnh",
    "wrong_hash": "SHA-256 không khớp manifest",
    "invalid_class": "Class detection không hợp lệ",
    "bbox_out_of_bounds": "Box vượt biên ảnh",
    "duplicate_box": "Box trùng lớp/kích thước, gần trùng tâm",
    "scene_leak": "Một cảnh xuất hiện ở nhiều split",
    "guideline_mismatch": "Guideline ảnh khác guideline của job",
    "invalid_category_id": "Category panoptic không có trong danh mục",
    "thing_overlap": "Diện tích segment khai lớn hơn pixel thật",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def citation(path: Path, predicate) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, 1):
        if predicate(line):
            try:
                relative = path.relative_to(ROOT).as_posix()
                public = not relative.startswith("runs/")
            except ValueError:
                relative, public = f"delivery/guideline/{path.name}", False
            return {"source": relative, "line": number, "quote": line.strip(),
                    "url": f"https://github.com/VamosHuiJia/K5-L23-DAY10-NguyenGiaHuy-Release/blob/nguyenhaidang_03190/{relative}#L{number}" if public else None}
    raise ValueError(f"Không tìm thấy trích dẫn trong {path.name}")


def rule_citations(kind: str, delivery: Path) -> list[dict]:
    result = [citation(ROOT/"docs/DELIVERY.md", lambda line: line.startswith("|") and f"`{kind}`" in line)]
    if kind in {"missing_image", "wrong_hash"}:
        key = "**File:**"
    elif kind in {"invalid_class", "bbox_out_of_bounds", "duplicate_box"}:
        key = "**Nhãn detection:**"
    elif kind in {"scene_leak", "guideline_mismatch"}:
        key = "**Metadata:**"
    else:
        key = "**Panoptic:**"
    result.append(citation(ROOT/"GUIDE.md", lambda line: key in line))
    if kind == "wrong_hash":
        result.append(citation(ROOT/"GUIDE.md", lambda line: line.startswith("- **Sửa ngay") and "wrong_hash" in line))
    if kind == "guideline_mismatch" and (delivery/"guideline/changelog.md").exists():
        # The changelog supplies project context; jobs.csv remains authoritative
        # for each job's version. It is not invented as the source of every rule.
        result.append(citation(delivery/"guideline/changelog.md", lambda line: line.startswith("- Lớp")))
    return result


class RuleEvidence:
    def __init__(self, delivery: Path):
        self.delivery = delivery.resolve()
        self.manifest = cc.read_json(delivery/"manifest.json")["items"]
        self.panoptic = cc.read_json(delivery/"panoptic.json")
        self.images = {Path(i["file_name"]).stem: i for i in self.panoptic["images"]}
        self.annotations = {a["image_id"]: a for a in self.panoptic["annotations"]}
        self.categories = {int(c["id"]): c for c in self.panoptic["categories"]}
        self.metadata = {Path(m["image"]).stem: m for m in cc.read_json(delivery/"metadata.json")["items"]}
        import csv
        with (delivery/"jobs.csv").open(encoding="utf-8", newline="") as handle:
            self.jobs = {j["job_id"]: j for j in csv.DictReader(handle)}
        self.classes = cc.load_classes(delivery)
        self.assets: dict[str, dict] = {}

    def asset(self, image: str) -> dict:
        if image in self.assets:
            return self.assets[image]
        item = self.manifest[image]
        info = self.images[image]
        width, height = int(info["width"]), int(info["height"])
        image_path = cc.delivery_path(self.delivery, item["image"])
        label_path = cc.delivery_path(self.delivery, item["label"])
        rows = label_path.read_text(encoding="utf-8").splitlines() if label_path.is_file() else []
        boxes = []
        for row, text in enumerate(rows, 1):
            try:
                raw_class, x, y, w, h = map(float, text.split())
                if not all(map(math.isfinite, [raw_class,x,y,w,h])) or not raw_class.is_integer():
                    continue
            except ValueError:
                continue
            cid = int(raw_class)
            boxes.append({"row": row, "class_id": cid, "class_name": self.classes.get(cid, f"INVALID {cid}"),
                          "bbox_xyxy": [(x-w/2)*width, (y-h/2)*height, (x+w/2)*width, (y+h/2)*height],
                          "normalized": [x,y,w,h], "raw": text})
        annotation = self.annotations.get(info["id"], {})
        raw_segments = annotation.get("segments_info", [])
        segments, mask_error = [], None
        try:
            mw, mh, pixels = cc.pngutil.read_rgb(cc.delivery_path(self.delivery, item["mask"]))
            if (mw,mh) != (width,height):
                raise ValueError("Mask có kích thước khác metadata ảnh")
            spans = cc.mask_spans(mw,mh,pixels,{int(s["id"]) for s in raw_segments if int(s['id'])>0})
            for s in raw_segments:
                category = self.categories.get(s["category_id"])
                x,y,w,h = s["bbox"]
                runs = spans.get(s["id"], [])
                segments.append({"id": s["id"], "category_id": s["category_id"],
                                 "class_name": category["name"] if category else f"INVALID CATEGORY {s['category_id']}",
                                 "valid_category": category is not None, "isthing": category.get("isthing",0) if category else None,
                                 "iscrowd": s.get("iscrowd",0), "declared_area": s["area"],
                                 "mask_pixels": sum(x1-x0 for _,x0,x1 in runs),
                                 "bbox_xyxy": [x,y,x+w,y+h], "path": svg_runs(runs)})
        except (OSError, ValueError, IndexError, zlib.error) as exc:
            mask_error = str(exc)
        image_data = None
        if image_path.is_file():
            mime = "image/png" if image_path.suffix.lower()==".png" else "image/jpeg"
            image_data = f"data:{mime};base64,"+base64.b64encode(image_path.read_bytes()).decode("ascii")
        result = {"image": image, "file_name": image_path.name, "image_data": image_data,
                  "width": width, "height": height, "boxes": boxes, "segments": segments,
                  "image_missing": not image_path.is_file(), "mask_error": mask_error,
                  "label_text": "\n".join(rows), "metadata": self.metadata.get(image, {}),
                  "job": self.jobs.get(self.metadata.get(image, {}).get("job_id"), {})}
        self.assets[image] = result
        return result

    def build_case(self, error: dict) -> dict:
        kind, target = error["type"], error["target"]
        if kind not in TITLES:
            raise ValueError(f"Không phải loại lỗi luật: {kind}")
        images = (sorted(image for image,m in self.metadata.items() if m.get("scene_id")==target)
                  if kind=="scene_leak" else [target])
        if not images:
            raise ValueError(f"Không tìm thấy ảnh cho {kind}:{target}")
        assets = [self.asset(image) for image in images]
        facts, rows, segment_ids = {}, [], []
        a = assets[0]
        if kind=="scene_leak":
            facts = {"scene_id": target, "splits": sorted({asset['metadata']['split'] for asset in assets}),
                     "members": [{"image": asset['image'], **asset['metadata']} for asset in assets]}
        elif kind=="missing_image":
            item = self.manifest[target]
            facts = {"manifest_image": item["image"], "image_file_exists": not a["image_missing"],
                     "manifest_image_sha256": item.get("image_sha256"),
                     "label_file_exists": cc.delivery_path(self.delivery,item['label']).is_file(),
                     "mask_file_exists": cc.delivery_path(self.delivery,item['mask']).is_file(),
                     "explanation": "Đối chiếu file với manifest; không cần có ảnh để xác nhận một file bị thiếu."}
        elif kind=="wrong_hash":
            item = self.manifest[target]
            path = cc.delivery_path(self.delivery,item['image'])
            actual = sha(path) if path.is_file() else None
            facts = {"file": item['image'], "expected_sha256": item.get('image_sha256'),
                     "actual_sha256": actual, "hashes_match": actual==item.get('image_sha256'),
                     "instruction": "Mở ảnh để kiểm tra còn nguyên hay hỏng; hash khác không tự chứng minh ảnh bị hỏng."}
        elif kind=="guideline_mismatch":
            facts = {"image_metadata": a['metadata'], "authoritative_job": a['job'],
                     "versions_match": a['metadata'].get('guideline_version')==a['job'].get('guideline')}
        elif kind=="invalid_class":
            rows = [b['row'] for b in a['boxes'] if b['class_id'] not in self.classes]
            facts = {"valid_class_ids": sorted(self.classes), "invalid_boxes": [b for b in a['boxes'] if b['row'] in rows]}
        elif kind=="bbox_out_of_bounds":
            violations = []
            for b in a['boxes']:
                l,t,r,bt = b['bbox_xyxy']
                overflow = {"left": max(0,-l), "top": max(0,-t), "right": max(0,r-a['width']), "bottom": max(0,bt-a['height'])}
                if max(overflow.values())>1e-6:
                    violations.append({**b,"overflow_pixels":overflow})
            rows = [b['row'] for b in violations]
            facts = {"image_bounds_xyxy": [0,0,a['width'],a['height']], "overflow_boxes": violations,
                     "note": "Hiện cả tràn nhẹ theo định nghĩa GUIDE; validator P1 hiện có tolerance 0.01 chuẩn hóa."}
        elif kind=="duplicate_box":
            duplicates = []
            for i,b in enumerate(a['boxes']):
                l,t,r,bt=b['bbox_xyxy']
                for other in a['boxes'][i+1:]:
                    ol,ot,orr,ob=other['bbox_xyxy']
                    dx,dy=abs((l+r-ol-orr)/2),abs((t+bt-ot-ob)/2)
                    dw,dh=abs((r-l)-(orr-ol)),abs((bt-t)-(ob-ot))
                    if b['class_id']==other['class_id'] and dx<=2.01 and dy<=2.01 and dw<=.25 and dh<=.25:
                        duplicates.append({"box_rows":[b['row'],other['row']], "class_name":b['class_name'],
                                           "center_delta_pixels":[dx,dy], "size_delta_pixels":[dw,dh],
                                           "bbox_iou":cc.bbox_iou(b['bbox_xyxy'],other['bbox_xyxy'])})
            rows = sorted({row for pair in duplicates for row in pair['box_rows']})
            facts = {"duplicate_pairs": duplicates, "validator_tolerances": {"center_pixels":2.01,"size_pixels":.25},
                     "note":"Đây là tolerance code hiện tại; GUIDE mô tả cùng lớp, cùng kích thước và lệch 1–2 pixel."}
        else:
            flagged = [s for s in a['segments'] if not s['valid_category']] if kind=="invalid_category_id" else [s for s in a['segments'] if s['isthing']==1 and not s['iscrowd'] and s['declared_area']>s['mask_pixels']]
            segment_ids = [s['id'] for s in flagged]
            facts = {"segments": [{k:v for k,v in s.items() if k!='path'} for s in flagged],
                     "valid_category_ids":sorted(self.categories) if kind=="invalid_category_id" else None,
                     "mask_error":a['mask_error']}
        return {"id":f"{kind}:{target}", "type":kind, "target":target, "title":TITLES[kind],
                "images":images, "highlight_box_rows":rows, "highlight_segment_ids":segment_ids,
                "facts":facts, "citations":rule_citations(kind,self.delivery)}


def build_payload(run: Path) -> dict:
    delivery = run/"delivery"
    rules = cc.read_json(run/"rules.json")["errors"]
    reader = RuleEvidence(delivery)
    unique = sorted({(e['type'],e['target']) for e in rules})
    cases = [reader.build_case({"type":kind,"target":target}) for kind,target in unique]
    release = cc.read_json(delivery/"release.json")["release_id"]
    fingerprint = cc.fingerprint({"release_id":release,"cases":cases})
    return {"schema_version":1,"mode":"rule_review","release_id":release,"fingerprint":fingerprint,
            "cases":cases,"assets":reader.assets,"summary":{"findings":len(cases),"related_images":len(reader.assets),
            "by_type":dict(Counter(c['type'] for c in cases))}}


def validate_progress(payload: dict, progress: dict, require_complete: bool = False) -> list[dict]:
    if (progress.get('mode')!='rule_review' or progress.get('release_id')!=payload['release_id']
            or progress.get('fingerprint')!=payload['fingerprint']):
        raise ValueError('Progress không khớp lô hoặc bằng chứng luật')
    expected={c['id']:c for c in payload['cases']}
    decisions=progress.get('decisions')
    if not isinstance(decisions,dict) or not set(decisions)<=set(expected):
        raise ValueError('Danh sách review không hợp lệ')
    result=[]
    for key,decision in decisions.items():
        if (not isinstance(decision,dict) or type(decision.get('reviewed')) is not bool
                or decision.get('verdict') not in ['', 'confirmed','rejected']
                or not isinstance(decision.get('note'),str) or not isinstance(decision.get('reviewer'),str)):
            raise ValueError('Dòng review không hợp lệ')
    for key,c in expected.items():
        d=decisions.get(key,{})
        done=d.get('reviewed') is True and d.get('verdict') in ['confirmed','rejected'] and bool(d.get('reviewer','').strip())
        if require_complete and not done:
            raise ValueError(f"Chưa kiểm tra xong {key}")
        if done:
            result.append({'target':c['target'],'type':c['type'],'verdict':d['verdict'],
                           'note':d['note'],'reviewer':d['reviewer']})
    return result


MACHINE_DELEGATED = {"scene_leak", "wrong_hash"}


def audit_progress(payload: dict, progress: dict, machine_rules: dict) -> dict:
    """Reconcile literal human verdicts with a separate fresh machine run.

    UI completion is irrelevant for delegated scene/hash checks. No machine
    decision is signed as a human review and no existing finding is edited.
    """
    validate_progress(payload, progress)
    machine = {(e['type'], e['target']) for e in machine_rules['errors']}
    records = []
    for case in payload['cases']:
        human = progress['decisions'].get(case['id'], {})
        kind, target = case['type'], case['target']
        reproduced = (kind, target) in machine
        if kind in MACHINE_DELEGATED:
            facts = case['facts']
            evidence_confirms = (len(facts['splits'])>1 if kind=='scene_leak' else
                                 facts['actual_sha256'] is not None and facts['actual_sha256']!=facts['expected_sha256'])
            if not reproduced or not evidence_confirms:
                raise ValueError(f"Máy không tái lập đủ bằng chứng cho {case['id']}")
        manual_done = human.get('reviewed') is True and human.get('verdict') in ['confirmed','rejected'] and bool(human.get('reviewer','').strip())
        if kind not in MACHINE_DELEGATED and not manual_done:
            raise ValueError(f"Thiếu kết luận người xem cho mục không giao máy: {case['id']}")
        human_verdict = human.get('verdict','')
        machine_result = 'confirmed' if reproduced else 'not_reproduced'
        disagreement = manual_done and ((human_verdict=='rejected' and reproduced) or (human_verdict=='confirmed' and not reproduced))
        resolution = ('human_machine_disagreement' if disagreement else
                      'machine_confirmed' if kind in MACHINE_DELEGATED and not human_verdict else 'agreed')
        records.append({'id':case['id'],'type':kind,'target':target,
                        'human':dict(human),'machine_result':machine_result,
                        'machine_provenance':'fresh validator run; delivery evidence recomputed',
                        'resolution':resolution,'facts':case['facts'],'citations':case['citations']})
    known={(case['type'],case['target']) for case in payload['cases']}
    return {'schema_version':1,'mode':'rule_review_audit','release_id':payload['release_id'],
            'fingerprint':payload['fingerprint'], 'session_reviewer':progress.get('reviewer',''),
            'summary':{'audit_complete':True,'findings':len(records),
                       'human_confirmed':sum(r['human'].get('verdict')=='confirmed' for r in records),
                       'human_rejected':sum(r['human'].get('verdict')=='rejected' for r in records),
                       'delegated_to_machine':sum(r['resolution']=='machine_confirmed' for r in records),
                       'machine_confirmed':sum(r['machine_result']=='confirmed' for r in records),
                       'human_machine_disagreements':sum(r['resolution']=='human_machine_disagreement' for r in records)},
            'records':records,'new_machine_findings':[{'type':kind,'target':target} for kind,target in sorted(machine-known)],
            'human_verdicts_changed':False,'canonical_errors_modified':False,
            'note':'machine_result là kết quả luật tự động, không phải verdict hoặc chữ ký của người review.'}


def write_audit(run: Path, payload: dict, progress_path: Path, machine_path: Path) -> Path:
    progress = cc.read_json(progress_path)
    result = audit_progress(payload, progress, cc.read_json(machine_path))
    result['provenance']={'human_source':progress_path.name,'human_source_sha256':sha(progress_path),
                          'machine_source':machine_path.name,'machine_source_sha256':sha(machine_path)}
    folder=run/'rule_review_audit'
    output=cc.output_path(run/'delivery',folder/'result.json')
    csv_output=cc.output_path(run/'delivery',folder/'rule_review_log.csv')
    if {output.resolve(),csv_output.resolve()} & {progress_path.resolve(),machine_path.resolve()}:
        raise ValueError('Đầu ra audit không được ghi đè nguồn review hoặc kết quả máy')
    folder.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with csv_output.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['target','type','human_verdict','machine_result','resolution','reviewer','note'],lineterminator='\n')
        writer.writeheader()
        writer.writerows({'target':r['target'],'type':r['type'],'human_verdict':r['human'].get('verdict',''),
                         'machine_result':r['machine_result'],'resolution':r['resolution'],
                         'reviewer':r['human'].get('reviewer',''),'note':r['human'].get('note','')} for r in result['records'])
    return output


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=ROOT/'runs/r6007')
    parser.add_argument('--audit-progress',type=Path,help='Human rule progress; scene/hash may be delegated to machine')
    parser.add_argument('--machine-rules',type=Path,help='Fresh validator rule output for audit')
    args=parser.parse_args()
    try:
        payload=build_payload(args.run)
        if args.audit_progress or args.machine_rules:
            if not args.audit_progress or not args.machine_rules:
                parser.error('Audit cần cả --audit-progress và --machine-rules')
            output=write_audit(args.run,payload,args.audit_progress,args.machine_rules)
            print(f'Đã đối chiếu review người xem và kiểm tra máy: {output}')
            return 0
        output=cc.output_path(args.run/'delivery',args.run/'review_rules.html')
        template=Path(__file__).with_suffix('.html').read_text(encoding='utf-8')
        data=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
        output.write_text(template.replace('/*__RULE_DATA__*/',data),encoding='utf-8')
        (args.run/'rule_review_evidence.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(f"Đã tạo {payload['summary']['findings']} mục luật, {payload['summary']['related_images']} ảnh liên quan: {output}")
    except (OSError,ValueError,KeyError,TypeError) as exc:
        parser.exit(1,f'Rule review: {exc}\n')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
