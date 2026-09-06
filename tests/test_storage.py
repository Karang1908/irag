from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from irag import config, db, ingest, reports, sessions, structure
from irag.dashboard import _advance_job, _create_job, _job_row


class StorageTests(unittest.TestCase):
    def test_migration_backup_history_and_downgrade_refusal(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / ".irag" / "memory.db"
            path.parent.mkdir()
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            conn.execute("INSERT INTO meta VALUES('schema_version','1')")
            conn.commit()
            conn.close()
            upgraded = db.ensure_db(path)
            versions = [row[0] for row in upgraded.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
            self.assertEqual(versions, [2, 3, 4, 5, 6, 7])
            self.assertTrue(list((path.parent / "backups").glob(
                "pre-migration-v1-to-v7-*.db")))
            upgraded.execute(
                "UPDATE meta SET value='999' WHERE key='schema_version'")
            upgraded.commit()
            upgraded.close()
            with self.assertRaises(SystemExit):
                db.ensure_db(path)

    def test_incremental_scan_and_snapshot_rename_keep_page_identity(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            cfg = config.load(root)
            path = root / ".irag" / "memory.db"
            conn = db.ensure_db(path)
            old = root / "old.py"
            old.write_text("def first():\n    return 1\n", encoding="utf-8")
            ingest.snapshot(conn, cfg, root)
            first = structure.scan(conn, cfg, root)
            self.assertFalse(first["incremental"])
            page_id = conn.execute(
                "SELECT page_id FROM pages WHERE subject_id='old.py'"
            ).fetchone()["page_id"]
            old.write_text("def second():\n    return 2\n", encoding="utf-8")
            ingest.snapshot(conn, cfg, root)
            second = structure.scan(conn, cfg, root)
            self.assertTrue(second["incremental"])
            self.assertEqual(second["parsed_files"], 1)
            new = root / "new.py"
            old.rename(new)
            ingest.snapshot(conn, cfg, root)
            renamed = conn.execute(
                "SELECT page_id FROM pages WHERE subject_id='new.py'"
            ).fetchone()
            self.assertEqual(renamed["page_id"], page_id)
            conn.close()

    def test_replayed_git_rename_does_not_recreate_old_tombstone(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"],
                           cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "t"],
                           cwd=root, check=True)
            old = root / "old.py"
            old.write_text("def stable():\n    return 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "old.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root,
                           check=True)
            (root / ".irag").mkdir()
            cfg = config.load(root)
            conn = db.ensure_db(root / ".irag" / "memory.db")
            ingest.sync(conn, cfg, root)
            page = conn.execute(
                "SELECT page_id FROM pages WHERE subject_id='old.py'"
            ).fetchone()
            conn.execute(
                "INSERT INTO revisions(page_id,version_number,body_markdown) "
                "VALUES(?,1,'old history')", (page["page_id"],))
            conn.commit()
            old.rename(root / "new.py")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "rename"], cwd=root,
                           check=True)
            ingest.ingest_commit(conn, cfg, "HEAD", root)
            ingest.ingest_commit(conn, cfg, "HEAD", root)
            self.assertIsNone(conn.execute(
                "SELECT page_id FROM pages WHERE subject_id='old.py'"
            ).fetchone())
            moved = conn.execute(
                "SELECT page_id FROM pages WHERE subject_id='new.py'"
            ).fetchone()
            self.assertEqual(moved["page_id"], page["page_id"])
            revisions = conn.execute(
                "SELECT COUNT(*) n FROM revisions WHERE page_id=?",
                (moved["page_id"],)).fetchone()["n"]
            self.assertEqual(revisions, 1)
            conn.close()

    def test_merge_commit_ingests_its_first_parent_changes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-qb", "main"], cwd=root,
                           check=True)
            subprocess.run(["git", "config", "user.email", "t@t"],
                           cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=root,
                           check=True)
            (root / "a.py").write_text("base = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root,
                           check=True)
            subprocess.run(["git", "checkout", "-qb", "feature"], cwd=root,
                           check=True)
            (root / "a.py").write_text("feature = 2\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "feature"], cwd=root,
                           check=True)
            subprocess.run(["git", "checkout", "-q", "main"], cwd=root,
                           check=True)
            (root / "main.py").write_text("main = 3\n", encoding="utf-8")
            subprocess.run(["git", "add", "main.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "main work"], cwd=root,
                           check=True)
            subprocess.run(["git", "merge", "-q", "--no-ff", "feature",
                            "-m", "merge feature"], cwd=root, check=True)
            (root / ".irag").mkdir()
            cfg = config.load(root)
            conn = db.ensure_db(root / ".irag" / "memory.db")
            inserted = ingest.ingest_commit(conn, cfg, "HEAD", root)
            subjects = {row["subject_id"] for row in conn.execute(
                "SELECT subject_id FROM events")}
            self.assertEqual(inserted, 1)
            self.assertEqual(subjects, {"a.py"})
            conn.close()

    def test_durable_job_and_escaped_agent_report(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            conn = db.ensure_db(root / ".irag" / "memory.db")
            job = _create_job(conn, "update")
            _advance_job(conn, job["job_id"], "scan complete", 30)
            self.assertEqual(_job_row(conn, job["job_id"])["progress"], 30)
            page = db.get_or_create_page(conn, "<script>.py", "file", "file")
            contradiction_id = conn.execute(
                "INSERT INTO contradictions(page_id,claim,truth,ctype) "
                "VALUES(?,?,?,'missing_path')",
                (page["page_id"], "<img src=x onerror=alert(1)>", "missing")
            ).lastrowid
            conn.commit()
            rendered = reports.contradiction_html(conn, root)
            self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered)
            self.assertNotIn("<img src=x onerror=alert(1)>", rendered)
            self.assertIn("Master contradiction repair brief", rendered)
            single = reports.contradiction_html(
                conn, root, [contradiction_id])
            self.assertIn(f"Contradiction #{contradiction_id}", single)
            self.assertNotIn("Master contradiction repair brief", single)
            missing = reports.contradiction_html(conn, root, [999])
            self.assertIn("Contradiction #999", missing)
            self.assertIn("No open contradictions in this scope", missing)
            self.assertNotIn("Master contradiction repair brief", missing)
            conn.close()

    def test_session_keeps_lossless_critical_context(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".irag").mkdir()
            conn = db.ensure_db(root / ".irag" / "memory.db")
            sid = sessions.begin(conn, agent="test", key="session-key")
            page = db.get_or_create_page(conn, "core.py", "file", "file")
            conn.execute(
                "INSERT INTO revisions(page_id,version_number,body_markdown,"
                "change_summary,session_key) VALUES(?,1,'body',?,?)",
                (page["page_id"], "Preserved the atomic write invariant",
                 "session-key"))
            conn.execute(
                "INSERT INTO events(event_type,subject_id,payload,status,"
                "session_key) VALUES('decision','.',?,'completed',?)",
                ('{"text":"Never overwrite the only memory database"}',
                 "session-key"))
            conn.commit()
            result = sessions.end(conn, None, narrate=False,
                                  key="session-key")
            self.assertEqual(result["session_id"], sid)
            stored = conn.execute(
                "SELECT critical_context FROM sessions WHERE session_id=?",
                (sid,)).fetchone()["critical_context"]
            critical = db.json_object(stored)
            self.assertEqual(critical["decisions"],
                             ["Never overwrite the only memory database"])
            self.assertEqual(
                critical["changes_detail"][0]["change_summary"],
                "Preserved the atomic write invariant")
            conn.close()


if __name__ == "__main__":
    unittest.main()
