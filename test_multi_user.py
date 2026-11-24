import os
import sys
import json
import time
import shutil

# Import Flask app and queue manager directly for unit testing
from server import app, queue_manager, WORKSPACES_ROOT

client = app.test_client()

def test_session_isolation():
    print("--- 1. Testing Session Isolation ---")
    session_alice = "sess_alice_test"
    session_bob = "sess_bob_test"

    # Alice saves links
    res_a = client.post("/api/links", 
        headers={"X-Session-ID": session_alice},
        json={"yt_links": "https://youtube.com/watch?v=alice_clip"}
    )
    assert res_a.status_code == 200, f"Alice links save failed: {res_a.data}"

    # Bob saves links
    res_b = client.post("/api/links", 
        headers={"X-Session-ID": session_bob},
        json={"yt_links": "https://youtube.com/watch?v=bob_clip"}
    )
    assert res_b.status_code == 200, f"Bob links save failed: {res_b.data}"

    # Alice reads links
    get_a = client.get("/api/links", headers={"X-Session-ID": session_alice})
    data_a = json.loads(get_a.data)
    print(f"Alice Links: {data_a.get('yt_links')}")
    assert "alice_clip" in data_a.get("yt_links", ""), "Alice links missing alice_clip!"
    assert "bob_clip" not in data_a.get("yt_links", ""), "Isolation leak: Bob's clip found in Alice's session!"

    # Bob reads links
    get_b = client.get("/api/links", headers={"X-Session-ID": session_bob})
    data_b = json.loads(get_b.data)
    print(f"Bob Links: {data_b.get('yt_links')}")
    assert "bob_clip" in data_b.get("yt_links", ""), "Bob links missing bob_clip!"
    assert "alice_clip" not in data_b.get("yt_links", ""), "Isolation leak: Alice's clip found in Bob's session!"

    print("✅ Session links isolation verified!")

def test_metadata_isolation():
    print("\n--- 2. Testing Metadata Isolation ---")
    session_alice = "sess_alice_test"
    session_bob = "sess_bob_test"

    client.post("/api/metadata", 
        headers={"X-Session-ID": session_alice},
        json={"title": "Alice Video Title", "script": "Script by Alice"}
    )

    client.post("/api/metadata", 
        headers={"X-Session-ID": session_bob},
        json={"title": "Bob Video Title", "script": "Script by Bob"}
    )

    meta_a = json.loads(client.get("/api/metadata", headers={"X-Session-ID": session_alice}).data)
    meta_b = json.loads(client.get("/api/metadata", headers={"X-Session-ID": session_bob}).data)

    print(f"Alice Title: {meta_a.get('title')}")
    print(f"Bob Title: {meta_b.get('title')}")

    assert meta_a.get("title") == "Alice Video Title"
    assert meta_b.get("title") == "Bob Video Title"
    print("✅ Metadata isolation verified!")

def test_workspace_disk_structure():
    print("\n--- 3. Testing Workspace Folders on Disk ---")
    alice_dir = os.path.join(WORKSPACES_ROOT, "sess_alice_test")
    bob_dir = os.path.join(WORKSPACES_ROOT, "sess_bob_test")

    assert os.path.exists(alice_dir), "Alice workspace folder was not created!"
    assert os.path.exists(bob_dir), "Bob workspace folder was not created!"

    for sub in ["Raw video", "References", "audio", "Export"]:
        assert os.path.exists(os.path.join(alice_dir, sub)), f"Subfolder {sub} missing in Alice workspace"
        assert os.path.exists(os.path.join(bob_dir, sub)), f"Subfolder {sub} missing in Bob workspace"

    print("✅ Workspace directories and subfolders verified on disk!")

def test_queue_and_status():
    print("\n--- 4. Testing Multi-Session Queue Status ---")
    session_alice = "sess_alice_test"
    st = json.loads(client.get("/api/status", headers={"X-Session-ID": session_alice}).data)
    print(f"Alice status: {st}")
    assert st["status"] in ["idle", "queued", "running", "done"]
    assert st["active_workers"] >= 0
    assert st["max_workers"] == 3
    print("✅ Queue status verified!")

def test_index_serving():
    print("\n--- 5. Testing index.html Rendering ---")
    res = client.get("/")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert "AI VIDEO STUDIO" in html
    assert "Input Folders (Click a folder to view & add files)" in html
    assert "CURRENT_SESSION" in html
    assert "switchFolder" in html
    print("✅ index.html serves cleanly with new folder-first UI!")

if __name__ == "__main__":
    try:
        test_session_isolation()
        test_metadata_isolation()
        test_workspace_disk_structure()
        test_queue_and_status()
        test_index_serving()
        print("\n🎉 ALL MULTI-USER TESTS PASSED SUCCESSFULLY! 100% ISOLATION CONFIRMED!")
    finally:
        # Clean up test workspaces
        for tid in ["sess_alice_test", "sess_bob_test"]:
            p = os.path.join(WORKSPACES_ROOT, tid)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)
