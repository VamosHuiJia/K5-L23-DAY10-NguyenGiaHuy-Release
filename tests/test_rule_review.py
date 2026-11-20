"""Rule audit UI/evidence tests with handcrafted delivery, no private truth."""
import copy
import csv
import hashlib
import json
import shutil
import unittest

import test_phase2 as fixtures
from tools import review_rules as rr


class RuleReviewTest(unittest.TestCase):
    setUp = fixtures.Phase2Test.setUp
    label = fixtures.Phase2Test.label
    write = fixtures.Phase2Test.write
    sync = fixtures.Phase2Test.sync

    def payload(self, kind, target=None):
        self.sync()
        (self.root/'rules.json').write_text(json.dumps({'errors':[{'type':kind,'target':target or self.image}]}),encoding='utf-8')
        return rr.build_payload(self.root)

    def test_citations_are_exact_lines_from_existing_guides(self):
        for kind in rr.TITLES:
            references=rr.rule_citations(kind,self.delivery)
            self.assertGreaterEqual(len(references),2)
            for reference in references:
                source=rr.ROOT/reference['source']
                self.assertEqual(source.read_text(encoding='utf-8').splitlines()[reference['line']-1].strip(),reference['quote'])
                self.assertIn(f"#L{reference['line']}",reference['url'])

    def test_missing_image_has_metadata_and_can_be_reviewed_without_a_picture(self):
        (self.delivery/'images/img_0001.png').unlink()
        payload=self.payload('missing_image')
        case=payload['cases'][0]
        self.assertFalse(case['facts']['image_file_exists'])
        self.assertIsNone(payload['assets'][self.image]['image_data'])
        progress={'mode':'rule_review','release_id':payload['release_id'],'fingerprint':payload['fingerprint'],
                  'decisions':{case['id']:{'reviewed':True,'verdict':'confirmed','reviewer':'Fixture reviewer','note':''}}}
        self.assertEqual(len(rr.validate_progress(payload,progress,True)),1)

    def test_hash_expected_and_computed_are_shown(self):
        self.manifest['items'][self.image]['image_sha256']='0'*64
        self.write('manifest.json',self.manifest)
        facts=self.payload('wrong_hash')['cases'][0]['facts']
        self.assertEqual(facts['expected_sha256'],'0'*64)
        self.assertEqual(facts['actual_sha256'],hashlib.sha256((self.delivery/'images/img_0001.png').read_bytes()).hexdigest())
        self.assertFalse(facts['hashes_match'])

    def test_overflow_coordinates_and_box_rows_are_highlighted(self):
        self.label('0 .99 .3125 .3125 .3125\n')
        case=self.payload('bbox_out_of_bounds')['cases'][0]
        self.assertEqual(case['highlight_box_rows'],[1])
        self.assertGreater(case['facts']['overflow_boxes'][0]['overflow_pixels']['right'],0)

    def test_duplicate_boxes_have_pair_and_center_measurements(self):
        self.label('0 .3125 .3125 .3125 .3125\n0 .328125 .3125 .3125 .3125\n')
        case=self.payload('duplicate_box')['cases'][0]
        self.assertEqual(case['highlight_box_rows'],[1,2])
        self.assertEqual(case['facts']['duplicate_pairs'][0]['center_delta_pixels'],[1,0])

    def test_invalid_class_is_visible_even_when_no_name_exists(self):
        self.label('99 .3125 .3125 .3125 .3125\n')
        case=self.payload('invalid_class')['cases'][0]
        self.assertEqual(case['facts']['invalid_boxes'][0]['class_name'],'INVALID 99')

    def test_invalid_category_mask_is_retained_for_review(self):
        self.panoptic['annotations'][0]['segments_info'][0]['category_id']=999
        payload=self.payload('invalid_category_id')
        case=payload['cases'][0]
        self.assertEqual(case['highlight_segment_ids'],[523])
        self.assertTrue(payload['assets'][self.image]['segments'][0]['path'])
        self.assertFalse(case['facts']['segments'][0]['valid_category'])

    def test_segment_declared_area_is_compared_with_actual_mask_pixels(self):
        self.panoptic['annotations'][0]['segments_info'][0]['area']=450
        case=self.payload('thing_overlap')['cases'][0]
        segment=case['facts']['segments'][0]
        self.assertEqual((segment['declared_area'],segment['mask_pixels']),(450,400))

    def test_job_version_is_authoritative_and_changelog_is_context_only(self):
        self.write('metadata.json',{'items':[{'image':self.image,'scene_id':'s','split':'train','job_id':'j','guideline_version':'wrong'}]})
        (self.delivery/'guideline').mkdir()
        (self.delivery/'guideline/changelog.md').write_text('# Changes\n- Lớp cup: fixture context only\n',encoding='utf-8')
        case=self.payload('guideline_mismatch')['cases'][0]
        self.assertEqual(case['facts']['authoritative_job']['guideline'],'v1')
        self.assertFalse(case['facts']['versions_match'])
        self.assertEqual(len(case['citations']),3)
        self.assertIsNone(case['citations'][-1]['url'])

    def test_scene_review_groups_members_and_preserves_scene_target(self):
        second='img_0002'
        item=copy.deepcopy(self.manifest['items'][self.image])
        for key in ['image','label','mask']:
            original=self.delivery/item[key]
            item[key]=item[key].replace(self.image,second)
            shutil.copyfile(original,self.delivery/item[key])
        self.manifest['items'][second]=item
        self.write('manifest.json',self.manifest)
        info=dict(self.panoptic['images'][0],id=724,file_name=second+'.png')
        annotation=copy.deepcopy(self.panoptic['annotations'][0]);annotation.update(image_id=724,file_name=second+'.png')
        self.panoptic['images'].append(info);self.panoptic['annotations'].append(annotation)
        self.write('metadata.json',{'items':[{'image':name,'scene_id':'scene_test','split':split,'job_id':'j','guideline_version':'v1'} for name,split in [(self.image,'train'),(second,'val')]]})
        payload=self.payload('scene_leak','scene_test')
        case=payload['cases'][0]
        self.assertEqual(case['images'],[self.image,second])
        self.assertEqual(case['target'],'scene_test')
        self.assertEqual(case['facts']['splits'],['train','val'])
        self.assertEqual(payload['summary']['related_images'],2)

    def test_drafts_import_modes_and_rejected_without_note(self):
        payload=self.payload('wrong_hash')
        case=payload['cases'][0]
        progress={'mode':'rule_review','release_id':payload['release_id'],'fingerprint':payload['fingerprint'],'decisions':{}}
        self.assertEqual(rr.validate_progress(payload,progress),[])
        with self.assertRaises(ValueError):rr.validate_progress(payload,progress,True)
        progress['decisions'][case['id']]={'reviewed':True,'verdict':'rejected','reviewer':'Fixture reviewer','note':''}
        self.assertEqual(rr.validate_progress(payload,progress,True)[0]['verdict'],'rejected')
        progress['mode']='semantic'
        with self.assertRaises(ValueError):rr.validate_progress(payload,progress)

    def test_build_is_deterministic_and_does_not_edit_delivery_or_final_results(self):
        self.sync()
        before={p:p.read_bytes() for p in self.delivery.rglob('*') if p.is_file()}
        (self.root/'errors.json').write_text('original findings',encoding='utf-8')
        first=self.payload('wrong_hash');second=self.payload('wrong_hash')
        self.assertEqual(first,second)
        self.assertTrue(all(p.read_bytes()==contents for p,contents in before.items()))
        self.assertEqual((self.root/'errors.json').read_text(encoding='utf-8'),'original findings')

    def test_hash_can_be_machine_confirmed_without_ui_verdict_or_human_signature(self):
        self.manifest['items'][self.image]['image_sha256']='0'*64
        self.write('manifest.json',self.manifest)
        payload=self.payload('wrong_hash')
        progress={'mode':'rule_review','release_id':payload['release_id'],'fingerprint':payload['fingerprint'],'decisions':{}}
        machine={'errors':[{'type':'wrong_hash','target':self.image}]}
        result=rr.audit_progress(payload,progress,machine)
        self.assertTrue(result['summary']['audit_complete'])
        self.assertEqual(result['summary']['delegated_to_machine'],1)
        self.assertEqual(result['records'][0]['human'],{})
        self.assertEqual(result['records'][0]['machine_result'],'confirmed')
        with self.assertRaises(ValueError):rr.audit_progress(payload,progress,{'errors':[]})

    def test_rejected_overlap_is_preserved_as_human_machine_disagreement(self):
        self.panoptic['annotations'][0]['segments_info'][0]['area']=450
        payload=self.payload('thing_overlap');case=payload['cases'][0]
        decision={'reviewed':True,'verdict':'rejected','note':'mask có ít pixel hơn','reviewer':'Fixture reviewer'}
        progress={'mode':'rule_review','release_id':payload['release_id'],'fingerprint':payload['fingerprint'],'decisions':{case['id']:decision}}
        original=copy.deepcopy(progress)
        result=rr.audit_progress(payload,progress,{'errors':[{'type':'thing_overlap','target':self.image}]})
        self.assertEqual(result['summary']['human_machine_disagreements'],1)
        self.assertEqual(result['records'][0]['human']['verdict'],'rejected')
        self.assertEqual(progress,original)
        progress['decisions'][case['id']]['verdict']=''
        with self.assertRaises(ValueError):rr.audit_progress(payload,progress,{'errors':[]})

    def test_scene_is_machine_checked_from_split_metadata(self):
        self.test_scene_review_groups_members_and_preserves_scene_target()
        payload=rr.build_payload(self.root)
        progress={'mode':'rule_review','release_id':payload['release_id'],'fingerprint':payload['fingerprint'],'decisions':{}}
        result=rr.audit_progress(payload,progress,{'errors':[{'type':'scene_leak','target':'scene_test'}]})
        self.assertEqual(result['summary']['delegated_to_machine'],1)
        self.assertEqual(result['records'][0]['facts']['splits'],['train','val'])

    def test_audit_export_keeps_machine_separate_and_never_overwrites_sources(self):
        self.manifest['items'][self.image]['image_sha256']='0'*64
        self.write('manifest.json',self.manifest)
        payload=self.payload('wrong_hash')
        progress={'mode':'rule_review','release_id':payload['release_id'],'fingerprint':payload['fingerprint'],'decisions':{}}
        human_path=self.root/'progress.json';machine_path=self.root/'machine.json'
        human_path.write_text(json.dumps(progress),encoding='utf-8')
        machine_path.write_text(json.dumps({'errors':[{'type':'wrong_hash','target':self.image}]}),encoding='utf-8')
        before=human_path.read_bytes(),machine_path.read_bytes()
        output=rr.write_audit(self.root,payload,human_path,machine_path)
        with output.with_name('rule_review_log.csv').open(encoding='utf-8',newline='') as handle:
            row=next(csv.DictReader(handle))
        self.assertEqual(row['human_verdict'],'')
        self.assertEqual(row['machine_result'],'confirmed')
        self.assertEqual((human_path.read_bytes(),machine_path.read_bytes()),before)
        output.write_bytes(human_path.read_bytes())
        with self.assertRaises(ValueError):rr.write_audit(self.root,payload,output,machine_path)
        self.assertEqual(output.read_bytes(),human_path.read_bytes())


if __name__=='__main__':unittest.main()
