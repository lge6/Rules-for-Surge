import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_rules.py"
SPEC = importlib.util.spec_from_file_location("sync_rules", SCRIPT)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "Direct.list").write_text("DOMAIN-SUFFIX,example.cn\n", encoding="utf-8")
        (self.root / "Proxy.list").write_text("DOMAIN-KEYWORD,example\n", encoding="utf-8")
        sync.synchronize(self.root, bootstrap=True)

    def read(self, path):
        return (self.root / path).read_text(encoding="utf-8")

    def write(self, path, content):
        (self.root / path).write_text(content, encoding="utf-8")

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def assert_group(self, group, entries):
        for path in sync.paths_for(group):
            self.assertEqual(sync.parse_document(path, self.read(path)), entries, path)

    def test_bootstrap_retains_existing_rules_and_creates_valid_yaml(self):
        self.assert_group("Direct", ["DOMAIN-SUFFIX,example.cn"])
        self.assert_group("Proxy", ["DOMAIN-KEYWORD,example"])
        self.assertEqual(yaml.safe_load(self.read("clash/Direct.yaml")), {"payload": ["DOMAIN-SUFFIX,example.cn"]})

    def test_each_client_and_legacy_path_can_be_the_source(self):
        for group in sync.GROUPS:
            for index, path in enumerate(sync.paths_for(group)):
                with self.subTest(path=path):
                    entries = [f"DOMAIN,new-{group.lower()}-{index}.example"]
                    self.write(path, sync.render_document(path, entries))
                    sync.synchronize(self.root)
                    self.assert_group(group, entries)
                    self.assertEqual(sync.synchronize(self.root, check=True), [])

    def test_deletion_from_list_propagates(self):
        entries = ["DOMAIN,a.example", "DOMAIN,b.example"]
        self.write("surge/Direct.list", sync.render_document("surge/Direct.list", entries))
        sync.synchronize(self.root)
        self.write("loon/Direct.list", "DOMAIN,b.example\n")
        sync.synchronize(self.root)
        self.assert_group("Direct", ["DOMAIN,b.example"])

    def test_empty_yaml_and_empty_list_can_clear_a_group(self):
        self.write("clash/Direct.yaml", "payload: []\n")
        sync.synchronize(self.root)
        self.assert_group("Direct", [])
        self.write("Proxy.list", "")
        sync.synchronize(self.root)
        self.assert_group("Proxy", [])

    def test_different_groups_can_change_together(self):
        self.write("surge/Direct.list", "DOMAIN,new-direct.example\n")
        self.write("clash/Proxy.yaml", "payload:\n  - 'DOMAIN,new-proxy.example'\n")
        sync.synchronize(self.root)
        self.assert_group("Direct", ["DOMAIN,new-direct.example"])
        self.assert_group("Proxy", ["DOMAIN,new-proxy.example"])

    def test_identical_edits_in_multiple_replicas_are_accepted(self):
        self.write("surge/Direct.list", "DOMAIN,new.example\n")
        self.write("loon/Direct.list", "DOMAIN,new.example\n")
        sync.synchronize(self.root)
        self.assert_group("Direct", ["DOMAIN,new.example"])

    def test_conflicting_changes_fail_without_partial_writes(self):
        self.write("surge/Direct.list", "DOMAIN,new-direct.example\n")
        self.write("loon/Proxy.list", "DOMAIN,one.example\n")
        self.write("clash/Proxy.yaml", "payload:\n  - DOMAIN,two.example\n")
        before = self.snapshot()
        with self.assertRaisesRegex(sync.SyncError, "冲突"):
            sync.synchronize(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_check_reports_pending_files_without_writing(self):
        self.write("loon/Direct.list", "DOMAIN,new.example\n")
        before = self.snapshot()
        self.assertIn("clash/Direct.yaml", sync.synchronize(self.root, check=True))
        self.assertEqual(self.snapshot(), before)

    def test_unsupported_rule_and_parameter_fail_without_writing(self):
        for content in ("URL-REGEX,^https://example.com\n", "DOMAIN,example.com,extended-matching\n", "DOMAIN,example.com,DIRECT\n"):
            with self.subTest(content=content):
                self.write("surge/Proxy.list", content)
                before = self.snapshot()
                with self.assertRaises(sync.SyncError):
                    sync.synchronize(self.root)
                self.assertEqual(self.snapshot(), before)

    def test_missing_file_requires_explicit_empty_list(self):
        (self.root / "loon/Direct.list").unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(sync.SyncError, "文件缺失"):
            sync.synchronize(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_yaml_rejects_extra_keys_duplicate_payload_and_wrong_types(self):
        for content in ("payload: null", "payload: [123]", "payload: []\nrules: []", "payload: []\npayload: []", "payload: ["):
            with self.subTest(content=content):
                self.write("clash/Direct.yaml", content)
                before = self.snapshot()
                with self.assertRaises(sync.SyncError):
                    sync.synchronize(self.root)
                self.assertEqual(self.snapshot(), before)

    def test_comments_order_and_ip_options_survive_round_trip(self):
        entries = ["# 自定义直连", "DOMAIN-SUFFIX,example.cn", "", "# IP", "IP-CIDR,192.0.2.0/24,no-resolve", "IP-CIDR6,2001:db8::/32,no-resolve", "GEOIP,CN,no-resolve", "IP-ASN,13335,no-resolve"]
        self.write("surge/Direct.list", sync.render_document("surge/Direct.list", entries))
        sync.synchronize(self.root)
        self.assert_group("Direct", entries)
        updated = [*entries, "DOMAIN,last.example"]
        self.write("clash/Direct.yaml", sync.render_document("clash/Direct.yaml", updated))
        sync.synchronize(self.root)
        self.assert_group("Direct", updated)

    def test_invalid_ip_and_domain_fail(self):
        for rule in ("DOMAIN,https://example.com", "IP-CIDR,999.2.3.4/24", "IP-CIDR6,192.0.2.0/24", "IP-ASN,xyz", "GEOIP,China"):
            with self.subTest(rule=rule):
                with self.assertRaises((sync.SyncError, UnicodeError)):
                    sync.normalize_rule(rule, "test")

    def test_equivalent_formatting_changes_do_not_create_conflict(self):
        self.write("surge/Direct.list", "domain-suffix, EXAMPLE.CN\n\n")
        self.write("clash/Direct.yaml", "payload: ['DOMAIN-SUFFIX,example.cn']\n")
        sync.synchronize(self.root)
        self.assert_group("Direct", ["DOMAIN-SUFFIX,example.cn"])

    def test_second_bootstrap_cannot_replace_existing_state(self):
        before = self.snapshot()
        with self.assertRaises(sync.SyncError):
            sync.synchronize(self.root, bootstrap=True)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
