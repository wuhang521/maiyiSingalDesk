"""Isolated packaging/privacy contracts; no final delivery, server or UI."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC=importlib.util.spec_from_file_location('health_release',Path(__file__).with_name('prepare-health-release.py'))
release=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(release)


class PackagingContracts(unittest.TestCase):
    def setUp(self):
        parent=release.DELIVERY/'packaging/self-tests'; parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='self-test-',dir=parent)
        self.root=Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent,parent.resolve())
        self.assertTrue(self.root.name.startswith('self-test-'))

    def tearDown(self):
        # Verify the exact resolved disposable target before recursive cleanup.
        self.assertEqual(self.root.parent,(release.DELIVERY/'packaging/self-tests').resolve())
        self.temp.cleanup()

    def test_path_traversal_drive_and_backslash_are_rejected(self):
        for name in ('../secret','a/../b','/absolute','C:/secret','a\\b','a//b'):
            with self.subTest(name=name),self.assertRaises(ValueError): release.safe_name(name)

    def test_private_runtime_names_are_denied_but_public_source_data_is_retained(self):
        for name in ('server/.env','private.env','evidence/run-private/profile/a.log','a/node_modules/x.json','data/secret.log','profile/auth-session.json','a.db'):
            self.assertTrue(release.denied(name),name)
        self.assertTrue(release.denied('src/data/demo.ts'))
        self.assertFalse(release.denied('src/data/demo.ts',source=True))
        self.assertFalse(release.denied('server/.env.example',template=True,source=True))

    def test_json_redaction_preserves_hashes_flags_and_original_bytes(self):
        opaque='fixture-only-'+('A'*24)
        original=json.dumps({'accessToken':opaque,'username':'fixture-user','token_sha256':'f'*64,
            'token_present':True,'topic':'unpublished-'+opaque,'body':'fixture body','message':'fixture message'}).encode()
        before=original[:]; payload,counts=release.evidence_payload(original,'.json'); value=json.loads(payload)
        self.assertEqual(original,before)
        self.assertNotIn(opaque,payload.decode())
        self.assertEqual(value['token_sha256'],'f'*64); self.assertTrue(value['token_present'])
        self.assertEqual(value['body'],'[REDACTED PAYLOAD]'); self.assertEqual(value['message'],'[REDACTED PAYLOAD]')
        self.assertGreaterEqual(sum(counts.values()),4)

    def test_log_redacts_authorization_dsn_and_windows_profile(self):
        opaque='fixture-only-'+('A'*24)
        text='Bearer '+opaque+' Basic '+('A'*24)+' mysql://fixture-user:'+opaque+'@127.0.0.1/db C:/Users/FixturePerson/a'
        payload,counts=release.evidence_payload(text.encode(),'.log')
        self.assertNotIn(opaque,payload.decode()); self.assertNotIn('FixturePerson',payload.decode())
        self.assertIn('[REDACTED-CREDENTIALS]@127.0.0.1',payload.decode()); self.assertEqual(len(counts),4)

    def test_utf16_and_cp936_logs_become_valid_utf8(self):
        for codec in ('utf-16','cp936'):
            payload,counts=release.evidence_payload('合成验收日志'.encode(codec),'.log')
            self.assertEqual(payload.decode('utf-8'),'合成验收日志'); self.assertEqual(len(counts),1)

    def test_actual_zip_crc_payload_and_new_only_behavior(self):
        target=self.root/'fixture.zip'; data={'safe.txt':b'fixture-only'}
        result=release.archive(target,data,'SOURCE-MANIFEST.json',{'synthetic':True})
        self.assertEqual(result['file_count'],2)
        with zipfile.ZipFile(target) as bundle:
            self.assertIsNone(bundle.testzip()); self.assertEqual(bundle.read('safe.txt'),data['safe.txt'])
        before=target.read_bytes()
        with self.assertRaises(FileExistsError): release.archive(target,data,'SOURCE-MANIFEST.json',{})
        self.assertEqual(target.read_bytes(),before)

    def test_frozen_plan_rejects_a_changed_payload_before_any_output(self):
        stale=self.root/'stale.json'; stale.write_text(json.dumps({'fingerprint':'stale'}),encoding='utf-8')
        source={'files':[],'privacy':{'unreviewed':[]}}; evidence={'files':[],'privacy':{'unreviewed':[]}}
        argv=['packager','--write','--plan',str(stale),'--delivery',str(self.root),'--plan-output',str(self.root/'out.json')]
        with patch.object(release,'source_plan',return_value=(source,{})),patch.object(release,'evidence_plan',return_value=(evidence,{})),patch('sys.argv',argv):
            with self.assertRaisesRegex(ValueError,'changed since'): release.main()
        self.assertFalse((self.root/'packages').exists()); self.assertFalse((self.root/'out.json').exists())

    def test_case_or_generated_manifest_collision_is_rejected_before_creation(self):
        for data in ({'a.txt':b'a','A.txt':b'b'},{'SOURCE-MANIFEST.json':b'fixture-only'}):
            target=self.root/'collision.zip'
            with self.assertRaisesRegex(ValueError,'collision'): release.archive(target,data,'SOURCE-MANIFEST.json',{})
            self.assertFalse(target.exists())

    def test_runtime_directory_is_pruned_without_collecting_its_log(self):
        (self.root/'evidence/run-private/profile').mkdir(parents=True)
        (self.root/'evidence/run-private/profile/secret.log').write_text('fixture-only')
        (self.root/'evidence/okay.log').write_text('fixture-only')
        self.assertEqual([name for name,_ in release.walk(self.root,'evidence')],['evidence/okay.log'])

    def test_templates_require_empty_or_explicit_placeholders(self):
        self.assertTrue(release.check_template(b'TASK_API_KEY=\nTASK_DATABASE_URL=mysql://fixture:replace-me@localhost/db'))
        self.assertFalse(release.check_template(('TASK_API_KEY='+('A'*32)).encode()))

    def test_private_stage_raw_logs_and_dated_material_are_pruned_without_read(self):
        names=('scheduler/private-stage/2026-10-30.md','scheduler/stage-public-copy/worker.py',
            'scheduler/diagnostics/job.stderr.log','scheduler/collected/summary.json',
            'scheduler/job.stdout.log','scheduler/2026-10-30.md')
        for name in names:
            path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'not to be read')
            self.assertTrue(release.denied(name),name)
        unknown=self.root/'scheduler/unreviewed-digest.md'; unknown.write_bytes(b'not to be read')
        approved=self.root/'scheduler/README.md'; approved.write_bytes(b'Public synthetic guide only')
        real_read=Path.read_bytes
        def guarded(path):
            if path in {self.root/name for name in names}|{unknown}: self.fail('Private/unreviewed content was read')
            return real_read(path)
        with patch.object(Path,'read_bytes',guarded),patch.object(release,'IMAGE_REVIEW',self.root/'missing.json'):
            plan,payloads=release.evidence_plan(self.root)
        self.assertEqual(set(payloads),{'scheduler/README.md'})
        self.assertEqual(plan['omitted_unreviewed_markdown'][0]['path'],'scheduler/unreviewed-digest.md')

    def test_top_level_raw_stderr_is_omitted_before_content_read(self):
        raw=self.root/'private-probe.stderr.log'; raw.write_bytes(b'never read synthetic private stderr')
        real_read=Path.read_bytes
        def guarded(path):
            if path==raw:self.fail('Top-level stderr was read')
            return real_read(path)
        with patch.object(Path,'read_bytes',guarded),patch.object(release,'IMAGE_REVIEW',self.root/'missing.json'):
            plan,payloads=release.evidence_plan(self.root)
        self.assertNotIn(raw.name,payloads)
        self.assertEqual(plan['omitted_private_runtime_material'][0]['path'],raw.name)

    def test_raw_json_arrays_are_redacted_but_counts_and_hashes_survive(self):
        original=json.dumps({'articles':[{'title':'nonpublic fixture'}],'feed':[{'id':'fixture'}],
            'stdout':'nonpublic fixture output','stderr_sha256':'e'*64,'article_count':1}).encode()
        payload,counts=release.evidence_payload(original,'.json'); value=json.loads(payload)
        self.assertEqual(value['articles'],'[REDACTED PAYLOAD]'); self.assertEqual(value['feed'],'[REDACTED PAYLOAD]')
        self.assertEqual(value['stdout'],'[REDACTED PAYLOAD]')
        self.assertEqual(value['stderr_sha256'],'e'*64); self.assertEqual(value['article_count'],1)

    def test_historical_r1_and_r2_evidence_are_retained_while_current_role_selects_r3(self):
        for name,revision in [('installer/artifact-manifest.json','r1'),('installer/reopen-r2/artifact-manifest.json','reopen-r2'),
            ('installer/background-r3/artifact-manifest.json','background-r3'),('packages/background-r3/public-artifacts.json','background-r3')]:
            path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps({'revision':revision}))
        with patch.object(release,'IMAGE_REVIEW',self.root/'missing.json'):
            plan,payloads=release.evidence_plan(self.root)
        self.assertIn('installer/artifact-manifest.json',payloads)
        self.assertIn('installer/reopen-r2/artifact-manifest.json',payloads)
        self.assertIn('installer/background-r3/artifact-manifest.json',payloads)
        self.assertIn('packages/background-r3/public-artifacts.json',payloads)
        self.assertEqual(plan['release_roles']['current_program'],'installer/background-r3/artifact-manifest.json')
        self.assertEqual(plan['release_roles']['historical_reopen_r2_program'],'installer/reopen-r2/artifact-manifest.json')

    def test_only_explicit_public_scripts_are_read_even_when_unknown_name_contains_health(self):
        source=self.root/'candidate'; (source/'scripts').mkdir(parents=True)
        scanner=b'def scan_text(name,text): return []\n'
        metadata={'package.json':json.dumps({'version':release.VERSION}).encode(),
            'package-lock.json':json.dumps({'version':release.VERSION,'packages':{'':{'version':release.VERSION}}}).encode(),
            'scripts/prepare-public-release.py':scanner}
        for name,data in metadata.items():
            (source/name).write_bytes(data)
        known=source/'scripts/known-health.py'; known.write_bytes(b'PUBLIC = True\n')
        unknown=source/'scripts/health-private-stage.py'; unknown.write_bytes(b'NOT_PUBLIC = True\n')
        baseline=self.root/'synthetic-baseline.zip'
        baseline_manifest={'files':[{'path':name,'sha256':release.sha(data)} for name,data in metadata.items()]}
        with zipfile.ZipFile(baseline,'w') as archive: archive.writestr('SOURCE-MANIFEST.json',json.dumps(baseline_manifest))
        audit=self.root/'audit.json'; audit.write_text(json.dumps({'passed':True,'archive_sha256':release.sha(baseline.read_bytes()),'privacy':{'findings_redacted':[]}}))
        real_read=Path.read_bytes
        def guarded(path):
            if path==unknown:self.fail('Unknown health-named source was read')
            return real_read(path)
        with patch.multiple(release,BASELINE=baseline,BASELINE_SHA=release.sha(baseline.read_bytes()),BASELINE_AUDIT=audit,
            SOURCE_REVIEW=self.root/'missing-review.json',IMAGE_REVIEW=self.root/'missing-images.json',EXTRA_SOURCE={},
            PUBLIC_GUIDES={},EXPLICIT_SOURCE=set(),PUBLIC_HEALTH_SCRIPTS={'scripts/known-health.py'},
            CRITICAL={'scripts/known-health.py'},FROZEN_CRITICAL_SHA256={},NEW_TREES=()),patch.object(Path,'read_bytes',guarded):
            plan,payloads=release.source_plan(source)
        self.assertIn('scripts/known-health.py',payloads); self.assertNotIn('scripts/health-private-stage.py',payloads)
        self.assertEqual(plan['privacy']['unreviewed'],[{'file':'scripts/health-private-stage.py','rule':'new-script-outside-reviewed-health-whitelist'}])

    def test_root_reviewed_critical_hash_rejects_missing_or_changed_bytes(self):
        name='server/app/health_delivery.py'; data=b'ROOT_REVIEWED=True\n'
        with patch.object(release,'FROZEN_CRITICAL_SHA256',{name:release.sha(data)}):
            release.verify_critical_hashes({name:data})
            for payloads in ({},{name:b'CHANGED=True\n'}):
                with self.subTest(payloads=bool(payloads)),self.assertRaisesRegex(ValueError,'critical source SHA differs'):
                    release.verify_critical_hashes(payloads)

    def test_pending_activation_source_prevents_zip_even_with_a_matching_plan(self):
        source={'files':[],'privacy':{'unreviewed':[]},'activation_source_freeze_pending':True}
        evidence={'files':[],'privacy':{'unreviewed':[]}}
        plan=self.root/'matching.json';plan.write_text(json.dumps({'fingerprint':release.fingerprint(source,evidence)}))
        argv=['packager','--write','--plan',str(plan),'--delivery',str(self.root),'--plan-output',str(self.root/'out.json')]
        with patch.object(release,'source_plan',return_value=(source,{})),patch.object(release,'evidence_plan',return_value=(evidence,{})),patch('sys.argv',argv):
            with self.assertRaisesRegex(ValueError,'activation source final freeze'):
                release.main()
        self.assertFalse((self.root/'packages').exists())


if __name__=='__main__': unittest.main(verbosity=2)
