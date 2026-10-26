"""
TEST-ONLY: undo testing/generate_anomaly_test.py --apply (part of REVERT-REPLAY).

Deletes the generated readings and alerts (by the IDs in the manifest) and puts back the rows that
were replaced, copied from the backup taken before. Data recorded since then is kept.
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("ARGUS_TEST_DB", os.path.join(ROOT, "database.db"))
MANIFEST = os.environ.get("ARGUS_TEST_MANIFEST", os.path.join(ROOT, "testing", "anomaly_test_manifest.json"))


def main():
    if not os.path.exists(MANIFEST):
        sys.exit("no applied test run found (testing/anomaly_test_manifest.json)")
    m = json.load(open(MANIFEST))
    conn = sqlite3.connect(DB)
    conn.execute("ATTACH DATABASE ? AS backup", (m["backup"],))

    def ids_sql(ids):
        return ",".join(str(int(i)) for i in ids) or "NULL"

    conn.execute(f"DELETE FROM telemetry_logs WHERE id IN ({ids_sql(m['inserted_telemetry_ids'])})")
    conn.execute(f"DELETE FROM notification_logs WHERE id IN ({ids_sql(m['inserted_notification_ids'])})")
    for table, ids in (("telemetry_logs", m["removed_telemetry_ids"]), ("notification_logs", m["removed_notification_ids"])):
        cols = [r[1] for r in conn.execute(f"PRAGMA backup.table_info({table})")]
        conn.execute(f"INSERT OR REPLACE INTO main.{table} ({','.join(cols)}) "
                     f"SELECT {','.join(cols)} FROM backup.{table} WHERE id IN ({ids_sql(ids)})")
    conn.commit()
    conn.execute("DETACH DATABASE backup")
    print(f"removed {len(m['inserted_telemetry_ids'])} generated readings and {len(m['inserted_notification_ids'])} alerts; "
          f"restored {len(m['removed_telemetry_ids'])} readings and {len(m['removed_notification_ids'])} alerts")
    os.rename(MANIFEST, MANIFEST + ".reverted")


if __name__ == "__main__":
    main()
